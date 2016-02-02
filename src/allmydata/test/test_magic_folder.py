
import os, sys

from twisted.trial import unittest
from twisted.internet import defer, task, reactor

from allmydata.interfaces import IDirectoryNode
from allmydata.util.assertutil import precondition

from allmydata.util import fake_inotify, fileutil
from allmydata.util.encodingutil import get_filesystem_encoding, to_filepath
from allmydata.util.consumer import download_to_data
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common_util import ReallyEqualMixin, NonASCIIPathMixin
from allmydata.test.common import ShouldFailMixin
from .test_cli_magic_folder import MagicFolderCLITestMixin

from allmydata.frontends import magic_folder
from allmydata.frontends.magic_folder import MagicFolder, Downloader, WriteFileMixin
from allmydata import magicfolderdb, magicpath
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.immutable.upload import Data


def iterate_downloader(magic):
    d = magic.downloader._process_deque()
    magic.downloader._clock.advance(Downloader.REMOTE_SCAN_INTERVAL)
    return d


def iterate_uploader(magic):
    return magic.uploader._process_deque()



class CheckerMixin(object):
    """
    Factored out of one of the many test classes.

    *Ideally* these should just be bare helper methods, but many of
    them already depended upon self.* state. One major problem is that
    they're using self.magicfolder *but* some of the alice/bob tests
    use this, too, and they just do "self.magicfolder =
    self.bob_magicfolder" or whatever before calling them, which is
    *horrible*.
    """
    def _check_mkdir(self, name_u):
        return self._check_file(name_u + u"/", "", directory=True)

    def _check_file(self, name_u, data, temporary=False, directory=False):
        precondition(not (temporary and directory), temporary=temporary, directory=directory)

        print "%r._check_file(%r, %r, temporary=%r, directory=%r)" % (self, name_u, data, temporary, directory)
        previously_uploaded = self._get_count('uploader.objects_succeeded')
        previously_disappeared = self._get_count('uploader.objects_disappeared')

        d = self.magicfolder.uploader.set_hook('processed')

        path_u = abspath_expanduser_unicode(name_u, base=self.local_dir)
        path = to_filepath(path_u)

        if directory:
            os.mkdir(path_u)
            event_mask = self.inotify.IN_CREATE | self.inotify.IN_ISDIR
        else:
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
                self.notify(path, self.inotify.IN_DELETE, flush=False)
            event_mask = self.inotify.IN_CLOSE_WRITE

        self.notify(path, event_mask)
        encoded_name_u = magicpath.path2magic(name_u)

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        if temporary:
            d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_disappeared'),
                                                                 previously_disappeared + 1))
        else:
            def _here(res, n):
                print "here %r %r" % (n, res)
                return res
            d.addBoth(_here, 1)
            d.addCallback(lambda ign: self.upload_dirnode.list())
            d.addBoth(_here, 1.5)
            d.addCallback(lambda ign: self.upload_dirnode.get(encoded_name_u))
            d.addBoth(_here, 2)
            d.addCallback(download_to_data)
            d.addBoth(_here, 3)
            d.addCallback(lambda actual_data: self.failUnlessReallyEqual(actual_data, data))
            d.addBoth(_here, 4)
            d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'),
                                                                 previously_uploaded + 1))

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        return d

    @defer.inlineCallbacks
    def _check_version_in_dmd(self, magicfolder, relpath_u, expected_version):
        encoded_name_u = magicpath.path2magic(relpath_u)
        result = yield magicfolder.downloader._get_collective_latest_file(encoded_name_u)
        self.assertTrue(result is not None)
        node, metadata = result
        print("NODE", node, metadata)
        self.failUnlessEqual(metadata['version'], expected_version)

    def _check_version_in_local_db(self, magicfolder, relpath_u, expected_version):
        db_entry = magicfolder._db.get_db_entry(relpath_u)
        if db_entry is not None:
            #print "_check_version_in_local_db: %r has version %s" % (relpath_u, version)
            self.failUnlessEqual(db_entry.version, expected_version)

    def _check_file_gone(self, magicfolder, relpath_u):
        path = os.path.join(magicfolder.uploader._local_path_u, relpath_u)
        self.assertTrue(not os.path.exists(path))

    def _check_uploader_count(self, name, expected, magic=None):
        self.failUnlessReallyEqual(self._get_count('uploader.'+name, client=(magic or self.alice_magicfolder)._client),
                                   expected)

    def _check_downloader_count(self, name, expected, magic=None):
        self.failUnlessReallyEqual(self._get_count('downloader.'+name, client=(magic or self.bob_magicfolder)._client),
                                   expected)

    def _get_count(self, name, client=None):
        counters = (client or self.get_client()).stats_provider.get_stats()["counters"]
        return counters.get('magic_folder.%s' % (name,), 0)



