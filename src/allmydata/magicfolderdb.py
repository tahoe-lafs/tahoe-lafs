
import sys
from collections import namedtuple

from allmydata.util.dbutil import get_db, DBError
from allmydata.util.eliotutil import (
    RELPATH,
    VERSION,
    LAST_UPLOADED_URI,
    LAST_DOWNLOADED_URI,
    LAST_DOWNLOADED_TIMESTAMP,
    PATHINFO,
    validateSetMembership,
    validateInstanceOf,
)
from eliot import (
    Field,
    ActionType,
)

PathEntry = namedtuple('PathEntry', 'size mtime_ns ctime_ns version last_uploaded_uri '
                                    'last_downloaded_uri last_downloaded_timestamp')

PATHENTRY = Field(
    u"pathentry",
    lambda v: None if v is None else {
        "size": v.size,
        "mtime_ns": v.mtime_ns,
        "ctime_ns": v.ctime_ns,
        "version": v.version,
        "last_uploaded_uri": v.last_uploaded_uri,
        "last_downloaded_uri": v.last_downloaded_uri,
        "last_downloaded_timestamp": v.last_downloaded_timestamp,
    },
    u"The local database state of a file.",
    validateInstanceOf((type(None), PathEntry)),
)

_INSERT_OR_UPDATE = Field.for_types(
    u"insert_or_update",
    [unicode],
    u"An indication of whether the record for this upload was new or an update to a previous entry.",
    validateSetMembership({u"insert", u"update"}),
)

DID_UPLOAD_VERSION = ActionType(
    u"magic-folder-db:did-upload-version",
    [RELPATH, VERSION, LAST_UPLOADED_URI, LAST_DOWNLOADED_URI, LAST_DOWNLOADED_TIMESTAMP, PATHINFO],
    [_INSERT_OR_UPDATE],
    u"An file upload is being recorded in the database.",
)


# magic-folder db schema version 1
SCHEMA_v1 = """
CREATE TABLE version
(
 version INTEGER  -- contains one row, set to 1
);

CREATE TABLE local_files
(
 path                VARCHAR(1024) PRIMARY KEY,   -- UTF-8 filename relative to local magic folder dir
 size                INTEGER,                     -- ST_SIZE, or NULL if the file has been deleted
 mtime_ns            INTEGER,                     -- ST_MTIME in nanoseconds
 ctime_ns            INTEGER,                     -- ST_CTIME in nanoseconds
 version             INTEGER,
 last_uploaded_uri   VARCHAR(256),                -- URI:CHK:...
 last_downloaded_uri VARCHAR(256),                -- URI:CHK:...
 last_downloaded_timestamp TIMESTAMP
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

    def close(self):
        self.connection.close()

    def get_db_entry(self, relpath_u):
        """
        Retrieve the entry in the database for a given path, or return None
        if there is no such entry.
        """
        c = self.cursor
        c.execute("SELECT size, mtime_ns, ctime_ns, version, last_uploaded_uri,"
                  "       last_downloaded_uri, last_downloaded_timestamp"
                  " FROM local_files"
                  " WHERE path=?",
                  (relpath_u,))
        row = self.cursor.fetchone()
        if not row:
            return None
        else:
            (size, mtime_ns, ctime_ns, version, last_uploaded_uri,
             last_downloaded_uri, last_downloaded_timestamp) = row
            return PathEntry(size=size, mtime_ns=mtime_ns, ctime_ns=ctime_ns, version=version,
                             last_uploaded_uri=last_uploaded_uri,
                             last_downloaded_uri=last_downloaded_uri,
                             last_downloaded_timestamp=last_downloaded_timestamp)

    def get_all_relpaths(self):
        """
        Retrieve a set of all relpaths of files that have had an entry in magic folder db
        (i.e. that have been downloaded at least once).
        """
        self.cursor.execute("SELECT path FROM local_files")
        rows = self.cursor.fetchall()
        return set([r[0] for r in rows])

    def did_upload_version(self, relpath_u, version, last_uploaded_uri, last_downloaded_uri, last_downloaded_timestamp, pathinfo):
        action = DID_UPLOAD_VERSION(
            relpath=relpath_u,
            version=version,
            last_uploaded_uri=last_uploaded_uri,
            last_downloaded_uri=last_downloaded_uri,
            last_downloaded_timestamp=last_downloaded_timestamp,
            pathinfo=pathinfo,
        )
        with action:
            try:
                self.cursor.execute("INSERT INTO local_files VALUES (?,?,?,?,?,?,?,?)",
                                    (relpath_u, pathinfo.size, pathinfo.mtime_ns, pathinfo.ctime_ns,
                                     version, last_uploaded_uri, last_downloaded_uri,
                                     last_downloaded_timestamp))
                action.add_success_fields(insert_or_update=u"insert")
            except (self.sqlite_module.IntegrityError, self.sqlite_module.OperationalError):
                self.cursor.execute("UPDATE local_files"
                                    " SET size=?, mtime_ns=?, ctime_ns=?, version=?, last_uploaded_uri=?,"
                                    "     last_downloaded_uri=?, last_downloaded_timestamp=?"
                                    " WHERE path=?",
                                    (pathinfo.size, pathinfo.mtime_ns, pathinfo.ctime_ns, version,
                                     last_uploaded_uri, last_downloaded_uri, last_downloaded_timestamp,
                                     relpath_u))
                action.add_success_fields(insert_or_update=u"update")
            self.connection.commit()
