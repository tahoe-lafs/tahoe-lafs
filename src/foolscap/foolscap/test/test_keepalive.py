
from twisted.trial import unittest
from twisted.internet import reactor, defer
from twisted.python.failure import Failure

from foolscap import Tub, UnauthenticatedTub, DeadReferenceError
from foolscap.broker import Broker
from foolscap.eventual import flushEventualQueue
from foolscap.test.common import TargetWithoutInterfaces

crypto_available = False
try:
    from foolscap import crypto
    crypto_available = crypto.available
except ImportError:
    pass

# we use authenticated tubs if possible. If crypto is not available, fall
# back to unauthenticated ones
GoodEnoughTub = UnauthenticatedTub
if crypto_available:
    GoodEnoughTub = Tub

from twisted.python import log

class PingCountingBroker(Broker):
    pings = 0
    pongs = 0
    def sendPING(self, number=0):
        self.pings += 1
        log.msg("PING: %d" % number)
        Broker.sendPING(self, number)
    def sendPONG(self, number):
        self.pongs += 1
        log.msg("PONG: %d" % number)
        Broker.sendPONG(self, number)

class Keepalives(unittest.TestCase):
    def setUp(self):
        s0, s1 = self.services = [GoodEnoughTub(), GoodEnoughTub()]
        s0.brokerClass = PingCountingBroker
        s1.brokerClass = PingCountingBroker
        s0.startService()
        s1.startService()
        l = s0.listenOn("tcp:0:interface=127.0.0.1")
        s0.setLocation("127.0.0.1:%d" % l.getPortnum())
        self.target = TargetWithoutInterfaces()
        public_url = s0.registerReference(self.target, "target")
        self.public_url = public_url

    def tearDown(self):
        d = defer.DeferredList([s.stopService() for s in self.services])
        d.addCallback(flushEventualQueue)
        return d

    def getRef(self):
        d = self.services[1].getReference(self.public_url)
        return d

    def stall(self, res, timeout):
        d = defer.Deferred()
        reactor.callLater(timeout, d.callback, res)
        return d

    def testSendPings(self):
        # establish a connection with very short idle timers, to provoke
        # plenty of PINGs and PONGs
        self.services[0].setOption("keepaliveTimeout", 0.1)
        self.services[1].setOption("keepaliveTimeout", 0.1)
        # but we don't set disconnectTimeout, so we'll never
        # actually drop the connection
        d = self.getRef()
        d.addCallback(self.stall, 2)
        def _count_pings(rref):
            b = rref.tracker.broker
            # we're only watching one side here (the initiating side,
            # services[0]). Either side could produce a PING that the other
            # side responds to with a PONG, depending upon how the timers
            # interleave. And a side that hears a PING will not bother to
            # send a PING of its own. So only count the sum of the two kinds
            # of messages. What I really care about is that the timers are
            # restarted after the first timeout, so that more than one
            # message per side is being generated. If we have no scheduling
            # latency and high-resolution clocks, we expect to see about 10
            # or 20 ping+pongs.
            self.failUnless(b.pings + b.pongs > 4,
                            "b.pings=%d, b.pongs=%d" % (b.pings, b.pongs))
            # and the connection should still be alive and usable
            return rref.callRemote("add", 1, 2)
        d.addCallback(_count_pings)
        def _check_add(res):
            self.failUnlessEqual(res, 3)
        d.addCallback(_check_add)

        return d

    def do_testDisconnect(self, which):
        # establish a connection with a very short disconnect timeout, so it
        # will be abandoned. We only set this on one side, since either the
        # initiating side or the receiving side should be able to timeout the
        # connection. Because we don't set keepaliveTimeout, there will be no
        # keepalives, so if we don't use the connection for 0.5 seconds, it
        # will be dropped.
        self.services[which].setOption("disconnectTimeout", 0.5)

        d = self.getRef()
        d.addCallback(self.stall, 2)
        def _check_ref(rref):
            d2 = rref.callRemote("add", 1, 2)
            def _check(res):
                self.failUnless(isinstance(res, Failure))
                self.failUnless(res.check(DeadReferenceError))
            d2.addBoth(_check)
            return d2
        d.addCallback(_check_ref)

        return d

    def testDisconnect0(self):
        return self.do_testDisconnect(0)
    def testDisconnect1(self):
        return self.do_testDisconnect(1)

    def do_testNoDisconnect(self, which):
        # establish a connection with a short disconnect timeout, but an even
        # shorter keepalive timeout, so the connection should stay alive. We
        # only provide the keepalives on one side, but enforce the disconnect
        # timeout on both: just one side doing keepalives should keep the
        # whole connection alive.
        self.services[which].setOption("keepaliveTimeout", 0.1)
        self.services[0].setOption("disconnectTimeout", 1.0)
        self.services[1].setOption("disconnectTimeout", 1.0)

        d = self.getRef()
        d.addCallback(self.stall, 2)
        def _check(rref):
            # the connection should still be alive
            return rref.callRemote("add", 1, 2)
        d.addCallback(_check)
        def _check_add(res):
            self.failUnlessEqual(res, 3)
        d.addCallback(_check_add)

        return d

    def testNoDisconnect0(self):
        return self.do_testNoDisconnect(0)
    def testNoDisconnect1(self):
        return self.do_testNoDisconnect(1)
