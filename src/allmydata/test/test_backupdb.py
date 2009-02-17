
import os.path, time
from StringIO import StringIO
from twisted.trial import unittest

from allmydata.util import fileutil
from allmydata.scripts import backupdb

class BackupDB(unittest.TestCase):
    def create_or_skip(self, dbfile):
        stderr = StringIO()
        bdb = backupdb.get_backupdb(dbfile, stderr=stderr)
        if not bdb:
            if "I was unable to import a python sqlite library" in stderr.getvalue():
                raise unittest.SkipTest("sqlite unavailable, skipping test")
        return bdb

    def test_basic(self):
        self.basedir = basedir = os.path.join("backupdb", "create")
        fileutil.make_dirs(basedir)
        dbfile = os.path.join(basedir, "dbfile")
        bdb = self.create_or_skip(dbfile)
        self.failUnless(bdb)
        self.failUnlessEqual(bdb.VERSION, 1)

    def test_fail(self):
        self.basedir = basedir = os.path.join("backupdb", "fail")
        fileutil.make_dirs(basedir)

        # put a non-DB file in the way
        self.writeto("not-a-database", "I do not look like a sqlite database")
        stderr_f = StringIO()
        bdb = backupdb.get_backupdb(os.path.join(basedir, "not-a-database"),
                                    stderr_f)
        self.failUnlessEqual(bdb, None)
        stderr = stderr_f.getvalue()
        if "I was unable to import a python sqlite library" in stderr:
            pass
        else:
            self.failUnless("backupdb file is unusable" in stderr, stderr)
            self.failUnless("file is encrypted or is not a database" in stderr,
                            stderr)

        # put a directory in the way, to exercise a different error path
        where = os.path.join(basedir, "roadblock-dir")
        fileutil.make_dirs(where)
        stderr_f = StringIO()
        bdb = backupdb.get_backupdb(where, stderr_f)
        self.failUnlessEqual(bdb, None)
        stderr = stderr_f.getvalue()
        if "I was unable to import a python sqlite library" in stderr:
            pass
        else:
            self.failUnless(("Unable to create/open backupdb file %s" % where)
                            in stderr, stderr)
            self.failUnless("unable to open database file" in stderr, stderr)


    def writeto(self, filename, data):
        fn = os.path.join(self.basedir, filename)
        parentdir = os.path.dirname(fn)
        fileutil.make_dirs(parentdir)
        f = open(fn, "w")
        f.write(data)
        f.close()
        return fn

    def test_check(self):
        self.basedir = basedir = os.path.join("backupdb", "check")
        fileutil.make_dirs(basedir)
        dbfile = os.path.join(basedir, "dbfile")
        bdb = self.create_or_skip(dbfile)
        self.failUnless(bdb)

        foo_fn = self.writeto("foo.txt", "foo.txt")
        blah_fn = self.writeto("bar/blah.txt", "blah.txt")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("foo-cap")

        r = bdb.check_file(blah_fn)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("blah-cap")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), "foo-cap")
        self.failUnlessEqual(r.should_check(), False)

        time.sleep(1.0) # make sure the timestamp changes
        self.writeto("foo.txt", "NEW")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("new-cap")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), "new-cap")
        self.failUnlessEqual(r.should_check(), False)
        # if we spontaneously decide to upload it anyways, nothing should
        # break
        r.did_upload("new-cap")

        r = bdb.check_file(foo_fn, use_timestamps=False)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("new-cap")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), "new-cap")
        self.failUnlessEqual(r.should_check(), False)

        bdb.NO_CHECK_BEFORE = 0
        bdb.ALWAYS_CHECK_AFTER = 0.1

        r = bdb.check_file(blah_fn)
        self.failUnlessEqual(r.was_uploaded(), "blah-cap")
        self.failUnlessEqual(r.should_check(), True)
        r.did_check_healthy("results") # we know they're ignored for now

        bdb.NO_CHECK_BEFORE = 200
        bdb.ALWAYS_CHECK_AFTER = 400

        r = bdb.check_file(blah_fn)
        self.failUnlessEqual(r.was_uploaded(), "blah-cap")
        self.failUnlessEqual(r.should_check(), False)

        os.unlink(os.path.join(basedir, "foo.txt"))
        fileutil.make_dirs(os.path.join(basedir, "foo.txt")) # file becomes dir
        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), False)

    def test_wrong_version(self):
        self.basedir = basedir = os.path.join("backupdb", "wrong_version")
        fileutil.make_dirs(basedir)

        where = os.path.join(basedir, "tooold.db")
        bdb = self.create_or_skip(where)
        # reach into the DB and make it old
        bdb.cursor.execute("UPDATE version SET version=0")
        bdb.connection.commit()

        # now the next time we open the database, it should be an unusable
        # version
        stderr_f = StringIO()
        bdb = backupdb.get_backupdb(where, stderr_f)
        self.failUnlessEqual(bdb, None)
        stderr = stderr_f.getvalue()
        self.failUnlessEqual(stderr.strip(),
                             "Unable to handle backupdb version 0")
