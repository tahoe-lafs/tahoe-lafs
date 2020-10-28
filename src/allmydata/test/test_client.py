import os, sys
import mock
from functools import (
    partial,
)

import twisted
from yaml import (
    safe_dump,
)
from fixtures import (
    Fixture,
    TempDir,
)
from eliot.testing import (
    capture_logging,
    assertHasAction,
)
from twisted.trial import unittest
from twisted.application import service
from twisted.internet import defer
from twisted.python.filepath import (
    FilePath,
)
from testtools.matchers import (
    Equals,
    AfterPreprocessing,
    MatchesListwise,
    MatchesDict,
    Always,
    Is,
    raises,
)
from testtools.twistedsupport import (
    succeeded,
    failed,
)

import allmydata
import allmydata.util.log

from allmydata.node import OldConfigError, UnescapedHashError, _Config, create_node_dir
from allmydata.frontends.auth import NeedRootcapLookupScheme
from allmydata.version_checks import (
    get_package_versions_string,
)
from allmydata import client
from allmydata.storage_client import (
    StorageClientConfig,
    StorageFarmBroker,
)
from allmydata.util import (
    base32,
    fileutil,
    encodingutil,
    configutil,
)
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.interfaces import IFilesystemNode, IFileNode, \
     IImmutableFileNode, IMutableFileNode, IDirectoryNode
from foolscap.api import flushEventualQueue
import allmydata.test.common_util as testutil
from .common import (
    EMPTY_CLIENT_CONFIG,
    SyncTestCase,
    UseTestPlugins,
    MemoryIntroducerClient,
    get_published_announcements,
)
from .matchers import (
    MatchesSameElements,
    matches_storage_announcement,
    matches_furl,
)

SOME_FURL = b"pb://abcde@nowhere/fake"

BASECONFIG = ("[client]\n"
              "introducer.furl = \n"
              )

BASECONFIG_I = ("[client]\n"
              "introducer.furl = %s\n"
              )

