
import os, stat, sys, time
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import log

from mock import patch

from foolscap.api import flushEventualQueue
from twisted.application import service
from allmydata.node import Node, formatTimeTahoeStyle, MissingConfigEntry
from allmydata.util import fileutil
import allmydata.test.common_util as testutil

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

    def test_tahoe_cfg_utf8(self):
        basedir = "test_node/test_tahoe_cfg_utf8"
        fileutil.make_dirs(basedir)
        f = open(os.path.join(basedir, 'tahoe.cfg'), 'wt')
        f.write(u"\uFEFF[node]\n".encode('utf-8'))
        f.write(u"nickname = \u2621\n".encode('utf-8'))
        f.close()

        n = TestNode(basedir)
        n.setServiceParent(self.parent)
        d = n.when_tub_ready()
        d.addCallback(lambda ign: self.failUnlessEqual(n.get_config("node", "nickname").decode('utf-8'),
                                                       u"\u2621"))
        return d

    def test_private_config(self):
        basedir = "test_node/test_private_config"
        privdir = os.path.join(basedir, "private")
        fileutil.make_dirs(privdir)
        f = open(os.path.join(privdir, 'already'), 'wt')
        f.write("secret")
        f.close()

        n = TestNode(basedir)
        self.failUnlessEqual(n.get_private_config("already"), "secret")
        self.failUnlessRaises(MissingConfigEntry, n.get_private_config, "not")
        value = n.get_or_create_private_config("new", "start")
        self.failUnlessEqual(value, "start")
        self.failUnlessEqual(n.get_private_config("new"), "start")
        counter = []
        def make_newer():
            counter.append("called")
            return "newer"
        value = n.get_or_create_private_config("newer", make_newer)
        self.failUnlessEqual(len(counter), 1)
        self.failUnlessEqual(value, "newer")
        self.failUnlessEqual(n.get_private_config("newer"), "newer")

        value = n.get_or_create_private_config("newer", make_newer)
        self.failUnlessEqual(len(counter), 1) # don't call unless necessary
        self.failUnlessEqual(value, "newer")

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

    @patch("foolscap.logging.log.setLogDir")
    def test_logdir_is_str(self, mock_setLogDir):
        basedir = "test_node/test_logdir_is_str"
        fileutil.make_dirs(basedir)

        def call_setLogDir(logdir):
            self.failUnless(isinstance(logdir, str), logdir)
        mock_setLogDir.side_effect = call_setLogDir

        TestNode(basedir)
        self.failUnless(mock_setLogDir.called)
