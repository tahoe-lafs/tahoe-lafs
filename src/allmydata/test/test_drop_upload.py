
import os, sys, stat, time

from twisted.trial import unittest
from twisted.python import runtime
from twisted.internet import defer

from allmydata.interfaces import IDirectoryNode, NoSuchChildError

from allmydata.util import fake_inotify, fileutil
from allmydata.util.encodingutil import get_filesystem_encoding, to_filepath
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

    def setUp(self):
        GridTestMixin.setUp(self)
        temp = self.mktemp()
        self.basedir = abspath_expanduser_unicode(temp.decode(get_filesystem_encoding()))
        self.uploader = None
        self.dir_node = None

    def _get_count(self, name):
        return self.stats_provider.get_stats()["counters"].get(name, 0)

    def _createdb(self):
        dbfile = abspath_expanduser_unicode(u"magicfolderdb.sqlite", base=self.basedir)
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
        dbfile = abspath_expanduser_unicode(u"magicfolderdb.sqlite", base=self.basedir)
        self.uploader = DropUploader(self.client, self.upload_dircap, self.local_dir,
                                     dbfile, inotify=self.inotify, pending_delay=0.2)
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

    def test_db_basic(self):
        fileutil.make_dirs(self.basedir)
        self._createdb()

    def test_db_persistence(self):
        """Test that a file upload creates an entry in the database."""

        fileutil.make_dirs(self.basedir)
        db = self._createdb()

        path = abspath_expanduser_unicode(u"myFile1", base=self.basedir)
        db.did_upload_file('URI:LIT:1', path, 0, 0, 33)

        c = db.cursor
        c.execute("SELECT size,mtime,ctime,fileid"
                  " FROM local_files"
                  " WHERE path=?",
                  (path,))
        row = db.cursor.fetchone()
        self.failIfEqual(row, None)

        # Second test uses db.check_file instead of SQL query directly
        # to confirm the previous upload entry in the db.
        path = abspath_expanduser_unicode(u"myFile2", base=self.basedir)
        fileutil.write(path, "meow\n")
        s = os.stat(path)
        size = s[stat.ST_SIZE]
        ctime = s[stat.ST_CTIME]
        mtime = s[stat.ST_MTIME]
        db.did_upload_file('URI:LIT:2', path, mtime, ctime, size)
        r = db.check_file(path)
        self.failUnless(r.was_uploaded())

    def test_uploader_start_service(self):
        self.set_up_grid()

        self.local_dir = abspath_expanduser_unicode(u"l\u00F8cal_dir", base=self.basedir)
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        d = self.client.create_dirnode()
        d.addCallback(self._made_upload_dir)
        d.addCallback(self._create_uploader)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.dirs_monitored'), 1))
        d.addBoth(self._cleanup)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.dirs_monitored'), 0))
        return d

    def test_move_tree(self):
        self.set_up_grid()

        self.local_dir = abspath_expanduser_unicode(u"l\u00F8cal_dir", base=self.basedir)
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        empty_tree_name = u"empty_tr\u00EAe"
        empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.basedir)
        new_empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.local_dir)

        small_tree_name = u"small_tr\u00EAe"
        small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.basedir)
        new_small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.local_dir)

        d = self.client.create_dirnode()
        d.addCallback(self._made_upload_dir)

        d.addCallback(self._create_uploader)

        def _check_move_empty_tree(res):
            self.mkdir_nonascii(empty_tree_dir)
            d2 = defer.Deferred()
            self.uploader.set_uploaded_callback(d2.callback, ignore_count=0)
            os.rename(empty_tree_dir, new_empty_tree_dir)
            self.notify_close_write(to_filepath(new_empty_tree_dir))
            return d2
        d.addCallback(_check_move_empty_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.files_uploaded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.directories_created'), 1))

        def _check_move_small_tree(res):
            self.mkdir_nonascii(small_tree_dir)
            fileutil.write(abspath_expanduser_unicode(u"what", base=small_tree_dir), "say when")
            d2 = defer.Deferred()
            self.uploader.set_uploaded_callback(d2.callback, ignore_count=1)
            os.rename(small_tree_dir, new_small_tree_dir)
            self.notify_close_write(to_filepath(new_small_tree_dir))
            return d2
        d.addCallback(_check_move_small_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'), 3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.files_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.directories_created'), 2))

        def _check_moved_tree_is_watched(res):
            d2 = defer.Deferred()
            self.uploader.set_uploaded_callback(d2.callback, ignore_count=0)
            fileutil.write(abspath_expanduser_unicode(u"another", base=new_small_tree_dir), "file")
            self.notify_close_write(to_filepath(abspath_expanduser_unicode(u"another", base=new_small_tree_dir)))
            return d2
        d.addCallback(_check_moved_tree_is_watched)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'), 4))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.files_uploaded'), 2))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.directories_created'), 2))

        d.addBoth(self._cleanup)
        return d

    def test_persistence(self):
        """
        Perform an upload of a given file and then stop the client.
        Start a new client and uploader... and verify that the file is NOT uploaded
        a second time. This test is meant to test the database persistence along with
        the startup and shutdown code paths of the uploader.
        """
        self.set_up_grid()
        self.local_dir = abspath_expanduser_unicode(u"test_persistence", base=self.basedir)
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider
        d = self.client.create_dirnode()
        d.addCallback(self._made_upload_dir)
        d.addCallback(self._create_uploader)

        def create_file(val):
            d2 = defer.Deferred()
            self.uploader.set_uploaded_callback(d2.callback)
            test_file = abspath_expanduser_unicode(u"what", base=self.local_dir)
            fileutil.write(test_file, "meow")
            self.notify_close_write(to_filepath(test_file))
            return d2
        d.addCallback(create_file)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.objects_queued'), 0))
        d.addCallback(self._cleanup)

        def _restart(ign):
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

    def test_drop_upload(self):
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, self.unicode_or_fallback(u"loc\u0101l_dir", u"local_dir"))
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        d = self.client.create_dirnode()

        d.addCallback(self._made_upload_dir)
        d.addCallback(self._create_uploader)

        # Write something short enough for a LIT file.
        d.addCallback(lambda ign: self._check_file(u"short", "test"))

        # Write to the same file again with different data.
        d.addCallback(lambda ign: self._check_file(u"short", "different"))

        # Test that temporary files are not uploaded.
        d.addCallback(lambda ign: self._check_file(u"tempfile", "test", temporary=True))

        # Test that we tolerate creation of a subdirectory.
        d.addCallback(lambda ign: os.mkdir(os.path.join(self.local_dir, u"directory")))

        # Write something longer, and also try to test a Unicode name if the fs can represent it.
        name_u = self.unicode_or_fallback(u"l\u00F8ng", u"long")
        d.addCallback(lambda ign: self._check_file(name_u, "test"*100))

        # TODO: test that causes an upload failure.
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('drop_upload.files_failed'), 0))

        d.addBoth(self._cleanup)
        return d

    def _check_file(self, name_u, data, temporary=False):
        previously_uploaded = self._get_count('drop_upload.objects_uploaded')
        previously_disappeared = self._get_count('drop_upload.objects_disappeared')

        d = defer.Deferred()

        # Note: this relies on the fact that we only get one IN_CLOSE_WRITE notification per file
        # (otherwise we would get a defer.AlreadyCalledError). Should we be relying on that?
        self.uploader.set_uploaded_callback(d.callback)

        path_u = abspath_expanduser_unicode(name_u, base=self.local_dir)
        path = to_filepath(path_u)

        # We don't use FilePath.setContent() here because it creates a temporary file that
        # is renamed into place, which causes events that the test is not expecting.
        f = open(path_u, "wb")
        try:
            if temporary and sys.platform != "win32":
                os.unlink(path_u)
            f.write(data)
        finally:
            f.close()
        if temporary and sys.platform == "win32":
            os.unlink(path_u)
        fileutil.flush_volume(path_u)
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

    def setUp(self):
        DropUploadTestMixin.setUp(self)
        self.inotify = fake_inotify

    def notify_close_write(self, path):
        self.uploader._notifier.event(path, self.inotify.IN_CLOSE_WRITE)

    def test_errors(self):
        self.set_up_grid()

        basedir = abspath_expanduser_unicode(unicode(self.basedir))
        errors_dir = abspath_expanduser_unicode(u"errors_dir", base=basedir)
        os.mkdir(errors_dir)
        not_a_dir = abspath_expanduser_unicode(u"NOT_A_DIR", base=basedir)
        fileutil.write(not_a_dir, "")
        magicfolderdb = abspath_expanduser_unicode(u"magicfolderdb", base=basedir)
        doesnotexist  = abspath_expanduser_unicode(u"doesnotexist", base=basedir)

        client = self.g.clients[0]
        d = client.create_dirnode()
        def _check_errors(n):
            self.failUnless(IDirectoryNode.providedBy(n))
            upload_dircap = n.get_uri()
            readonly_dircap = n.get_readonly_uri()

            self.shouldFail(AssertionError, 'nonexistent local.directory', 'there is no directory',
                            DropUploader, client, upload_dircap, doesnotexist, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'non-directory local.directory', 'is not a directory',
                            DropUploader, client, upload_dircap, not_a_dir, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'bad upload.dircap', 'does not refer to a directory',
                            DropUploader, client, 'bad', errors_dir, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'non-directory upload.dircap', 'does not refer to a directory',
                            DropUploader, client, 'URI:LIT:foo', errors_dir, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'readonly upload.dircap', 'is not a writecap to a directory',
                            DropUploader, client, readonly_dircap, errors_dir, magicfolderdb, inotify=fake_inotify)
        d.addCallback(_check_errors)
        return d


class RealTest(DropUploadTestMixin, unittest.TestCase):
    """This is skipped unless both Twisted and the platform support inotify."""

    def setUp(self):
        DropUploadTestMixin.setUp(self)
        self.inotify = None

    def notify_close_write(self, path):
        # Writing to the file causes the notification.
        pass

if sys.platform != "win32" and not runtime.platform.supportsINotify():
    RealTest.skip = "Drop-upload support can only be tested for-real on an OS that supports inotify or equivalent."
