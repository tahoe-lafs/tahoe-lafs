
import os, sys
import shutil, simplejson

if False:
    from twisted.internet.base import DelayedCall
    DelayedCall.debug = True

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
from allmydata.frontends.magic_folder import MagicFolder, WriteFileMixin
from allmydata import magicfolderdb, magicpath
from allmydata.util.fileutil import get_pathinfo
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.immutable.upload import Data

from twisted.internet.base import DelayedCall
#DelayedCall.debug = True

class MagicFolderDbTests(unittest.TestCase):

    def setUp(self):
        self.temp = abspath_expanduser_unicode(unicode(self.mktemp()))
        os.mkdir(self.temp)
        dbfile = abspath_expanduser_unicode(u"testdb.sqlite", base=self.temp)
        self.db = magicfolderdb.get_magicfolderdb(dbfile, create_version=(magicfolderdb.SCHEMA_v1, 1))
        self.failUnless(self.db, "unable to create magicfolderdb from %r" % (dbfile,))
        self.failUnlessEqual(self.db.VERSION, 1)

    def tearDown(self):
        if hasattr(self, 'db'):
            self.db.close()
        shutil.rmtree(self.temp)
        return super(MagicFolderDbTests, self).tearDown()

    def test_create(self):
        self.db.did_upload_version(
            relpath_u=u'fake_path',
            version=0,
            last_uploaded_uri=None,
            last_downloaded_uri='URI:foo',
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )

        entry = self.db.get_db_entry(u'fake_path')
        self.assertTrue(entry is not None)
        self.assertEqual(entry.last_downloaded_uri, 'URI:foo')

    def test_update(self):
        self.db.did_upload_version(
            relpath_u=u'fake_path',
            version=0,
            last_uploaded_uri=None,
            last_downloaded_uri='URI:foo',
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )
        self.db.did_upload_version(
            relpath_u=u'fake_path',
            version=1,
            last_uploaded_uri=None,
            last_downloaded_uri='URI:bar',
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )

        entry = self.db.get_db_entry(u'fake_path')
        self.assertTrue(entry is not None)
        self.assertEqual(entry.last_downloaded_uri, 'URI:bar')
        self.assertEqual(entry.version, 1)

    def test_same_content_different_path(self):
        content_uri = 'URI:CHK:27d2yruqwk6zb2w7hkbbfxxbue:ipmszjysmn4vdeaxz7rtxtv3gwv6vrqcg2ktrdmn4oxqqucltxxq:2:4:1052835840'
        self.db.did_upload_version(
            relpath_u=u'path0',
            version=0,
            last_uploaded_uri=None,
            last_downloaded_uri=content_uri,
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )
        self.db.did_upload_version(
            relpath_u=u'path1',
            version=0,
            last_uploaded_uri=None,
            last_downloaded_uri=content_uri,
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )

        entry = self.db.get_db_entry(u'path0')
        self.assertTrue(entry is not None)
        self.assertEqual(entry.last_downloaded_uri, content_uri)

        entry = self.db.get_db_entry(u'path1')
        self.assertTrue(entry is not None)
        self.assertEqual(entry.last_downloaded_uri, content_uri)


def iterate_downloader(magic):
    # can do either of these:
    #d = magic.downloader._process_deque()
    d = magic.downloader.set_hook('iteration')
    magic.downloader._clock.advance(magic.downloader.scan_interval + 1)
    return d


def iterate_uploader(magic):
    d = magic.uploader.set_hook('iteration')
    magic.uploader._clock.advance(magic.uploader.scan_interval + 1)
    return d

