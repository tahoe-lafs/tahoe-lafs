"""
Tests for allmydata.util.deferredutil.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python.failure import Failure

from allmydata.util import deferredutil


class DeferredUtilTests(unittest.TestCase, deferredutil.WaitForDelayedCallsMixin):
    def test_gather_results(self):
        d1 = defer.Deferred()
        d2 = defer.Deferred()
        res = deferredutil.gatherResults([d1, d2])
        d1.errback(ValueError("BAD"))
        def _callb(res):
            self.fail("Should have errbacked, not resulted in %s" % (res,))
        def _errb(thef):
            thef.trap(ValueError)
        res.addCallbacks(_callb, _errb)
        return res

    def test_success(self):
        d1, d2 = defer.Deferred(), defer.Deferred()
        good = []
        bad = []
        dlss = deferredutil.DeferredListShouldSucceed([d1,d2])
        dlss.addCallbacks(good.append, bad.append)
        d1.callback(1)
        d2.callback(2)
        self.failUnlessEqual(good, [[1,2]])
        self.failUnlessEqual(bad, [])

    def test_failure(self):
        d1, d2 = defer.Deferred(), defer.Deferred()
        good = []
        bad = []
        dlss = deferredutil.DeferredListShouldSucceed([d1,d2])
        dlss.addCallbacks(good.append, bad.append)
        d1.addErrback(lambda _ignore: None)
        d2.addErrback(lambda _ignore: None)
        d1.callback(1)
        d2.errback(ValueError())
        self.failUnlessEqual(good, [])
        self.failUnlessEqual(len(bad), 1)
        f = bad[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(ValueError))

    def test_wait_for_delayed_calls(self):
        """
        This tests that 'wait_for_delayed_calls' does in fact wait for a
        delayed call that is active when the test returns. If it didn't,
        Trial would report an unclean reactor error for this test.
        """
        def _trigger():
            #print("trigger")
            pass
        reactor.callLater(0.1, _trigger)

        d = defer.succeed(None)
        d.addBoth(self.wait_for_delayed_calls)
        return d


class UntilTests(unittest.TestCase):
    """
    Tests for ``deferredutil.until``.
    """
    def test_exception(self):
        """
        If the action raises an exception, the ``Deferred`` returned by ``until``
        fires with a ``Failure``.
        """
        self.assertFailure(
            deferredutil.until(lambda: 1/0, lambda: True),
            ZeroDivisionError,
        )

    def test_stops_on_condition(self):
        """
        The action is called repeatedly until ``condition`` returns ``True``.
        """
        calls = []
        def action():
            calls.append(None)

        def condition():
            return len(calls) == 3

        self.assertIs(
            self.successResultOf(
                deferredutil.until(action, condition),
            ),
            None,
        )
        self.assertEqual(3, len(calls))

    def test_waits_for_deferred(self):
        """
        If the action returns a ``Deferred`` then it is called again when the
        ``Deferred`` fires.
        """
        counter = [0]
        r1 = defer.Deferred()
        r2 = defer.Deferred()
        results = [r1, r2]
        def action():
            counter[0] += 1
            return results.pop(0)

        def condition():
            return False

        deferredutil.until(action, condition)
        self.assertEqual([1], counter)
        r1.callback(None)
        self.assertEqual([2], counter)
