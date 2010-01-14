
import os, stat, sys, time
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import log

from foolscap.api import flushEventualQueue
from twisted.application import service
from allmydata.node import Node, formatTimeTahoeStyle
from allmydata.util import fileutil
import common_util as testutil

class LoggingMultiService(service.MultiService):
    def log(self, msg, **kw):
        pass

class TestNode(Node):
    CERTFILE='DEFAULT_CERTFILE_BLANK'
    PORTNUMFILE='DEFAULT_PORTNUMFILE_BLANK'

class TestCase(testutil.SignalMixin, unittest.TestCase):
    def setUp(self):
        testutil.SignalMixin.setUp(self)
        self.parent = LoggingMultiService()
        self.parent.startService()
    def tearDown(self):
        log.msg("%s.tearDown" % self.__class__.__name__)
        testutil.SignalMixin.tearDown(self)
        d = defer.succeed(None)
        d.addCallback(lambda res: self.parent.stopService())
        d.addCallback(flushEventualQueue)
        return d

    def test_location(self):
        basedir = "test_node/test_location"
        fileutil.make_dirs(basedir)
        f = open(os.path.join(basedir, 'tahoe.cfg'), 'wt')
        f.write("[node]\n")
        f.write("tub.location = 1.2.3.4:5\n")
        f.close()

        n = TestNode(basedir)
        n.setServiceParent(self.parent)
        d = n.when_tub_ready()

        def _check_addresses(ignored_result):
            furl = n.tub.registerReference(n)
            self.failUnless("1.2.3.4:5" in furl, furl)

        d.addCallback(_check_addresses)
        return d

    def test_location2(self):
        basedir = "test_node/test_location2"
        fileutil.make_dirs(basedir)
        f = open(os.path.join(basedir, 'tahoe.cfg'), 'wt')
        f.write("[node]\n")
        f.write("tub.location = 1.2.3.4:5,example.org:8091\n")
        f.close()

        n = TestNode(basedir)
        n.setServiceParent(self.parent)
        d = n.when_tub_ready()

        def _check_addresses(ignored_result):
            furl = n.tub.registerReference(n)
            self.failUnless("1.2.3.4:5" in furl, furl)
            self.failUnless("example.org:8091" in furl, furl)

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
        self.failUnless(isinstance(n, TestNode))
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
        self.failUnless(isinstance(n, TestNode))
        privdir = os.path.join(basedir, "private")
        st = os.stat(privdir)
        bits = stat.S_IMODE(st[stat.ST_MODE])
        self.failUnless(bits & 0001 == 0, bits)
