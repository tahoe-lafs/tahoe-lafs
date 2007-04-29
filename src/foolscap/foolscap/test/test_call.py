
import gc
import re
import sets

if False:
    import sys
    from twisted.python import log
    log.startLogging(sys.stderr)

from twisted.python import failure
from twisted.internet import reactor, defer
from twisted.trial import unittest
from twisted.internet.main import CONNECTION_LOST

from foolscap.tokens import Violation
from foolscap.eventual import flushEventualQueue
from foolscap.test.common import HelperTarget, TargetMixin
from foolscap.test.common import RIMyTarget, Target, TargetWithoutInterfaces, \
     BrokenTarget

class Unsendable:
    pass


class TestCall(TargetMixin, unittest.TestCase):
    def setUp(self):
        TargetMixin.setUp(self)
        self.setupBrokers()

    def testCall1(self):
        # this is done without interfaces
        rr, target = self.setupTarget(TargetWithoutInterfaces())
        d = rr.callRemote("add", a=1, b=2)
        d.addCallback(lambda res: self.failUnlessEqual(res, 3))
        d.addCallback(lambda res: self.failUnlessEqual(target.calls, [(1,2)]))
        d.addCallback(self._testCall1_1, rr)
        return d
    testCall1.timeout = 3
    def _testCall1_1(self, res, rr):
        # the caller still holds the RemoteReference
        self.failUnless(self.callingBroker.yourReferenceByCLID.has_key(1))

        # release the RemoteReference. This does two things: 1) the
        # callingBroker will forget about it. 2) they will send a decref to
        # the targetBroker so *they* can forget about it.
        del rr # this fires a DecRef
        gc.collect() # make sure
        # we need to give it a moment to deliver the DecRef message and act
        # on it
        d = defer.Deferred()
        reactor.callLater(0.1, d.callback, None)
        d.addCallback(self._testCall1_2)
        return d
    def _testCall1_2(self, res):
        self.failIf(self.callingBroker.yourReferenceByCLID.has_key(1))
        self.failIf(self.targetBroker.myReferenceByCLID.has_key(1))

    def testCall1a(self):
        # no interfaces, but use positional args
        rr, target = self.setupTarget(TargetWithoutInterfaces())
        d = rr.callRemote("add", 1, 2)
        d.addCallback(lambda res: self.failUnlessEqual(res, 3))
        d.addCallback(lambda res: self.failUnlessEqual(target.calls, [(1,2)]))
        return d
    testCall1a.timeout = 2

    def testCall1b(self):
        # no interfaces, use both positional and keyword args
        rr, target = self.setupTarget(TargetWithoutInterfaces())
        d = rr.callRemote("add", 1, b=2)
        d.addCallback(lambda res: self.failUnlessEqual(res, 3))
        d.addCallback(lambda res: self.failUnlessEqual(target.calls, [(1,2)]))
        return d
    testCall1b.timeout = 2

    def testFail1(self):
        # this is done without interfaces
        rr, target = self.setupTarget(TargetWithoutInterfaces())
        d = rr.callRemote("fail")
        self.failIf(target.calls)
        d.addBoth(self._testFail1_1)
        return d
    testFail1.timeout = 2
    def _testFail1_1(self, f):
        # f should be a CopiedFailure
        self.failUnless(isinstance(f, failure.Failure),
                        "Hey, we didn't fail: %s" % f)
        self.failUnless(f.check(ValueError),
                        "wrong exception type: %s" % f)
        self.failUnlessSubstring("you asked me to fail", f.value)

    def testFail2(self):
        # this is done without interfaces
        rr, target = self.setupTarget(TargetWithoutInterfaces())
        d = rr.callRemote("add", a=1, b=2, c=3)
        # add() does not take a 'c' argument, so we get a TypeError here
        self.failIf(target.calls)
        d.addBoth(self._testFail2_1)
        return d
    testFail2.timeout = 2
    def _testFail2_1(self, f):
        self.failUnless(isinstance(f, failure.Failure),
                        "Hey, we didn't fail: %s" % f)
        self.failUnless(f.check(TypeError),
                        "wrong exception type: %s" % f.type)
        self.failUnlessSubstring("remote_add() got an unexpected keyword "
                                 "argument 'c'", f.value)

    def testFail3(self):
        # this is done without interfaces
        rr, target = self.setupTarget(TargetWithoutInterfaces())
        d = rr.callRemote("bogus", a=1, b=2)
        # the target does not have .bogus method, so we get an AttributeError
        self.failIf(target.calls)
        d.addBoth(self._testFail3_1)
        return d
    testFail3.timeout = 2
    def _testFail3_1(self, f):
        self.failUnless(isinstance(f, failure.Failure),
                        "Hey, we didn't fail: %s" % f)
        self.failUnless(f.check(AttributeError),
                        "wrong exception type: %s" % f.type)
        self.failUnlessSubstring("TargetWithoutInterfaces", str(f))
        self.failUnlessSubstring(" has no attribute 'remote_bogus'", str(f))

    def testCall2(self):
        # server end uses an interface this time, but not the client end
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote("add", a=3, b=4, _useSchema=False)
        # the schema is enforced upon receipt
        d.addCallback(lambda res: self.failUnlessEqual(res, 7))
        return d
    testCall2.timeout = 2

    def testCall3(self):
        # use interface on both sides
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote('add', 3, 4) # enforces schemas
        d.addCallback(lambda res: self.failUnlessEqual(res, 7))
        return d
    testCall3.timeout = 2

    def testCall4(self):
        # call through a manually-defined RemoteMethodSchema
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote("add", 3, 4, _methodConstraint=RIMyTarget['add1'])
        d.addCallback(lambda res: self.failUnlessEqual(res, 7))
        return d
    testCall4.timeout = 2

    def testMegaSchema(self):
        # try to exercise all our constraints at once
        rr, target = self.setupTarget(HelperTarget())
        t = (sets.Set([1, 2, 3]),
             "str", True, 12, 12L, 19.3, None,
             "any", 14.3,
             15,
             "a"*95,
             "1234567890",
              )
        obj1 = {"key": [t]}
        obj2 = (sets.Set([1,2,3]), [1,2,3], {1:"two"})
        d = rr.callRemote("megaschema", obj1, obj2)
        d.addCallback(lambda res: self.failUnlessEqual(res, None))
        return d

    def testUnconstrainedMethod(self):
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote('free', 3, 4, x="boo")
        def _check(res):
            self.failUnlessEqual(res, "bird")
            self.failUnlessEqual(target.calls, [((3,4), {"x": "boo"})])
        d.addCallback(_check)
        return d

    def testFailWrongMethodLocal(self):
        # the caller knows that this method does not really exist
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote("bogus") # RIMyTarget doesn't implement .bogus()
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testFailWrongMethodLocal_1)
        return d
    testFailWrongMethodLocal.timeout = 2
    def _testFailWrongMethodLocal_1(self, f):
        self.failUnless(f.check(Violation))
        self.failUnless(re.search(r'RIMyTarget\(.*\) does not offer bogus',
                                  str(f)))

    def testFailWrongMethodRemote(self):
        # if the target doesn't specify any remote interfaces, then the
        # calling side shouldn't try to do any checking. The problem is
        # caught on the target side.
        rr, target = self.setupTarget(Target(), False)
        d = rr.callRemote("bogus") # RIMyTarget doesn't implement .bogus()
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testFailWrongMethodRemote_1)
        return d
    testFailWrongMethodRemote.timeout = 2
    def _testFailWrongMethodRemote_1(self, f):
        self.failUnless(f.check(Violation))
        self.failUnlessSubstring("method 'bogus' not defined in RIMyTarget",
                                 str(f))

    def testFailWrongMethodRemote2(self):
        # call a method which doesn't actually exist. The sender thinks
        # they're ok but the recipient catches the violation
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote("bogus", _useSchema=False)
        # RIMyTarget2 has a 'sub' method, but RIMyTarget (the real interface)
        # does not
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testFailWrongMethodRemote2_1)
        d.addCallback(lambda res: self.failIf(target.calls))
        return d
    testFailWrongMethodRemote2.timeout = 2
    def _testFailWrongMethodRemote2_1(self, f):
        self.failUnless(f.check(Violation))
        self.failUnless(re.search(r'RIMyTarget\(.*\) does not offer bogus',
                                  str(f)))

    def testFailWrongArgsLocal1(self):
        # we violate the interface (extra arg), and the sender should catch it
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote("add", a=1, b=2, c=3)
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testFailWrongArgsLocal1_1)
        d.addCallback(lambda res: self.failIf(target.calls))
        return d
    testFailWrongArgsLocal1.timeout = 2
    def _testFailWrongArgsLocal1_1(self, f):
        self.failUnless(f.check(Violation))
        self.failUnlessSubstring("unknown argument 'c'", str(f.value))

    def testFailWrongArgsLocal2(self):
        # we violate the interface (bad arg), and the sender should catch it
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote("add", a=1, b="two")
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testFailWrongArgsLocal2_1)
        d.addCallback(lambda res: self.failIf(target.calls))
        return d
    testFailWrongArgsLocal2.timeout = 2
    def _testFailWrongArgsLocal2_1(self, f):
        self.failUnless(f.check(Violation))
        self.failUnlessSubstring("not a number", str(f.value))

    def testFailWrongArgsRemote1(self):
        # the sender thinks they're ok but the recipient catches the
        # violation
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote("add", a=1, b="foo", _useSchema=False)
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testFailWrongArgsRemote1_1)
        d.addCallbacks(lambda res: self.failIf(target.calls))
        return d
    testFailWrongArgsRemote1.timeout = 2
    def _testFailWrongArgsRemote1_1(self, f):
        self.failUnless(f.check(Violation))
        self.failUnlessSubstring("STRING token rejected by IntegerConstraint",
                                 f.value)
        self.failUnlessSubstring("<RootUnslicer>.<methodcall", f.value)
        self.failUnlessSubstring(" methodname=add", f.value)
        self.failUnlessSubstring("<arguments arg[b]>", f.value)

    def testFailWrongReturnRemote(self):
        rr, target = self.setupTarget(BrokenTarget(), True)
        d = rr.callRemote("add", 3, 4) # violates return constraint
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testFailWrongReturnRemote_1)
        return d
    testFailWrongReturnRemote.timeout = 2
    def _testFailWrongReturnRemote_1(self, f):
        self.failUnless(f.check(Violation))
        self.failUnlessSubstring("in return value of <foolscap.test.common.BrokenTarget object at ", f.value)
        self.failUnlessSubstring(">.add", f.value)
        self.failUnlessSubstring("not a number", f.value)

    def testFailWrongReturnLocal(self):
        # the target returns a value which violates our _resultConstraint
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote("add", a=1, b=2, _resultConstraint=str)
        # The target returns an int, which matches the schema they're using,
        # so they think they're ok. We've overridden our expectations to
        # require a string.
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testFailWrongReturnLocal_1)
        # the method should have been run
        d.addCallback(lambda res: self.failUnless(target.calls))
        return d
    testFailWrongReturnLocal.timeout = 2
    def _testFailWrongReturnLocal_1(self, f):
        self.failUnless(f.check(Violation))
        self.failUnlessSubstring("INT token rejected by StringConstraint",
                                 str(f))
        self.failUnlessSubstring("in inbound method results", str(f))
        self.failUnlessSubstring("<RootUnslicer>.Answer(req=1)", str(f))



    def testDefer(self):
        rr, target = self.setupTarget(HelperTarget())
        d = rr.callRemote("defer", obj=12)
        d.addCallback(lambda res: self.failUnlessEqual(res, 12))
        return d
    testDefer.timeout = 2

    def testDisconnect1(self):
        rr, target = self.setupTarget(HelperTarget())
        d = rr.callRemote("hang")
        e = RuntimeError("lost connection")
        rr.tracker.broker.transport.loseConnection(e)
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       lambda why: why.trap(RuntimeError) and None)
        return d
    testDisconnect1.timeout = 2

    def disconnected(self, *args, **kwargs):
        self.lost = 1
        self.lost_args = (args, kwargs)

    def testDisconnect2(self):
        rr, target = self.setupTarget(HelperTarget())
        self.lost = 0
        rr.notifyOnDisconnect(self.disconnected)
        rr.tracker.broker.transport.loseConnection(CONNECTION_LOST)
        d = flushEventualQueue()
        def _check(res):
            self.failUnless(self.lost)
            self.failUnlessEqual(self.lost_args, ((),{}))
        d.addCallback(_check)
        return d

    def testDisconnect3(self):
        rr, target = self.setupTarget(HelperTarget())
        self.lost = 0
        m = rr.notifyOnDisconnect(self.disconnected)
        rr.dontNotifyOnDisconnect(m)
        rr.tracker.broker.transport.loseConnection(CONNECTION_LOST)
        d = flushEventualQueue()
        d.addCallback(lambda res: self.failIf(self.lost))
        return d

    def testDisconnect4(self):
        rr, target = self.setupTarget(HelperTarget())
        self.lost = 0
        rr.notifyOnDisconnect(self.disconnected, "arg", foo="kwarg")
        rr.tracker.broker.transport.loseConnection(CONNECTION_LOST)
        d = flushEventualQueue()
        def _check(res):
            self.failUnless(self.lost)
            self.failUnlessEqual(self.lost_args, (("arg",),
                                                  {"foo": "kwarg"}))
        d.addCallback(_check)
        return d

    def testUnsendable(self):
        rr, target = self.setupTarget(HelperTarget())
        d = rr.callRemote("set", obj=Unsendable())
        d.addCallbacks(lambda res: self.fail("should have failed"),
                       self._testUnsendable_1)
        return d
    testUnsendable.timeout = 2
    def _testUnsendable_1(self, why):
        self.failUnless(why.check(Violation))
        self.failUnlessSubstring("cannot serialize", why.value.args[0])


class TestCallOnly(TargetMixin, unittest.TestCase):
    def setUp(self):
        TargetMixin.setUp(self)
        self.setupBrokers()

    def testCallOnly(self):
        rr, target = self.setupTarget(TargetWithoutInterfaces())
        ret = rr.callRemoteOnly("add", a=1, b=2)
        self.failUnlessIdentical(ret, None)
        # since we don't have a Deferred to wait upon, we just have to poll
        # for the call to take place. It should happen pretty quickly.
        def _check():
            if target.calls:
                self.failUnlessEqual(target.calls, [(1,2)])
                return True
            return False
        d = self.poll(_check)
        return d
    testCallOnly.timeout = 2
