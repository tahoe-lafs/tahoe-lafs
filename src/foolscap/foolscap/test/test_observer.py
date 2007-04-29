# -*- test-case-name: foolscap.test_observer -*-

from twisted.trial import unittest
from twisted.internet import defer
from foolscap import observer

class Observer(unittest.TestCase):
    def test_oneshot(self):
        ol = observer.OneShotObserverList()
        rep = repr(ol)
        d1 = ol.whenFired()
        d2 = ol.whenFired()
        def _addmore(res):
            self.failUnlessEqual(res, "result")
            d3 = ol.whenFired()
            d3.addCallback(self.failUnlessEqual, "result")
            return d3
        d1.addCallback(_addmore)
        ol.fire("result")
        rep = repr(ol)
        d4 = ol.whenFired()
        dl = defer.DeferredList([d1,d2,d4])
        return dl
