
import os, sys
import shutil
import time

from twisted.trial import unittest
from twisted.python import runtime
from twisted.python.filepath import FilePath
from twisted.internet import defer
from twisted.application import service

from allmydata.interfaces import IDirectoryNode, NoSuchChildError

from allmydata.util import fake_inotify, fileutil
from allmydata.util.encodingutil import get_filesystem_encoding
from allmydata.util.consumer import download_to_data
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common_util import ReallyEqualMixin, NonASCIIPathMixin
from allmydata.test.common import ShouldFailMixin

from allmydata.frontends.drop_upload import DropUploader
from allmydata.scripts import backupdb
from allmydata.util.fileutil import abspath_expanduser_unicode


class DropUploadTestMixin(GridTestMixin, ShouldFailMixin, ReallyEqualMixin, NonASCIIPathMixin):
    """
    These tests will be run both with a mock notifier, and (on platforms that support it)
    with the real INotify.
    """

    def _get_count(self, name):
        return self.stats_provider.get_stats()["counters"].get(name, 0)

    def _createdb(self, dbfile):
        bdb = backupdb.get_backupdb(dbfile)
        self.failUnless(bdb, "unable to create backupdb from %r" % (dbfile,))
        self.failUnlessEqual(bdb.VERSION, 2)
        return bdb

    def _made_upload_dir(self, n):
        if self.dir_node == None:
            self.dir_node = n
        else:
            n = self.dir_node
        self.failUnless(IDirectoryNode.providedBy(n))
        self.upload_dirnode = n
        self.upload_dircap = n.get_uri()

    def _create_uploader(self, ign):
        self.uploader = DropUploader(self.client, self.upload_dircap, self.local_dir.encode('utf-8'),
                                         "magicfolderdb.sqlite", inotify=self.inotify, pending_delay=0.2)
        self.uploader.setServiceParent(self.client)
        self.uploader.upload_ready()
        self.failUnlessEqual(self.uploader._db.VERSION, 2)

    # Prevent unclean reactor errors.
    def _cleanup(self, res):
        d = defer.succeed(None)
        if self.uploader is not None:
            d.addCallback(lambda ign: self.uploader.finish(for_tests=True))
            d.addCallback(lambda ign: res)
        return d

    def _test_db_basic(self):
        fileutil.make_dirs(self.basedir)
        dbfile = os.path.join(self.basedir, "dbfile")
        bdb = self._createdb(dbfile)

    def _test_db_persistence(self):
        """Test that a file upload creates an entry in the database.
        """
        fileutil.make_dirs(self.basedir)
        path = os.path.join(self.basedir, u"myFile")
        path = abspath_expanduser_unicode(path)

        dbfile = os.path.join(self.basedir, "dbfile")
        db = self._createdb(dbfile)
        db.did_upload_file('URI:LIT:meow', path, 0, 0, 1234)

        c = db.cursor
        c.execute("SELECT size,mtime,ctime,fileid"
                  " FROM local_files"
                  " WHERE path=?",
                  (path,))
        row = db.cursor.fetchone()
        self.failIfEqual(row, None)

        ##
        # Test that a file upload AND a check_file results in a database entry
        # declaring the file previously uploaded.
        ##
        path = os.path.join(self.basedir, u"file123")
        f = open(path,"wb")
        f.write("say something")
        f.close()

        abspath = abspath_expanduser_unicode(path)
        print "\n\nabspath %s" % (abspath,)
        s = os.stat(abspath)
        #print "stat output: path %s mtime %s ctime %s size %s" % (abspath, s.st_mtime, s.st_ctime, s.st_size)
        db.did_upload_file('URI:LIT:mruwmztfojsw45a', abspath, s.st_mtime, s.st_ctime, s.st_size)

        r = db.check_file(abspath)
        print "r %s" % (r,)
        was_uploaded = r.was_uploaded()
        print "was_uploaded %s" % (was_uploaded,)

        c.execute("SELECT path,size,mtime,ctime,fileid"
                  " FROM local_files")
                  #" FROM local_files"
                  #" WHERE path=?",
                  #(abspath,))
        row = db.cursor.fetchone()
        print "row %s" % (row,)
        row = db.cursor.fetchone()
        print "row %s" % (row,)

        self.failUnlessReallyEqual(was_uploaded, True)

    def _test_uploader_start_service(self):
        self.uploader = None
        self.set_up_grid()
        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        d = self.client.create_dirnode()
        d.addCallback(self._made_upload_dir)
        d.addCallback(self._create_uploader)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.dirs_monitored'), 1))
        d.addBoth(self._cleanup)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.dirs_monitored'), 0))
        return d

    def _test_move_tree(self):
        self.uploader = None
        self.set_up_grid()

        self.local_dir = os.path.join(self.basedir, u"l\u00F8cal_dir")
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        d = self.client.create_dirnode()
        d.addCallback(self._made_upload_dir)
        d.addCallback(self._create_uploader)

        def testMoveEmptyTree(res):
            tree_name = 'empty_tree'
            tree_dir = os.path.join(self.basedir, tree_name)
            os.mkdir(tree_dir)

            d2 = defer.Deferred()
            self.uploader.set_uploaded_callback(d2.callback, ignore_count=0)

            new_tree_dir = os.path.join(self.local_dir, tree_name)
            os.rename(tree_dir, new_tree_dir)
            self.notify_close_write(FilePath(new_tree_dir))
            return d2
        d.addCallback(testMoveEmptyTree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.files_uploaded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.directories_created'), 1))

        def testMoveSmallTree(res):
            tree_name = 'small_tree'
            tree_dir = os.path.join(self.basedir, tree_name)
            os.mkdir(tree_dir)
            f = open(os.path.join(tree_dir, 'what'), "wb")
            f.write("meow")
            f.close()

            d2 = defer.Deferred()
            self.uploader.set_uploaded_callback(d2.callback, ignore_count=1)

            new_tree_dir = os.path.join(self.local_dir, tree_name)
            os.rename(tree_dir, new_tree_dir)
            self.notify_close_write(FilePath(new_tree_dir))
            return d2

        d.addCallback(testMoveSmallTree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'), 3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.directories_created'), 2))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.files_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))

        d.addBoth(self._cleanup)
        return d

    def _test_persistence(self):
        """ Perform an upload of a given file and then stop the client.
        Start a new client and uploader... and verify that the file is NOT uploaded
        a second time. This test is meant to test the database persistence along with
        the startup and shutdown code paths of the uploader.
        """
        self.uploader = None
        self.dir_node = None
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, u"test_persistence")
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider
        d = self.client.create_dirnode()
        d.addCallback(self._made_upload_dir)
        d.addCallback(self._create_uploader)

        def create_file(val):
            d2 = defer.Deferred()
            self.uploader.set_uploaded_callback(d2.callback)
            myFile = os.path.join(self.local_dir, "what")
            f = open(myFile, "wb")
            f.write("meow")
            f.close()
            self.notify_close_write(FilePath(myFile))
            return d2
        d.addCallback(create_file)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))
        d.addCallback(self._cleanup)

        def _restart(ign):
            print "in _restart"
            self.set_up_grid()
            self.client = self.g.clients[0]
            self.stats_provider = self.client.stats_provider
        d.addCallback(_restart)
        d.addCallback(self._create_uploader)
        d.addCallback(lambda ign: time.sleep(3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))
        d.addBoth(self._cleanup)
        return d

    def _test(self):
        self.uploader = None
        self.dir_node = None
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, self.unicode_or_fallback(u"loc\u0101l_dir", u"local_dir"))
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        d = self.client.create_dirnode()

        d.addCallback(self._made_upload_dir)
        d.addCallback(self._create_uploader)

        # Write something short enough for a LIT file.
        d.addCallback(lambda ign: self._test_file(u"short", "test"))

        # Write to the same file again with different data.
        d.addCallback(lambda ign: self._test_file(u"short", "different"))

        # Test that temporary files are not uploaded.
        d.addCallback(lambda ign: self._test_file(u"tempfile", "test", temporary=True))

        # Test that we tolerate creation of a subdirectory.
        d.addCallback(lambda ign: os.mkdir(os.path.join(self.local_dir, u"directory")))

        # Write something longer, and also try to test a Unicode name if the fs can represent it.
        name_u = self.unicode_or_fallback(u"l\u00F8ng", u"long")
        d.addCallback(lambda ign: self._test_file(name_u, "test"*100))

        # TODO: test that causes an upload failure.
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.files_failed'), 0))

        d.addBoth(self._cleanup)
        return d

    def _test_file(self, name_u, data, temporary=False):
        previously_uploaded = self._get_count('drop_upload.objects_uploaded')
        previously_disappeared = self._get_count('drop_upload.objects_disappeared')

        d = defer.Deferred()

        # Note: this relies on the fact that we only get one IN_CLOSE_WRITE notification per file
        # (otherwise we would get a defer.AlreadyCalledError). Should we be relying on that?
        self.uploader.set_uploaded_callback(d.callback)

        path_u = os.path.join(self.local_dir, name_u)
        if sys.platform == "win32":
            path = FilePath(path_u)
        else:
            path = FilePath(path_u.encode(get_filesystem_encoding()))

        # We don't use FilePath.setContent() here because it creates a temporary file that
        # is renamed into place, which causes events that the test is not expecting.
        f = open(path.path, "wb")
        try:
            if temporary and sys.platform != "win32":
                os.unlink(path.path)
            f.write(data)
        finally:
            f.close()
        if temporary and sys.platform == "win32":
            os.unlink(path.path)
        fileutil.flush_volume(path.path)
        self.notify_close_write(path)

        if temporary:
            d.addCallback(lambda ign: self.shouldFail(NoSuchChildError, 'temp file not uploaded', None,
                                                      self.upload_dirnode.get, name_u))
            d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_disappeared'),
                                                                 previously_disappeared + 1))
        else:
            d.addCallback(lambda ign: self.upload_dirnode.get(name_u))
            d.addCallback(download_to_data)
            d.addCallback(lambda actual_data: self.failUnlessReallyEqual(actual_data, data))
            d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'),
                                                                 previously_uploaded + 1))

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))
        return d