class MagicFolderAliceBobTestMixin(MagicFolderCLITestMixin, ShouldFailMixin, ReallyEqualMixin, NonASCIIPathMixin, CheckerMixin):

    def setUp(self):
        # super(MagicFolderAliceBobTestMixin, self).setUp() # XXX huh, why isn't this working?
        GridTestMixin.setUp(self) # XXX totally wrong
        temp = self.mktemp()
        self.basedir = abspath_expanduser_unicode(temp.decode(get_filesystem_encoding()))
        # set_up_grid depends on self.basedir existing
        self.set_up_grid(num_clients=2)

        self.alice_clock = task.Clock()
        self.bob_clock = task.Clock()

        # this is all just .setup_alice_and_bob(), essentially
        self.alice_magicfolder = None
        self.bob_magicfolder = None

        self.alice_magic_dir = abspath_expanduser_unicode(u"Alice-magic", base=self.basedir)
        self.mkdir_nonascii(self.alice_magic_dir)
        self.bob_magic_dir = abspath_expanduser_unicode(u"Bob-magic", base=self.basedir)
        self.mkdir_nonascii(self.bob_magic_dir)

        # Alice creates a Magic Folder,
        # invites herself then and joins.
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, u"Alice\u00F8"))
        def get_invite_code(result):
            self.invite_code = result[1].strip()
        d.addCallback(get_invite_code)
        d.addCallback(lambda ign: self.do_join(0, self.alice_magic_dir, self.invite_code))
        def get_alice_caps(ign):
            self.alice_collective_dircap, self.alice_upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_alice_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.alice_upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, self.alice_magic_dir))
        def get_Alice_magicfolder(result):
            self.alice_magicfolder = self.init_magicfolder(0, self.alice_upload_dircap,
                                                           self.alice_collective_dircap,
                                                           self.alice_magic_dir, self.alice_clock)
            return result
        d.addCallback(get_Alice_magicfolder)

        # Alice invites Bob. Bob joins.
        d.addCallback(lambda ign: self.do_invite(0, u"Bob\u00F8"))
        def get_invite_code(result):
            self.invite_code = result[1].strip()
        d.addCallback(get_invite_code)
        d.addCallback(lambda ign: self.do_join(1, self.bob_magic_dir, self.invite_code))
        def get_bob_caps(ign):
            self.bob_collective_dircap, self.bob_upload_dircap = self.get_caps_from_files(1)
        d.addCallback(get_bob_caps)
        d.addCallback(lambda ign: self.check_joined_config(1, self.bob_upload_dircap))
        d.addCallback(lambda ign: self.check_config(1, self.bob_magic_dir))
        def get_Bob_magicfolder(result):
            self.bob_magicfolder = self.init_magicfolder(1, self.bob_upload_dircap,
                                                         self.bob_collective_dircap,
                                                         self.bob_magic_dir, self.bob_clock)
            return result
        d.addCallback(get_Bob_magicfolder)
        return d

    def tearDown(self):
        print("TEARDOWN!")
        d = GridTestMixin.tearDown(self)
        if self.alice_magicfolder:
            d.addCallback(lambda ign: self.alice_magicfolder.finish())
        if self.bob_magicfolder:
            d.addCallback(lambda ign: self.bob_magicfolder.finish())
        self.alice_magicfolder.uploader._clock.advance(4)
        self.alice_magicfolder.downloader._clock.advance(4)
        self.bob_magicfolder.uploader._clock.advance(4)
        self.bob_magicfolder.downloader._clock.advance(4)
        # XXX double-check: are self.mktemp() dirs blown away automagically?
        return d

    @defer.inlineCallbacks
    def test_alice_delete_bob_restore(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        print("writing", alice_fname)
        alice_up = self.alice_magicfolder.uploader.set_hook('processed')
        fileutil.write(alice_fname, 'contents0\n')
        self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        # alice uploads
        yield iterate_uploader(self.alice_magicfolder)
        yield alice_up
        print("BOOOOOM\n\n\n")

        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)

        # bob downloads
        yield iterate_downloader(self.bob_magicfolder)

        # check the state
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
        yield self.failUnlessReallyEqual(
            self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
            0
        )
        yield self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            1
        )

        print("BOB DELETE")
        # now bob deletes it (bob should upload, alice download)
        bob_proc = self.bob_magicfolder.uploader.set_hook('processed')
        alice_proc = self.alice_magicfolder.downloader.set_hook('processed')
        os.unlink(bob_fname)
        self.notify(to_filepath(bob_fname), self.inotify.IN_DELETE, magic=self.bob_magicfolder)

        yield iterate_uploader(self.bob_magicfolder)
        yield bob_proc
        yield iterate_downloader(self.alice_magicfolder)
        yield alice_proc

        # check versions
        node, metadata = yield self.alice_magicfolder.downloader._get_collective_latest_file(u'blam')
        self.assertTrue(metadata['deleted'])
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)

        print("ALICE RESTORE")
        # now alice restores it (alice should upload, bob download)
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')
        fileutil.write(alice_fname, 'new contents\n')
        self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        yield iterate_uploader(self.alice_magicfolder)
        yield alice_proc
        yield iterate_downloader(self.bob_magicfolder)
        yield bob_proc

        # check versions
        node, metadata = yield self.alice_magicfolder.downloader._get_collective_latest_file(u'blam')
        self.assertTrue('deleted' not in metadata or not metadata['deleted'])
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 2)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 2)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 2)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 2)

    @defer.inlineCallbacks
    def test_alice_sees_bobs_delete_with_error(self):
        # alice creates a file, bob deletes it -- and we also arrange
        # for Alice's file to have "gone missing" as well.
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # alice creates a file, bob downloads it
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        fileutil.write(alice_fname, 'contents0\n')
        self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        yield iterate_uploader(self.alice_magicfolder)
        yield alice_proc  # alice uploads

        yield iterate_downloader(self.bob_magicfolder)
        yield bob_proc    # bob downloads

        # check the state (XXX I had to switch the versions to 0; is that really right? why?)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
        yield self.failUnlessReallyEqual(
            self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
            0
        )
        yield self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            1
        )

        # now bob deletes it (bob should upload, alice download)
        bob_proc = self.bob_magicfolder.uploader.set_hook('processed')
        alice_proc = self.alice_magicfolder.downloader.set_hook('processed')
        os.unlink(bob_fname)
        self.notify(to_filepath(bob_fname), self.inotify.IN_DELETE, magic=self.bob_magicfolder)
        # just after notifying bob, we also delete alice's,
        # covering the 'except' flow in _rename_deleted_file()
        os.unlink(alice_fname)

        yield iterate_uploader(self.bob_magicfolder)
        yield bob_proc
        yield iterate_downloader(self.alice_magicfolder)
        yield alice_proc

        # check versions
        node, metadata = yield self.alice_magicfolder.downloader._get_collective_latest_file(u'blam')
        self.assertTrue(metadata['deleted'])
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)

    @defer.inlineCallbacks
    def test_alice_create_bob_update(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # alice creates a file, bob downloads it
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        fileutil.write(alice_fname, 'contents0\n')
        self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        yield iterate_uploader(self.alice_magicfolder)
        yield alice_proc  # alice uploads

        yield iterate_downloader(self.bob_magicfolder)
        yield bob_proc    # bob downloads

        # check the state (XXX ditto, had to switch to veresion 0; right?)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
        yield self.failUnlessReallyEqual(
            self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
            0
        )
        yield self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            1
        )

        # now bob updates it (bob should upload, alice download)
        bob_proc = self.bob_magicfolder.uploader.set_hook('processed')
        alice_proc = self.alice_magicfolder.downloader.set_hook('processed')
        fileutil.write(bob_fname, 'bob wuz here\n')
        self.notify(to_filepath(bob_fname), self.inotify.IN_CLOSE_WRITE, magic=self.bob_magicfolder)

        yield iterate_uploader(self.bob_magicfolder)
        yield bob_proc
        yield iterate_downloader(self.alice_magicfolder)
        yield alice_proc

        # check the state
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)

    @defer.inlineCallbacks
    def test_alice_delete_and_restore(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # alice creates a file, bob downloads it
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        fileutil.write(alice_fname, 'contents0\n')
        self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        yield iterate_uploader(self.alice_magicfolder)
        yield alice_proc  # alice uploads

        yield iterate_downloader(self.bob_magicfolder)
        yield bob_proc    # bob downloads

        # check the state
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
        yield self.failUnlessReallyEqual(
            self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
            0
        )
        yield self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            1
        )
        self.failUnless(os.path.exists(bob_fname))

        # now alice deletes it (alice should upload, bob download)
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')
        os.unlink(alice_fname)
        self.notify(to_filepath(alice_fname), self.inotify.IN_DELETE, magic=self.alice_magicfolder)

        yield iterate_uploader(self.alice_magicfolder)
        yield alice_proc
        yield iterate_downloader(self.bob_magicfolder)
        yield bob_proc

        # check the state
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)
        self.failIf(os.path.exists(bob_fname))

        # now alice restores the file (with new contents)
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')
        fileutil.write(alice_fname, 'alice wuz here\n')
        self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        yield iterate_uploader(self.alice_magicfolder)
        yield iterate_downloader(self.alice_magicfolder)  #  why?
        yield alice_proc
        yield iterate_downloader(self.bob_magicfolder)
        yield iterate_uploader(self.bob_magicfolder)
        yield bob_proc

        # check the state
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 2)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 2)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 2)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 2)
        self.failUnless(os.path.exists(bob_fname))


