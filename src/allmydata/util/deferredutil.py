"""
Utilities for working with Twisted Deferreds.
"""

from __future__ import annotations

import time
from functools import wraps

from typing import (
    Callable,
    Any,
    Sequence,
    TypeVar,
    Optional,
    Coroutine,
    Generator
)
from typing_extensions import ParamSpec

from foolscap.api import eventually
from eliot.twisted import (
    inline_callbacks,
)
from twisted.internet import defer, reactor, error
from twisted.internet.defer import Deferred
from twisted.python.failure import Failure

from allmydata.util import log
from allmydata.util.assertutil import _assert
from allmydata.util.pollmixin import PollMixin


class TimeoutError(Exception):
    pass


def timeout_call(reactor, d, timeout):
    """
    This returns the result of 'd', unless 'timeout' expires before
    'd' is completed in which case a TimeoutError is raised.
    """
    timer_d = defer.Deferred()

    def _timed_out():
        timer_d.errback(Failure(TimeoutError()))

    def _got_result(x):
        try:
            timer.cancel()
            timer_d.callback(x)
        except (error.AlreadyCalled, defer.AlreadyCalledError):
            pass
        return None

    timer = reactor.callLater(timeout, _timed_out)
    d.addBoth(_got_result)
    return timer_d



# utility wrapper for DeferredList
def _check_deferred_list(results):
    # if any of the component Deferreds failed, return the first failure such
    # that an addErrback() would fire. If all were ok, return a list of the
    # results (without the success/failure booleans)
    for success,f in results:
        if not success:
            return f
    return [r[1] for r in results]

def DeferredListShouldSucceed(dl):
    d = defer.DeferredList(dl)
    d.addCallback(_check_deferred_list)
    return d

def _parseDListResult(l):
    return [x[1] for x in l]

def _unwrapFirstError(f):
    f.trap(defer.FirstError)
    raise f.value.subFailure

def gatherResults(deferredList):
    """Returns list with result of given Deferreds.

    This builds on C{DeferredList} but is useful since you don't
    need to parse the result for success/failure.

    @type deferredList:  C{list} of L{Deferred}s
    """
    d = defer.DeferredList(deferredList, fireOnOneErrback=True, consumeErrors=True)
    d.addCallbacks(_parseDListResult, _unwrapFirstError)
    return d


def _with_log(op, res):
    """
    The default behaviour on firing an already-fired Deferred is unhelpful for
    debugging, because the AlreadyCalledError can easily get lost or be raised
    in a context that results in a different error. So make sure it is logged
    (for the abstractions defined here). If we are in a test, log.err will cause
    the test to fail.
    """
    try:
        op(res)
    except defer.AlreadyCalledError as e:
        log.err(e, op=repr(op), level=log.WEIRD)

def eventually_callback(d):
    def _callback(res):
        eventually(_with_log, d.callback, res)
        return res
    return _callback

def eventually_errback(d):
    def _errback(res):
        eventually(_with_log, d.errback, res)
        return res
    return _errback

def eventual_chain(source, target):
    source.addCallbacks(eventually_callback(target), eventually_errback(target))


class HookMixin(object):
    """
    I am a helper mixin that maintains a collection of named hooks, primarily
    for use in tests. Each hook is set to an unfired Deferred using 'set_hook',
    and can then be fired exactly once at the appropriate time by '_call_hook'.
    If 'ignore_count' is given, that number of calls to '_call_hook' will be
    ignored before firing the hook.

    I assume a '_hooks' attribute that should set by the class constructor to
    a dict mapping each valid hook name to None.
    """
    def set_hook(self, name, d=None, ignore_count=0):
        """
        Called by the hook observer (e.g. by a test).
        If d is not given, an unfired Deferred is created and returned.
        The hook must not already be set.
        """
        self._log("set_hook %r, ignore_count=%r" % (name, ignore_count))
        if d is None:
            d = defer.Deferred()
        _assert(ignore_count >= 0, ignore_count=ignore_count)
        _assert(name in self._hooks, name=name)
        _assert(self._hooks[name] is None, name=name, hook=self._hooks[name])
        _assert(isinstance(d, defer.Deferred), d=d)

        self._hooks[name] = (d, ignore_count)
        return d

    def _call_hook(self, res, name, **kwargs):
        """
        Called to trigger the hook, with argument 'res'. This is a no-op if
        the hook is unset. If the hook's ignore_count is positive, it will be
        decremented; if it was already zero, the hook will be unset, and then
        its Deferred will be fired synchronously.

        The expected usage is "deferred.addBoth(self._call_hook, 'hookname')".
        This ensures that if 'res' is a failure, the hook will be errbacked,
        which will typically cause the test to also fail.
        'res' is returned so that the current result or failure will be passed
        through.

        Accepts a single keyword argument, async, defaulting to False.
        """
        async_ = kwargs.get("async", False)
        hook = self._hooks[name]
        if hook is None:
            return res  # pass on error/result

        (d, ignore_count) = hook
        self._log("call_hook %r, ignore_count=%r" % (name, ignore_count))
        if ignore_count > 0:
            self._hooks[name] = (d, ignore_count - 1)
        else:
            self._hooks[name] = None
            if async_:
                _with_log(eventually_callback(d), res)
            else:
                _with_log(d.callback, res)
        return res

    def _log(self, msg):
        log.msg(msg, level=log.NOISY)


