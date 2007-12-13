
import os, time
from zope.interface import implements
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import log

from foolscap import Tub, Referenceable
from foolscap.eventual import fireEventually, flushEventualQueue
from twisted.application import service
import allmydata
from allmydata.node import Node, formatTimeTahoeStyle
from allmydata.util import testutil, fileutil
from allmydata import logpublisher

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

    def test_timestamp(self):
        # this modified logger doesn't seem to get used during the tests,
        # probably because we don't modify the LogObserver that trial
        # installs (only the one that twistd installs). So manually exercise
        # it a little bit.
        t = formatTimeTahoeStyle("ignored", time.time())
        self.failUnless("Z" in t)
        t2 = formatTimeTahoeStyle("ignored", int(time.time()))
        self.failUnless("Z" in t2)

class Gatherer(Referenceable):
    implements(logpublisher.RILogGatherer)
    def remote_logport(self, nodeid, logport):
        d = logport.callRemote("get_versions")
        d.addCallback(self.d.callback)

class LogObserver(Referenceable):
    implements(logpublisher.RILogObserver)
    def __init__(self):
        self.messages = []
    def remote_msg(self, d):
        self.messages.append(d)
