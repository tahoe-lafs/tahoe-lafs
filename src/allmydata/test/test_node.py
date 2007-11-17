
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
    def log(self, msg):
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

    def test_log(self):
        basedir = "test_node/test_log"
        fileutil.make_dirs(basedir)
        n = TestNode(basedir)
        n.log("this is a message")
        n.log("with %d %s %s", args=(2, "interpolated", "parameters"))
        n.log("with bogus %d expansion", args=("not an integer",))

    def test_logpublisher(self):
        basedir = "test_node/test_logpublisher"
        fileutil.make_dirs(basedir)
        observer = LogObserver()
        n = TestNode(basedir)
        n.setServiceParent(self.parent)
        d = n.when_tub_ready()
        def _ready(res):
            n.log("starting up")
            flogport = open(os.path.join(n.basedir,"logport.furl"), "r").read()
            return n.tub.getReference(flogport.strip())
        d.addCallback(_ready)
        def _got_logport(logport):
            d = logport.callRemote("get_versions")
            def _check(versions):
                self.failUnlessEqual(versions["allmydata"],
                                     allmydata.__version__)
            d.addCallback(_check)
            d.addCallback(lambda res:
                          logport.callRemote("subscribe_to_all", observer))
            def _emit(subscription):
                self._subscription = subscription
                log.msg("message here")
            d.addCallback(_emit)
            d.addCallback(fireEventually)
            d.addCallback(fireEventually)
            def _check_observer(res):
                msgs = observer.messages
                self.failUnlessEqual(len(msgs), 1)
                #print msgs
                self.failUnlessEqual(msgs[0]["message"], ("message here",) )
            d.addCallback(_check_observer)
            def _done(res):
                return logport.callRemote("unsubscribe", self._subscription)
            d.addCallback(_done)
            return d
        d.addCallback(_got_logport)
        return d

    def test_log_gatherer(self):
        t = Tub()
        t.setServiceParent(self.parent)
        t.listenOn("tcp:0:interface=127.0.0.1")
        l = t.getListeners()[0]
        portnum = l.getPortnum()
        t.setLocation("127.0.0.1:%d" % portnum)
        gatherer = Gatherer()
        gatherer.d = defer.Deferred()
        gatherer_furl = t.registerReference(gatherer)

        basedir = "test_node/test_log_gatherer"
        fileutil.make_dirs(basedir)
        f = open(os.path.join(basedir, "log_gatherer.furl"), "w")
        f.write(gatherer_furl + "\n")
        f.close()

        n = TestNode(basedir)
        n.setServiceParent(self.parent)
        d = n.when_tub_ready()
        def _ready(res):
            n.log("starting up")
            # about now, the node will be contacting the Gatherer and
            # offering its logport.
            return gatherer.d
        d.addCallback(_ready)
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
