"""
Utilities for working with Twisted Deferreds.

Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import time

try:
    from typing import (
        Callable,
        Any,
    )
except ImportError:
    pass

from foolscap.api import eventually
from eliot.twisted import (
    inline_callbacks,
)
from twisted.internet import defer, reactor, error
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
        action,     # type: Callable[[], defer.Deferred[Any]]
        condition,  # type: Callable[[], bool]
):
    # type: (...) -> defer.Deferred[None]
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