class SingleMagicFolderTestMixin(MagicFolderCLITestMixin, ShouldFailMixin, ReallyEqualMixin, NonASCIIPathMixin):
    """
    These tests will be run both with a mock notifier, and (on platforms that support it)
    with the real INotify.
    """

    def setUp(self):
        GridTestMixin.setUp(self)
        temp = self.mktemp()
        self.basedir = abspath_expanduser_unicode(temp.decode(get_filesystem_encoding()))
        self.magicfolder = None
        self.patch(Downloader, 'REMOTE_SCAN_INTERVAL', 0)# XXX FIXME remove

        # factored out of (some?) tests. if the answer isn't "all
        # tests in this class", then "something" should change. Soooo
        # much nicer to have py.test @fixture things for this stuf
        # though :/
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, u"local_dir")
        self.mkdir_nonascii(self.local_dir)

        d = self.create_invite_join_magic_folder(u"Alice\u0101", self.local_dir)
        d.addCallback(self._restart_client)
        return d

    def tearDown(self):
        d = GridTestMixin.tearDown(self)
        d.addCallback(self.cleanup)
        return d

    def _createdb(self):
        dbfile = abspath_expanduser_unicode(u"magicfolderdb.sqlite", base=self.basedir)
        mdb = magicfolderdb.get_magicfolderdb(dbfile, create_version=(magicfolderdb.SCHEMA_v1, 1))
        self.failUnless(mdb, "unable to create magicfolderdb from %r" % (dbfile,))
        self.failUnlessEqual(mdb.VERSION, 1)
        return mdb

    def _restart_client(self, ign):
        #print "_restart_client"
        d = self.restart_client()
        d.addCallback(self._wait_until_started)
        return d

    def _wait_until_started(self, ign):
        #print "_wait_until_started"
        self.magicfolder = self.get_client().getServiceNamed('magic-folder')
        self.up_clock = task.Clock()
        self.down_clock = task.Clock()
        self.magicfolder.uploader._clock = self.up_clock
        self.magicfolder.downloader._clock = self.down_clock
        # XXX should probably be passing the reactor to instances when
        # they're created, but that's a ton of re-factoring, so we
        # side-step that issue by hacking it in here. However, we
        # *have* to "hack it in" before we call ready() so that the
        # first iteration of the loop doesn't call the "real"
        # reactor's callLater. :(
        return self.magicfolder.ready()

    def test_db_basic(self):
        fileutil.make_dirs(self.basedir)
        self._createdb()

    def test_db_persistence(self):
        """Test that a file upload creates an entry in the database."""

        fileutil.make_dirs(self.basedir)
        db = self._createdb()

        relpath1 = u"myFile1"
        pathinfo = fileutil.PathInfo(isdir=False, isfile=True, islink=False,
                                     exists=True, size=1, mtime=123, ctime=456)
        db.did_upload_version(relpath1, 0, 'URI:LIT:1', 'URI:LIT:0', 0, pathinfo)

        c = db.cursor
        c.execute("SELECT size, mtime, ctime"
                  " FROM local_files"
                  " WHERE path=?",
                  (relpath1,))
        row = c.fetchone()
        self.failUnlessEqual(row, (pathinfo.size, pathinfo.mtime, pathinfo.ctime))

        # Second test uses magic_folder.is_new_file instead of SQL query directly
        # to confirm the previous upload entry in the db.
        relpath2 = u"myFile2"
        path2 = os.path.join(self.basedir, relpath2)
        fileutil.write(path2, "meow\n")
        pathinfo = fileutil.get_pathinfo(path2)
        db.did_upload_version(relpath2, 0, 'URI:LIT:2', 'URI:LIT:1', 0, pathinfo)
        db_entry = db.get_db_entry(relpath2)
        self.failUnlessFalse(magic_folder.is_new_file(pathinfo, db_entry))

        different_pathinfo = fileutil.PathInfo(isdir=False, isfile=True, islink=False,
                                               exists=True, size=0, mtime=pathinfo.mtime, ctime=pathinfo.ctime)
        self.failUnlessTrue(magic_folder.is_new_file(different_pathinfo, db_entry))

    def test_magicfolder_start_service(self):
        self.set_up_grid()

        self.local_dir = abspath_expanduser_unicode(self.unicode_or_fallback(u"l\u00F8cal_dir", u"local_dir"),
                                                    base=self.basedir)
        self.mkdir_nonascii(self.local_dir)

        d = defer.succeed(None)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 0))

        d.addCallback(lambda ign: self.create_invite_join_magic_folder(u"Alice", self.local_dir))
        d.addCallback(self._restart_client)

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 1))
        d.addBoth(self.cleanup)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 0))
        return d

    def test_scan_once_on_startup(self):
        self.set_up_grid()
        self.local_dir = abspath_expanduser_unicode(u"test_scan_once_on_startup", base=self.basedir)
        self.mkdir_nonascii(self.local_dir)
        self.collective_dircap = ""

        alice_clock = task.Clock()
        bob_clock = task.Clock()
        d = self.setup_alice_and_bob(alice_clock, bob_clock)

        def upload_stuff(ignore):
            uploadable = Data("", self.alice_magicfolder._client.convergence)
            return self.alice_magicfolder._client.upload(uploadable)
        d.addCallback(upload_stuff)
        def check_is_upload(ignore):
            alice_clock.advance(99)
            d.addCallback(lambda ign: self._check_uploader_count('files_uploaded', 0, magic=self.alice_magicfolder))
            d.addCallback(lambda ign: self._check_uploader_count('objects_queued', 0, magic=self.alice_magicfolder))
            d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
            d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 0, magic=self.alice_magicfolder))
            d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
            d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 0, magic=self.alice_magicfolder))

        d.addCallback(check_is_upload)
        def _cleanup(ign, magicfolder, clock):
            if magicfolder is not None:
                d2 = magicfolder.finish()
                clock.advance(0)
                return d2
        def cleanup_Alice_and_Bob(result):
            print "cleanup alice bob test\n"
            d = defer.succeed(None)
            d.addCallback(_cleanup, self.alice_magicfolder, alice_clock)
            d.addCallback(_cleanup, self.bob_magicfolder, bob_clock)
            d.addCallback(lambda ign: result)
            return d

        d.addBoth(cleanup_Alice_and_Bob)
        return d

    def test_move_tree(self):
        self.set_up_grid()

        self.local_dir = abspath_expanduser_unicode(self.unicode_or_fallback(u"l\u00F8cal_dir", u"local_dir"),
                                                    base=self.basedir)
        self.mkdir_nonascii(self.local_dir)

        empty_tree_name = self.unicode_or_fallback(u"empty_tr\u00EAe", u"empty_tree")
        empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.basedir)
        new_empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.local_dir)

        small_tree_name = self.unicode_or_fallback(u"small_tr\u00EAe", u"empty_tree")
        small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.basedir)
        new_small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.local_dir)

        d = self.create_invite_join_magic_folder(u"Alice", self.local_dir)
        d.addCallback(self._restart_client)

        def _check_move_empty_tree(res):
            print "_check_move_empty_tree"
            uploaded_d = self.magicfolder.uploader.set_hook('processed')
            self.mkdir_nonascii(empty_tree_dir)
            os.rename(empty_tree_dir, new_empty_tree_dir)
            self.notify(to_filepath(new_empty_tree_dir), self.inotify.IN_MOVED_TO)

            return uploaded_d
        d.addCallback(_check_move_empty_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 1))

        def _check_move_small_tree(res):
            print "_check_move_small_tree"
            uploaded_d = self.magicfolder.uploader.set_hook('processed', ignore_count=1)
            self.mkdir_nonascii(small_tree_dir)
            what_path = abspath_expanduser_unicode(u"what", base=small_tree_dir)
            fileutil.write(what_path, "say when")
            os.rename(small_tree_dir, new_small_tree_dir)
            self.notify(to_filepath(new_small_tree_dir), self.inotify.IN_MOVED_TO)

            return uploaded_d
        d.addCallback(_check_move_small_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        def _check_moved_tree_is_watched(res):
            print "_check_moved_tree_is_watched"
            uploaded_d = self.magicfolder.uploader.set_hook('processed')
            another_path = abspath_expanduser_unicode(u"another", base=new_small_tree_dir)
            fileutil.write(another_path, "file")
            self.notify(to_filepath(another_path), self.inotify.IN_CLOSE_WRITE)

            return uploaded_d
        d.addCallback(_check_moved_tree_is_watched)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 4))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 2))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        # Files that are moved out of the upload directory should no longer be watched.
        #def _move_dir_away(ign):
        #    os.rename(new_empty_tree_dir, empty_tree_dir)
        #    # Wuh? Why don't we get this event for the real test?
        #    #self.notify(to_filepath(new_empty_tree_dir), self.inotify.IN_MOVED_FROM)
        #d.addCallback(_move_dir_away)
        #def create_file(val):
        #    test_file = abspath_expanduser_unicode(u"what", base=empty_tree_dir)
        #    fileutil.write(test_file, "meow")
        #    #self.notify(...)
        #    return
        #d.addCallback(create_file)
        #d.addCallback(lambda ign: time.sleep(1))  # XXX ICK
        #d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        #d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 4))
        #d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 2))
        #d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        #d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

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
        self.collective_dircap = ""

        d = defer.succeed(None)
        d.addCallback(lambda ign: self.create_invite_join_magic_folder(u"Alice", self.local_dir))
        d.addCallback(self._restart_client)

        def create_test_file(filename):
            d2 = self.magicfolder.uploader.set_hook('processed')
            test_file = abspath_expanduser_unicode(filename, base=self.local_dir)
            fileutil.write(test_file, "meow %s" % filename)
            self.notify(to_filepath(test_file), self.inotify.IN_CLOSE_WRITE)
            return d2
        d.addCallback(lambda ign: create_test_file(u"what1"))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(self.cleanup)

        d.addCallback(self._restart_client)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: create_test_file(u"what2"))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 2))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addBoth(self.cleanup)
        return d

    # all this "self.*" state via 9000 mix-ins is really really
    # hard to read, keep track of, etc. Very hard to understand
    # what each test uses for setup, etc. :(

    @defer.inlineCallbacks
    def test_delete(self):
        # setup: create a file 'foo'
        path = os.path.join(self.local_dir, u'foo')
        fileutil.write(path, 'foo\n')
        yield self.notify(to_filepath(path), self.inotify.IN_CLOSE_WRITE)
        yield iterate_uploader(self.magicfolder)
        self.assertTrue(os.path.exists(path))
        node, metadata = yield self.magicfolder.downloader._get_collective_latest_file(u'foo')
        self.assertTrue(node is not None, "Failed to find %r in DMD" % (path,))

        # the test: delete the file (and do fake notifies)
        os.unlink(path)
        yield self.notify(to_filepath(path), self.inotify.IN_DELETE)

        yield iterate_uploader(self.magicfolder)
        self.assertFalse(os.path.exists(path))

        yield iterate_downloader(self.magicfolder)
        # ensure we still have a DB entry, and that the version is 1
        node, metadata = yield self.magicfolder.downloader._get_collective_latest_file(u'foo')
        self.assertTrue(node is not None, "Failed to find %r in DMD" % (path,))
        self.failUnlessEqual(metadata['version'], 1)

    @defer.inlineCallbacks
    def test_delete_and_restore(self):
        # setup: create a file
        path = os.path.join(self.local_dir, u'foo')
        fileutil.write(path, 'foo\n')
        yield self.notify(to_filepath(path), self.inotify.IN_CLOSE_WRITE)
        yield iterate_uploader(self.magicfolder)
        self.assertTrue(os.path.exists(path))

        # ...and delete the file
        os.unlink(path)
        yield self.notify(to_filepath(path), self.inotify.IN_DELETE)
        yield iterate_uploader(self.magicfolder)
        self.assertFalse(os.path.exists(path))

        # ensure we still have a DB entry, and that the version is 1
        node, metadata = yield self.magicfolder.downloader._get_collective_latest_file(u'foo')
        self.assertTrue(node is not None, "Failed to find %r in DMD" % (path,))
        self.failUnlessEqual(metadata['version'], 1)

        # restore the file, with different contents
        path = os.path.join(self.local_dir, u'foo')
        fileutil.write(path, 'bar\n')
        yield self.notify(to_filepath(path), self.inotify.IN_CLOSE_WRITE)
        yield iterate_uploader(self.magicfolder)

        # ensure we still have a DB entry, and that the version is 2
        node, metadata = yield self.magicfolder.downloader._get_collective_latest_file(u'foo')
        self.assertTrue(node is not None, "Failed to find %r in DMD" % (path,))
        self.failUnlessEqual(metadata['version'], 2)

    def test_magic_folder(self):
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, self.unicode_or_fallback(u"loc\u0101l_dir", u"local_dir"))
        self.mkdir_nonascii(self.local_dir)

        d = self.create_invite_join_magic_folder(u"Alice\u0101", self.local_dir)
        d.addCallback(self._restart_client)

        # Write something short enough for a LIT file.
        d.addCallback(lambda ign: self._check_file(u"short", "test"))

        # Write to the same file again with different data.
        d.addCallback(lambda ign: self._check_file(u"short", "different"))

        # Test that temporary files are not uploaded.
        d.addCallback(lambda ign: self._check_file(u"tempfile", "test", temporary=True))

        # Test creation of a subdirectory.
        d.addCallback(lambda ign: self._check_mkdir(u"directory"))

        # Write something longer, and also try to test a Unicode name if the fs can represent it.
        name_u = self.unicode_or_fallback(u"l\u00F8ng", u"long")
        d.addCallback(lambda ign: self._check_file(name_u, "test"*100))

        # TODO: test that causes an upload failure.
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))

        d.addBoth(self.cleanup)
        return d

    def test_alice_bob(self):
        alice_clock = task.Clock()
        bob_clock = task.Clock()
        d = self.setup_alice_and_bob(alice_clock, bob_clock)

        def _wait_for_Alice(ign, downloaded_d):
            print "Now waiting for Alice to download\n"
            alice_clock.advance(4)
            return downloaded_d

        def _wait_for_Bob(ign, downloaded_d):
            print "Now waiting for Bob to download\n"
            bob_clock.advance(4)
            return downloaded_d

        def _wait_for(ign, something_to_do, alice=True):
            if alice:
                downloaded_d = self.bob_magicfolder.downloader.set_hook('processed')
                uploaded_d = self.alice_magicfolder.uploader.set_hook('processed')
            else:
                downloaded_d = self.alice_magicfolder.downloader.set_hook('processed')
                uploaded_d = self.bob_magicfolder.uploader.set_hook('processed')
            something_to_do()
            if alice:
                print "Waiting for Alice to upload\n"
                alice_clock.advance(4)
                uploaded_d.addCallback(_wait_for_Bob, downloaded_d)
            else:
                print "Waiting for Bob to upload\n"
                bob_clock.advance(4)
                uploaded_d.addCallback(_wait_for_Alice, downloaded_d)
            return uploaded_d

        def Alice_to_write_a_file():
            print "Alice writes a file\n"
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "meow, meow meow. meow? meow meow! meow.")
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_write_a_file)

        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file1", 0))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.alice_magicfolder, u"file1", 0))
        d.addCallback(lambda ign: self._check_uploader_count('objects_failed', 0))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 1))
        d.addCallback(lambda ign: self._check_uploader_count('files_uploaded', 1))
        d.addCallback(lambda ign: self._check_uploader_count('objects_queued', 0))
        d.addCallback(lambda ign: self._check_uploader_count('directories_created', 0))
        d.addCallback(lambda ign: self._check_uploader_count('objects_conflicted', 0))
        d.addCallback(lambda ign: self._check_uploader_count('objects_conflicted', 0, magic=self.bob_magicfolder))

        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, u"file1", 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 1))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 0, magic=self.bob_magicfolder))

        def Alice_to_delete_file():
            print "Alice deletes the file!\n"
            os.unlink(self.file_path)
            self.notify(to_filepath(self.file_path), self.inotify.IN_DELETE, magic=self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_delete_file)

        def notify_bob_moved(ign):
            d0 = self.bob_magicfolder.uploader.set_hook('processed')
            p = abspath_expanduser_unicode(u"file1", base=self.bob_magicfolder.uploader._local_path_u)
            self.notify(to_filepath(p), self.inotify.IN_MOVED_FROM, magic=self.bob_magicfolder, flush=False)
            self.notify(to_filepath(p + u'.backup'), self.inotify.IN_MOVED_TO, magic=self.bob_magicfolder)
            bob_clock.advance(4)
            return d0
        d.addCallback(notify_bob_moved)

        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file1", 1))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.alice_magicfolder, u"file1", 1))
        d.addCallback(lambda ign: self._check_uploader_count('objects_failed', 0))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 2))
        d.addCallback(lambda ign: self._check_uploader_count('objects_not_uploaded', 1, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 1, magic=self.bob_magicfolder))

        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, u"file1", 1))
        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file1", 1))
        d.addCallback(lambda ign: self._check_file_gone(self.bob_magicfolder, u"file1"))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 2))

        def Alice_to_rewrite_file():
            print "Alice rewrites file\n"
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "Alice suddenly sees the white rabbit running into the forest.")
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_rewrite_file)

        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file1", 2))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.alice_magicfolder, u"file1", 2))
        d.addCallback(lambda ign: self._check_uploader_count('objects_failed', 0))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 3))
        d.addCallback(lambda ign: self._check_uploader_count('files_uploaded', 3))
        d.addCallback(lambda ign: self._check_uploader_count('objects_queued', 0))
        d.addCallback(lambda ign: self._check_uploader_count('directories_created', 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0))

        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file1", 2))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, u"file1", 2))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 3))

        path_u = u"/tmp/magic_folder_test"
        encoded_path_u = magicpath.path2magic(u"/tmp/magic_folder_test")

        def Alice_tries_to_p0wn_Bob(ign):
            print "Alice tries to p0wn Bob\n"
            processed_d = self.bob_magicfolder.downloader.set_hook('processed')

            # upload a file that would provoke the security bug from #2506
            uploadable = Data("", self.alice_magicfolder._client.convergence)
            alice_dmd = self.alice_magicfolder.uploader._upload_dirnode

            d2 = alice_dmd.add_file(encoded_path_u, uploadable, metadata={"version": 0}, overwrite=True)
            d2.addCallback(lambda ign: self.failUnless(alice_dmd.has_child(encoded_path_u)))
            d2.addCallback(_wait_for_Bob, processed_d)
            return d2
        d.addCallback(Alice_tries_to_p0wn_Bob)

        d.addCallback(lambda ign: self.failIf(os.path.exists(path_u)))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, encoded_path_u, None))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 3))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0))

        def Bob_to_rewrite_file():
            print "Bob rewrites file\n"
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.bob_magicfolder.uploader._local_path_u)
            print "---- bob's file is %r" % (self.file_path,)
            fileutil.write(self.file_path, "No white rabbit to be found.")
            self.magicfolder = self.bob_magicfolder
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE)
        d.addCallback(lambda ign: _wait_for(None, Bob_to_rewrite_file, alice=False))

        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file1", 3))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, u"file1", 3))
        d.addCallback(lambda ign: self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 2, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('files_uploaded', 1, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_queued', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0))

        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file1", 3))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.alice_magicfolder, u"file1", 3))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 1, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))

        def Alice_conflicts_with_Bobs_last_downloaded_uri():
            print "Alice conflicts with Bob\n"
            downloaded_d = self.bob_magicfolder.downloader.set_hook('processed')
            uploadable = Data("do not follow the white rabbit", self.alice_magicfolder._client.convergence)
            alice_dmd = self.alice_magicfolder.uploader._upload_dirnode
            d2 = alice_dmd.add_file(u"file1", uploadable,
                                    metadata={"version": 5,
                                              "last_downloaded_uri" : "URI:LIT:" },
                                    overwrite=True)
            print "Waiting for Alice to upload\n"
            d2.addCallback(lambda ign: bob_clock.advance(6))
            d2.addCallback(lambda ign: downloaded_d)
            d2.addCallback(lambda ign: self.failUnless(alice_dmd.has_child(encoded_path_u)))
            return d2

        d.addCallback(lambda ign: Alice_conflicts_with_Bobs_last_downloaded_uri())
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 4))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 1))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 1, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))

        # prepare to perform another conflict test
        def Alice_to_write_file2():
            print "Alice writes a file\n"
            self.file_path = abspath_expanduser_unicode(u"file2", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "something")
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_write_file2)
        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file2", 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))

        def Bob_to_rewrite_file2():
            print "Bob rewrites file\n"
            self.file_path = abspath_expanduser_unicode(u"file2", base=self.bob_magicfolder.uploader._local_path_u)
            print "---- bob's file is %r" % (self.file_path,)
            fileutil.write(self.file_path, "roger roger. what vector?")
            self.magicfolder = self.bob_magicfolder
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE)
        d.addCallback(lambda ign: _wait_for(None, Bob_to_rewrite_file2, alice=False))
        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file2", 1))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 5))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 1))
        d.addCallback(lambda ign: self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 3, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('files_uploaded', 2, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_queued', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder))

        # XXX here we advance the clock and then test again to make sure no values are monotonically increasing
        # with each queue turn ;-p
        alice_clock.advance(6)
        bob_clock.advance(6)
        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file2", 1))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 5))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 1))
        d.addCallback(lambda ign: self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 3, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('files_uploaded', 2, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_queued', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder))

        def Alice_conflicts_with_Bobs_last_uploaded_uri():
            print "Alice conflicts with Bob\n"
            encoded_path_u = magicpath.path2magic(u"file2")
            downloaded_d = self.bob_magicfolder.downloader.set_hook('processed')
            uploadable = Data("rabbits with sharp fangs", self.alice_magicfolder._client.convergence)
            alice_dmd = self.alice_magicfolder.uploader._upload_dirnode
            d2 = alice_dmd.add_file(u"file2", uploadable,
                                    metadata={"version": 5,
                                              "last_uploaded_uri" : "URI:LIT:" },
                                    overwrite=True)
            print "Waiting for Alice to upload\n"
            d2.addCallback(lambda ign: bob_clock.advance(6))
            d2.addCallback(lambda ign: downloaded_d)
            d2.addCallback(lambda ign: self.failUnless(alice_dmd.has_child(encoded_path_u)))
            return d2
        d.addCallback(lambda ign: Alice_conflicts_with_Bobs_last_uploaded_uri())
        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file2", 5))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 6))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 2))
        d.addCallback(lambda ign: self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 3, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('files_uploaded', 2, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_queued', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder))

        alice_clock.advance(6)
        bob_clock.advance(6)
        alice_clock.advance(6)
        bob_clock.advance(6)

        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 2))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 6))

        # prepare to perform another conflict test
        def Alice_to_write_file3():
            print "Alice writes a file\n"
            self.file_path = abspath_expanduser_unicode(u"file3", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "something")
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_write_file3)
        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file3", 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 7))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 2))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))

        def Bob_to_rewrite_file3():
            print "Bob rewrites file\n"
            self.file_path = abspath_expanduser_unicode(u"file3", base=self.bob_magicfolder.uploader._local_path_u)
            print "---- bob's file is %r" % (self.file_path,)
            fileutil.write(self.file_path, "roger roger")
            self.magicfolder = self.bob_magicfolder
            self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE)
        d.addCallback(lambda ign: _wait_for(None, Bob_to_rewrite_file3, alice=False))
        d.addCallback(lambda ign: self._check_version_in_dmd(self.bob_magicfolder, u"file3", 1))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 7))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 2))
        d.addCallback(lambda ign: self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_succeeded', 4, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('files_uploaded', 3, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('objects_queued', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 3, magic=self.alice_magicfolder))



        def _cleanup(ign, magicfolder, clock):
            if magicfolder is not None:
                d2 = magicfolder.finish()
                clock.advance(4)
                return d2

        def cleanup_Alice_and_Bob(result):
            print "cleanup alice bob test\n"
            d = defer.succeed(None)
            d.addCallback(_cleanup, self.alice_magicfolder, alice_clock)
            d.addCallback(_cleanup, self.bob_magicfolder, bob_clock)
            d.addCallback(lambda ign: result)
            return d
        d.addBoth(cleanup_Alice_and_Bob)
        return d


class MockTestAliceBob(MagicFolderAliceBobTestMixin, unittest.TestCase):
    def setUp(self):
        d = super(MockTestAliceBob, self).setUp()
        self.inotify = fake_inotify
        self.patch(magic_folder, 'get_inotify_module', lambda: self.inotify)
        return d

    def notify(self, path, mask, magic=None, flush=True):
        if magic is None:
            magic = self.magicfolder
        magic.uploader._notifier.event(path, mask)
        # no flush for the mock test.
        return task.deferLater(reactor, 0.1, lambda: None)



class MockTest(SingleMagicFolderTestMixin, unittest.TestCase):
    """This can run on any platform, and even if twisted.internet.inotify can't be imported."""

    def setUp(self):
        d = super(MockTest, self).setUp()
        self.inotify = fake_inotify
        self.patch(magic_folder, 'get_inotify_module', lambda: self.inotify)
        return d

    def notify(self, path, mask, magic=None, flush=True):
        if magic is None:
            magic = self.magicfolder
        magic.uploader._notifier.event(path, mask)
        # no flush for the mock test.
        return task.deferLater(reactor, 0.1, lambda: None)

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
                            MagicFolder, client, upload_dircap, '', doesnotexist, magicfolderdb, 0077)
            self.shouldFail(AssertionError, 'non-directory local.directory', 'is not a directory',
                            MagicFolder, client, upload_dircap, '', not_a_dir, magicfolderdb, 0077)
            self.shouldFail(AssertionError, 'bad upload.dircap', 'does not refer to a directory',
                            MagicFolder, client, 'bad', '', errors_dir, magicfolderdb, 0077)
            self.shouldFail(AssertionError, 'non-directory upload.dircap', 'does not refer to a directory',
                            MagicFolder, client, 'URI:LIT:foo', '', errors_dir, magicfolderdb, 0077)
            self.shouldFail(AssertionError, 'readonly upload.dircap', 'is not a writecap to a directory',
                            MagicFolder, client, readonly_dircap, '', errors_dir, magicfolderdb, 0077)
            self.shouldFail(AssertionError, 'collective dircap', 'is not a readonly cap to a directory',
                            MagicFolder, client, upload_dircap, upload_dircap, errors_dir, magicfolderdb, 0077)

            def _not_implemented():
                raise NotImplementedError("blah")
            self.patch(magic_folder, 'get_inotify_module', _not_implemented)
            self.shouldFail(NotImplementedError, 'unsupported', 'blah',
                            MagicFolder, client, upload_dircap, '', errors_dir, magicfolderdb, 0077)
        d.addCallback(_check_errors)
        return d

    def test_write_downloaded_file(self):
        workdir = u"cli/MagicFolder/write-downloaded-file"
        local_file = fileutil.abspath_expanduser_unicode(os.path.join(workdir, "foobar"))

        class TestWriteFileMixin(WriteFileMixin):
            def _log(self, msg):
                pass

        writefile = TestWriteFileMixin()
        writefile._umask = 0077

        # create a file with name "foobar" with content "foo"
        # write downloaded file content "bar" into "foobar" with is_conflict = False
        fileutil.make_dirs(workdir)
        fileutil.write(local_file, "foo")

        # if is_conflict is False, then the .conflict file shouldn't exist.
        writefile._write_downloaded_file(local_file, "bar", False, None)
        conflicted_path = local_file + u".conflict"
        self.failIf(os.path.exists(conflicted_path))

        # At this point, the backup file should exist with content "foo"
        backup_path = local_file + u".backup"
        self.failUnless(os.path.exists(backup_path))
        self.failUnlessEqual(fileutil.read(backup_path), "foo")

        # .tmp file shouldn't exist
        self.failIf(os.path.exists(local_file + u".tmp"))

        # .. and the original file should have the new content
        self.failUnlessEqual(fileutil.read(local_file), "bar")

        # now a test for conflicted case
        writefile._write_downloaded_file(local_file, "bar", True, None)
        self.failUnless(os.path.exists(conflicted_path))

        # .tmp file shouldn't exist
        self.failIf(os.path.exists(local_file + u".tmp"))


class RealTest(SingleMagicFolderTestMixin, unittest.TestCase):
    """This is skipped unless both Twisted and the platform support inotify."""

    def setUp(self):
        d = MagicFolderTestMixin.setUp(self)
        self.inotify = magic_folder.get_inotify_module()
        return d

    def notify(self, path, mask, magic=None, flush=True):
        # Writing to the filesystem causes the notification.
        # However, flushing filesystem buffers may be necessary on Windows.
        if flush:
            fileutil.flush_volume(path.path)
        # actually, there's no way to know when the actual
        # notification will occur, and anyway we're not waiting for
        # them in any case...so we'll just fudge it and home 100ms is enough.
        return task.deferLater(reactor, 0.1, lambda: None)

try:
    magic_folder.get_inotify_module()
except NotImplementedError:
    RealTest.skip = "Magic Folder support can only be tested for-real on an OS that supports inotify or equivalent."
