
import os, sys

class DBError(Exception):
    pass

def get_db(dbfile, stderr=sys.stderr,
           create_version=(None, None), updaters={}, just_create=False):
    """Open or create the given db file. The parent directory must exist.
    create_version=(SCHEMA, VERNUM), and SCHEMA must have a 'version' table.
    Updaters is a {newver: commands} mapping, where e.g. updaters[2] is used
    to get from ver=1 to ver=2. Returns a (sqlite,db) tuple, or raises
    DBError.
    """
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
        raise DBError("Unable to create/open db file %s: %s" % (dbfile, e))

    schema, target_version = create_version
    c = db.cursor()
    if must_create:
        c.executescript(schema)
        c.execute("INSERT INTO version (version) VALUES (?)", (target_version,))
        db.commit()

    try:
        c.execute("SELECT version FROM version")
        version = c.fetchone()[0]
    except sqlite.DatabaseError, e:
        # this indicates that the file is not a compatible database format.
        # Perhaps it was created with an old version, or it might be junk.
        raise DBError("db file is unusable: %s" % e)

    if just_create: # for tests
        return (sqlite, db)

    while version < target_version:
        c.executescript(updaters[version+1])
        db.commit()
        version = version+1
    if version != target_version:
        raise DBError("Unable to handle db version %s" % version)

    return (sqlite, db)


