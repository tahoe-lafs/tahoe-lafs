import os, sys
import mock
import twisted
from twisted.trial import unittest
from twisted.application import service
from twisted.internet import defer

import allmydata
import allmydata.frontends.magic_folder
import allmydata.util.log

from allmydata.node import OldConfigError, OldConfigOptionError, UnescapedHashError, _Config, read_config, create_node_dir
from allmydata.frontends.auth import NeedRootcapLookupScheme
from allmydata import client
from allmydata.storage_client import StorageFarmBroker
from allmydata.util import base32, fileutil, encodingutil
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.interfaces import IFilesystemNode, IFileNode, \
     IImmutableFileNode, IMutableFileNode, IDirectoryNode
from foolscap.api import flushEventualQueue
import allmydata.test.common_util as testutil


BASECONFIG = ("[client]\n"
              "introducer.furl = \n"
              )

BASECONFIG_I = ("[client]\n"
              "introducer.furl = %s\n"
              )

class Basic(testutil.ReallyEqualMixin, testutil.NonASCIIPathMixin, unittest.TestCase):
    def test_loadable(self):
        basedir = "test_client.Basic.test_loadable"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG)
        return client.create_client(basedir)

    @defer.inlineCallbacks
    def test_comment(self):
        should_fail = [r"test#test", r"#testtest", r"test\\#test"]
        should_not_fail = [r"test\#test", r"test\\\#test", r"testtest"]

        basedir = "test_client.Basic.test_comment"
        os.mkdir(basedir)

        def write_config(s):
            config = ("[client]\n"
                      "introducer.furl = %s\n" % s)
            fileutil.write(os.path.join(basedir, "tahoe.cfg"), config)

        for s in should_fail:
            self.failUnless(_Config._contains_unescaped_hash(s))
            write_config(s)
            with self.assertRaises(UnescapedHashError) as ctx:
                yield client.create_client(basedir)
            self.assertIn("[client]introducer.furl", str(ctx.exception))

        for s in should_not_fail:
            self.failIf(_Config._contains_unescaped_hash(s))
            write_config(s)
            yield client.create_client(basedir)

    def test_unreadable_config(self):
        if sys.platform == "win32":
            # if somebody knows a clever way to do this (cause
            # EnvironmentError when reading a file that really exists), on
            # windows, please fix this
            raise unittest.SkipTest("can't make unreadable files on windows")
        basedir = "test_client.Basic.test_unreadable_config"
        os.mkdir(basedir)
        fn = os.path.join(basedir, "tahoe.cfg")
        fileutil.write(fn, BASECONFIG)
        old_mode = os.stat(fn).st_mode
        os.chmod(fn, 0)
        try:
            e = self.assertRaises(
                EnvironmentError,
                read_config,
                basedir,
                "client.port",
                _valid_config_sections=client._valid_config_sections,
            )
            self.assertIn("Permission denied", str(e))
        finally:
            # don't leave undeleteable junk lying around
            os.chmod(fn, old_mode)

    def test_error_on_old_config_files(self):
        basedir = "test_client.Basic.test_error_on_old_config_files"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       BASECONFIG +
                       "[storage]\n" +
                       "enabled = false\n" +
                       "reserved_space = bogus\n")
        fileutil.write(os.path.join(basedir, "introducer.furl"), "")
        fileutil.write(os.path.join(basedir, "no_storage"), "")
        fileutil.write(os.path.join(basedir, "readonly_storage"), "")
        fileutil.write(os.path.join(basedir, "debug_discard_storage"), "")

        logged_messages = []
        self.patch(twisted.python.log, 'msg', logged_messages.append)

        e = self.failUnlessRaises(
            OldConfigError,
            read_config,
            basedir,
            "client.port",
            _valid_config_sections=client._valid_config_sections,
        )
        abs_basedir = fileutil.abspath_expanduser_unicode(unicode(basedir)).encode(sys.getfilesystemencoding())
        self.failUnlessIn(os.path.join(abs_basedir, "introducer.furl"), e.args[0])
        self.failUnlessIn(os.path.join(abs_basedir, "no_storage"), e.args[0])
        self.failUnlessIn(os.path.join(abs_basedir, "readonly_storage"), e.args[0])
        self.failUnlessIn(os.path.join(abs_basedir, "debug_discard_storage"), e.args[0])

        for oldfile in ['introducer.furl', 'no_storage', 'readonly_storage',
                        'debug_discard_storage']:
            logged = [ m for m in logged_messages if
                       ("Found pre-Tahoe-LAFS-v1.3 configuration file" in str(m) and oldfile in str(m)) ]
            self.failUnless(logged, (oldfile, logged_messages))

        for oldfile in [
            'nickname', 'webport', 'keepalive_timeout', 'log_gatherer.furl',
            'disconnect_timeout', 'advertised_ip_addresses', 'helper.furl',
            'key_generator.furl', 'stats_gatherer.furl', 'sizelimit',
            'run_helper']:
            logged = [ m for m in logged_messages if
                       ("Found pre-Tahoe-LAFS-v1.3 configuration file" in str(m) and oldfile in str(m)) ]
            self.failIf(logged, (oldfile, logged_messages))

    @defer.inlineCallbacks
    def test_secrets(self):
        basedir = "test_client.Basic.test_secrets"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG)
        c = yield client.create_client(basedir)
        secret_fname = os.path.join(basedir, "private", "secret")
        self.failUnless(os.path.exists(secret_fname), secret_fname)
        renew_secret = c.get_renewal_secret()
        self.failUnless(base32.b2a(renew_secret))
        cancel_secret = c.get_cancel_secret()
        self.failUnless(base32.b2a(cancel_secret))

    @defer.inlineCallbacks
    def test_nodekey_yes_storage(self):
        basedir = "test_client.Basic.test_nodekey_yes_storage"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       BASECONFIG)
        c = yield client.create_client(basedir)
        self.failUnless(c.get_long_nodeid().startswith("v0-"))

    @defer.inlineCallbacks
    def test_nodekey_no_storage(self):
        basedir = "test_client.Basic.test_nodekey_no_storage"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       BASECONFIG + "[storage]\n" + "enabled = false\n")
        c = yield client.create_client(basedir)
        self.failUnless(c.get_long_nodeid().startswith("v0-"))

    @defer.inlineCallbacks
    def test_reserved_1(self):
        basedir = "client.Basic.test_reserved_1"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = 1000\n")
        c = yield client.create_client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space, 1000)

    @defer.inlineCallbacks
    def test_reserved_2(self):
        basedir = "client.Basic.test_reserved_2"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),  \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = 10K\n")
        c = yield client.create_client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space, 10*1000)

    @defer.inlineCallbacks
    def test_reserved_3(self):
        basedir = "client.Basic.test_reserved_3"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = 5mB\n")
        c = yield client.create_client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space,
                             5*1000*1000)

    @defer.inlineCallbacks
    def test_reserved_4(self):
        basedir = "client.Basic.test_reserved_4"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = 78Gb\n")
        c = yield client.create_client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space,
                             78*1000*1000*1000)

    @defer.inlineCallbacks
    def test_reserved_bad(self):
        basedir = "client.Basic.test_reserved_bad"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = bogus\n")
        with self.assertRaises(ValueError) as ctx:
            yield client.create_client(basedir)

    @defer.inlineCallbacks
    def test_web_apiauthtoken(self):
        """
        Client loads the proper API auth token from disk
        """
        basedir = u"client.Basic.test_web_apiauthtoken"
        create_node_dir(basedir, "testing")

        c = yield client.create_client(basedir)
        # this must come after we create the client, as it will create
        # a new, random authtoken itself
        with open(os.path.join(basedir, "private", "api_auth_token"), "w") as f:
            f.write("deadbeef")

        token = c.get_auth_token()
        self.assertEqual("deadbeef", token)

    @defer.inlineCallbacks
    def test_web_staticdir(self):
        basedir = u"client.Basic.test_web_staticdir"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       BASECONFIG +
                       "[node]\n" +
                       "web.port = tcp:0:interface=127.0.0.1\n" +
                       "web.static = relative\n")
        c = yield client.create_client(basedir)
        w = c.getServiceNamed("webish")
        abs_basedir = fileutil.abspath_expanduser_unicode(basedir)
        expected = fileutil.abspath_expanduser_unicode(u"relative", abs_basedir)
        self.failUnlessReallyEqual(w.staticdir, expected)

    # TODO: also test config options for SFTP.

    @defer.inlineCallbacks
    def test_ftp_create(self):
        """
        configuration for sftpd results in it being started
        """
        basedir = u"client.Basic.test_ftp_create"
        create_node_dir(basedir, "testing")
        with open(os.path.join(basedir, "tahoe.cfg"), "w") as f:
            f.write(
                '[sftpd]\n'
                'enabled = true\n'
                'accounts.file = foo\n'
                'host_pubkey_file = pubkey\n'
                'host_privkey_file = privkey\n'
            )
        with mock.patch('allmydata.frontends.sftpd.SFTPServer') as p:
            yield client.create_client(basedir)
        self.assertTrue(p.called)

    @defer.inlineCallbacks
    def test_ftp_auth_keyfile(self):
        basedir = u"client.Basic.test_ftp_auth_keyfile"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       (BASECONFIG +
                        "[ftpd]\n"
                        "enabled = true\n"
                        "port = tcp:0:interface=127.0.0.1\n"
                        "accounts.file = private/accounts\n"))
        os.mkdir(os.path.join(basedir, "private"))
        fileutil.write(os.path.join(basedir, "private", "accounts"), "\n")
        c = yield client.create_client(basedir) # just make sure it can be instantiated
        del c

    @defer.inlineCallbacks
    def test_ftp_auth_url(self):
        basedir = u"client.Basic.test_ftp_auth_url"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       (BASECONFIG +
                        "[ftpd]\n"
                        "enabled = true\n"
                        "port = tcp:0:interface=127.0.0.1\n"
                        "accounts.url = http://0.0.0.0/\n"))
        c = yield client.create_client(basedir) # just make sure it can be instantiated
        del c

    @defer.inlineCallbacks
    def test_ftp_auth_no_accountfile_or_url(self):
        basedir = u"client.Basic.test_ftp_auth_no_accountfile_or_url"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       (BASECONFIG +
                        "[ftpd]\n"
                        "enabled = true\n"
                        "port = tcp:0:interface=127.0.0.1\n"))
        with self.assertRaises(NeedRootcapLookupScheme):
            yield client.create_client(basedir)

    def _storage_dir_test(self, basedir, storage_path, expected_path):
        os.mkdir(basedir)
        cfg_path = os.path.join(basedir, "tahoe.cfg")
        fileutil.write(
            cfg_path,
            BASECONFIG +
            "[storage]\n"
            "enabled = true\n",
        )
        if storage_path is not None:
            fileutil.write(
                cfg_path,
                "storage_dir = %s\n" % (storage_path,),
                mode="ab",
        )
        c = client.create_client(basedir)
        self.assertEqual(
            c.getServiceNamed("storage").storedir,
            expected_path,
        )

    def test_default_storage_dir(self):
        """
        If no value is given for ``storage_dir`` in the ``storage`` section of
        ``tahoe.cfg`` then the ``storage`` directory beneath the node
        directory is used.
        """
        basedir = u"client.Basic.test_default_storage_dir"
        config_path = None
        expected_path = os.path.join(
            abspath_expanduser_unicode(basedir),
            u"storage",
        )
        self._storage_dir_test(
            basedir,
            config_path,
            expected_path,
        )

    def test_relative_storage_dir(self):
        """
        A storage node can be directed to use a particular directory for share
        file storage by setting ``storage_dir`` in the ``storage`` section of
        ``tahoe.cfg``.  If the path is relative, it is interpreted relative to
        the node's basedir.
        """
        basedir = u"client.Basic.test_relative_storage_dir"
        config_path = b"myowndir"
        expected_path = os.path.join(
            abspath_expanduser_unicode(basedir),
            u"myowndir",
        )
        self._storage_dir_test(
            basedir,
            config_path,
            expected_path,
        )

    def test_absolute_storage_dir(self):
        """
        If the ``storage_dir`` item in the ``storage`` section of the
        configuration gives an absolute path then exactly that path is used.
        """
        basedir = u"client.Basic.test_absolute_storage_dir"
        # create_client is going to try to make the storage directory so we
        # don't want a literal absolute path like /myowndir which we won't
        # have write permission to.  So construct an absolute path that we
        # should be able to write to.
        base = u"\N{SNOWMAN}"
        if encodingutil.filesystem_encoding != "utf-8":
            base = u"melted_snowman"
        expected_path = abspath_expanduser_unicode(
            u"client.Basic.test_absolute_storage_dir_myowndir/" + base
        )
        config_path = expected_path.encode("utf-8")
        self._storage_dir_test(
            basedir,
            config_path,
            expected_path,
        )

    def _permute(self, sb, key):
        return [ s.get_longname() for s in sb.get_servers_for_psi(key) ]

    def test_permute(self):
        sb = StorageFarmBroker(True, None)
        for k in ["%d" % i for i in range(5)]:
            ann = {"anonymous-storage-FURL": "pb://abcde@nowhere/fake",
                   "permutation-seed-base32": base32.b2a(k) }
            sb.test_add_rref(k, "rref", ann)

        self.failUnlessReallyEqual(self._permute(sb, "one"), ['3','1','0','4','2'])
        self.failUnlessReallyEqual(self._permute(sb, "two"), ['0','4','2','1','3'])
        sb.servers.clear()
        self.failUnlessReallyEqual(self._permute(sb, "one"), [])

    def test_permute_with_preferred(self):
        sb = StorageFarmBroker(True, None, preferred_peers=['1','4'])
        for k in ["%d" % i for i in range(5)]:
            ann = {"anonymous-storage-FURL": "pb://abcde@nowhere/fake",
                   "permutation-seed-base32": base32.b2a(k) }
            sb.test_add_rref(k, "rref", ann)

        self.failUnlessReallyEqual(self._permute(sb, "one"), ['1','4','3','0','2'])
        self.failUnlessReallyEqual(self._permute(sb, "two"), ['4','1','0','2','3'])
        sb.servers.clear()
        self.failUnlessReallyEqual(self._permute(sb, "one"), [])

    @defer.inlineCallbacks
    def test_versions(self):
        basedir = "test_client.Basic.test_versions"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n")
        c = yield client.create_client(basedir)
        ss = c.getServiceNamed("storage")
        verdict = ss.remote_get_version()
        self.failUnlessReallyEqual(verdict["application-version"],
                                   str(allmydata.__full_version__))
        self.failIfEqual(str(allmydata.__version__), "unknown")
        self.failUnless("." in str(allmydata.__full_version__),
                        "non-numeric version in '%s'" % allmydata.__version__)
        all_versions = allmydata.get_package_versions_string()
        self.failUnless(allmydata.__appname__ in all_versions)
        # also test stats
        stats = c.get_stats()
        self.failUnless("node.uptime" in stats)
        self.failUnless(isinstance(stats["node.uptime"], float))

    @defer.inlineCallbacks
    def test_helper_furl(self):
        basedir = "test_client.Basic.test_helper_furl"
        os.mkdir(basedir)

        @defer.inlineCallbacks
        def _check(config, expected_furl):
            fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                           BASECONFIG + config)
            c = yield client.create_client(basedir)
            uploader = c.getServiceNamed("uploader")
            furl, connected = uploader.get_helper_info()
            self.failUnlessEqual(furl, expected_furl)

        yield _check("", None)
        yield _check("helper.furl =\n", None)
        yield _check("helper.furl = \n", None)
        yield _check("helper.furl = None", None)
        yield _check("helper.furl = pb://blah\n", "pb://blah")

    @defer.inlineCallbacks
    def test_create_magic_folder_service(self):
        boom = False
        class Boom(Exception):
            pass

        class MockMagicFolder(allmydata.frontends.magic_folder.MagicFolder):
            name = 'magic-folder'

            def __init__(self, client, upload_dircap, collective_dircap, local_path_u, dbfile, umask, name,
                         inotify=None, uploader_delay=1.0, clock=None, downloader_delay=3):
                if boom:
                    raise Boom()

                service.MultiService.__init__(self)
                self.client = client
                self._umask = umask
                self.upload_dircap = upload_dircap
                self.collective_dircap = collective_dircap
                self.local_dir = local_path_u
                self.dbfile = dbfile
                self.inotify = inotify

            def startService(self):
                self.running = True

            def stopService(self):
                self.running = False

            def ready(self):
                pass

        self.patch(allmydata.frontends.magic_folder, 'MagicFolder', MockMagicFolder)

        upload_dircap = "URI:DIR2:blah"
        local_dir_u = self.unicode_or_fallback(u"loc\u0101l_dir", u"local_dir")
        local_dir_utf8 = local_dir_u.encode('utf-8')
        config = (BASECONFIG +
                  "[storage]\n" +
                  "enabled = false\n" +
                  "[magic_folder]\n" +
                  "enabled = true\n")

        basedir1 = "test_client.Basic.test_create_magic_folder_service1"
        os.mkdir(basedir1)
        os.mkdir(local_dir_u)

        # which config-entry should be missing?
        fileutil.write(os.path.join(basedir1, "tahoe.cfg"),
                       config + "local.directory = " + local_dir_utf8 + "\n")
        with self.assertRaises(IOError):
            yield client.create_client(basedir1)

        # local.directory entry missing .. but that won't be an error
        # now, it'll just assume there are not magic folders
        # .. hrm...should we make that an error (if enabled=true but
        # there's not yaml AND no local.directory?)
        fileutil.write(os.path.join(basedir1, "tahoe.cfg"), config)
        fileutil.write(os.path.join(basedir1, "private", "magic_folder_dircap"), "URI:DIR2:blah")
        fileutil.write(os.path.join(basedir1, "private", "collective_dircap"), "URI:DIR2:meow")

        fileutil.write(os.path.join(basedir1, "tahoe.cfg"),
                       config.replace("[magic_folder]\n", "[drop_upload]\n"))

        with self.assertRaises(OldConfigOptionError):
            yield client.create_client(basedir1)

        fileutil.write(os.path.join(basedir1, "tahoe.cfg"),
                       config + "local.directory = " + local_dir_utf8 + "\n")
        c1 = yield client.create_client(basedir1)
        magicfolder = c1.getServiceNamed('magic-folder')
        self.failUnless(isinstance(magicfolder, MockMagicFolder), magicfolder)
        self.failUnlessReallyEqual(magicfolder.client, c1)
        self.failUnlessReallyEqual(magicfolder.upload_dircap, upload_dircap)
        self.failUnlessReallyEqual(os.path.basename(magicfolder.local_dir), local_dir_u)
        self.failUnless(magicfolder.inotify is None, magicfolder.inotify)
        self.failUnless(magicfolder.running)

        # See above.
        boom = True

        basedir2 = "test_client.Basic.test_create_magic_folder_service2"
        os.mkdir(basedir2)
        os.mkdir(os.path.join(basedir2, "private"))
        fileutil.write(os.path.join(basedir2, "tahoe.cfg"),
                       BASECONFIG +
                       "[magic_folder]\n" +
                       "enabled = true\n" +
                       "local.directory = " + local_dir_utf8 + "\n")
        fileutil.write(os.path.join(basedir2, "private", "magic_folder_dircap"), "URI:DIR2:blah")
        fileutil.write(os.path.join(basedir2, "private", "collective_dircap"), "URI:DIR2:meow")
        with self.assertRaises(Boom):
            yield client.create_client(basedir2)