class Basic(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_loadable(self):
        basedir = "test_client.Basic.test_loadable"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG)
        return client.create_client(basedir)

    @defer.inlineCallbacks
    def test_unreadable_introducers(self):
        """
        The Deferred from create_client fails when
        private/introducers.yaml is unreadable (but exists)
        """
        basedir = "test_client.Basic.test_unreadable_introduers"
        os.mkdir(basedir, 0o700)
        os.mkdir(os.path.join(basedir, 'private'), 0o700)
        intro_fname = os.path.join(basedir, 'private', 'introducers.yaml')
        with open(intro_fname, 'w') as f:
            f.write("---\n")
        os.chmod(intro_fname, 0o000)
        self.addCleanup(lambda: os.chmod(intro_fname, 0o700))

        with self.assertRaises(EnvironmentError):
            yield client.create_client(basedir)

    @defer.inlineCallbacks
    def test_comment(self):
        """
        An unescaped comment character (#) in a furl results in an
        UnescapedHashError Failure.
        """
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
                client.read_config,
                basedir,
                "client.port",
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
            client.read_config,
            basedir,
            "client.port",
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
        """
        A new client has renewal + cancel secrets
        """
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
        """
        We have a nodeid if we're providing storage
        """
        basedir = "test_client.Basic.test_nodekey_yes_storage"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       BASECONFIG)
        c = yield client.create_client(basedir)
        self.failUnless(c.get_long_nodeid().startswith("v0-"))

    @defer.inlineCallbacks
    def test_nodekey_no_storage(self):
        """
        We have a nodeid if we're not providing storage
        """
        basedir = "test_client.Basic.test_nodekey_no_storage"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       BASECONFIG + "[storage]\n" + "enabled = false\n")
        c = yield client.create_client(basedir)
        self.failUnless(c.get_long_nodeid().startswith("v0-"))

    def test_storage_anonymous_enabled_by_default(self):
        """
        Anonymous storage access is enabled if storage is enabled and *anonymous*
        is not set.
        """
        config = client.config_from_string(
            "test_storage_default_anonymous_enabled",
            "tub.port",
            BASECONFIG + (
                "[storage]\n"
                "enabled = true\n"
            )
        )
        self.assertTrue(client.anonymous_storage_enabled(config))

    def test_storage_anonymous_enabled_explicitly(self):
        """
        Anonymous storage access is enabled if storage is enabled and *anonymous*
        is set to true.
        """
        config = client.config_from_string(
            self.id(),
            "tub.port",
            BASECONFIG + (
                "[storage]\n"
                "enabled = true\n"
                "anonymous = true\n"
            )
        )
        self.assertTrue(client.anonymous_storage_enabled(config))

    def test_storage_anonymous_disabled_explicitly(self):
        """
        Anonymous storage access is disabled if storage is enabled and *anonymous*
        is set to false.
        """
        config = client.config_from_string(
            self.id(),
            "tub.port",
            BASECONFIG + (
                "[storage]\n"
                "enabled = true\n"
                "anonymous = false\n"
            )
        )
        self.assertFalse(client.anonymous_storage_enabled(config))

    def test_storage_anonymous_disabled_by_storage(self):
        """
        Anonymous storage access is disabled if storage is disabled and *anonymous*
        is set to true.
        """
        config = client.config_from_string(
            self.id(),
            "tub.port",
            BASECONFIG + (
                "[storage]\n"
                "enabled = false\n"
                "anonymous = true\n"
            )
        )
        self.assertFalse(client.anonymous_storage_enabled(config))

    @defer.inlineCallbacks
    def test_reserved_1(self):
        """
        reserved_space option is propagated
        """
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
        """
        reserved_space option understands 'K' to mean kilobytes
        """
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
        """
        reserved_space option understands 'mB' to mean megabytes
        """
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
        """
        reserved_space option understands 'Gb' to mean gigabytes
        """
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
        """
        reserved_space option produces errors on non-numbers
        """
        basedir = "client.Basic.test_reserved_bad"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = bogus\n")
        with self.assertRaises(ValueError):
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
        """
        a relative web.static dir is expanded properly
        """
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
        """
        ftpd accounts.file is parsed properly
        """
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
        """
        ftpd accounts.url is parsed properly
        """
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
        """
        ftpd requires some way to look up accounts
        """
        basedir = u"client.Basic.test_ftp_auth_no_accountfile_or_url"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       (BASECONFIG +
                        "[ftpd]\n"
                        "enabled = true\n"
                        "port = tcp:0:interface=127.0.0.1\n"))
        with self.assertRaises(NeedRootcapLookupScheme):
            yield client.create_client(basedir)

    @defer.inlineCallbacks
    def _storage_dir_test(self, basedir, storage_path, expected_path):
        """
        generic helper for following storage_dir tests
        """
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
        c = yield client.create_client(basedir)
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
        return self._storage_dir_test(
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
        return self._storage_dir_test(
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
        return self._storage_dir_test(
            basedir,
            config_path,
            expected_path,
        )

    def _permute(self, sb, key):
        return [ s.get_longname() for s in sb.get_servers_for_psi(key) ]

    def test_permute(self):
        sb = StorageFarmBroker(True, None, EMPTY_CLIENT_CONFIG)
        for k in ["%d" % i for i in range(5)]:
            ann = {"anonymous-storage-FURL": SOME_FURL,
                   "permutation-seed-base32": base32.b2a(k) }
            sb.test_add_rref(k, "rref", ann)

        self.failUnlessReallyEqual(self._permute(sb, "one"), ['3','1','0','4','2'])
        self.failUnlessReallyEqual(self._permute(sb, "two"), ['0','4','2','1','3'])
        sb.servers.clear()
        self.failUnlessReallyEqual(self._permute(sb, "one"), [])

    def test_permute_with_preferred(self):
        sb = StorageFarmBroker(
            True,
            None,
            EMPTY_CLIENT_CONFIG,
            StorageClientConfig(preferred_peers=['1','4']),
        )
        for k in ["%d" % i for i in range(5)]:
            ann = {"anonymous-storage-FURL": SOME_FURL,
                   "permutation-seed-base32": base32.b2a(k) }
            sb.test_add_rref(k, "rref", ann)

        self.failUnlessReallyEqual(self._permute(sb, "one"), ['1','4','3','0','2'])
        self.failUnlessReallyEqual(self._permute(sb, "two"), ['4','1','0','2','3'])
        sb.servers.clear()
        self.failUnlessReallyEqual(self._permute(sb, "one"), [])

    @defer.inlineCallbacks
    def test_versions(self):
        """
        A client knows the versions of software it has
        """
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
        all_versions = get_package_versions_string()
        self.failUnless(allmydata.__appname__ in all_versions)
        # also test stats
        stats = c.get_stats()
        self.failUnless("node.uptime" in stats)
        self.failUnless(isinstance(stats["node.uptime"], float))

    @defer.inlineCallbacks
    def test_helper_furl(self):
        """
        various helper.furl arguments are parsed correctly
        """
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


def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d


class AnonymousStorage(SyncTestCase):
    """
    Tests for behaviors of the client object with respect to the anonymous
    storage service.
    """
    @defer.inlineCallbacks
    def test_anonymous_storage_enabled(self):
        """
        If anonymous storage access is enabled then the client announces it.
        """
        basedir = self.id()
        os.makedirs(basedir + b"/private")
        config = client.config_from_string(
            basedir,
            "tub.port",
            BASECONFIG_I % (SOME_FURL,) + (
                "[storage]\n"
                "enabled = true\n"
                "anonymous = true\n"
            )
        )
        node = yield client.create_client_from_config(
            config,
            _introducer_factory=MemoryIntroducerClient,
        )
        self.assertThat(
            get_published_announcements(node),
            MatchesListwise([
                matches_storage_announcement(
                    basedir,
                    anonymous=True,
                ),
            ]),
        )

    @defer.inlineCallbacks
    def test_anonymous_storage_disabled(self):
        """
        If anonymous storage access is disabled then the client does not announce
        it nor does it write a fURL for it to beneath the node directory.
        """
        basedir = self.id()
        os.makedirs(basedir + b"/private")
        config = client.config_from_string(
            basedir,
            "tub.port",
            BASECONFIG_I % (SOME_FURL,) + (
                "[storage]\n"
                "enabled = true\n"
                "anonymous = false\n"
            )
        )
        node = yield client.create_client_from_config(
            config,
            _introducer_factory=MemoryIntroducerClient,
        )
        self.expectThat(
            get_published_announcements(node),
            MatchesListwise([
                matches_storage_announcement(
                    basedir,
                    anonymous=False,
                ),
            ]),
        )
        self.expectThat(
            config.get_private_config("storage.furl", default=None),
            Is(None),
        )

    @defer.inlineCallbacks
    def test_anonymous_storage_enabled_then_disabled(self):
        """
        If a node is run with anonymous storage enabled and then later anonymous
        storage is disabled in the configuration for that node, it is not
        possible to reach the anonymous storage server via the originally
        published fURL.
        """
        basedir = self.id()
        os.makedirs(basedir + b"/private")
        enabled_config = client.config_from_string(
            basedir,
            "tub.port",
            BASECONFIG_I % (SOME_FURL,) + (
                "[storage]\n"
                "enabled = true\n"
                "anonymous = true\n"
            )
        )
        node = yield client.create_client_from_config(
            enabled_config,
            _introducer_factory=MemoryIntroducerClient,
        )
        anonymous_storage_furl = enabled_config.get_private_config("storage.furl")
        def check_furl():
            return node.tub.getReferenceForURL(anonymous_storage_furl)
        # Perform a sanity check that our test code makes sense: is this a
        # legit way to verify whether a fURL will refer to an object?
        self.assertThat(
            check_furl(),
            # If it doesn't raise a KeyError we're in business.
            Always(),
        )

        disabled_config = client.config_from_string(
            basedir,
            "tub.port",
            BASECONFIG_I % (SOME_FURL,) + (
                "[storage]\n"
                "enabled = true\n"
                "anonymous = false\n"
            )
        )
        node = yield client.create_client_from_config(
            disabled_config,
            _introducer_factory=MemoryIntroducerClient,
        )
        self.assertThat(
            check_furl,
            raises(KeyError),
        )


class IntroducerClients(unittest.TestCase):

    def test_invalid_introducer_furl(self):
        """
        An introducer.furl of 'None' is invalid and causes
        create_introducer_clients to fail.
        """
        cfg = (
            "[client]\n"
            "introducer.furl = None\n"
        )
        config = client.config_from_string("basedir", "client.port", cfg)

        with self.assertRaises(ValueError) as ctx:
            client.create_introducer_clients(config, main_tub=None)
        self.assertIn(
            "invalid 'introducer.furl = None'",
            str(ctx.exception)
        )


def get_known_server_details(a_client):
    """
    Get some details about known storage servers from a client.

    :param _Client a_client: The client to inspect.

    :return: A ``list`` of two-tuples.  Each element of the list corresponds
        to a "known server".  The first element of each tuple is a server id.
        The second is the server's announcement.
    """
    return list(
        (s.get_serverid(), s.get_announcement())
        for s
        in a_client.storage_broker.get_known_servers()
    )


class StaticServers(Fixture):
    """
    Create a ``servers.yaml`` file.
    """
    def __init__(self, basedir, server_details):
        super(StaticServers, self).__init__()
        self._basedir = basedir
        self._server_details = server_details

    def _setUp(self):
        private = self._basedir.child(u"private")
        private.makedirs()
        servers = private.child(u"servers.yaml")
        servers.setContent(safe_dump({
            u"storage": {
                serverid: {
                    u"ann": announcement,
                }
                for (serverid, announcement)
                in self._server_details
            },
        }))


class StorageClients(SyncTestCase):
    """
    Tests for storage-related behavior of ``_Client``.
    """
    def setUp(self):
        super(StorageClients, self).setUp()
        # Some other tests create Nodes and Node mutates tempfile.tempdir and
        # that screws us up because we're *not* making a Node.  "Fix" it.  See
        # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3052 for the real fix,
        # though.
        import tempfile
        tempfile.tempdir = None

        tempdir = TempDir()
        self.useFixture(tempdir)
        self.basedir = FilePath(tempdir.path)

    @capture_logging(
        lambda case, logger: assertHasAction(
            case,
            logger,
            actionType=u"storage-client:broker:set-static-servers",
            succeeded=True,
        ),
    )
    def test_static_servers(self, logger):
        """
        Storage servers defined in ``private/servers.yaml`` are loaded into the
        storage broker.
        """
        serverid = u"v0-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        announcement = {
            u"nickname": u"some-storage-server",
            u"anonymous-storage-FURL": u"pb://xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx@tcp:storage.example:100/swissnum",
        }
        self.useFixture(
            StaticServers(
                self.basedir,
                [(serverid, announcement)],
            ),
        )
        self.assertThat(
            client.create_client(self.basedir.asTextMode().path),
            succeeded(
                AfterPreprocessing(
                    get_known_server_details,
                    Equals([(serverid, announcement)]),
                ),
            ),
        )

    @capture_logging(
        lambda case, logger: assertHasAction(
            case,
            logger,
            actionType=u"storage-client:broker:make-storage-server",
            succeeded=False,
        ),
    )
    def test_invalid_static_server(self, logger):
        """
        An invalid announcement for a static server does not prevent other static
        servers from being loaded.
        """
        # Some good details
        serverid = u"v1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        announcement = {
            u"nickname": u"some-storage-server",
            u"anonymous-storage-FURL": u"pb://xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx@tcp:storage.example:100/swissnum",
        }
        self.useFixture(
            StaticServers(
                self.basedir,
                [(serverid, announcement),
                 # Along with a "bad" server announcement.  Order in this list
                 # doesn't matter, yaml serializer and Python dicts are going
                 # to shuffle everything around kind of randomly.
                 (u"v0-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                  {u"nickname": u"another-storage-server",
                   u"anonymous-storage-FURL": None,
                  }),
                ],
            ),
        )
        self.assertThat(
            client.create_client(self.basedir.asTextMode().path),
            succeeded(
                AfterPreprocessing(
                    get_known_server_details,
                    # It should have the good server details.
                    Equals([(serverid, announcement)]),
                ),
            ),
        )


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



