# -*- test-case-name: foolscap.test.test_interfaces -*-

from zope.interface import implementsOnly
from twisted.trial import unittest

from foolscap import schema, remoteinterface
from foolscap import RemoteInterface
from foolscap.remoteinterface import getRemoteInterface, RemoteMethodSchema
from foolscap.remoteinterface import RemoteInterfaceRegistry
from foolscap.tokens import Violation
from foolscap.referenceable import RemoteReference

from foolscap.test.common import TargetMixin
from foolscap.test.common import getRemoteInterfaceName, Target, RIMyTarget, \
     RIMyTarget2, TargetWithoutInterfaces, IFoo, Foo, TypesTarget, RIDummy, \
     DummyTarget


class Target2(Target):
    implementsOnly(IFoo, RIMyTarget2)

class TestInterface(TargetMixin, unittest.TestCase):

    def testTypes(self):
        self.failUnless(isinstance(RIMyTarget,
                                   remoteinterface.RemoteInterfaceClass))
        self.failUnless(isinstance(RIMyTarget2,
                                   remoteinterface.RemoteInterfaceClass))

    def testRegister(self):
        reg = RemoteInterfaceRegistry
        self.failUnlessEqual(reg["RIMyTarget"], RIMyTarget)
        self.failUnlessEqual(reg["RIMyTargetInterface2"], RIMyTarget2)

    def testDuplicateRegistry(self):
        try:
            class RIMyTarget(RemoteInterface):
                def foo(bar=int): return int
        except remoteinterface.DuplicateRemoteInterfaceError:
            pass
        else:
            self.fail("duplicate registration not caught")

    def testInterface1(self):
        # verify that we extract the right interfaces from a local object.
        # also check that the registry stuff works.
        self.setupBrokers()
        rr, target = self.setupTarget(Target())
        iface = getRemoteInterface(target)
        self.failUnlessEqual(iface, RIMyTarget)
        iname = getRemoteInterfaceName(target)
        self.failUnlessEqual(iname, "RIMyTarget")
        self.failUnlessIdentical(RemoteInterfaceRegistry["RIMyTarget"],
                                 RIMyTarget)
        
        rr, target = self.setupTarget(Target2())
        iname = getRemoteInterfaceName(target)
        self.failUnlessEqual(iname, "RIMyTargetInterface2")
        self.failUnlessIdentical(\
            RemoteInterfaceRegistry["RIMyTargetInterface2"], RIMyTarget2)


    def testInterface2(self):
        # verify that RemoteInterfaces have the right attributes
        t = Target()
        iface = getRemoteInterface(t)
        self.failUnlessEqual(iface, RIMyTarget)

        # 'add' is defined with 'def'
        s1 = RIMyTarget['add']
        self.failUnless(isinstance(s1, RemoteMethodSchema))
        ok, s2 = s1.getKeywordArgConstraint("a")
        self.failUnless(ok)
        self.failUnless(isinstance(s2, schema.IntegerConstraint))
        self.failUnless(s2.checkObject(12, False) == None)
        self.failUnlessRaises(schema.Violation,
                              s2.checkObject, "string", False)
        s3 = s1.getResponseConstraint()
        self.failUnless(isinstance(s3, schema.IntegerConstraint))

        # 'add1' is defined as a class attribute
        s1 = RIMyTarget['add1']
        self.failUnless(isinstance(s1, RemoteMethodSchema))
        ok, s2 = s1.getKeywordArgConstraint("a")
        self.failUnless(ok)
        self.failUnless(isinstance(s2, schema.IntegerConstraint))
        self.failUnless(s2.checkObject(12, False) == None)
        self.failUnlessRaises(schema.Violation,
                              s2.checkObject, "string", False)
        s3 = s1.getResponseConstraint()
        self.failUnless(isinstance(s3, schema.IntegerConstraint))

        s1 = RIMyTarget['join']
        self.failUnless(isinstance(s1.getKeywordArgConstraint("a")[1],
                                   schema.StringConstraint))
        self.failUnless(isinstance(s1.getKeywordArgConstraint("c")[1],
                                   schema.IntegerConstraint))
        s3 = RIMyTarget['join'].getResponseConstraint()
        self.failUnless(isinstance(s3, schema.StringConstraint))

        s1 = RIMyTarget['disputed']
        self.failUnless(isinstance(s1.getKeywordArgConstraint("a")[1],
                                   schema.IntegerConstraint))
        s3 = s1.getResponseConstraint()
        self.failUnless(isinstance(s3, schema.IntegerConstraint))


    def testInterface3(self):
        t = TargetWithoutInterfaces()
        iface = getRemoteInterface(t)
        self.failIf(iface)

    def testStack(self):
        # when you violate your outbound schema, the Failure you get should
        # have a stack trace that includes the actual callRemote invocation
        self.setupBrokers()
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote('add', "not a number", "oops")
        def _check_failure(f):
            s = f.getTraceback().split("\n")
            for i in range(len(s)):
                line = s[i]
                #print line
                if ("test/test_interfaces.py" in line
                    and i+1 < len(s)
                    and "rr.callRemote" in s[i+1]):
                    return # all good
            print "failure looked like this:"
            print f
            self.fail("didn't see invocation of callRemote in stacktrace")
        d.addCallbacks(lambda res: self.fail("hey, this was supposed to fail"),
                       _check_failure)
        return d

