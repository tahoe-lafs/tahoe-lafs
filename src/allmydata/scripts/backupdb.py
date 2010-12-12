
# the backupdb is only available if sqlite3 is available. Python-2.5.x and
# beyond include sqlite3 in the standard library. For python-2.4, the
# "pysqlite2" "package" (or "module") (which, despite the confusing name, uses
# sqlite3, and which, confusingly, comes in the "pysqlite" "distribution" (or
# "package")) must be installed. On debian, install python-pysqlite2

import os.path, sys, time, random, stat
from allmydata.util.netstring import netstring
from allmydata.util.hashutil import backupdb_dirhash
from allmydata.util import base32
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import to_str

DAY = 24*60*60
MONTH = 30*DAY

SCHEMA_v1 = """
CREATE TABLE version -- added in v1
(
 version INTEGER  -- contains one row, set to 2
);

CREATE TABLE local_files -- added in v1
(
 path  VARCHAR(1024) PRIMARY KEY, -- index, this is an absolute UTF-8-encoded local filename
 size  INTEGER,       -- os.stat(fn)[stat.ST_SIZE]
 mtime NUMBER,        -- os.stat(fn)[stat.ST_MTIME]
 ctime NUMBER,        -- os.stat(fn)[stat.ST_CTIME]
 fileid INTEGER
);

CREATE TABLE caps -- added in v1
(
 fileid INTEGER PRIMARY KEY AUTOINCREMENT,
 filecap VARCHAR(256) UNIQUE       -- URI:CHK:...
);

CREATE TABLE last_upload -- added in v1
(
 fileid INTEGER PRIMARY KEY,
 last_uploaded TIMESTAMP,
 last_checked TIMESTAMP
);

"""

TABLE_DIRECTORY = """

CREATE TABLE directories -- added in v2
(
 dirhash varchar(256) PRIMARY KEY,  -- base32(dirhash)
 dircap varchar(256),               -- URI:DIR2-CHK:...
 last_uploaded TIMESTAMP,
 last_checked TIMESTAMP
);

"""

SCHEMA_v2 = SCHEMA_v1 + TABLE_DIRECTORY

UPDATE_v1_to_v2 = TABLE_DIRECTORY + """
UPDATE version SET version=2;
"""


def get_backupdb(dbfile, stderr=sys.stderr,
                 create_version=(SCHEMA_v2, 2), just_create=False):
    # open or create the given backupdb file. The parent directory must
    # exist.
    try:
        import sqlite3
        sqlite = sqlite3 # pyflakes whines about 'import sqlite3 as sqlite' ..
    except ImportError:
        from pysqlite2 import dbapi2
        sqlite = dbapi2 # .. when this clause does it too
        # This import should never fail, because setuptools requires that the
        # "pysqlite" distribution is present at start time (if on Python < 2.5).

    must_create = not os.path.exists(dbfile)
    try:
        db = sqlite.connect(dbfile)
    except (EnvironmentError, sqlite.OperationalError), e:
        print >>stderr, "Unable to create/open backupdb file %s: %s" % (dbfile, e)
        return None

    c = db.cursor()
    if must_create:
        schema, version = create_version
        c.executescript(schema)
        c.execute("INSERT INTO version (version) VALUES (?)", (version,))
        db.commit()

    try:
        c.execute("SELECT version FROM version")
        version = c.fetchone()[0]
    except sqlite.DatabaseError, e:
        # this indicates that the file is not a compatible database format.
        # Perhaps it was created with an old version, or it might be junk.
        print >>stderr, "backupdb file is unusable: %s" % e
        return None

    if just_create: # for tests
        return True

    if version == 1:
        c.executescript(UPDATE_v1_to_v2)
        db.commit()
        version = 2
    if version == 2:
        return BackupDB_v2(sqlite, db)
    print >>stderr, "Unable to handle backupdb version %s" % version
    return None

class FileResult:
    def __init__(self, bdb, filecap, should_check,
                 path, mtime, ctime, size):
        self.bdb = bdb
        self.filecap = filecap
        self.should_check_p = should_check

        self.path = path
        self.mtime = mtime
        self.ctime = ctime
        self.size = size

    def was_uploaded(self):
        if self.filecap:
            return self.filecap
        return False

    def did_upload(self, filecap):
        self.bdb.did_upload_file(filecap, self.path,
                                 self.mtime, self.ctime, self.size)

    def should_check(self):
        return self.should_check_p

    def did_check_healthy(self, results):
        self.bdb.did_check_file_healthy(self.filecap, results)

