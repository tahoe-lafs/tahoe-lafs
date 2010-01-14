
from twisted.trial import unittest
from twisted.internet import defer, reactor
from allmydata.util import observer

def nextTurn(res=None):
    d = defer.Deferred()
    reactor.callLater(1, d.callback, res)
    return d

class Observer(unittest.TestCase):
    def test_oneshot(self):
        ol = observer.OneShotObserverList()
        rep = repr(ol)
        self.failUnlessEqual(rep, "<OneShotObserverList [[]]>")
        d1 = ol.when_fired()
        d2 = ol.when_fired()
        def _addmore(res):
            self.failUnlessEqual(res, "result")
            d3 = ol.when_fired()
            d3.addCallback(self.failUnlessEqual, "result")
            return d3
        d1.addCallback(_addmore)
        ol.fire("result")
        rep = repr(ol)
        self.failUnlessEqual(rep, "<OneShotObserverList -> result>")
        d4 = ol.when_fired()
        dl = defer.DeferredList([d1,d2,d4])
        return dl

    def test_oneshot_fireagain(self):
        ol = observer.OneShotObserverList()
        d = ol.when_fired()
        def _addmore(res):
            self.failUnlessEqual(res, "result")
            ol.fire_if_not_fired("result3") # should be ignored
            d2 = ol.when_fired()
            d2.addCallback(self.failUnlessEqual, "result")
            return d2
        d.addCallback(_addmore)
        ol.fire_if_not_fired("result")
        ol.fire_if_not_fired("result2")
        return d

    def test_lazy_oneshot(self):
        ol = observer.LazyOneShotObserverList()
        d1 = ol.when_fired()
        d2 = ol.when_fired()
        def _addmore(res):
            self.failUnlessEqual(res, "result")
            d3 = ol.when_fired()
            d3.addCallback(self.failUnlessEqual, "result")
            return d3
        d1.addCallback(_addmore)
        def _get_result():
            return "result"
        ol.fire(_get_result)
        d4 = ol.when_fired()
        dl = defer.DeferredList([d1,d2,d4])
        return dl

    def test_observerlist(self):
        ol = observer.ObserverList()
        l1 = []
        l2 = []
        l3 = []
        ol.subscribe(l1.append)
        ol.notify(1)
        ol.subscribe(l2.append)
        ol.notify(2)
        ol.unsubscribe(l1.append)
        ol.notify(3)
        def _check(res):
            self.failUnlessEqual(l1, [1,2])
            self.failUnlessEqual(l2, [2,3])
        d = nextTurn()
        d.addCallback(_check)
        def _step2(res):
            def _add(a, b, c=None):
                l3.append((a,b,c))
            ol.unsubscribe(l2.append)
            ol.subscribe(_add)
            ol.notify(4, 5, c=6)
            return nextTurn()
        def _check2(res):
            self.failUnlessEqual(l3, [(4,5,6)])
        d.addCallback(_step2)
        d.addCallback(_check2)
        return d