class Types(TargetMixin, unittest.TestCase):
    def setUp(self):
        TargetMixin.setUp(self)
        self.setupBrokers()

    def deferredShouldFail(self, d, ftype=None, checker=None):
        if not ftype and not checker:
            d.addCallbacks(lambda res:
                           self.fail("hey, this was supposed to fail"),
                           lambda f: None)
        elif ftype and not checker:
            d.addCallbacks(lambda res:
                           self.fail("hey, this was supposed to fail"),
                           lambda f: f.trap(ftype) or None)
        else:
            d.addCallbacks(lambda res:
                           self.fail("hey, this was supposed to fail"),
                           checker)

    def testCall(self):
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote('add', 3, 4) # enforces schemas
        d.addCallback(lambda res: self.failUnlessEqual(res, 7))
        return d

    def testFail(self):
        # make sure exceptions (and thus CopiedFailures) pass a schema check
        rr, target = self.setupTarget(Target(), True)
        d = rr.callRemote('fail')
        self.deferredShouldFail(d, ftype=ValueError)
        return d

    def testNoneGood(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        d = rr.callRemote('returns_none', True)
        d.addCallback(lambda res: self.failUnlessEqual(res, None))
        return d

    def testNoneBad(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        d = rr.callRemote('returns_none', False)
        def _check_failure(f):
            f.trap(Violation)
            self.failUnlessIn("(in return value of <foolscap.test.common.TypesTarget object", str(f))
            self.failUnlessIn(">.returns_none", str(f))
            self.failUnlessIn("'not None' is not None", str(f))
        self.deferredShouldFail(d, checker=_check_failure)
        return d

    def testTakesRemoteInterfaceGood(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        d = rr.callRemote('takes_remoteinterface', DummyTarget())
        d.addCallback(lambda res: self.failUnlessEqual(res, "good"))
        return d

    def testTakesRemoteInterfaceBad(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        # takes_remoteinterface is specified to accept an RIDummy
        d = rr.callRemote('takes_remoteinterface', 12)
        def _check_failure(f):
            f.trap(Violation)
            self.failUnlessIn("RITypes.takes_remoteinterface(a=))", str(f))
            self.failUnlessIn("'12' is not a Referenceable", str(f))
        self.deferredShouldFail(d, checker=_check_failure)
        return d

    def testTakesRemoteInterfaceBad2(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        # takes_remoteinterface is specified to accept an RIDummy
        d = rr.callRemote('takes_remoteinterface', TypesTarget())
        def _check_failure(f):
            f.trap(Violation)
            self.failUnlessIn("RITypes.takes_remoteinterface(a=))", str(f))
            self.failUnlessIn(" does not provide RemoteInterface ", str(f))
            self.failUnlessIn("foolscap.test.common.RIDummy", str(f))
        self.deferredShouldFail(d, checker=_check_failure)
        return d

    def failUnlessRemoteProvides(self, obj, riface):
        # TODO: really, I want to just be able to say:
        #   self.failUnless(RIDummy.providedBy(res))
        iface = obj.tracker.interface
        # TODO: this test probably doesn't handle subclasses of
        # RemoteInterface, which might be useful (if it even works)
        if not iface or iface != riface:
            self.fail("%s does not provide RemoteInterface %s" % (obj, riface))

    def testReturnsRemoteInterfaceGood(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        d = rr.callRemote('returns_remoteinterface', 1)
        def _check(res):
            self.failUnless(isinstance(res, RemoteReference))
            #self.failUnless(RIDummy.providedBy(res))
            self.failUnlessRemoteProvides(res, RIDummy)
        d.addCallback(_check)
        return d

    def testReturnsRemoteInterfaceBad(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        # returns_remoteinterface is specified to return an RIDummy
        d = rr.callRemote('returns_remoteinterface', 0)
        def _check_failure(f):
            f.trap(Violation)
            self.failUnlessIn("(in return value of <foolscap.test.common.TypesTarget object at ", str(f))
            self.failUnlessIn(">.returns_remoteinterface)", str(f))
            self.failUnlessIn("'15' is not a Referenceable", str(f))
        self.deferredShouldFail(d, checker=_check_failure)
        return d

    def testReturnsRemoteInterfaceBad2(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        # returns_remoteinterface is specified to return an RIDummy
        d = rr.callRemote('returns_remoteinterface', -1)
        def _check_failure(f):
            f.trap(Violation)
            self.failUnlessIn("(in return value of <foolscap.test.common.TypesTarget object at ", str(f))
            self.failUnlessIn(">.returns_remoteinterface)", str(f))
            self.failUnlessIn("<foolscap.test.common.TypesTarget object ",
                              str(f))
            self.failUnlessIn(" does not provide RemoteInterface ", str(f))
            self.failUnlessIn("foolscap.test.common.RIDummy", str(f))
        self.deferredShouldFail(d, checker=_check_failure)
        return d

class LocalTypes(TargetMixin, unittest.TestCase):
    def setUp(self):
        TargetMixin.setUp(self)
        self.setupBrokers()

    def testTakesInterfaceGood(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        d = rr.callRemote('takes_interface', DummyTarget())
        d.addCallback(lambda res: self.failUnlessEqual(res, "good"))
        return d

    def testTakesInterfaceBad(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        d = rr.callRemote('takes_interface', Foo())
        def _check_failure(f):
            f.trap(Violation)
            print f
        self.deferredShouldFail(d, checker=_check_failure)
        return d

    def testReturnsInterfaceGood(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        d = rr.callRemote('returns_interface', True)
        def _check(res):
            #self.failUnless(isinstance(res, RemoteReference))
            self.failUnless(IFoo.providedBy(res))
        d.addCallback(_check)
        return d

    def testReturnsInterfaceBad(self):
        rr, target = self.setupTarget(TypesTarget(), True)
        d = rr.callRemote('returns_interface', False)
        def _check_failure(f):
            f.trap(Violation)
            print f
        self.deferredShouldFail(d, checker=_check_failure)
        return d

del LocalTypes # TODO: how could these tests possibly work? we need Guards.