class WaitForDelayedCallsMixin(PollMixin):
    def _delayed_calls_done(self):
        # We're done when the only remaining DelayedCalls fire after threshold.
        # (These will be associated with the test timeout, or else they *should*
        # cause an unclean reactor error because the test should have waited for
        # them.)
        threshold = time.time() + 10
        for delayed in reactor.getDelayedCalls():
            if delayed.getTime() < threshold:
                return False
        return True

    def wait_for_delayed_calls(self, res=None):
        """
        Use like this at the end of a test:
          d.addBoth(self.wait_for_delayed_calls)
        """
        d = self.poll(self._delayed_calls_done)
        d.addErrback(log.err, "error while waiting for delayed calls")
        d.addBoth(lambda ign: res)
        return d

@inline_callbacks
def until(
        action: Callable[[], defer.Deferred[Any]],
        condition: Callable[[], bool],
) -> Generator[Any, None, None]:
    """
    Run a Deferred-returning function until a condition is true.

    :param action: The action to run.
    :param condition: The predicate signaling stop.

    :return: A Deferred that fires after the condition signals stop.
    """
    while True:
        yield action()
        if condition():
            break


P = ParamSpec("P")
R = TypeVar("R")


def async_to_deferred(f: Callable[P, Coroutine[defer.Deferred[R], None, R]]) -> Callable[P, Deferred[R]]:
    """
    Wrap an async function to return a Deferred instead.

    Maybe solution to https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3886
    """

    @wraps(f)
    def not_async(*args: P.args, **kwargs: P.kwargs) -> Deferred[R]:
        return defer.Deferred.fromCoroutine(f(*args, **kwargs))

    return not_async


class MultiFailure(Exception):
    """
    More than one failure occurred.
    """

    def __init__(self, failures: Sequence[Failure]) -> None:
        super(MultiFailure, self).__init__()
        self.failures = failures


_T = TypeVar("_T")

# Eventually this should be in Twisted upstream:
# https://github.com/twisted/twisted/pull/11818
def race(ds: Sequence[Deferred[_T]]) -> Deferred[tuple[int, _T]]:
    """
    Select the first available result from the sequence of Deferreds and
    cancel the rest.
    @return: A cancellable L{Deferred} that fires with the index and output of
        the element of C{ds} to have a success result first, or that fires
        with L{MultiFailure} holding a list of their failures if they all
        fail.
    """
    # Keep track of the Deferred for the action which completed first.  When
    # it completes, all of the other Deferreds will get cancelled but this one
    # shouldn't be.  Even though it "completed" it isn't really done - the
    # caller will still be using it for something.  If we cancelled it,
    # cancellation could propagate down to them.
    winner: Optional[Deferred] = None

    # The cancellation function for the Deferred this function returns.
    def cancel(result: Deferred) -> None:
        # If it is cancelled then we cancel all of the Deferreds for the
        # individual actions because there is no longer the possibility of
        # delivering any of their results anywhere.  We don't have to fire
        # `result` because the Deferred will do that for us.
        for d in to_cancel:
            d.cancel()

    # The Deferred that this function will return.  It will fire with the
    # index and output of the action that completes first, or None if all of
    # the actions fail.  If it is cancelled, all of the actions will be
    # cancelled.
    final_result: Deferred[tuple[int, _T]] = Deferred(canceller=cancel)

    # A callback for an individual action.
    def succeeded(this_output: _T, this_index: int) -> None:
        # If it is the first action to succeed then it becomes the "winner",
        # its index/output become the externally visible result, and the rest
        # of the action Deferreds get cancelled.  If it is not the first
        # action to succeed (because some action did not support
        # cancellation), just ignore the result.  It is uncommon for this
        # callback to be entered twice.  The only way it can happen is if one
        # of the input Deferreds has a cancellation function that fires the
        # Deferred with a success result.
        nonlocal winner
        if winner is None:
            # This is the first success.  Act on it.
            winner = to_cancel[this_index]

            # Cancel the rest.
            for d in to_cancel:
                if d is not winner:
                    d.cancel()

            # Fire our Deferred
            final_result.callback((this_index, this_output))

    # Keep track of how many actions have failed.  If they all fail we need to
    # deliver failure notification on our externally visible result.
    failure_state = []

    def failed(failure: Failure, this_index: int) -> None:
        failure_state.append((this_index, failure))
        if len(failure_state) == len(to_cancel):
            # Every operation failed.
            failure_state.sort()
            failures = [f for (ignored, f) in failure_state]
            final_result.errback(MultiFailure(failures))

    # Copy the sequence of Deferreds so we know it doesn't get mutated out
    # from under us.
    to_cancel = list(ds)
    for index, d in enumerate(ds):
        # Propagate the position of this action as well as the argument to f
        # to the success callback so we can cancel the right Deferreds and
        # propagate the result outwards.
        d.addCallbacks(succeeded, failed, callbackArgs=(index,), errbackArgs=(index,))

    return final_result
