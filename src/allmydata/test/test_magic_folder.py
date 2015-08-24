
import os, sys, stat, time

from twisted.trial import unittest
from twisted.internet import defer

from allmydata.interfaces import IDirectoryNode

from allmydata.util import fake_inotify, fileutil
from allmydata.util.encodingutil import get_filesystem_encoding, to_filepath
from allmydata.util.consumer import download_to_data
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common_util import ReallyEqualMixin, NonASCIIPathMixin
from allmydata.test.common import ShouldFailMixin
from allmydata.test.test_cli_magic_folder import MagicFolderTestMixin

from allmydata.frontends import magic_folder
from allmydata.frontends.magic_folder import MagicFolder
from allmydata import backupdb, magicpath
from allmydata.util.fileutil import abspath_expanduser_unicode


class MagicFolderTestMixin(MagicFolderTestMixin, ShouldFailMixin, ReallyEqualMixin, NonASCIIPathMixin):
    """
    These tests will be run both with a mock notifier, and (on platforms that support it)
    with the real INotify.
    """

    def setUp(self):
        GridTestMixin.setUp(self)
        temp = self.mktemp()
        self.basedir = abspath_expanduser_unicode(temp.decode(get_filesystem_encoding()))
        self.magicfolder = None

    def _get_count(self, name, client=None):
        return (client or self).stats_provider.get_stats()["counters"].get('magic_folder.%s' % (name,), 0)

    def _createdb(self):
        dbfile = abspath_expanduser_unicode(u"magicfolderdb.sqlite", base=self.basedir)
        bdb = backupdb.get_backupdb(dbfile, create_version=(backupdb.SCHEMA_v3, 3))
        self.failUnless(bdb, "unable to create backupdb from %r" % (dbfile,))
        self.failUnlessEqual(bdb.VERSION, 3)
        return bdb

    def _create_magicfolder(self, ign):
        dbfile = abspath_expanduser_unicode(u"magicfolderdb.sqlite", base=self.basedir)
        self.magicfolder = MagicFolder(self.client, self.upload_dircap, self.collective_dircap, self.local_dir,
                                       dbfile, inotify=self.inotify, pending_delay=0.2)
        self.magicfolder.setServiceParent(self.client)
        self.magicfolder.ready()

    # Prevent unclean reactor errors.

    def test_db_basic(self):
        fileutil.make_dirs(self.basedir)
        self._createdb()

    def test_db_persistence(self):
        """Test that a file upload creates an entry in the database."""

        fileutil.make_dirs(self.basedir)
        db = self._createdb()

        path = abspath_expanduser_unicode(u"myFile1", base=self.basedir)
        db.did_upload_file('URI:LIT:1', path, 1, 0, 0, 33)

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
        db.did_upload_file('URI:LIT:2', path, 1, mtime, ctime, size)
        r = db.check_file(path)
        self.failUnless(r.was_uploaded())

    def test_magicfolder_start_service(self):
        self.set_up_grid()

        self.local_dir = abspath_expanduser_unicode(self.unicode_or_fallback(u"l\u00F8cal_dir", u"local_dir"),
                                                    base=self.basedir)
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        d = self.create_invite_join_magic_folder(u"Alice", self.local_dir)
        d.addCallback(self._create_magicfolder)

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 1))
        d.addBoth(self.cleanup)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 0))
        return d

    def test_move_tree(self):
        self.set_up_grid()

        self.local_dir = abspath_expanduser_unicode(self.unicode_or_fallback(u"l\u00F8cal_dir", u"local_dir"),
                                                    base=self.basedir)
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        empty_tree_name = self.unicode_or_fallback(u"empty_tr\u00EAe", u"empty_tree")
        empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.basedir)
        new_empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.local_dir)

        small_tree_name = self.unicode_or_fallback(u"small_tr\u00EAe", u"empty_tree")
        small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.basedir)
        new_small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.local_dir)

        d = self.create_invite_join_magic_folder(u"Alice", self.local_dir)
        d.addCallback(self._create_magicfolder)

        def _check_move_empty_tree(res):
            self.mkdir_nonascii(empty_tree_dir)
            d2 = self.magicfolder.uploader.set_hook('processed')
            os.rename(empty_tree_dir, new_empty_tree_dir)
            self.notify(to_filepath(new_empty_tree_dir), self.inotify.IN_MOVED_TO)
            return d2
        d.addCallback(_check_move_empty_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 1))

        def _check_move_small_tree(res):
            self.mkdir_nonascii(small_tree_dir)
            fileutil.write(abspath_expanduser_unicode(u"what", base=small_tree_dir), "say when")
            d2 = self.magicfolder.uploader.set_hook('processed', ignore_count=1)
            os.rename(small_tree_dir, new_small_tree_dir)
            self.notify(to_filepath(new_small_tree_dir), self.inotify.IN_MOVED_TO)
            return d2
        d.addCallback(_check_move_small_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        def _check_moved_tree_is_watched(res):
            d2 = self.magicfolder.uploader.set_hook('processed')
            fileutil.write(abspath_expanduser_unicode(u"another", base=new_small_tree_dir), "file")
            self.notify(to_filepath(abspath_expanduser_unicode(u"another", base=new_small_tree_dir)), self.inotify.IN_CLOSE_WRITE)
            return d2
        d.addCallback(_check_moved_tree_is_watched)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 4))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 2))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        # Files that are moved out of the upload directory should no longer be watched.
        def _move_dir_away(ign):
            os.rename(new_empty_tree_dir, empty_tree_dir)
            # Wuh? Why don't we get this event for the real test?
            #self.notify(to_filepath(new_empty_tree_dir), self.inotify.IN_MOVED_FROM)
        d.addCallback(_move_dir_away)
        def create_file(val):
            test_file = abspath_expanduser_unicode(u"what", base=empty_tree_dir)
            fileutil.write(test_file, "meow")
            return
        d.addCallback(create_file)
        d.addCallback(lambda ign: time.sleep(1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 4))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 2))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        d.addBoth(self.cleanup)
        return d

    def test_persistence(self):
        """
        Perform an upload of a given file and then stop the client.
        Start a new client and magic-folder service... and verify that the file is NOT uploaded
        a second time. This test is meant to test the database persistence along with
        the startup and shutdown code paths of the magic-folder service.
        """
        self.set_up_grid()
        self.local_dir = abspath_expanduser_unicode(u"test_persistence", base=self.basedir)
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider
        self.collective_dircap = ""

        d = self.create_invite_join_magic_folder(u"Alice", self.local_dir)
        d.addCallback(self._create_magicfolder)

        def create_test_file(result):
            d2 = self.magicfolder.uploader.set_hook('processed')
            test_file = abspath_expanduser_unicode(u"what", base=self.local_dir)
            fileutil.write(test_file, "meow")
            self.notify(to_filepath(test_file), self.inotify.IN_CLOSE_WRITE)
            return d2
        d.addCallback(create_test_file)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))

        def restart(ignore):
            #print "restart"
            tahoe_config_file = os.path.join(self.get_clientdir(), "tahoe.cfg")
            tahoe_config = fileutil.read(tahoe_config_file)
            d3 = defer.succeed(None)
            def write_config(client_node_dir):
                #print "write_config"
                fileutil.write(os.path.join(client_node_dir, "tahoe.cfg"), tahoe_config)
            def setup_stats(result):
                #print "setup_stats"
                self.client = None
                self.set_up_grid(client_config_hooks={0: write_config})
                self.client = self.g.clients[0]
                self.stats_provider = self.client.stats_provider
                self.magicfolder = self.client.getServiceNamed("magic-folder")

            d3.addBoth(self.cleanup)
            d3.addCallback(setup_stats)
            #d3.addCallback(self._create_magicfolder)
            return d3
        d.addCallback(restart)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addBoth(self.cleanup)
        return d

    def test_magic_folder(self):
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, self.unicode_or_fallback(u"loc\u0101l_dir", u"local_dir"))
        self.mkdir_nonascii(self.local_dir)

        self.client = self.g.clients[0]
        self.stats_provider = self.client.stats_provider

        d = self.create_invite_join_magic_folder(u"Alice\u0101", self.local_dir)
        d.addCallback(self._create_magicfolder)

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
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_failed'), 0))

        d.addBoth(self.cleanup)
        return d

    def _check_file(self, name_u, data, temporary=False):
        previously_uploaded = self._get_count('uploader.objects_succeeded')
        previously_disappeared = self._get_count('uploader.objects_disappeared')

        d = self.magicfolder.uploader.set_hook('processed')

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
            self.notify(path, self.inotify.IN_DELETE)
        fileutil.flush_volume(path_u)
        self.notify(path, self.inotify.IN_CLOSE_WRITE)

        if temporary:
            d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_disappeared'),
                                                                 previously_disappeared + 1))
        else:
            d.addCallback(lambda ign: self.upload_dirnode.get(name_u))
            d.addCallback(download_to_data)
            d.addCallback(lambda actual_data: self.failUnlessReallyEqual(actual_data, data))
            d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'),
                                                                 previously_uploaded + 1))

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        return d

    def _check_version_in_dmd(self, magicfolder, relpath_u, expected_version):
        encoded_name_u = magicpath.path2magic(relpath_u)
        d = magicfolder.downloader._get_collective_latest_file(encoded_name_u)
        def check_latest(result):
            if result[0] is not None:
                node, metadata = result
                d.addCallback(lambda ign: self.failUnlessEqual(metadata['version'], expected_version))
        d.addCallback(check_latest)
        return d

    def _check_version_in_local_db(self, magicfolder, relpath_u, expected_version):
        version = magicfolder._db.get_local_file_version(relpath_u)
        #print "_check_version_in_local_db: %r has version %s" % (relpath_u, version)
        self.failUnlessEqual(version, expected_version)

    def test_alice_bob(self):
        d = self.setup_alice_and_bob()
        def get_results(result):
            # XXX are these used?
            (self.alice_collective_dircap, self.alice_upload_dircap, self.alice_magicfolder,
             self.bob_collective_dircap,   self.bob_upload_dircap,   self.bob_magicfolder) = result
            #print "Alice magicfolderdb is at %r" % (self.alice_magicfolder._client.basedir)
            #print "Bob   magicfolderdb is at %r" % (self.bob_magicfolder._client.basedir)
        d.addCallback(get_results)

        def Alice_write_a_file(result):
            #print "Alice writes a file\n"
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "meow, meow meow. meow? meow meow! meow.")
            self.magicfolder = self.alice_magicfolder
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE)

        d.addCallback(Alice_write_a_file)

        def Alice_wait_for_upload(result):
            #print "Alice waits for an upload\n"
            d2 = self.alice_magicfolder.uploader.set_hook('processed')
            return d2
        d.addCallback(Alice_wait_for_upload)
        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file1", 0))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.alice_magicfolder, u"file1", 0))

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded', client=self.alice_magicfolder._client), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded', client=self.alice_magicfolder._client), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued', client=self.alice_magicfolder._client), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created', client=self.alice_magicfolder._client), 0))

        def Bob_wait_for_download(result):
            #print "Bob waits for a download\n"
            d2 = self.bob_magicfolder.downloader.set_hook('processed')
            return d2
        d.addCallback(Bob_wait_for_download)
        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, u"file1", 0))
        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file1", 0)) # XXX prolly not needed
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client), 1))


        # test deletion of file behavior
        def Alice_delete_file(result):
            #print "Alice deletes the file!\n"
            os.unlink(self.file_path)
            self.notify(to_filepath(self.file_path), self.inotify.IN_DELETE)

            return None
        d.addCallback(Alice_delete_file)
        d.addCallback(Alice_wait_for_upload)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded', client=self.alice_magicfolder._client), 2))
        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file1", 1))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.alice_magicfolder, u"file1", 1))

        d.addCallback(Bob_wait_for_download)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client), 2))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, u"file1", 1))
        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file1", 1))


        def Alice_rewrite_file(result):
            #print "Alice rewrites file\n"
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "Alice suddenly sees the white rabbit running into the forest.")
            self.magicfolder = self.alice_magicfolder
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE)
        d.addCallback(Alice_rewrite_file)

        d.addCallback(Alice_wait_for_upload)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded', client=self.alice_magicfolder._client), 3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded', client=self.alice_magicfolder._client), 3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued', client=self.alice_magicfolder._client), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created', client=self.alice_magicfolder._client), 0))

        d.addCallback(Bob_wait_for_download)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client), 3))

        def cleanup_Alice_and_Bob(result):
            d = defer.succeed(None)
            d.addCallback(lambda ign: self.alice_magicfolder.finish())
            d.addCallback(lambda ign: self.bob_magicfolder.finish())
            d.addCallback(lambda ign: result)
            return d
        d.addCallback(cleanup_Alice_and_Bob)
        return d