def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

class Run(unittest.TestCase, testutil.StallMixin):

    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()
    def tearDown(self):
        d = self.sparent.stopService()
        d.addBoth(flush_but_dont_ignore)
        return d

    @defer.inlineCallbacks
    def test_loadable(self):
        basedir = "test_client.Run.test_loadable"
        os.mkdir(basedir)
        dummy = "pb://wl74cyahejagspqgy4x5ukrvfnevlknt@127.0.0.1:58889/bogus"
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), BASECONFIG_I % dummy)
        fileutil.write(os.path.join(basedir, client._Client.EXIT_TRIGGER_FILE), "")
        yield client.create_client(basedir)

    @defer.inlineCallbacks
    def test_reloadable(self):
        basedir = "test_client.Run.test_reloadable"
        os.mkdir(basedir)
        dummy = "pb://wl74cyahejagspqgy4x5ukrvfnevlknt@127.0.0.1:58889/bogus"
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), BASECONFIG_I % dummy)
        c1 = yield client.create_client(basedir)
        c1.setServiceParent(self.sparent)

        # delay to let the service start up completely. I'm not entirely sure
        # this is necessary.
        yield self.stall(delay=2.0)
        yield c1.disownServiceParent()
        # the cygwin buildslave seems to need more time to let the old
        # service completely shut down. When delay=0.1, I saw this test fail,
        # probably due to the logport trying to reclaim the old socket
        # number. This suggests that either we're dropping a Deferred
        # somewhere in the shutdown sequence, or that cygwin is just cranky.
        yield self.stall(delay=2.0)

        # TODO: pause for slightly over one second, to let
        # Client._check_exit_trigger poll the file once. That will exercise
        # another few lines. Then add another test in which we don't
        # update the file at all, and watch to see the node shutdown.
        # (To do this, use a modified node which overrides Node.shutdown(),
        # also change _check_exit_trigger to use it instead of a raw
        # reactor.stop, also instrument the shutdown event in an
        # attribute that we can check.)
        c2 = yield client.create_client(basedir)
        c2.setServiceParent(self.sparent)
        yield c2.disownServiceParent()

