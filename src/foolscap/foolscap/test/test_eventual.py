
from twisted.trial import unittest

from foolscap.eventual import eventually, fireEventually, flushEventualQueue

class TestEventual(unittest.TestCase):

    def tearDown(self):
        return flushEventualQueue()

    def testSend(self):
        results = []
        eventually(results.append, 1)
        self.failIf(results)
        def _check():
            self.failUnlessEqual(results, [1])
        eventually(_check)
        def _check2():
            self.failUnlessEqual(results, [1,2])
        eventually(results.append, 2)
        eventually(_check2)

    def testFlush(self):
        results = []
        eventually(results.append, 1)
        eventually(results.append, 2)
        d = flushEventualQueue()
        def _check(res):
            self.failUnlessEqual(results, [1,2])
        d.addCallback(_check)
        return d

    def testFire(self):
        results = []
        fireEventually(1).addCallback(results.append)
        fireEventually(2).addCallback(results.append)
        self.failIf(results)
        def _check(res):
            self.failUnlessEqual(results, [1,2])
        d = flushEventualQueue()
        d.addCallback(_check)
        return d