class MockTest(MagicFolderTestMixin, unittest.TestCase):
    """This can run on any platform, and even if twisted.internet.inotify can't be imported."""

    def setUp(self):
        MagicFolderTestMixin.setUp(self)
        self.inotify = fake_inotify

    def notify(self, path, mask):
        self.magicfolder.uploader._notifier.event(path, mask)

    def test_errors(self):
        self.set_up_grid()

        errors_dir = abspath_expanduser_unicode(u"errors_dir", base=self.basedir)
        os.mkdir(errors_dir)
        not_a_dir = abspath_expanduser_unicode(u"NOT_A_DIR", base=self.basedir)
        fileutil.write(not_a_dir, "")
        magicfolderdb = abspath_expanduser_unicode(u"magicfolderdb", base=self.basedir)
        doesnotexist  = abspath_expanduser_unicode(u"doesnotexist", base=self.basedir)

        client = self.g.clients[0]
        d = client.create_dirnode()
        def _check_errors(n):
            self.failUnless(IDirectoryNode.providedBy(n))
            upload_dircap = n.get_uri()
            readonly_dircap = n.get_readonly_uri()

            self.shouldFail(AssertionError, 'nonexistent local.directory', 'there is no directory',
                            MagicFolder, client, upload_dircap, '', doesnotexist, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'non-directory local.directory', 'is not a directory',
                            MagicFolder, client, upload_dircap, '', not_a_dir, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'bad upload.dircap', 'does not refer to a directory',
                            MagicFolder, client, 'bad', '', errors_dir, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'non-directory upload.dircap', 'does not refer to a directory',
                            MagicFolder, client, 'URI:LIT:foo', '', errors_dir, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'readonly upload.dircap', 'is not a writecap to a directory',
                            MagicFolder, client, readonly_dircap, '', errors_dir, magicfolderdb, inotify=fake_inotify)
            self.shouldFail(AssertionError, 'collective dircap',
                            "The URI in 'private/collective_dircap' is not a readonly cap to a directory.",
                            MagicFolder, client, upload_dircap, upload_dircap, errors_dir, magicfolderdb, inotify=fake_inotify)

            def _not_implemented():
                raise NotImplementedError("blah")
            self.patch(magic_folder, 'get_inotify_module', _not_implemented)
            self.shouldFail(NotImplementedError, 'unsupported', 'blah',
                            MagicFolder, client, upload_dircap, '', errors_dir, magicfolderdb)
        d.addCallback(_check_errors)
        return d


class RealTest(MagicFolderTestMixin, unittest.TestCase):
    """This is skipped unless both Twisted and the platform support inotify."""

    def setUp(self):
        MagicFolderTestMixin.setUp(self)
        self.inotify = magic_folder.get_inotify_module()

    def notify(self, path, mask):
        # Writing to the filesystem causes the notification.
        pass

try:
    magic_folder.get_inotify_module()
except NotImplementedError:
    RealTest.skip = "Magic Folder support can only be tested for-real on an OS that supports inotify or equivalent."
