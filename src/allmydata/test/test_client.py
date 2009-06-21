
import os
from twisted.trial import unittest
from twisted.application import service
from twisted.python import log

import allmydata
from allmydata import client
from allmydata.storage_client import StorageFarmBroker
from allmydata.introducer.client import IntroducerClient
from allmydata.util import base32
from foolscap.api import flushEventualQueue
import common_util as testutil

class FakeIntroducerClient(IntroducerClient):
    def __init__(self):
        self._connections = set()
    def add_peer(self, nodeid):
        entry = (nodeid, "storage", "rref")
        self._connections.add(entry)
    def remove_all_peers(self):
        self._connections.clear()

class Basic(unittest.TestCase):
    def test_loadable(self):
        basedir = "test_client.Basic.test_loadable"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        open(os.path.join(basedir, "vdrive.furl"), "w").write("")
        c = client.Client(basedir)

    def test_loadable_without_vdrive(self):
        basedir = "test_client.Basic.test_loadable_without_vdrive"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        c = client.Client(basedir)

    def test_loadable_old_config_bits(self):
        basedir = "test_client.Basic.test_loadable_old_config_bits"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        open(os.path.join(basedir, "vdrive.furl"), "w").write("")
        open(os.path.join(basedir, "no_storage"), "w").write("")
        open(os.path.join(basedir, "readonly_storage"), "w").write("")
        open(os.path.join(basedir, "debug_discard_storage"), "w").write("")
        c = client.Client(basedir)
        try:
            c.getServiceNamed("storage")
            self.fail("that was supposed to fail")
        except KeyError:
            pass

    def test_loadable_old_storage_config_bits(self):
        basedir = "test_client.Basic.test_loadable_old_storage_config_bits"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        open(os.path.join(basedir, "vdrive.furl"), "w").write("")
        open(os.path.join(basedir, "readonly_storage"), "w").write("")
        open(os.path.join(basedir, "debug_discard_storage"), "w").write("")
        c = client.Client(basedir)
        s = c.getServiceNamed("storage")
        self.failUnless(s.no_storage)
        self.failUnless(s.readonly_storage)

    def test_secrets(self):
        basedir = "test_client.Basic.test_secrets"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        open(os.path.join(basedir, "vdrive.furl"), "w").write("")
        c = client.Client(basedir)
        secret_fname = os.path.join(basedir, "private", "secret")
        self.failUnless(os.path.exists(secret_fname), secret_fname)
        renew_secret = c.get_renewal_secret()
        self.failUnless(base32.b2a(renew_secret))
        cancel_secret = c.get_cancel_secret()
        self.failUnless(base32.b2a(cancel_secret))

    BASECONFIG = ("[client]\n"
                  "introducer.furl = \n"
                  )

    def test_reserved_1(self):
        basedir = "client.Basic.test_reserved_1"
        os.mkdir(basedir)
        f = open(os.path.join(basedir, "tahoe.cfg"), "w")
        f.write(self.BASECONFIG)
        f.write("[storage]\n")
        f.write("enabled = true\n")
        f.write("reserved_space = 1000\n")
        f.close()
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space, 1000)

    def test_reserved_2(self):
        basedir = "client.Basic.test_reserved_2"
        os.mkdir(basedir)
        f = open(os.path.join(basedir, "tahoe.cfg"), "w")
        f.write(self.BASECONFIG)
        f.write("[storage]\n")
        f.write("enabled = true\n")
        f.write("reserved_space = 10K\n")
        f.close()
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space, 10*1000)

    def test_reserved_3(self):
        basedir = "client.Basic.test_reserved_3"
        os.mkdir(basedir)
        f = open(os.path.join(basedir, "tahoe.cfg"), "w")
        f.write(self.BASECONFIG)
        f.write("[storage]\n")
        f.write("enabled = true\n")
        f.write("reserved_space = 5mB\n")
        f.close()
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space,
                             5*1000*1000)

    def test_reserved_4(self):
        basedir = "client.Basic.test_reserved_4"
        os.mkdir(basedir)
        f = open(os.path.join(basedir, "tahoe.cfg"), "w")
        f.write(self.BASECONFIG)
        f.write("[storage]\n")
        f.write("enabled = true\n")
        f.write("reserved_space = 78Gb\n")
        f.close()
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space,
                             78*1000*1000*1000)

    def test_reserved_bad(self):
        basedir = "client.Basic.test_reserved_bad"
        os.mkdir(basedir)
        f = open(os.path.join(basedir, "tahoe.cfg"), "w")
        f.write(self.BASECONFIG)
        f.write("[storage]\n")
        f.write("enabled = true\n")
        f.write("reserved_space = bogus\n")
        f.close()
        c = client.Client(basedir)
        self.failUnlessEqual(c.getServiceNamed("storage").reserved_space, 0)

    def _permute(self, sb, key):
        return [ peerid
                 for (peerid,rref) in sb.get_servers_for_index(key) ]

    def test_permute(self):
        sb = StorageFarmBroker()
        for k in ["%d" % i for i in range(5)]:
            sb.add_server(k, None)

        self.failUnlessEqual(self._permute(sb, "one"), ['3','1','0','4','2'])
        self.failUnlessEqual(self._permute(sb, "two"), ['0','4','2','1','3'])
        sb.servers = {}
        self.failUnlessEqual(self._permute(sb, "one"), [])

    def test_versions(self):
        basedir = "test_client.Basic.test_versions"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        open(os.path.join(basedir, "vdrive.furl"), "w").write("")
        c = client.Client(basedir)
        ss = c.getServiceNamed("storage")
        verdict = ss.remote_get_version()
        self.failUnlessEqual(verdict["application-version"],
                             str(allmydata.__full_version__))
        self.failIfEqual(str(allmydata.__version__), "unknown")
        self.failUnless("." in str(allmydata.__full_version__),
                        "non-numeric version in '%s'" % allmydata.__version__)
        all_versions = allmydata.get_package_versions_string()
        self.failUnless("allmydata-tahoe" in all_versions)
        log.msg("tahoe versions: %s" % all_versions)
        # also test stats
        stats = c.get_stats()
        self.failUnless("node.uptime" in stats)
        self.failUnless(isinstance(stats["node.uptime"], float))

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
        open(os.path.join(basedir, "introducer.furl"), "w").write(dummy)
        open(os.path.join(basedir, "suicide_prevention_hotline"), "w")
        c = client.Client(basedir)

    def test_reloadable(self):
        basedir = "test_client.Run.test_reloadable"
        os.mkdir(basedir)
        dummy = "pb://wl74cyahejagspqgy4x5ukrvfnevlknt@127.0.0.1:58889/bogus"
        open(os.path.join(basedir, "introducer.furl"), "w").write(dummy)
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

