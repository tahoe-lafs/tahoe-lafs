
import sys
from collections import namedtuple

from allmydata.util.dbutil import get_db, DBError

from eliot._validation import (
    ValidationError,
)
from eliot import (
    Field,
    ActionType,
)

def _validateInstanceOf(t):
    """
    Return an Eliot validator that requires values to be instances of ``t``.
    """
    def validator(v):
        if not isinstance(v, t):
            raise ValidationError("{} not an instance of {}".format(v, t))
    return validator

def _validateSetMembership(s):
    """
    Return an Eliot validator that requires values to be elements of ``s``.
    """
    def validator(v):
        if v not in s:
            raise ValidationError("{} not in {}".format(v, s))
    return validator

PathEntry = namedtuple('PathEntry', 'size mtime_ns ctime_ns version last_uploaded_uri '
                                    'last_downloaded_uri last_downloaded_timestamp')

_RELPATH = Field.for_types(
    u"relpath",
    [unicode],
    u"The relative path of a file in a magic-folder.",
)

_VERSION = Field.for_types(
    u"version",
    [int, long],
    u"The version of the file.",
)

_UPLOADED_URI = Field.for_types(
    u"last-uploaded-uri",
    [unicode],
    u"The filecap to which this version of this file was uploaded.",
)

_DOWNLOADED_URI = Field.for_types(
    u"last-downloaded-uri",
    [unicode],
    u"The filecap from which the previous version of this file was downloaded.",
)

_DOWNLOADED_TIMESTAMP = Field.for_types(
    u"last-downloaded-timestamp",
    [unicode],
    u"(XXX probably not really, don't trust this) The timestamp of the last download of this file.",
)

_PATHINFO = Field(
    u"pathinfo",
    lambda v: tuple(v),
    u"The metadata for this version of this file.",
    _validateInstanceOf(PathEntry),
)

_INSERT_OR_UPDATE = Field.for_types(
    u"inserted-or-updated",
    [unicode],
    u"An indication of whether the record for this upload was new or an update to a previous entry.",
    _validateSetMembership({u"insert", u"update"}),
)

DID_UPLOAD_VERSION = ActionType(
    u"magic-folder-db:did-upload-version",
    [_RELPATH, _VERSION, _UPLOADED_URI, _DOWNLOADED_URI, _DOWNLOADED_TIMESTAMP, _PATHINFO],
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
            uploaded_uri=last_uploaded_uri,
            downloaded_uri=last_downloaded_uri,
            downloaded_timestamp=last_downloaded_timestamp,
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
                action.add_success_fields(inserted_or_updated=u"update")
            self.connection.commit()
