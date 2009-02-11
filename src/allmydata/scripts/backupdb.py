
# the backupdb is only available if sqlite3 is available. Python-2.5.x and
# beyond include sqlite3 in the standard library. For python-2.4, the
# "pysqlite2" package (which, despite the confusing name, uses sqlite3) must
# be installed. On debian, install python-pysqlite2

import os.path, sys, time, random, stat

DAY = 24*60*60
MONTH = 30*DAY

SCHEMA_v1 = """
CREATE TABLE version
(
 version INTEGER  -- contains one row, set to 1
);

CREATE TABLE local_files
(
 path  VARCHAR(1024) PRIMARY KEY, -- index, this is os.path.abspath(fn)
 size  INTEGER,       -- os.stat(fn)[stat.ST_SIZE]
 mtime NUMBER,        -- os.stat(fn)[stat.ST_MTIME]
 ctime NUMBER,        -- os.stat(fn)[stat.ST_CTIME]
 fileid INTEGER
);

CREATE TABLE caps
(
 fileid INTEGER PRIMARY KEY AUTOINCREMENT,
 filecap VARCHAR(256) UNIQUE       -- URI:CHK:...
);

CREATE TABLE last_upload
(
 fileid INTEGER PRIMARY KEY,
 last_uploaded TIMESTAMP,
 last_checked TIMESTAMP
);

"""

def get_backupdb(dbfile, stderr=sys.stderr):
    # open or create the given backupdb file. The parent directory must
    # exist.
    try:
        import sqlite3
        sqlite = sqlite3 # pyflakes whines about 'import sqlite3 as sqlite' ..
    except ImportError:
        try:
            from pysqlite2 import dbapi2
            sqlite = dbapi2 # .. when this clause does it too
        except ImportError:
            print >>stderr, """\
The backup command uses a SQLite database to avoid duplicate uploads, but
I was unable to import a python sqlite library. You have two options:

 1: Install a python sqlite library. python2.5 and beyond have one built-in.
    If you are using python2.4, you can install the 'pysqlite' package,
    perhaps with 'apt-get install python-pysqlite2', or 'easy_install
    pysqlite', or by installing the 'pysqlite' package from
    http://pypi.python.org . Make sure you get the version with support for
    SQLite 3.

 2: Run me with the --no-backupdb option to disable use of the database. This
    will be somewhat slower, since I will be unable to avoid re-uploading
    files that were uploaded in the past, but the basic functionality will be
    unimpaired.
"""
            return None

    must_create = not os.path.exists(dbfile)
    try:
        db = sqlite.connect(dbfile)
    except (EnvironmentError, sqlite.OperationalError), e:
        print >>stderr, "Unable to create/open backupdb file %s: %s" % (dbfile, e)
        return None

    c = db.cursor()
    if must_create:
        c.executescript(SCHEMA_v1)
        c.execute("INSERT INTO version (version) VALUES (1)")
        db.commit()

    try:
        c.execute("SELECT version FROM version")
        version = c.fetchone()[0]
    except sqlite.DatabaseError, e:
        # this indicates that the file is not a compatible database format.
        # Perhaps it was created with an old version, or it might be junk.
        print >>stderr, "backupdb file is unusable: %s" % e
        return None

    if version == 1:
        return BackupDB_v1(sqlite, db)
    print >>stderr, "Unable to handle backupdb version %s" % version
    return None

MUST_UPLOAD, ALREADY_UPLOADED = range(2)
class Result:
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
        self.bdb.did_upload(filecap,
                            self.path,
                            self.mtime, self.ctime, self.size)

    def should_check(self):
        return self.should_check_p

    def did_check_healthy(self, results):
        self.bdb.did_check_healthy(self.filecap, results)

class BackupDB_v1:
    VERSION = 1
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

        I return a Results object, synchronously. If r.was_uploaded() returns
        False, you should upload the file. When you are finished uploading
        it, call r.did_upload(filecap), so I can update my database.

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

        path = os.path.abspath(path)
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
            return Result(self, None, False, path, mtime, ctime, size)
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
            return Result(self, None, False, path, mtime, ctime, size)

        # at this point, we're allowed to assume the file hasn't been changed
        (filecap, last_checked) = row2
        age = now - last_checked

        probability = ((age - self.NO_CHECK_BEFORE) /
                       (self.ALWAYS_CHECK_AFTER - self.NO_CHECK_BEFORE))
        probability = min(max(probability, 0.0), 1.0)
        should_check = bool(random.random() < probability)

        return Result(self, filecap, should_check, path, mtime, ctime, size)

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

    def did_upload(self, filecap, path, mtime, ctime, size):
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

    def did_check_healthy(self, filecap, results):
        now = time.time()
        fileid = self.get_or_allocate_fileid_for_cap(filecap)
        self.cursor.execute("UPDATE last_upload"
                            " SET last_checked=?"
                            " WHERE fileid=?",
                            (now, fileid))
        self.connection.commit()