class DirectoryResult:
    def __init__(self, bdb, dirhash, dircap, should_check):
        self.bdb = bdb
        self.dircap = dircap
        self.should_check_p = should_check
        self.dirhash = dirhash

    def was_created(self):
        if self.dircap:
            return self.dircap
        return False

    def did_create(self, dircap):
        self.bdb.did_create_directory(dircap, self.dirhash)

    def should_check(self):
        return self.should_check_p

    def did_check_healthy(self, results):
        self.bdb.did_check_directory_healthy(self.dircap, results)

class BackupDB_v2:
    VERSION = 2
    NO_CHECK_BEFORE = 1*MONTH
    ALWAYS_CHECK_AFTER = 2*MONTH

    def __init__(self, sqlite_module, connection):
        self.sqlite_module = sqlite_module
        self.connection = connection
        self.cursor = connection.cursor()

    def check_file(self, path, use_timestamps=True):
        """I will tell you if a given local file needs to be uploaded or not,
        by looking in a database and seeing if I have a record of this file
        having been uploaded earlier.

        I return a FileResults object, synchronously. If r.was_uploaded()
        returns False, you should upload the file. When you are finished
        uploading it, call r.did_upload(filecap), so I can update my
        database.

        If was_uploaded() returns a filecap, you might be able to avoid an
        upload. Call r.should_check(), and if it says False, you can skip the
        upload and use the filecap returned by was_uploaded().

        If should_check() returns True, you should perform a filecheck on the
        filecap returned by was_uploaded(). If the check indicates the file
        is healthy, please call r.did_check_healthy(checker_results) so I can
        update the database, using the de-JSONized response from the webapi
        t=check call for 'checker_results'. If the check indicates the file
        is not healthy, please upload the file and call r.did_upload(filecap)
        when you're done.

        I use_timestamps=True (the default), I will compare ctime and mtime
        of the local file against an entry in my database, and consider the
        file to be unchanged if ctime, mtime, and filesize are all the same
        as the earlier version. If use_timestamps=False, I will not trust the
        timestamps, so more files (perhaps all) will be marked as needing
        upload. A future version of this database may hash the file to make
        equality decisions, in which case use_timestamps=False will not
        always imply r.must_upload()==True.

        'path' points to a local file on disk, possibly relative to the
        current working directory. The database stores absolute pathnames.
        """

        path = abspath_expanduser_unicode(path)
        s = os.stat(path)
        size = s[stat.ST_SIZE]
        ctime = s[stat.ST_CTIME]
        mtime = s[stat.ST_MTIME]

        now = time.time()
        c = self.cursor

        c.execute("SELECT size,mtime,ctime,fileid"
                  " FROM local_files"
                  " WHERE path=?",
                  (path,))
        row = self.cursor.fetchone()
        if not row:
            return FileResult(self, None, False, path, mtime, ctime, size)
        (last_size,last_mtime,last_ctime,last_fileid) = row

        c.execute("SELECT caps.filecap, last_upload.last_checked"
                  " FROM caps,last_upload"
                  " WHERE caps.fileid=? AND last_upload.fileid=?",
                  (last_fileid, last_fileid))
        row2 = c.fetchone()

        if ((last_size != size
             or not use_timestamps
             or last_mtime != mtime
             or last_ctime != ctime) # the file has been changed
            or (not row2) # we somehow forgot where we put the file last time
            ):
            c.execute("DELETE FROM local_files WHERE path=?", (path,))
            self.connection.commit()
            return FileResult(self, None, False, path, mtime, ctime, size)

        # at this point, we're allowed to assume the file hasn't been changed
        (filecap, last_checked) = row2
        age = now - last_checked

        probability = ((age - self.NO_CHECK_BEFORE) /
                       (self.ALWAYS_CHECK_AFTER - self.NO_CHECK_BEFORE))
        probability = min(max(probability, 0.0), 1.0)
        should_check = bool(random.random() < probability)

        return FileResult(self, to_str(filecap), should_check,
                          path, mtime, ctime, size)

    def get_or_allocate_fileid_for_cap(self, filecap):
        # find an existing fileid for this filecap, or insert a new one. The
        # caller is required to commit() afterwards.

        # mysql has "INSERT ... ON DUPLICATE KEY UPDATE", but not sqlite
        # sqlite has "INSERT ON CONFLICT REPLACE", but not mysql
        # So we use INSERT, ignore any error, then a SELECT
        c = self.cursor
        try:
            c.execute("INSERT INTO caps (filecap) VALUES (?)", (filecap,))
        except (self.sqlite_module.IntegrityError, self.sqlite_module.OperationalError):
            # sqlite3 on sid gives IntegrityError
            # pysqlite2 on dapper gives OperationalError
            pass
        c.execute("SELECT fileid FROM caps WHERE filecap=?", (filecap,))
        foundrow = c.fetchone()
        assert foundrow
        fileid = foundrow[0]
        return fileid

    def did_upload_file(self, filecap, path, mtime, ctime, size):
        now = time.time()
        fileid = self.get_or_allocate_fileid_for_cap(filecap)
        try:
            self.cursor.execute("INSERT INTO last_upload VALUES (?,?,?)",
                                (fileid, now, now))
        except (self.sqlite_module.IntegrityError, self.sqlite_module.OperationalError):
            self.cursor.execute("UPDATE last_upload"
                                " SET last_uploaded=?, last_checked=?"
                                " WHERE fileid=?",
                                (now, now, fileid))
        try:
            self.cursor.execute("INSERT INTO local_files VALUES (?,?,?,?,?)",
                                (path, size, mtime, ctime, fileid))
        except (self.sqlite_module.IntegrityError, self.sqlite_module.OperationalError):
            self.cursor.execute("UPDATE local_files"
                                " SET size=?, mtime=?, ctime=?, fileid=?"
                                " WHERE path=?",
                                (size, mtime, ctime, fileid, path))
        self.connection.commit()

    def did_check_file_healthy(self, filecap, results):
        now = time.time()
        fileid = self.get_or_allocate_fileid_for_cap(filecap)
        self.cursor.execute("UPDATE last_upload"
                            " SET last_checked=?"
                            " WHERE fileid=?",
                            (now, fileid))
        self.connection.commit()

    def check_directory(self, contents):
        """I will tell you if a new directory needs to be created for a given
        set of directory contents, or if I know of an existing (immutable)
        directory that can be used instead.

        'contents' should be a dictionary that maps from child name (a single
        unicode string) to immutable childcap (filecap or dircap).

        I return a DirectoryResult object, synchronously. If r.was_created()
        returns False, you should create the directory (with
        t=mkdir-immutable). When you are finished, call r.did_create(dircap)
        so I can update my database.

        If was_created() returns a dircap, you might be able to avoid the
        mkdir. Call r.should_check(), and if it says False, you can skip the
        mkdir and use the dircap returned by was_created().

        If should_check() returns True, you should perform a check operation
        on the dircap returned by was_created(). If the check indicates the
        directory is healthy, please call
        r.did_check_healthy(checker_results) so I can update the database,
        using the de-JSONized response from the webapi t=check call for
        'checker_results'. If the check indicates the directory is not
        healthy, please repair or re-create the directory and call
        r.did_create(dircap) when you're done.
        """

        now = time.time()
        entries = []
        for name in contents:
            entries.append( [name.encode("utf-8"), contents[name]] )
        entries.sort()
        data = "".join([netstring(name_utf8)+netstring(cap)
                        for (name_utf8,cap) in entries])
        dirhash = backupdb_dirhash(data)
        dirhash_s = base32.b2a(dirhash)
        c = self.cursor
        c.execute("SELECT dircap, last_checked"
                  " FROM directories WHERE dirhash=?", (dirhash_s,))
        row = c.fetchone()
        if not row:
            return DirectoryResult(self, dirhash_s, None, False)
        (dircap, last_checked) = row
        age = now - last_checked

        probability = ((age - self.NO_CHECK_BEFORE) /
                       (self.ALWAYS_CHECK_AFTER - self.NO_CHECK_BEFORE))
        probability = min(max(probability, 0.0), 1.0)
        should_check = bool(random.random() < probability)

        return DirectoryResult(self, dirhash_s, to_str(dircap), should_check)

    def did_create_directory(self, dircap, dirhash):
        now = time.time()
        # if the dirhash is already present (i.e. we've re-uploaded an
        # existing directory, possibly replacing the dircap with a new one),
        # update the record in place. Otherwise create a new record.)
        self.cursor.execute("REPLACE INTO directories VALUES (?,?,?,?)",
                            (dirhash, dircap, now, now))
        self.connection.commit()

    def did_check_directory_healthy(self, dircap, results):
        now = time.time()
        self.cursor.execute("UPDATE directories"
                            " SET last_checked=?"
                            " WHERE dircap=?",
                            (now, dircap))
        self.connection.commit()
