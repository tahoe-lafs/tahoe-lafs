import os
from twisted.trial import unittest
from twisted.application import service

import allmydata
from allmydata.node import OldConfigError
from allmydata import client
from allmydata.storage_client import StorageFarmBroker
from allmydata.util import base32, fileutil
from allmydata.interfaces import IFilesystemNode, IFileNode, \
     IImmutableFileNode, IMutableFileNode, IDirectoryNode
from foolscap.api import flushEventualQueue
import allmydata.test.common_util as testutil

import mock

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
        client.Client(basedir)

    @mock.patch('twisted.python.log.msg')
    def test_error_on_old_config_files(self, mock_log_msg):
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

        e = self.failUnlessRaises(OldConfigError, client.Client, basedir)
        self.failUnlessIn(os.path.abspath(os.path.join(basedir, "introducer.furl")), e.args[0])
        self.failUnlessIn(os.path.abspath(os.path.join(basedir, "no_storage")), e.args[0])
        self.failUnlessIn(os.path.abspath(os.path.join(basedir, "readonly_storage")), e.args[0])
        self.failUnlessIn(os.path.abspath(os.path.join(basedir, "debug_discard_storage")), e.args[0])

        for oldfile in ['introducer.furl', 'no_storage', 'readonly_storage',
                        'debug_discard_storage']:
            logged = [ m for m in mock_log_msg.call_args_list if
                       ("Found pre-Tahoe-LAFS-v1.3 configuration file" in str(m[0][0]) and oldfile in str(m[0][0])) ]
            self.failUnless(logged, (oldfile, mock_log_msg.call_args_list))

        for oldfile in [
            'nickname', 'webport', 'keepalive_timeout', 'log_gatherer.furl',
            'disconnect_timeout', 'advertised_ip_addresses', 'helper.furl',
            'key_generator.furl', 'stats_gatherer.furl', 'sizelimit',
            'run_helper']:
            logged = [ m for m in mock_log_msg.call_args_list if
                       ("Found pre-Tahoe-LAFS-v1.3 configuration file" in str(m[0][0]) and oldfile in str(m[0][0])) ]
            self.failIf(logged, oldfile)

    def test_secrets(self):
        basedir = "test_client.Basic.test_secrets"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG)
        c = client.Client(basedir)
        secret_fname = os.path.join(basedir, "private", "secret")
        self.failUnless(os.path.exists(secret_fname), secret_fname)
        renew_secret = c.get_renewal_secret()
        self.failUnless(base32.b2a(renew_secret))
        cancel_secret = c.get_cancel_secret()
        self.failUnless(base32.b2a(cancel_secret))

    def test_reserved_1(self):
        basedir = "client.Basic.test_reserved_1"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = 1000\n")
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space, 1000)

    def test_reserved_2(self):
        basedir = "client.Basic.test_reserved_2"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),  \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = 10K\n")
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space, 10*1000)

    def test_reserved_3(self):
        basedir = "client.Basic.test_reserved_3"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = 5mB\n")
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space,
                             5*1000*1000)

    def test_reserved_4(self):
        basedir = "client.Basic.test_reserved_4"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = 78Gb\n")
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space,
                             78*1000*1000*1000)

    def test_reserved_bad(self):
        basedir = "client.Basic.test_reserved_bad"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n" + \
                           "reserved_space = bogus\n")
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space, 0)

    def _permute(self, sb, key):
        return [ s.get_serverid() for s in sb.get_servers_for_psi(key) ]

    def test_permute(self):
        sb = StorageFarmBroker(None, True)
        for k in ["%d" % i for i in range(5)]:
            sb.test_add_rref(k, "rref")

        self.failUnlessReallyEqual(self._permute(sb, "one"), ['3','1','0','4','2'])
        self.failUnlessReallyEqual(self._permute(sb, "two"), ['0','4','2','1','3'])
        sb.servers.clear()
        self.failUnlessReallyEqual(self._permute(sb, "one"), [])

    def test_versions(self):
        basedir = "test_client.Basic.test_versions"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), \
                           BASECONFIG + \
                           "[storage]\n" + \
                           "enabled = true\n")
        c = client.Client(basedir)
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

    @mock.patch('allmydata.util.log.msg')
    @mock.patch('allmydata.frontends.drop_upload.DropUploader')
    def test_create_drop_uploader(self, mock_drop_uploader, mock_log_msg):
        class MockDropUploader(service.MultiService):
            name = 'drop-upload'

            def __init__(self, client, upload_dircap, local_dir_utf8, inotify=None):
                service.MultiService.__init__(self)
                self.client = client
                self.upload_dircap = upload_dircap
                self.local_dir_utf8 = local_dir_utf8
                self.inotify = inotify

        mock_drop_uploader.side_effect = MockDropUploader

        upload_dircap = "URI:DIR2:blah"
        local_dir_utf8 = u"loc\u0101l_dir".encode('utf-8')
        config = (BASECONFIG +
                  "[storage]\n" +
                  "enabled = false\n" +
                  "[drop_upload]\n" +
                  "enabled = true\n" +
                  "upload.dircap = " + upload_dircap + "\n" +
                  "local.directory = " + local_dir_utf8 + "\n")

        basedir1 = "test_client.Basic.test_create_drop_uploader1"
        os.mkdir(basedir1)
        fileutil.write(os.path.join(basedir1, "tahoe.cfg"), config)
        c1 = client.Client(basedir1)
        uploader = c1.getServiceNamed('drop-upload')
        self.failUnless(isinstance(uploader, MockDropUploader), uploader)
        self.failUnlessReallyEqual(uploader.client, c1)
        self.failUnlessReallyEqual(uploader.upload_dircap, upload_dircap)
        self.failUnlessReallyEqual(uploader.local_dir_utf8, local_dir_utf8)
        self.failUnless(uploader.inotify is None, uploader.inotify)
        self.failUnless(uploader.running)

        class Boom(Exception):
            pass
        mock_drop_uploader.side_effect = Boom()

        basedir2 = "test_client.Basic.test_create_drop_uploader2"
        os.mkdir(basedir2)
        fileutil.write(os.path.join(basedir2, "tahoe.cfg"),
                       BASECONFIG +
                       "[drop_upload]\n" +
                       "enabled = true\n")
        c2 = client.Client(basedir2)
        self.failUnlessRaises(KeyError, c2.getServiceNamed, 'drop-upload')
        self.failIf([True for arg in mock_log_msg.call_args_list if "Boom" in repr(arg)],
                    mock_log_msg.call_args_list)
        self.failUnless([True for arg in mock_log_msg.call_args_list if "upload.dircap or local.directory not specified" in repr(arg)],
                        mock_log_msg.call_args_list)

        basedir3 = "test_client.Basic.test_create_drop_uploader3"
        os.mkdir(basedir3)
        fileutil.write(os.path.join(basedir3, "tahoe.cfg"), config)
        client.Client(basedir3)
        self.failUnless([True for arg in mock_log_msg.call_args_list if "Boom" in repr(arg)],
                        mock_log_msg.call_args_list)


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

    def test_loadable(self):
        basedir = "test_client.Run.test_loadable"
        os.mkdir(basedir)
        dummy = "pb://wl74cyahejagspqgy4x5ukrvfnevlknt@127.0.0.1:58889/bogus"
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), BASECONFIG_I % dummy)
        fileutil.write(os.path.join(basedir, "suicide_prevention_hotline"), "")
        client.Client(basedir)

    def test_reloadable(self):
        basedir = "test_client.Run.test_reloadable"
        os.mkdir(basedir)
        dummy = "pb://wl74cyahejagspqgy4x5ukrvfnevlknt@127.0.0.1:58889/bogus"
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), BASECONFIG_I % dummy)
        c1 = client.Client(basedir)
        c1.setServiceParent(self.sparent)

        # delay to let the service start up completely. I'm not entirely sure
        # this is necessary.
        d = self.stall(delay=2.0)
        d.addCallback(lambda res: c1.disownServiceParent())
        # the cygwin buildslave seems to need more time to let the old
        # service completely shut down. When delay=0.1, I saw this test fail,
        # probably due to the logport trying to reclaim the old socket
        # number. This suggests that either we're dropping a Deferred
        # somewhere in the shutdown sequence, or that cygwin is just cranky.
        d.addCallback(self.stall, delay=2.0)
        def _restart(res):
            # TODO: pause for slightly over one second, to let
            # Client._check_hotline poll the file once. That will exercise
            # another few lines. Then add another test in which we don't
            # update the file at all, and watch to see the node shutdown. (to
            # do this, use a modified node which overrides Node.shutdown(),
            # also change _check_hotline to use it instead of a raw
            # reactor.stop, also instrument the shutdown event in an
            # attribute that we can check)
            c2 = client.Client(basedir)
            c2.setServiceParent(self.sparent)
            return c2.disownServiceParent()
        d.addCallback(_restart)
        return d

class NodeMaker(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_maker(self):
        basedir = "client/NodeMaker/maker"
        fileutil.make_dirs(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), BASECONFIG)
        c = client.Client(basedir)

        n = c.create_node_from_uri("URI:CHK:6nmrpsubgbe57udnexlkiwzmlu:bjt7j6hshrlmadjyr7otq3dc24end5meo5xcr5xe5r663po6itmq:3:10:7277")
        self.failUnless(IFilesystemNode.providedBy(n))
        self.failUnless(IFileNode.providedBy(n))
        self.failUnless(IImmutableFileNode.providedBy(n))
        self.failIf(IMutableFileNode.providedBy(n))
        self.failIf(IDirectoryNode.providedBy(n))
        self.failUnless(n.is_readonly())
        self.failIf(n.is_mutable())

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
