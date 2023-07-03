"""
SQLite3 utilities.

Test coverage currently provided by test_backupdb.py.

Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os, sys

import sqlite3


class DBError(Exception):
    pass


def get_db(dbfile, stderr=sys.stderr,
           create_version=(None, None), updaters=None, just_create=False, dbname="db",
           ):
    """Open or create the given db file. The parent directory must exist.
    create_version=(SCHEMA, VERNUM), and SCHEMA must have a 'version' table.
    Updaters is a {newver: commands} mapping, where e.g. updaters[2] is used
    to get from ver=1 to ver=2. Returns a (sqlite3,db) tuple, or raises
    DBError.
    """
    if updaters is None:
        updaters = {}
    must_create = not os.path.exists(dbfile)
    try:
        db = sqlite3.connect(dbfile)
    except (EnvironmentError, sqlite3.OperationalError) as e:
        raise DBError("Unable to create/open %s file %s: %s" % (dbname, dbfile, e))

    schema, target_version = create_version
    c = db.cursor()

    # Enabling foreign keys allows stricter integrity checking.
    # The default is unspecified according to <http://www.sqlite.org/foreignkeys.html#fk_enable>.
    c.execute("PRAGMA foreign_keys = ON;")

    if must_create:
        c.executescript(schema)
        c.execute("INSERT INTO version (version) VALUES (?)", (target_version,))
        db.commit()

    try:
        c.execute("SELECT version FROM version")
        version = c.fetchone()[0]
    except sqlite3.DatabaseError as e:
        # this indicates that the file is not a compatible database format.
        # Perhaps it was created with an old version, or it might be junk.
        raise DBError("%s file is unusable: %s" % (dbname, e))

    if just_create: # for tests
        return (sqlite3, db)

    while version < target_version and version+1 in updaters:
        c.executescript(updaters[version+1])
        db.commit()
        version = version+1
    if version != target_version:
        raise DBError("Unable to handle %s version %s" % (dbname, version))

    return (sqlite3, db)