@defer.inlineCallbacks
def iterate(magic):
    yield iterate_uploader(magic)
    yield iterate_downloader(magic)


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

    @defer.inlineCallbacks
    def _check_file(self, name_u, data, temporary=False, directory=False):
        precondition(not (temporary and directory), temporary=temporary, directory=directory)

        print "%r._check_file(%r, %r, temporary=%r, directory=%r)" % (self, name_u, data, temporary, directory)
        previously_uploaded = self._get_count('uploader.objects_succeeded')
        previously_disappeared = self._get_count('uploader.objects_disappeared')

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
                yield self.notify(path, self.inotify.IN_DELETE, flush=False)
            event_mask = self.inotify.IN_CLOSE_WRITE

        yield self.notify(path, event_mask)
        yield iterate(self.magicfolder)
        encoded_name_u = magicpath.path2magic(name_u)

        yield self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0)
        if temporary:
            yield self.failUnlessReallyEqual(self._get_count('uploader.objects_disappeared'),
                                             previously_disappeared + 1)
        else:
            yield self.magicfolder.uploader._upload_dirnode.list()
            x = yield self.magicfolder.uploader._upload_dirnode.get(encoded_name_u)
            actual_data = yield download_to_data(x)
            self.failUnlessReallyEqual(actual_data, data)
            self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'),
                                       previously_uploaded + 1)

        self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0)

    @defer.inlineCallbacks
    def _check_version_in_dmd(self, magicfolder, relpath_u, expected_version):
        encoded_name_u = magicpath.path2magic(relpath_u)
        result = yield magicfolder.downloader._get_collective_latest_file(encoded_name_u)
        self.assertTrue(result is not None)
        node, metadata = result
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
            d0 = self.alice_magicfolder.uploader.set_hook('iteration')
            d1 = self.alice_magicfolder.downloader.set_hook('iteration')
            self.alice_clock.advance(self.alice_magicfolder.uploader.scan_interval + 1)
            d0.addCallback(lambda ign: d1)
            d0.addCallback(lambda ign: result)
            return d0
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
            d0 = self.bob_magicfolder.uploader.set_hook('iteration')
            d1 = self.bob_magicfolder.downloader.set_hook('iteration')
            self.bob_clock.advance(self.alice_magicfolder.uploader.scan_interval + 1)
            d0.addCallback(lambda ign: d1)
            d0.addCallback(lambda ign: result)
            return d0
        d.addCallback(get_Bob_magicfolder)
        return d

    def tearDown(self):
        d = GridTestMixin.tearDown(self)
        d.addCallback(lambda ign: self.alice_magicfolder.finish())
        d.addCallback(lambda ign: self.bob_magicfolder.finish())
        for mf in [self.alice_magicfolder, self.bob_magicfolder]:
            for loader in [mf.uploader, mf.downloader]:
                loader._clock.advance(loader.scan_interval + 1)
        # XXX double-check: are self.mktemp() dirs blown away automagically?
        return d

    @defer.inlineCallbacks
    def test_alice_delete_bob_restore(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        print("writing", alice_fname)
        alice_up = self.alice_magicfolder.uploader.set_hook('processed')
        fileutil.write(alice_fname, 'contents0\n')
        yield self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        # alice uploads
        yield iterate_uploader(self.alice_magicfolder)
        yield alice_up

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
        yield self.notify(to_filepath(bob_fname), self.inotify.IN_DELETE, magic=self.bob_magicfolder)

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
        yield self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

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
        yield self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        yield iterate_uploader(self.alice_magicfolder)
        yield alice_proc  # alice uploads

        yield iterate_downloader(self.bob_magicfolder)
        yield bob_proc    # bob downloads

        # check the state (XXX I had to switch the versions to 0; is that really right? why?)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
        yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
            0
        )
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            1
        )

        # now bob deletes it (bob should upload, alice download)
        bob_proc = self.bob_magicfolder.uploader.set_hook('processed')
        alice_proc = self.alice_magicfolder.downloader.set_hook('processed')
        os.unlink(bob_fname)
        yield self.notify(to_filepath(bob_fname), self.inotify.IN_DELETE, magic=self.bob_magicfolder)
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
        fileutil.write(alice_fname, 'contents0\n')
        yield self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

        yield iterate(self.alice_magicfolder)
        yield iterate(self.bob_magicfolder)

        # check the state (XXX ditto, had to switch to veresion 0; right?)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
        self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
            0
        )
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            1
        )

        # now bob updates it (bob should upload, alice download)
        fileutil.write(bob_fname, 'bob wuz here\n')
        yield self.notify(to_filepath(bob_fname), self.inotify.IN_CLOSE_WRITE, magic=self.bob_magicfolder)

        yield iterate(self.bob_magicfolder)
        yield iterate(self.alice_magicfolder)

        # check the state
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
        self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
        self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)

    @defer.inlineCallbacks
    def test_download_retry(self):
        print("starting actual download_retry test")
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # Alice creates a file
        fileutil.write(alice_fname, ''.join(['contents-%04d\n' % i for i in range(1024)]))
        yield self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
        yield iterate(self.alice_magicfolder)
        # check alice created the file
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)

        # now, we ONLY want to do the scan, not a full iteration of
        # the process loop. So we do just the scan part "by hand" in
        # Bob's downloader
        yield self.bob_magicfolder.downloader._when_queue_is_empty()
        # while we're delving into internals, I guess we might as well
        # confirm that we did queue up an item to download
        self.assertEqual(1, len(self.bob_magicfolder.downloader._deque))

        # break all the servers so the download fails. the count is 2
        # because the "full iteration" will do a scan (downloading the
        # metadata file) and then process the deque (trying to
        # download the item we queued up already)
        for server_id in self.g.get_all_serverids():
            self.g.break_server(server_id, count=2)

        # now let bob try to do the download
        yield iterate(self.bob_magicfolder)

        # ...however Bob shouldn't have downloaded anything
        self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
        # bob should *not* have downloaded anything, as we failed all the servers
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            0
        )
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
            1
        )

        # now we let Bob try again
        yield iterate(self.bob_magicfolder)

        # ...and he should have succeeded
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            1
        )
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)


    @defer.inlineCallbacks
    def test_alice_delete_and_restore(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # alice creates a file, bob downloads it
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        fileutil.write(alice_fname, 'contents0\n')
        yield self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

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
        yield self.notify(to_filepath(alice_fname), self.inotify.IN_DELETE, magic=self.alice_magicfolder)

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
        yield self.notify(to_filepath(alice_fname), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)

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

    # XXX this should be shortened -- as in, any cases not covered by
    # the other tests in here should get their own minimal test-case.
    def test_alice_bob(self):
        d = defer.succeed(None)

        # XXX FIXME just quickly porting this test via aliases -- the
        # "real" solution is to break out any relevant test-cases as
        # their own (smaller!) tests.
        alice_clock = self.alice_magicfolder.uploader._clock
        bob_clock = self.bob_magicfolder.uploader._clock

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

            d = something_to_do()

            def advance(ign):
                if alice:
                    print "Waiting for Alice to upload 3\n"
                    alice_clock.advance(4)
                    alice_clock.advance(4)
                    uploaded_d.addCallback(_wait_for_Bob, downloaded_d)
                else:
                    print "Waiting for Bob to upload\n"
                    bob_clock.advance(4)
                    uploaded_d.addCallback(_wait_for_Alice, downloaded_d)
                return uploaded_d
            d.addCallback(advance)
            return d

        def Alice_to_write_a_file():
            print "Alice writes a file\n"
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "meow, meow meow. meow? meow meow! meow.")
            return self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
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

        @defer.inlineCallbacks
        def Alice_to_delete_file():
            print "Alice deletes the file!\n"
            os.unlink(self.file_path)
            yield self.notify(to_filepath(self.file_path), self.inotify.IN_DELETE, magic=self.alice_magicfolder)
            yield iterate(self.alice_magicfolder)
            yield iterate(self.bob_magicfolder)
        d.addCallback(_wait_for, Alice_to_delete_file)

        @defer.inlineCallbacks
        def notify_bob_moved(ign):
            p = abspath_expanduser_unicode(u"file1", base=self.bob_magicfolder.uploader._local_path_u)
            yield self.notify(to_filepath(p), self.inotify.IN_MOVED_FROM, magic=self.bob_magicfolder, flush=False)
            yield self.notify(to_filepath(p + u'.backup'), self.inotify.IN_MOVED_TO, magic=self.bob_magicfolder)
            yield iterate(self.bob_magicfolder)
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
            return self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
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
            return self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.bob_magicfolder)
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
            print "Alice writes a file2\n"
            self.file_path = abspath_expanduser_unicode(u"file2", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "something")
            return self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_write_file2)
        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file2", 0))
        d.addCallback(lambda ign: self._check_version_in_local_db(self.alice_magicfolder, u"file2", 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))

        def advance(ign):
            alice_clock.advance(4)
            bob_clock.advance(4)
            # we need to pause here, or make "is_new_file()" more
            # robust, because this is now fast enough that the mtime
            # of the allegedly-new file matches, so Bob decides not to
            # upload (and the test hangs). Not sure why it worked
            # before; must have been *just* slow enough?
            # XXX FIXME for the new real-test had to jack this to 0.5;
            # related to the 0.1 notify pause??
            return task.deferLater(reactor, 0.5, lambda: None)
        d.addCallback(advance)
        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, u"file2", 0))

        def Bob_to_rewrite_file2():
            print "Bob rewrites file2\n"
            self.file_path = abspath_expanduser_unicode(u"file2", base=self.bob_magicfolder.uploader._local_path_u)
            print "---- bob's file is %r" % (self.file_path,)
            fileutil.write(self.file_path, "roger roger. what vector?")
            return self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.bob_magicfolder)
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

        def foo(ign):
            alice_clock.advance(6)
            bob_clock.advance(6)
            alice_clock.advance(6)
            bob_clock.advance(6)
        d.addCallback(foo)

        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 2))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 6))

        # prepare to perform another conflict test
        def Alice_to_write_file3():
            print "Alice writes a file\n"
            self.file_path = abspath_expanduser_unicode(u"file3", base=self.alice_magicfolder.uploader._local_path_u)
            fileutil.write(self.file_path, "something")
            return self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_write_file3)
        d.addCallback(lambda ign: self._check_version_in_dmd(self.alice_magicfolder, u"file3", 0))
        d.addCallback(lambda ign: self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 7))
        d.addCallback(lambda ign: self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 2))
        d.addCallback(lambda ign: self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder))

        def Bob_to_rewrite_file3():
            print "Bob rewrites file3\n"
            self.file_path = abspath_expanduser_unicode(u"file3", base=self.bob_magicfolder.uploader._local_path_u)
            print "---- bob's file is %r" % (self.file_path,)
            fileutil.write(self.file_path, "roger roger")
            return self.notify(to_filepath(self.file_path), self.inotify.IN_CLOSE_WRITE, magic=self.bob_magicfolder)
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

        return d