class MockTest(DropUploadTestMixin, unittest.TestCase):
    """This can run on any platform, and even if twisted.internet.inotify can't be imported."""

    def notify_close_write(self, path):
        self.uploader._notifier.event(path, self.inotify.IN_CLOSE_WRITE)

    def test_errors(self):
        self.basedir = "drop_upload.MockTest.test_errors"
        self.set_up_grid()
        errors_dir = os.path.join(self.basedir, "errors_dir")
        os.mkdir(errors_dir)
        not_a_dir = os.path.join(self.basedir, 'NOT_A_DIR')
        fileutil.write(not_a_dir, "")

        client = self.g.clients[0]
        d = client.create_dirnode()
        def _check_errors(n):
            self.failUnless(IDirectoryNode.providedBy(n))
            upload_dircap = n.get_uri()
            readonly_dircap = n.get_readonly_uri()

            self.shouldFail(AssertionError, 'invalid local.directory', 'could not be represented',
                            DropUploader, client, upload_dircap, '\xFF', 'magicfolderdb', inotify=fake_inotify)
            self.shouldFail(AssertionError, 'nonexistent local.directory', 'there is no directory',
                            DropUploader, client, upload_dircap, os.path.join(self.basedir, "Laputa"), 'magicfolderdb', inotify=fake_inotify)

            self.shouldFail(AssertionError, 'non-directory local.directory', 'is not a directory',
                            DropUploader, client, upload_dircap, not_a_dir, 'magicfolderdb', inotify=fake_inotify)

            self.shouldFail(AssertionError, 'bad upload.dircap', 'does not refer to a directory',
                            DropUploader, client, 'bad', errors_dir, 'magicfolderdb', inotify=fake_inotify)
            self.shouldFail(AssertionError, 'non-directory upload.dircap', 'does not refer to a directory',
                            DropUploader, client, 'URI:LIT:foo', errors_dir, 'magicfolderdb', inotify=fake_inotify)
            self.shouldFail(AssertionError, 'readonly upload.dircap', 'is not a writecap to a directory',
                            DropUploader, client, readonly_dircap, errors_dir, 'magicfolderdb', inotify=fake_inotify)
        d.addCallback(_check_errors)
        return d

    def test_drop_upload(self):
        self.inotify = fake_inotify
        self.basedir = "drop_upload.MockTest.test_drop_upload"
        return self._test()

    def test_basic_db(self):
        self.inotify = fake_inotify
        self.basedir = "drop_upload.MockTest.test_basic_db"
        return self._test_db_basic()

    def test_db_persistence(self):
        self.inotify = fake_inotify
        self.basedir = "drop_upload.MockTest.test_db_persistence"
        return self._test_db_persistence()

    def test_uploader_start_service(self):
        self.inotify = fake_inotify
        self.basedir = "drop_upload.MockTest.test_uploader_start_service"
        return self._test_uploader_start_service()

    def test_move_tree(self):
        self.inotify = fake_inotify
        self.basedir = "drop_upload.MockTest.test_move_tree"
        return self._test_move_tree()

    def test_persistence(self):
        self.inotify = fake_inotify
        self.basedir = "drop_upload.MockTest.test_persistence"
        return self._test_persistence()


