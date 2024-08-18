"""
Tests for allmydata.util.deferredutil.
"""

from __future__ import annotations

from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.internet.defer import Deferred
from twisted.python.failure import Failure
from hypothesis.strategies import integers
from hypothesis import given

from allmydata.util import deferredutil
from allmydata.util.deferredutil import race, MultiFailure


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


class AsyncToDeferred(unittest.TestCase):
    """Tests for ``deferredutil.async_to_deferred.``"""

    def test_async_to_deferred_success(self):
        """
        Normal results from a ``@async_to_deferred``-wrapped function get
        turned into a ``Deferred`` with that value.
        """
        @deferredutil.async_to_deferred
        async def f(x, y):
            return x + y

        result = f(1, y=2)
        self.assertEqual(self.successResultOf(result), 3)

    def test_async_to_deferred_exception(self):
        """
        Exceptions from a ``@async_to_deferred``-wrapped function get
        turned into a ``Deferred`` with that value.
        """
        @deferredutil.async_to_deferred
        async def f(x, y):
            return x/y

        result = f(1, 0)
        self.assertIsInstance(self.failureResultOf(result).value, ZeroDivisionError)



def _setupRaceState(numDeferreds: int) -> tuple[list[int], list[Deferred[object]]]:
    """
    Create a list of Deferreds and a corresponding list of integers
    tracking how many times each Deferred has been cancelled.  Without
    additional steps the Deferreds will never fire.
    """
    cancelledState = [0] * numDeferreds

    ds: list[Deferred[object]] = []
    for n in range(numDeferreds):

        def cancel(d: Deferred, n: int = n) -> None:
            cancelledState[n] += 1

        ds.append(Deferred(canceller=cancel))

    return cancelledState, ds


class RaceTests(unittest.SynchronousTestCase):
    """
    Tests for L{race}.
    """

    @given(
        beforeWinner=integers(min_value=0, max_value=3),
        afterWinner=integers(min_value=0, max_value=3),
    )
    def test_success(self, beforeWinner: int, afterWinner: int) -> None:
        """
        When one of the L{Deferred}s passed to L{race} fires successfully,
        the L{Deferred} return by L{race} fires with the index of that
        L{Deferred} and its result and cancels the rest of the L{Deferred}s.
        @param beforeWinner: A randomly selected number of Deferreds to
            appear before the "winning" Deferred in the list passed in.
        @param beforeWinner: A randomly selected number of Deferreds to
            appear after the "winning" Deferred in the list passed in.
        """
        cancelledState, ds = _setupRaceState(beforeWinner + 1 + afterWinner)

        raceResult = race(ds)
        expected = object()
        ds[beforeWinner].callback(expected)

        # The result should be the index and result of the only Deferred that
        # fired.
        self.assertEqual(
            self.successResultOf(raceResult),
            (beforeWinner, expected),
        )
        # All Deferreds except the winner should have been cancelled once.
        expectedCancelledState = [1] * beforeWinner + [0] + [1] * afterWinner
        self.assertEqual(
            cancelledState,
            expectedCancelledState,
        )

    @given(
        beforeWinner=integers(min_value=0, max_value=3),
        afterWinner=integers(min_value=0, max_value=3),
    )
    def test_failure(self, beforeWinner: int, afterWinner: int) -> None:
        """
        When all of the L{Deferred}s passed to L{race} fire with failures,
        the L{Deferred} return by L{race} fires with L{MultiFailure} wrapping
        all of their failures.
        @param beforeWinner: A randomly selected number of Deferreds to
            appear before the "winning" Deferred in the list passed in.
        @param beforeWinner: A randomly selected number of Deferreds to
            appear after the "winning" Deferred in the list passed in.
        """
        cancelledState, ds = _setupRaceState(beforeWinner + 1 + afterWinner)

        failure = Failure(Exception("The test demands failures."))
        raceResult = race(ds)
        for d in ds:
            d.errback(failure)

        actualFailure = self.failureResultOf(raceResult, MultiFailure)
        self.assertEqual(
            actualFailure.value.failures,
            [failure] * len(ds),
        )
        self.assertEqual(
            cancelledState,
            [0] * len(ds),
        )

    @given(
        beforeWinner=integers(min_value=0, max_value=3),
        afterWinner=integers(min_value=0, max_value=3),
    )
    def test_resultAfterCancel(self, beforeWinner: int, afterWinner: int) -> None:
        """
        If one of the Deferreds fires after it was cancelled its result
        goes nowhere.  In particular, it does not cause any errors to be
        logged.
        """
        # Ensure we have a Deferred to win and at least one other Deferred
        # that can ignore cancellation.
        ds: list[Deferred[None]] = [
            Deferred() for n in range(beforeWinner + 2 + afterWinner)
        ]

        raceResult = race(ds)
        ds[beforeWinner].callback(None)
        ds[beforeWinner + 1].callback(None)

        self.successResultOf(raceResult)
        self.assertEqual(len(self.flushLoggedErrors()), 0)

    def test_resultFromCancel(self) -> None:
        """
        If one of the input Deferreds has a cancel function that fires it
        with success, nothing bad happens.
        """
        winner: Deferred[object] = Deferred()
        ds: list[Deferred[object]] = [
            winner,
            Deferred(canceller=lambda d: d.callback(object())),
        ]
        expected = object()
        raceResult = race(ds)
        winner.callback(expected)

        self.assertEqual(self.successResultOf(raceResult), (0, expected))

    @given(
        numDeferreds=integers(min_value=1, max_value=3),
    )
    def test_cancel(self, numDeferreds: int) -> None:
        """
        If the result of L{race} is cancelled then all of the L{Deferred}s
        passed in are cancelled.
        """
        cancelledState, ds = _setupRaceState(numDeferreds)

        raceResult = race(ds)
        raceResult.cancel()

        self.assertEqual(cancelledState, [1] * numDeferreds)
        self.failureResultOf(raceResult, MultiFailure)
