
import os, stat, sys, time
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import log

from foolscap.eventual import flushEventualQueue
from twisted.application import service
from allmydata.node import Node, formatTimeTahoeStyle
from allmydata.util import testutil, fileutil

class LoggingMultiService(service.MultiService):
    def log(self, msg, **kw):
        pass

class TestNode(Node):
    CERTFILE='DEFAULT_CERTFILE_BLANK'
    PORTNUMFILE='DEFAULT_PORTNUMFILE_BLANK'

class TestCase(unittest.TestCase, testutil.SignalMixin):
    def setUp(self):
        self.parent = LoggingMultiService()
        self.parent.startService()
    def tearDown(self):
        log.msg("%s.tearDown" % self.__class__.__name__)
        d = defer.succeed(None)
        d.addCallback(lambda res: self.parent.stopService())
        d.addCallback(flushEventualQueue)
        return d

    def test_advertised_ip_addresses(self):
        basedir = "test_node/test_advertised_ip_addresses"
        fileutil.make_dirs(basedir)
        f = open(os.path.join(basedir, 'advertised_ip_addresses'),'w')
        f.write('1.2.3.4:5')
        f.close()

        n = TestNode(basedir)
        n.setServiceParent(self.parent)
        d = n.when_tub_ready()

        def _check_addresses(ignored_result):
            furl = n.tub.registerReference(n)
            self.failUnless("1.2.3.4:5" in furl, furl)

        d.addCallback(_check_addresses)
        return d

    def test_advertised_ip_addresses2(self):
        basedir = "test_node/test_advertised_ip_addresses2"
        fileutil.make_dirs(basedir)

        n = TestNode(basedir)
        n.setServiceParent(self.parent)
        d = n.when_tub_ready()
        # this lets the 'port' file get written
        d.addCallback(lambda res: n.disownServiceParent())
        def _new_node(res):
            f = open(os.path.join(basedir, 'advertised_ip_addresses'),'w')
            f.write('1.2.3.4\n')
            f.write("6.7.8.9\n")
            f.close()
            n2 = self.node = TestNode(basedir)
            n2.setServiceParent(self.parent)
            return n2.when_tub_ready()
        d.addCallback(_new_node)

        def _check_addresses(ignored_result):
            portfile = os.path.join(basedir, self.node.PORTNUMFILE)
            port = int(open(portfile, "r").read().strip())
            furl = self.node.tub.registerReference(n)
            self.failUnless(("1.2.3.4:%d" % port) in furl, furl)
            self.failUnless(("6.7.8.9:%d" % port) in furl, furl)

        d.addCallback(_check_addresses)
        return d

    def test_timestamp(self):
        # this modified logger doesn't seem to get used during the tests,
        # probably because we don't modify the LogObserver that trial
        # installs (only the one that twistd installs). So manually exercise
        # it a little bit.
        t = formatTimeTahoeStyle("ignored", time.time())
        self.failUnless("Z" in t)
        t2 = formatTimeTahoeStyle("ignored", int(time.time()))
        self.failUnless("Z" in t2)

    def test_secrets_dir(self):
        basedir = "test_node/test_secrets_dir"
        fileutil.make_dirs(basedir)
        n = TestNode(basedir)
        self.failUnless(os.path.exists(os.path.join(basedir, "private")))

    def test_secrets_dir_protected(self):
        if "win32" in sys.platform.lower() or "cygwin" in sys.platform.lower():
            # We don't know how to test that unprivileged users can't read this
            # thing.  (Also we don't know exactly how to set the permissions so
            # that unprivileged users can't read this thing.)
            raise unittest.SkipTest("We don't know how to set permissions on Windows.")
        basedir = "test_node/test_secrets_dir_protected"
        fileutil.make_dirs(basedir)
        n = TestNode(basedir)
        privdir = os.path.join(basedir, "private")
        st = os.stat(privdir)
        bits = stat.S_IMODE(st[stat.ST_MODE])
        self.failUnless(bits & 0001 == 0, bits)
