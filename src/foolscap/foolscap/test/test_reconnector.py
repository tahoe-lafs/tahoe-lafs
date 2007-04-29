# -*- test-case-name: foolscap.test.test_reconnector -*-

from twisted.trial import unittest
from foolscap import UnauthenticatedTub
from foolscap.test.common import HelperTarget
from twisted.internet.main import CONNECTION_LOST
from twisted.internet import defer, reactor
from foolscap.eventual import eventually, flushEventualQueue
from foolscap import negotiate

class AlwaysFailNegotiation(negotiate.Negotiation):
    def evaluateHello(self, offer):
        raise negotiate.NegotiationError("I always fail")

class Reconnector(unittest.TestCase):

    def setUp(self):
        self.services = [UnauthenticatedTub(), UnauthenticatedTub()]
        self.tubA, self.tubB = self.services
        for s in self.services:
            s.startService()
            l = s.listenOn("tcp:0:interface=127.0.0.1")
            s.setLocation("127.0.0.1:%d" % l.getPortnum())

    def tearDown(self):
        d = defer.DeferredList([s.stopService() for s in self.services])
        d.addCallback(flushEventualQueue)
        return d


    def test_try(self):
        self.count = 0
        self.attached = False
        self.done = defer.Deferred()
        target = HelperTarget("bob")
        url = self.tubB.registerReference(target)
        rc = self.tubA.connectTo(url, self._got_ref, "arg", kw="kwarg")
        # at least make sure the stopConnecting method is present, even if we
        # don't have a real test for it yet
        self.failUnless(rc.stopConnecting)
        return self.done

    def _got_ref(self, rref, arg, kw):
        self.failUnlessEqual(self.attached, False)
        self.attached = True
        self.failUnlessEqual(arg, "arg")
        self.failUnlessEqual(kw, "kwarg")
        self.count += 1
        rref.notifyOnDisconnect(self._disconnected, self.count)
        if self.count < 2:
            # forcibly disconnect it
            eventually(rref.tracker.broker.transport.loseConnection,
                       CONNECTION_LOST)
        else:
            self.done.callback("done")

    def _disconnected(self, count):
        self.failUnlessEqual(self.attached, True)
        self.failUnlessEqual(count, self.count)
        self.attached = False

    def _connected(self, ref, notifiers, accumulate):
        accumulate.append(ref)
        if notifiers:
            notifiers.pop(0).callback(ref)

    def stall(self, timeout, res=None):
        d = defer.Deferred()
        reactor.callLater(timeout, d.callback, res)
        return d

    def test_retry(self):
        tubC = UnauthenticatedTub()
        connects = []
        target = HelperTarget("bob")
        url = self.tubB.registerReference(target, "target")
        portb = self.tubB.getListeners()[0].getPortnum()
        d1 = defer.Deferred()
        notifiers = [d1]
        self.services.remove(self.tubB)
        d = self.tubB.stopService()
        def _start_connecting(res):
            # this will fail, since tubB is not listening anymore
            self.rc = self.tubA.connectTo(url, self._connected,
                                          notifiers, connects)
            # give it a few tries, then start tubC listening on the same port
            # that tubB used to, which should allow the connection to
            # complete (since they're both UnauthenticatedTubs)
            return self.stall(2)
        d.addCallback(_start_connecting)
        def _start_tubC(res):
            self.failUnlessEqual(len(connects), 0)
            self.services.append(tubC)
            tubC.startService()
            tubC.listenOn("tcp:%d:interface=127.0.0.1" % portb)
            tubC.setLocation("127.0.0.1:%d" % portb)
            url2 = tubC.registerReference(target, "target")
            assert url2 == url
            return d1
        d.addCallback(_start_tubC)
        def _connected(res):
            self.failUnlessEqual(len(connects), 1)
            self.rc.stopConnecting()
        d.addCallback(_connected)
        return d

    def test_negotiate_fails_and_retry(self):
        connects = []
        target = HelperTarget("bob")
        url = self.tubB.registerReference(target, "target")
        l = self.tubB.getListeners()[0]
        l.negotiationClass = AlwaysFailNegotiation
        portb = l.getPortnum()
        d1 = defer.Deferred()
        notifiers = [d1]
        self.rc = self.tubA.connectTo(url, self._connected,
                                      notifiers, connects)
        d = self.stall(2)
        def _failed_a_few_times(res):
            # the reconnector should have failed once or twice, since the
            # negotiation would always fail.
            self.failUnlessEqual(len(connects), 0)
            # Now we fix tubB. We only touched the Listener, so re-doing the
            # listenOn should clear it.
            return self.tubB.stopListeningOn(l)
        d.addCallback(_failed_a_few_times)
        def _stopped(res):
            self.tubB.listenOn("tcp:%d:interface=127.0.0.1" % portb)
            # the next time the reconnector tries, it should succeed
            return d1
        d.addCallback(_stopped)
        def _connected(res):
            self.failUnlessEqual(len(connects), 1)
            self.rc.stopConnecting()
        d.addCallback(_connected)
        return d

    def test_lose_and_retry(self):
        tubC = UnauthenticatedTub()
        connects = []
        d1 = defer.Deferred()
        d2 = defer.Deferred()
        notifiers = [d1, d2]
        target = HelperTarget("bob")
        url = self.tubB.registerReference(target, "target")
        portb = self.tubB.getListeners()[0].getPortnum()
        self.rc = self.tubA.connectTo(url, self._connected,
                                      notifiers, connects)
        def _connected_first(res):
            # we are now connected to tubB. Shut it down to force a
            # disconnect.
            self.services.remove(self.tubB)
            d = self.tubB.stopService()
            return d
        d1.addCallback(_connected_first)
        def _wait(res):
            # wait a few seconds to give the Reconnector a chance to try and
            # fail a few times
            return self.stall(2)
        d1.addCallback(_wait)
        def _start_tubC(res):
            # now start tubC listening on the same port that tubB used to,
            # which should allow the connection to complete (since they're
            # both UnauthenticatedTubs)
            self.services.append(tubC)
            tubC.startService()
            tubC.listenOn("tcp:%d:interface=127.0.0.1" % portb)
            tubC.setLocation("127.0.0.1:%d" % portb)
            url2 = tubC.registerReference(target, "target")
            assert url2 == url
            # this will fire when the second connection has been made
            return d2
        d1.addCallback(_start_tubC)
        def _connected(res):
            self.failUnlessEqual(len(connects), 2)
            self.rc.stopConnecting()
        d1.addCallback(_connected)
        return d1

    def test_stop_trying(self):
        connects = []
        target = HelperTarget("bob")
        url = self.tubB.registerReference(target, "target")
        d1 = defer.Deferred()
        self.services.remove(self.tubB)
        d = self.tubB.stopService()
        def _start_connecting(res):
            # this will fail, since tubB is not listening anymore
            self.rc = self.tubA.connectTo(url, self._connected, d1, connects)
            self.rc.verbose = True # get better code coverage
            # give it a few tries, then tell it to stop trying
            return self.stall(2)
        d.addCallback(_start_connecting)
        def _stop_trying(res):
            self.failUnlessEqual(len(connects), 0)
            # this stopConnecting occurs while the reconnector's timer is
            # active
            self.rc.stopConnecting()
        d.addCallback(_stop_trying)
        # if it keeps trying, we'll see a dirty reactor
        return d

# another test: determine the target url early, but don't actually register
# the reference yet. Start the reconnector, let it fail once, then register
# the reference and make sure the retry succeeds. This will distinguish
# between connection/negotiation failures and object-lookup failures, both of
# which ought to be handled by Reconnector. I suspect the object-lookup
# failures are not yet.

# test that Tub shutdown really stops all Reconnectors