class SingleMagicFolderTestMixin(MagicFolderCLITestMixin, ShouldFailMixin, ReallyEqualMixin, NonASCIIPathMixin, CheckerMixin):
    """
    These tests will be run both with a mock notifier, and (on platforms that support it)
    with the real INotify.
    """

    def setUp(self):
        x = super(SingleMagicFolderTestMixin, self).setUp()
        temp = self.mktemp()
        self.basedir = abspath_expanduser_unicode(temp.decode(get_filesystem_encoding()))
        self.magicfolder = None
        self.set_up_grid()
        self.local_dir = os.path.join(self.basedir, u"local_dir")
        self.mkdir_nonascii(self.local_dir)

        d = self.create_invite_join_magic_folder(u"Alice\u0101", self.local_dir)
        d.addCallback(self._restart_client)
        # note: _restart_client ultimately sets self.magicfolder to not-None
        return d

    def tearDown(self):
        d = super(SingleMagicFolderTestMixin, self).tearDown()
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

    @defer.inlineCallbacks
    def test_scan_once_on_startup(self):
        # What is this test? Maybe it is just a stub and needs finishing.
        self.magicfolder.uploader._clock.advance(99)

        yield self._check_uploader_count('files_uploaded', 0, magic=self.magicfolder)
        yield self._check_uploader_count('objects_queued', 0, magic=self.magicfolder)
        yield self._check_downloader_count('objects_conflicted', 0, magic=self.magicfolder)
        yield self._check_uploader_count('objects_succeeded', 0, magic=self.magicfolder)
        yield self._check_downloader_count('objects_failed', 0, magic=self.magicfolder)
        yield self._check_downloader_count('objects_downloaded', 0, magic=self.magicfolder)

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

    def _test_magicfolder_start_service(self):
        # what is this even testing?
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 0))

        d.addCallback(lambda ign: self.create_invite_join_magic_folder(u"Alice", self.local_dir))
        d.addCallback(self._restart_client)

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 1))
        d.addBoth(self.cleanup)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 0))
        return d

    def test_move_tree(self):
        """
        create an empty directory tree and 'mv' it into the magic folder,
        noting the new directory and uploading it.

        also creates a directory tree with one file in it and 'mv's it
        into the magic folder, so we upload the file and record the
        directory. (XXX split to separate test)
        """
        empty_tree_name = self.unicode_or_fallback(u"empty_tr\u00EAe", u"empty_tree")
        empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.basedir)
        new_empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.local_dir)

        small_tree_name = self.unicode_or_fallback(u"small_tr\u00EAe", u"empty_tree")
        small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.basedir)
        new_small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.local_dir)

        d = defer.succeed(None)

        @defer.inlineCallbacks
        def _check_move_empty_tree(res):
            print "_check_move_empty_tree"
            self.mkdir_nonascii(empty_tree_dir)
            os.rename(empty_tree_dir, new_empty_tree_dir)
            yield self.notify(to_filepath(new_empty_tree_dir), self.inotify.IN_MOVED_TO)
            yield iterate(self.magicfolder)

        d.addCallback(_check_move_empty_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 1))

        @defer.inlineCallbacks
        def _check_move_small_tree(res):
            print "_check_move_small_tree"
            self.mkdir_nonascii(small_tree_dir)
            what_path = abspath_expanduser_unicode(u"what", base=small_tree_dir)
            fileutil.write(what_path, "say when")
            os.rename(small_tree_dir, new_small_tree_dir)
            yield self.notify(to_filepath(new_small_tree_dir), self.inotify.IN_MOVED_TO)
            yield iterate(self.magicfolder)
            # when we add the dir, we queue a scan of it; so we want
            # the upload to "go" as well requiring 1 more iteration
            yield iterate(self.magicfolder)

        d.addCallback(_check_move_small_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        @defer.inlineCallbacks
        def _check_moved_tree_is_watched(res):
            print "_check_moved_tree_is_watched"
            another_path = abspath_expanduser_unicode(u"another", base=new_small_tree_dir)
            fileutil.write(another_path, "file")
            yield self.notify(to_filepath(another_path), self.inotify.IN_CLOSE_WRITE)
            yield iterate(self.magicfolder)

        d.addCallback(_check_moved_tree_is_watched)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 4))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 2))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        return d

    def test_persistence(self):
        """
        Perform an upload of a given file and then stop the client.
        Start a new client and magic-folder service... and verify that the file is NOT uploaded
        a second time. This test is meant to test the database persistence along with
        the startup and shutdown code paths of the magic-folder service.
        """
        self.collective_dircap = "" # XXX hmmm?

        d = defer.succeed(None)

        @defer.inlineCallbacks
        def create_test_file(filename):
            test_file = abspath_expanduser_unicode(filename, base=self.local_dir)
            fileutil.write(test_file, "meow %s" % filename)
            yield self.notify(to_filepath(test_file), self.inotify.IN_CLOSE_WRITE)
            yield iterate(self.magicfolder)

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
        d = defer.succeed(None)
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
        workdir = fileutil.abspath_expanduser_unicode(u"cli/MagicFolder/write-downloaded-file")
        local_file = fileutil.abspath_expanduser_unicode(u"foobar", base=workdir)

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
        writefile._write_downloaded_file(workdir, local_file, "bar", False, None)
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
        writefile._write_downloaded_file(workdir, local_file, "bar", True, None)
        self.failUnless(os.path.exists(conflicted_path))

        # .tmp file shouldn't exist
        self.failIf(os.path.exists(local_file + u".tmp"))

    def test_periodic_full_scan(self):
        """
        Create a file in a subdir without doing a notify on it and
        fast-forward time to prove we do a full scan periodically.
        """
        sub_dir = abspath_expanduser_unicode(u"subdir", base=self.local_dir)
        self.mkdir_nonascii(sub_dir)

        d = defer.succeed(None)

        def _create_file_without_event(res):
            print "CREATE FILE WITHOUT EMITTING EVENT"
            processed_d = self.magicfolder.uploader.set_hook('processed')
            what_path = abspath_expanduser_unicode(u"what", base=sub_dir)
            fileutil.write(what_path, "say when")
            print "ADVANCE CLOCK"
            self.magicfolder.uploader._clock.advance(self.magicfolder.uploader._periodic_full_scan_duration + 1)
            # this will have now done the full scan, so we have to do
            # an iteration to process anything from it
            iterate_uploader(self.magicfolder)
            return processed_d
        d.addCallback(_create_file_without_event)
        def _advance_clock(res):
            print "_advance_clock"
            processed_d = self.magicfolder.uploader.set_hook('processed')
            self.magicfolder.uploader._clock.advance(4)
            return processed_d
        d.addCallback(_advance_clock)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 1))
        return d

    def test_statistics(self):
        d = defer.succeed(None)
        # Write something short enough for a LIT file.
        d.addCallback(lambda ign: self._check_file(u"short", "test"))

        # test magic-folder statistics
        d.addCallback(lambda res: self.GET("statistics"))
        def _got_stats(res):
            self.failUnlessIn("Operational Statistics", res)
            self.failUnlessIn("Magic Folder", res)
            self.failUnlessIn("<li>Local Directories Monitored: 1 directories</li>", res)
            self.failUnlessIn("<li>Files Uploaded: 1 files</li>", res)
            self.failUnlessIn("<li>Files Queued for Upload: 0 files</li>", res)
            self.failUnlessIn("<li>Failed Uploads: 0 files</li>", res)
            self.failUnlessIn("<li>Files Downloaded: 0 files</li>", res)
            self.failUnlessIn("<li>Files Queued for Download: 0 files</li>", res)
            self.failUnlessIn("<li>Failed Downloads: 0 files</li>", res)
        d.addCallback(_got_stats)
        d.addCallback(lambda res: self.GET("statistics?t=json"))
        def _got_stats_json(res):
            data = simplejson.loads(res)
            self.failUnlessEqual(data["counters"]["magic_folder.uploader.dirs_monitored"], 1)
            self.failUnlessEqual(data["counters"]["magic_folder.uploader.objects_succeeded"], 1)
            self.failUnlessEqual(data["counters"]["magic_folder.uploader.files_uploaded"], 1)
            self.failUnlessEqual(data["counters"]["magic_folder.uploader.objects_queued"], 0)
        d.addCallback(_got_stats_json)
        return d


class RealTest(SingleMagicFolderTestMixin, unittest.TestCase):
    """This is skipped unless both Twisted and the platform support inotify."""

    def setUp(self):
        d = super(RealTest, self).setUp()
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


class RealTestAliceBob(MagicFolderAliceBobTestMixin, unittest.TestCase):
    """This is skipped unless both Twisted and the platform support inotify."""

    def setUp(self):
        d = super(RealTestAliceBob, self).setUp()
        self.inotify = magic_folder.get_inotify_module()
        return d

    def notify(self, path, mask, magic=None, flush=True):
        # Writing to the filesystem causes the notification.
        # Actually, there's no way to know when the actual
        # notification will occur, and anyway we're not waiting for
        # them in any case...so we'll just fudge it and hope 100ms is enough.
        return task.deferLater(reactor, 0.1, lambda: None)

try:
    magic_folder.get_inotify_module()
except NotImplementedError:
    RealTest.skip = "Magic Folder support can only be tested for-real on an OS that supports inotify or equivalent."
