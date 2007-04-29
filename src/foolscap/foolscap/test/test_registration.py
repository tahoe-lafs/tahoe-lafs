# -*- test-case-name: foolscap.test.test_registration -*-

from twisted.trial import unittest

import weakref, gc
from foolscap import UnauthenticatedTub
from foolscap.test.common import HelperTarget

class Registration(unittest.TestCase):
    def testStrong(self):
        t1 = HelperTarget()
        tub = UnauthenticatedTub()
        tub.setLocation("bogus:1234567")
        u1 = tub.registerReference(t1)
        results = []
        w1 = weakref.ref(t1, results.append)
        del t1
        gc.collect()
        # t1 should still be alive
        self.failUnless(w1())
        self.failUnlessEqual(results, [])
        tub.unregisterReference(w1())
        gc.collect()
        # now it should be dead
        self.failIf(w1())
        self.failUnlessEqual(len(results), 1)

    def testWeak(self):
        t1 = HelperTarget()
        tub = UnauthenticatedTub()
        tub.setLocation("bogus:1234567")
        name = tub._assignName(t1)
        url = tub.buildURL(name)
        results = []
        w1 = weakref.ref(t1, results.append)
        del t1
        gc.collect()
        # t1 should be dead
        self.failIf(w1())
        self.failUnlessEqual(len(results), 1)

    def TODO_testNonweakrefable(self):
        # what happens when we register a non-Referenceable? We don't really
        # need this yet, but as registerReference() becomes more generalized
        # into just plain register(), we'll want to provide references to
        # Copyables and ordinary data structures too. Let's just test that
        # this doesn't cause an error.
        target = []
        tub = UnauthenticatedTub()
        tub.setLocation("bogus:1234567")
        url = tub.registerReference(target)