class NodeMaker(testutil.ReallyEqualMixin, unittest.TestCase):

    @defer.inlineCallbacks
    def test_maker(self):
        basedir = "client/NodeMaker/maker"
        fileutil.make_dirs(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), BASECONFIG)
        c = yield client.create_client(basedir)

        n = c.create_node_from_uri("URI:CHK:6nmrpsubgbe57udnexlkiwzmlu:bjt7j6hshrlmadjyr7otq3dc24end5meo5xcr5xe5r663po6itmq:3:10:7277")
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failUnless(IFileNode.providedBy(n))
        self.failUnless(IImmutableFileNode.providedBy(n))
        self.failIf(IMutableFileNode.providedBy(n))
        self.failIf(IDirectoryNode.providedBy(n))
        self.failUnless(n.is_readonly())
        self.failIf(n.is_mutable())

        # Testing #1679. There was a bug that would occur when downloader was
        # downloading the same readcap more than once concurrently, so the
        # filenode object was cached, and there was a failure from one of the
        # servers in one of the download attempts. No subsequent download
        # attempt would attempt to use that server again, which would lead to
        # the file being undownloadable until the gateway was restarted. The
        # current fix for this (hopefully to be superceded by a better fix
        # eventually) is to prevent re-use of filenodes, so the NodeMaker is
        # hereby required *not* to cache and re-use filenodes for CHKs.
        other_n = c.create_node_from_uri("URI:CHK:6nmrpsubgbe57udnexlkiwzmlu:bjt7j6hshrlmadjyr7otq3dc24end5meo5xcr5xe5r663po6itmq:3:10:7277")
        self.failIf(n is other_n, (n, other_n))

        n = c.create_node_from_uri("URI:LIT:n5xgk")
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failUnless(IFileNode.providedBy(n))
        self.failUnless(IImmutableFileNode.providedBy(n))
        self.failIf(IMutableFileNode.providedBy(n))
        self.failIf(IDirectoryNode.providedBy(n))
        self.failUnless(n.is_readonly())
        self.failIf(n.is_mutable())

        n = c.create_node_from_uri("URI:SSK:n6x24zd3seu725yluj75q5boaa:mm6yoqjhl6ueh7iereldqxue4nene4wl7rqfjfybqrehdqmqskvq")
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failUnless(IFileNode.providedBy(n))
        self.failIf(IImmutableFileNode.providedBy(n))
        self.failUnless(IMutableFileNode.providedBy(n))
        self.failIf(IDirectoryNode.providedBy(n))
        self.failIf(n.is_readonly())
        self.failUnless(n.is_mutable())

        n = c.create_node_from_uri("URI:SSK-RO:b7sr5qsifnicca7cbk3rhrhbvq:mm6yoqjhl6ueh7iereldqxue4nene4wl7rqfjfybqrehdqmqskvq")
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failUnless(IFileNode.providedBy(n))
        self.failIf(IImmutableFileNode.providedBy(n))
        self.failUnless(IMutableFileNode.providedBy(n))
        self.failIf(IDirectoryNode.providedBy(n))
        self.failUnless(n.is_readonly())
        self.failUnless(n.is_mutable())

        n = c.create_node_from_uri("URI:DIR2:n6x24zd3seu725yluj75q5boaa:mm6yoqjhl6ueh7iereldqxue4nene4wl7rqfjfybqrehdqmqskvq")
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failIf(IFileNode.providedBy(n))
        self.failIf(IImmutableFileNode.providedBy(n))
        self.failIf(IMutableFileNode.providedBy(n))
        self.failUnless(IDirectoryNode.providedBy(n))
        self.failIf(n.is_readonly())
        self.failUnless(n.is_mutable())

        n = c.create_node_from_uri("URI:DIR2-RO:b7sr5qsifnicca7cbk3rhrhbvq:mm6yoqjhl6ueh7iereldqxue4nene4wl7rqfjfybqrehdqmqskvq")
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failIf(IFileNode.providedBy(n))
        self.failIf(IImmutableFileNode.providedBy(n))
        self.failIf(IMutableFileNode.providedBy(n))
        self.failUnless(IDirectoryNode.providedBy(n))
        self.failUnless(n.is_readonly())
        self.failUnless(n.is_mutable())

        unknown_rw = "lafs://from_the_future"
        unknown_ro = "lafs://readonly_from_the_future"
        n = c.create_node_from_uri(unknown_rw, unknown_ro)
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failIf(IFileNode.providedBy(n))
        self.failIf(IImmutableFileNode.providedBy(n))
        self.failIf(IMutableFileNode.providedBy(n))
        self.failIf(IDirectoryNode.providedBy(n))
        self.failUnless(n.is_unknown())
        self.failUnlessReallyEqual(n.get_uri(), unknown_rw)
        self.failUnlessReallyEqual(n.get_write_uri(), unknown_rw)
        self.failUnlessReallyEqual(n.get_readonly_uri(), "ro." + unknown_ro)

        # Note: it isn't that we *intend* to deploy non-ASCII caps in
        # the future, it is that we want to make sure older Tahoe-LAFS
        # versions wouldn't choke on them if we were to do so. See
        # #1051 and wiki:NewCapDesign for details.
        unknown_rw = u"lafs://from_the_future_rw_\u263A".encode('utf-8')
        unknown_ro = u"lafs://readonly_from_the_future_ro_\u263A".encode('utf-8')
        n = c.create_node_from_uri(unknown_rw, unknown_ro)
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failIf(IFileNode.providedBy(n))
        self.failIf(IImmutableFileNode.providedBy(n))
        self.failIf(IMutableFileNode.providedBy(n))
        self.failIf(IDirectoryNode.providedBy(n))
        self.failUnless(n.is_unknown())
        self.failUnlessReallyEqual(n.get_uri(), unknown_rw)
        self.failUnlessReallyEqual(n.get_write_uri(), unknown_rw)
        self.failUnlessReallyEqual(n.get_readonly_uri(), "ro." + unknown_ro)