class RealTest(DropUploadTestMixin, unittest.TestCase):
    """This is skipped unless both Twisted and the platform support inotify."""

    def test_drop_upload(self):
        # We should always have runtime.platform.supportsINotify, because we're using
        # Twisted >= 10.1.
        if sys.platform != "win32" and not runtime.platform.supportsINotify():
            raise unittest.SkipTest("Drop-upload support can only be tested for-real on an OS that supports inotify or equivalent.")

        self.inotify = None  # use the appropriate inotify for the platform
        self.basedir = "drop_upload.RealTest.test_drop_upload"
        return self._test()

    def notify_close_write(self, path):
        # Writing to the file causes the notification.
        pass

    def test_basic_db(self):
        self.inotify = None
        self.basedir = "drop_upload.RealTest.test_basic_db"
        return self._test_db_basic()

    def test_db_persistence(self):
        self.inotify = None
        self.basedir = "drop_upload.RealTest.test_db_persistence"
        return self._test_db_persistence()

    def test_uploader_start_service(self):
        self.inotify = None
        self.basedir = "drop_upload.RealTest._test_uploader_start_service"
        return self._test_uploader_start_service()

    def test_move_tree(self):
        self.inotify = None
        self.basedir = "drop_upload.RealTest._test_move_tree"
        return self._test_move_tree()

    def test_persistence(self):
        self.inotify = None
        self.basedir = "drop_upload.RealTest.test_persistence"
        return self._test_persistence()