def matches_dummy_announcement(name, value):
    """
    Matches the portion of an announcement for the ``DummyStorage`` storage
        server plugin.

    :param unicode name: The name of the dummy plugin.

    :param unicode value: The arbitrary value in the dummy plugin
        announcement.

    :return: a testtools-style matcher
    """
    return MatchesDict({
        # Everyone gets a name and a fURL added to their announcement.
        u"name": Equals(name),
        u"storage-server-FURL": matches_furl(),
        # The plugin can contribute things, too.
        u"value": Equals(value),
    })



class StorageAnnouncementTests(SyncTestCase):
    """
    Tests for the storage announcement published by the client.
    """
    def setUp(self):
        super(StorageAnnouncementTests, self).setUp()
        self.basedir = self.useFixture(TempDir()).path
        create_node_dir(self.basedir, u"")


    def get_config(self, storage_enabled, more_storage="", more_sections=""):
        return """
[node]
tub.location = tcp:192.0.2.0:1234

[storage]
enabled = {storage_enabled}
{more_storage}

[client]
introducer.furl = pb://abcde@nowhere/fake

{more_sections}
""".format(
    storage_enabled=storage_enabled,
    more_storage=more_storage,
    more_sections=more_sections,
)


    def test_no_announcement(self):
        """
        No storage announcement is published if storage is not enabled.
        """
        config = client.config_from_string(
            self.basedir,
            "tub.port",
            self.get_config(storage_enabled=False),
        )
        self.assertThat(
            client.create_client_from_config(
                config,
                _introducer_factory=MemoryIntroducerClient,
            ),
            succeeded(AfterPreprocessing(
                get_published_announcements,
                Equals([]),
            )),
        )


    def test_anonymous_storage_announcement(self):
        """
        A storage announcement with the anonymous storage fURL is published when
        storage is enabled.
        """
        config = client.config_from_string(
            self.basedir,
            "tub.port",
            self.get_config(storage_enabled=True),
        )
        client_deferred = client.create_client_from_config(
            config,
            _introducer_factory=MemoryIntroducerClient,
        )
        self.assertThat(
            client_deferred,
            # The Deferred succeeds
            succeeded(AfterPreprocessing(
                # The announcements published by the client should ...
                get_published_announcements,
                # Match the following list (of one element) ...
                MatchesListwise([
                    # The only element in the list ...
                    matches_storage_announcement(self.basedir),
                ]),
            )),
        )


    def test_single_storage_plugin_announcement(self):
        """
        The announcement from a single enabled storage plugin is published when
        storage is enabled.
        """
        self.useFixture(UseTestPlugins())

        value = u"thing"
        config = client.config_from_string(
            self.basedir,
            "tub.port",
            self.get_config(
                storage_enabled=True,
                more_storage="plugins=tahoe-lafs-dummy-v1",
                more_sections=(
                    "[storageserver.plugins.tahoe-lafs-dummy-v1]\n"
                    "some = {}\n".format(value)
                ),
            ),
        )
        self.assertThat(
            client.create_client_from_config(
                config,
                _introducer_factory=MemoryIntroducerClient,
            ),
            succeeded(AfterPreprocessing(
                get_published_announcements,
                MatchesListwise([
                    matches_storage_announcement(
                        self.basedir,
                        options=[
                            matches_dummy_announcement(
                                u"tahoe-lafs-dummy-v1",
                                value,
                            ),
                        ],
                    ),
                ]),
            )),
        )


    def test_multiple_storage_plugin_announcements(self):
        """
        The announcements from several enabled storage plugins are published when
        storage is enabled.
        """
        self.useFixture(UseTestPlugins())

        config = client.config_from_string(
            self.basedir,
            "tub.port",
            self.get_config(
                storage_enabled=True,
                more_storage="plugins=tahoe-lafs-dummy-v1,tahoe-lafs-dummy-v2",
                more_sections=(
                    "[storageserver.plugins.tahoe-lafs-dummy-v1]\n"
                    "some = thing-1\n"
                    "[storageserver.plugins.tahoe-lafs-dummy-v2]\n"
                    "some = thing-2\n"
                ),
            ),
        )
        self.assertThat(
            client.create_client_from_config(
                config,
                _introducer_factory=MemoryIntroducerClient,
            ),
            succeeded(AfterPreprocessing(
                get_published_announcements,
                MatchesListwise([
                    matches_storage_announcement(
                        self.basedir,
                        options=[
                            matches_dummy_announcement(
                                u"tahoe-lafs-dummy-v1",
                                u"thing-1",
                            ),
                            matches_dummy_announcement(
                                u"tahoe-lafs-dummy-v2",
                                u"thing-2",
                            ),
                        ],
                    ),
                ]),
            )),
        )


    def test_stable_storage_server_furl(self):
        """
        The value for the ``storage-server-FURL`` item in the announcement for a
        particular storage server plugin is stable across different node
        instantiations.
        """
        self.useFixture(UseTestPlugins())

        config = client.config_from_string(
            self.basedir,
            "tub.port",
            self.get_config(
                storage_enabled=True,
                more_storage="plugins=tahoe-lafs-dummy-v1",
                more_sections=(
                    "[storageserver.plugins.tahoe-lafs-dummy-v1]\n"
                    "some = thing\n"
                ),
            ),
        )
        node_a = client.create_client_from_config(
            config,
            _introducer_factory=MemoryIntroducerClient,
        )
        node_b = client.create_client_from_config(
            config,
            _introducer_factory=MemoryIntroducerClient,
        )

        self.assertThat(
            defer.gatherResults([node_a, node_b]),
            succeeded(AfterPreprocessing(
                partial(map, get_published_announcements),
                MatchesSameElements(),
            )),
        )


    def test_storage_plugin_without_configuration(self):
        """
        A storage plugin with no configuration is loaded and announced.
        """
        self.useFixture(UseTestPlugins())

        config = client.config_from_string(
            self.basedir,
            "tub.port",
            self.get_config(
                storage_enabled=True,
                more_storage="plugins=tahoe-lafs-dummy-v1",
            ),
        )
        self.assertThat(
            client.create_client_from_config(
                config,
                _introducer_factory=MemoryIntroducerClient,
            ),
            succeeded(AfterPreprocessing(
                get_published_announcements,
                MatchesListwise([
                    matches_storage_announcement(
                        self.basedir,
                        options=[
                            matches_dummy_announcement(
                                u"tahoe-lafs-dummy-v1",
                                u"default-value",
                            ),
                        ],
                    ),
                ]),
            )),
        )


    def test_broken_storage_plugin(self):
        """
        A storage plugin that raises an exception from ``get_storage_server``
        causes ``client.create_client_from_config`` to return ``Deferred``
        that fails.
        """
        self.useFixture(UseTestPlugins())

        config = client.config_from_string(
            self.basedir,
            "tub.port",
            self.get_config(
                storage_enabled=True,
                more_storage="plugins=tahoe-lafs-dummy-v1",
                more_sections=(
                    "[storageserver.plugins.tahoe-lafs-dummy-v1]\n"
                    # This will make it explode on instantiation.
                    "invalid = configuration\n"
                )
            ),
        )
        self.assertThat(
            client.create_client_from_config(
                config,
                _introducer_factory=MemoryIntroducerClient,
            ),
            failed(Always()),
        )

    def test_storage_plugin_not_found(self):
        """
        ``client.create_client_from_config`` raises ``UnknownConfigError`` when
        called with a configuration which enables a storage plugin that is not
        available on the system.
        """
        config = client.config_from_string(
            self.basedir,
            "tub.port",
            self.get_config(
                storage_enabled=True,
                more_storage="plugins=tahoe-lafs-dummy-vX",
            ),
        )
        self.assertThat(
            client.create_client_from_config(
                config,
                _introducer_factory=MemoryIntroducerClient,
            ),
            failed(
                AfterPreprocessing(
                    lambda f: f.type,
                    Equals(configutil.UnknownConfigError),
                ),
            ),
        )
