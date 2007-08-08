
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import failure
from foolscap import util, eventual


class AsyncAND(unittest.TestCase):
    def setUp(self):
        self.fired = False
        self.failed = False

    def callback(self, res):
        self.fired = True
    def errback(self, res):
        self.failed = True

    def attach(self, d):
        d.addCallbacks(self.callback, self.errback)
        return d

    def shouldNotFire(self, ignored=None):
        self.failIf(self.fired)
        self.failIf(self.failed)
    def shouldFire(self, ignored=None):
        self.failUnless(self.fired)
        self.failIf(self.failed)
    def shouldFail(self, ignored=None):
        self.failUnless(self.failed)
        self.failIf(self.fired)

    def tearDown(self):
        return eventual.flushEventualQueue()

    def test_empty(self):
        self.attach(util.AsyncAND([]))
        self.shouldFire()

    def test_simple(self):
        d1 = eventual.fireEventually(None)
        a = util.AsyncAND([d1])
        self.attach(a)
        a.addBoth(self.shouldFire)
        return a

    def test_two(self):
        d1 = defer.Deferred()
        d2 = defer.Deferred()
        self.attach(util.AsyncAND([d1, d2]))
        self.shouldNotFire()
        d1.callback(1)
        self.shouldNotFire()
        d2.callback(2)
        self.shouldFire()

    def test_one_failure_1(self):
        d1 = defer.Deferred()
        d2 = defer.Deferred()
        self.attach(util.AsyncAND([d1, d2]))
        self.shouldNotFire()
        d1.callback(1)
        self.shouldNotFire()
        d2.errback(RuntimeError())
        self.shouldFail()

    def test_one_failure_2(self):
        d1 = defer.Deferred()
        d2 = defer.Deferred()
        self.attach(util.AsyncAND([d1, d2]))
        self.shouldNotFire()
        d1.errback(RuntimeError())
        self.shouldFail()
        d2.callback(1)
        self.shouldFail()

    def test_two_failure(self):
        d1 = defer.Deferred()
        d2 = defer.Deferred()
        self.attach(util.AsyncAND([d1, d2]))
        def _should_fire(res):
            self.failIf(isinstance(res, failure.Failure))
        def _should_fail(f):
            self.failUnless(isinstance(f, failure.Failure))
        d1.addBoth(_should_fire)
        d2.addBoth(_should_fail)
        self.shouldNotFire()
        d1.errback(RuntimeError())
        self.shouldFail()
        d2.errback(RuntimeError())
        self.shouldFail()

        
