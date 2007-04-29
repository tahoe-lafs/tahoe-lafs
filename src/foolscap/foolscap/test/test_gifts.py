
from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet.error import ConnectionDone, ConnectionLost
from foolscap import Tub, UnauthenticatedTub
from foolscap.referenceable import RemoteReference
from foolscap.test.common import HelperTarget
from foolscap.eventual import flushEventualQueue

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

def ignoreConnectionDone(f):
    f.trap(ConnectionDone, ConnectionLost)
    return None

class Gifts(unittest.TestCase):
    # Here we test the three-party introduction process as depicted in the
    # classic Granovetter diagram. Alice has a reference to Bob and another
    # one to Carol. Alice wants to give her Carol-reference to Bob, by
    # including it as the argument to a method she invokes on her
    # Bob-reference.

    debug = False

    def setUp(self):
        self.services = [GoodEnoughTub(), GoodEnoughTub(), GoodEnoughTub()]
        self.tubA, self.tubB, self.tubC = self.services
        for s in self.services:
            s.startService()
            l = s.listenOn("tcp:0:interface=127.0.0.1")
            s.setLocation("127.0.0.1:%d" % l.getPortnum())

    def tearDown(self):
        d = defer.DeferredList([s.stopService() for s in self.services])
        d.addCallback(flushEventualQueue)
        return d

    def createCharacters(self):
        self.alice = HelperTarget("alice")
        self.bob = HelperTarget("bob")
        self.bob_url = self.tubB.registerReference(self.bob)
        self.carol = HelperTarget("carol")
        self.carol_url = self.tubC.registerReference(self.carol)
        self.cindy = HelperTarget("cindy")
        # cindy is Carol's little sister. She doesn't have a phone, but
        # Carol might talk about her anyway.

    def createInitialReferences(self):
        # we must start by giving Alice a reference to both Bob and Carol.
        if self.debug: print "Alice gets Bob"
        d = self.tubA.getReference(self.bob_url)
        def _aliceGotBob(abob):
            if self.debug: print "Alice got bob"
            self.abob = abob # Alice's reference to Bob
            if self.debug: print "Alice gets carol"
            d = self.tubA.getReference(self.carol_url)
            return d
        d.addCallback(_aliceGotBob)
        def _aliceGotCarol(acarol):
            if self.debug: print "Alice got carol"
            self.acarol = acarol # Alice's reference to Carol
        d.addCallback(_aliceGotCarol)
        return d

    def testGift(self):
        #defer.setDebugging(True)
        self.createCharacters()
        d = self.createInitialReferences()
        def _introduce(res):
            d2 = self.bob.waitfor()
            if self.debug: print "Alice introduces Carol to Bob"
            # send the gift. This might not get acked by the time the test is
            # done and everything is torn down, so explicitly silence any
            # ConnectionDone error that might result. When we get
            # callRemoteOnly(), use that instead.
            d3 = self.abob.callRemote("set", obj=(self.alice, self.acarol))
            d3.addErrback(ignoreConnectionDone)
            return d2 # this fires with the gift that bob got
        d.addCallback(_introduce)
        def _bobGotCarol((balice,bcarol)):
            if self.debug: print "Bob got Carol"
            self.bcarol = bcarol
            if self.debug: print "Bob says something to Carol"
            d2 = self.carol.waitfor()
            # handle ConnectionDone as described before
            d3 = self.bcarol.callRemote("set", obj=12)
            d3.addErrback(ignoreConnectionDone)
            return d2
        d.addCallback(_bobGotCarol)
        def _carolCalled(res):
            if self.debug: print "Carol heard from Bob"
            self.failUnlessEqual(res, 12)
        d.addCallback(_carolCalled)
        return d


    def testImplicitGift(self):
        # in this test, Carol was registered in her Tub (using
        # registerReference), but Cindy was not. Alice is given a reference
        # to Carol, then uses that to get a reference to Cindy. Then Alice
        # sends a message to Bob and includes a reference to Cindy. The test
        # here is that we can make gifts out of references that were not
        # passed to registerReference explicitly.

        #defer.setDebugging(True)
        self.createCharacters()
        # the message from Alice to Bob will include a reference to Cindy
        d = self.createInitialReferences()
        def _tell_alice_about_cindy(res):
            self.carol.obj = self.cindy
            cindy_d = self.acarol.callRemote("get")
            return cindy_d
        d.addCallback(_tell_alice_about_cindy)
        def _introduce(a_cindy):
            # alice now has references to carol (self.acarol) and cindy
            # (a_cindy). She sends both of them (plus a reference to herself)
            # to bob.
            d2 = self.bob.waitfor()
            if self.debug: print "Alice introduces Carol to Bob"
            # send the gift. This might not get acked by the time the test is
            # done and everything is torn down, so explicitly silence any
            # ConnectionDone error that might result. When we get
            # callRemoteOnly(), use that instead.
            d3 = self.abob.callRemote("set", obj=(self.alice,
                                                  self.acarol,
                                                  a_cindy))
            d3.addErrback(ignoreConnectionDone)
            return d2 # this fires with the gift that bob got
        d.addCallback(_introduce)
        def _bobGotCarol((b_alice,b_carol,b_cindy)):
            if self.debug: print "Bob got Carol"
            self.failUnless(b_alice)
            self.failUnless(b_carol)
            self.failUnless(b_cindy)
            self.bcarol = b_carol
            if self.debug: print "Bob says something to Carol"
            d2 = self.carol.waitfor()
            if self.debug: print "Bob says something to Cindy"
            d3 = self.cindy.waitfor()

            # handle ConnectionDone as described before
            d4 = b_carol.callRemote("set", obj=4)
            d4.addErrback(ignoreConnectionDone)
            d5 = b_cindy.callRemote("set", obj=5)
            d5.addErrback(ignoreConnectionDone)
            return defer.DeferredList([d2,d3])
        d.addCallback(_bobGotCarol)
        def _carolAndCindyCalled(res):
            if self.debug: print "Carol heard from Bob"
            ((carol_s, carol_result), (cindy_s, cindy_result)) = res
            self.failUnless(carol_s)
            self.failUnless(cindy_s)
            self.failUnlessEqual(carol_result, 4)
            self.failUnlessEqual(cindy_result, 5)
        d.addCallback(_carolAndCindyCalled)
        return d


    def testOrdering(self):
        self.createCharacters()
        self.bob.calls = []
        d = self.createInitialReferences()
        def _introduce(res):
            # we send three messages to Bob. The second one contains the
            # reference to Carol.
            dl = []
            dl.append(self.abob.callRemote("append", obj=1))
            dl.append(self.abob.callRemote("append", obj=self.acarol))
            dl.append(self.abob.callRemote("append", obj=3))
            return defer.DeferredList(dl)
        d.addCallback(_introduce)
        def _checkBob(res):
            # this runs after all three messages have been acked by Bob
            self.failUnlessEqual(len(self.bob.calls), 3)
            self.failUnlessEqual(self.bob.calls[0], 1)
            self.failUnless(isinstance(self.bob.calls[1], RemoteReference))
            self.failUnlessEqual(self.bob.calls[2], 3)
        d.addCallback(_checkBob)
        return d

    # this was used to alice's reference to carol (self.acarol) appeared in
    # alice's gift table at the right time, to make sure that the
    # RemoteReference is kept alive while the gift is in transit. The whole
    # introduction pattern is going to change soon, so it has been disabled
    # until I figure out what the new scheme ought to be asserting.

    def OFF_bobGotCarol(self, (balice,bcarol)):
        if self.debug: print "Bob got Carol"
        # Bob has received the gift
        self.bcarol = bcarol

        # wait for alice to receive bob's 'decgift' sequence, which was sent
        # by now (it is sent after bob receives the gift but before the
        # gift-bearing message is delivered). To make sure alice has received
        # it, send a message back along the same path.
        def _check_alice(res):
            if self.debug: print "Alice should have the decgift"
            # alice's gift table should be empty
            brokerAB = self.abob.tracker.broker
            self.failUnlessEqual(brokerAB.myGifts, {})
            self.failUnlessEqual(brokerAB.myGiftsByGiftID, {})
        d1 = self.alice.waitfor()
        d1.addCallback(_check_alice)
        # the ack from this message doesn't always make it back by the time
        # we end the test and hang up the connection. That connectionLost
        # causes the deferred that this returns to errback, triggering an
        # error, so we must be sure to discard any error from it. TODO: turn
        # this into balice.callRemoteOnly("set", 39), which will have the
        # same semantics from our point of view (but in addition it will tell
        # the recipient to not bother sending a response).
        balice.callRemote("set", 39).addErrback(lambda ignored: None)

        if self.debug: print "Bob says something to Carol"
        d2 = self.carol.waitfor()
        d = self.bcarol.callRemote("set", obj=12)
        d.addCallback(lambda res: d2)
        d.addCallback(self._carolCalled)
        d.addCallback(lambda res: d1)
        return d

