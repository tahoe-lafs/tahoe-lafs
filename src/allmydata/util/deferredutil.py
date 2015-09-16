
import time

from foolscap.api import eventually, fireEventually
from twisted.internet import defer, reactor

from allmydata.util import log
from allmydata.util.assertutil import _assert
from allmydata.util.pollmixin import PollMixin


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
    except defer.AlreadyCalledError, e:
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


class HookMixin:
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
        if d is None:
            d = defer.Deferred()
        _assert(ignore_count >= 0, ignore_count=ignore_count)
        _assert(name in self._hooks, name=name)
        _assert(self._hooks[name] is None, name=name, hook=self._hooks[name])
        _assert(isinstance(d, defer.Deferred), d=d)

        self._hooks[name] = (d, ignore_count)
        return d

    def _call_hook(self, res, name):
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
        """
        hook = self._hooks[name]
        if hook is None:
            return defer.succeed(None)

        (d, ignore_count) = hook
        log.msg("call_hook", name=name, ignore_count=ignore_count, level=log.NOISY)
        if ignore_count > 0:
            self._hooks[name] = (d, ignore_count - 1)
        else:
            self._hooks[name] = None
            _with_log(d.callback, res)
        return res


def async_iterate(process, iterable, *extra_args, **kwargs):
    """
    I iterate over the elements of 'iterable' (which may be deferred), eventually
    applying 'process' to each one, optionally with 'extra_args' and 'kwargs'.
    'process' should return a (possibly deferred) boolean: True to continue the
    iteration, False to stop.

    I return a Deferred that fires with True if all elements of the iterable
    were processed (i.e. 'process' only returned True values); with False if
    the iteration was stopped by 'process' returning False; or that fails with
    the first failure of either 'process' or the iterator.
    """
    iterator = iter(iterable)

    d = defer.succeed(None)
    def _iterate(ign):
        d2 = defer.maybeDeferred(iterator.next)
        def _cb(item):
            d3 = defer.maybeDeferred(process, item, *extra_args, **kwargs)
            def _maybe_iterate(res):
                if res:
                    d4 = fireEventually()
                    d4.addCallback(_iterate)
                    return d4
                return False
            d3.addCallback(_maybe_iterate)
            return d3
        def _eb(f):
            f.trap(StopIteration)
            return True
        d2.addCallbacks(_cb, _eb)
        return d2
    d.addCallback(_iterate)
    return d


def for_items(cb, mapping):
    """
    For each (key, value) pair in a mapping, I add a callback to cb(None, key, value)
    to a Deferred that fires immediately. I return that Deferred.
    """
    d = defer.succeed(None)
    for k, v in mapping.items():
        d.addCallback(lambda ign, k=k, v=v: cb(None, k, v))
    return d


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
