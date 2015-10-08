
import sys

from allmydata.util.dbutil import get_db, DBError


# magic-folder db schema version 1
SCHEMA_v1 = """
CREATE TABLE version
(
 version INTEGER  -- contains one row, set to 1
);

CREATE TABLE local_files
(
 path                VARCHAR(1024) PRIMARY KEY, -- UTF-8 filename relative to local magic folder dir
 -- note that size is before mtime and ctime here, but after in function parameters
 size                INTEGER,                   -- ST_SIZE, or NULL if the file has been deleted
 mtime               REAL,                      -- ST_MTIME
 ctime               REAL,                      -- ST_CTIME
 version             INTEGER,
 last_uploaded_uri   VARCHAR(256) UNIQUE,       -- URI:CHK:...
 last_downloaded_uri VARCHAR(256) UNIQUE,       -- URI:CHK:...
 last_downloaded_timestamp REAL
);
"""


def get_magicfolderdb(dbfile, stderr=sys.stderr,
                      create_version=(SCHEMA_v1, 1), just_create=False):
    # Open or create the given backupdb file. The parent directory must
    # exist.
    try:
        (sqlite3, db) = get_db(dbfile, stderr, create_version,
                               just_create=just_create, dbname="magicfolderdb")
        if create_version[1] in (1, 2):
            return MagicFolderDB(sqlite3, db)
        else:
            print >>stderr, "invalid magicfolderdb schema version specified"
            return None
    except DBError, e:
        print >>stderr, e
        return None


class MagicFolderDB(object):
    VERSION = 1

    def __init__(self, sqlite_module, connection):
        self.sqlite_module = sqlite_module
        self.connection = connection
        self.cursor = connection.cursor()

    def check_file_db_exists(self, path):
        """I will tell you if a given file has an entry in my database or not
        by returning True or False.
        """
        c = self.cursor
        c.execute("SELECT size,mtime,ctime"
                  " FROM local_files"
                  " WHERE path=?",
                  (path,))
        row = self.cursor.fetchone()
        if not row:
            return False
        else:
            return True

    def get_all_relpaths(self):
        """
        Retrieve a set of all relpaths of files that have had an entry in magic folder db
        (i.e. that have been downloaded at least once).
        """
        self.cursor.execute("SELECT path FROM local_files")
        rows = self.cursor.fetchall()
        return set([r[0] for r in rows])

    def get_last_downloaded_uri(self, relpath_u):
        """
        Return the last downloaded uri recorded in the magic folder db.
        If none are found then return None.
        """
        c = self.cursor
        c.execute("SELECT last_downloaded_uri"
                  " FROM local_files"
                  " WHERE path=?",
                  (relpath_u,))
        row = self.cursor.fetchone()
        if not row:
            return None
        else:
            return row[0]

    def get_local_file_version(self, relpath_u):
        """
        Return the version of a local file tracked by our magic folder db.
        If no db entry is found then return None.
        """
        c = self.cursor
        c.execute("SELECT version"
                  " FROM local_files"
                  " WHERE path=?",
                  (relpath_u,))
        row = self.cursor.fetchone()
        if not row:
            return None
        else:
            return row[0]

    def did_upload_version(self, filecap, relpath_u, version, pathinfo):
        print "did_upload_version(%r, %r, %r, %r)" % (filecap, relpath_u, version, pathinfo)
        try:
            print "insert"
            self.cursor.execute("INSERT INTO local_files VALUES (?,?,?,?,?,?)",
                                (relpath_u, pathinfo.size, pathinfo.mtime, pathinfo.ctime, version, filecap, pathinfo.mtime))
        except (self.sqlite_module.IntegrityError, self.sqlite_module.OperationalError):
            print "err... update"
            self.cursor.execute("UPDATE local_files"
                                " SET size=?, mtime=?, ctime=?, version=?, last_downloaded_uri=?, last_downloaded_timestamp=?"
                                " WHERE path=?",
                                (pathinfo.size, pathinfo.mtime, pathinfo.ctime, version, filecap, pathinfo.mtime, relpath_u))
        self.connection.commit()
        print "commited"

    def is_new_file(self, pathinfo, relpath_u):
        """
        Returns true if the file's current pathinfo (size, mtime, and ctime) has
        changed from the pathinfo previously stored in the db.
        """
        #print "is_new_file(%r, %r)" % (pathinfo, relpath_u)
        c = self.cursor
        c.execute("SELECT size, mtime, ctime"
                  " FROM local_files"
                  " WHERE path=?",
                  (relpath_u,))
        row = self.cursor.fetchone()
        if not row:
            return True
        return (pathinfo.size, pathinfo.mtime, pathinfo.ctime) != row
