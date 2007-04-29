# -*- test-case-name: foolscap.test.test_promise -*-

from twisted.python import util
from twisted.python.failure import Failure
from twisted.internet import defer
from foolscap.eventual import eventually

id = util.unsignedID

EVENTUAL, CHAINED, NEAR, BROKEN = range(4)

class UsageError(Exception):
    """Raised when you do something inappropriate to a Promise."""

def _ignore(results):
    pass


class Promise:
    """I am a promise of a future result. I am a lot like a Deferred, except
    that my promised result is usually an instance. I make it possible to
    schedule method invocations on this future instance, returning Promises
    for the results.

    Promises are always in one of three states: Eventual, Fulfilled, and
    Broken. (see http://www.erights.org/elib/concurrency/refmech.html for a
    pretty picture). They start as Eventual, meaning we do not yet know
    whether they will resolve or not. In this state, method invocations are
    queued. Eventually the Promise will be 'resolved' into either the
    Fulfilled or the Broken state. Fulfilled means that the promise contains
    a live object to which methods can be dispatched synchronously. Broken
    promises are incapable of invoking methods: they all result in Failure.

    Method invocation is always asynchronous: it always returns a Promise.

    The only thing you can do with a promise 'p1' is to perform an
    eventual-send on it, like so::

     sendOnly(p1).foo(args)  # ignores the result
     p2 = send(p1).bar(args) # creates a Promise for the result
     p2 = p1.bar(args)       # same as send(p1).bar(args)

    Or wait for it to resolve, using one of the following::

     d = when(p); d.addCallback(cb)  # provides a Deferred
     p._then(cb, *args, **kwargs)    # like when(p).addCallback(cb,*a,**kw)
     p._except(cb, *args, **kwargs)  # like when(p).addErrback(cb,*a,**kw)

    The _then and _except forms return the same Promise. You can set up
    chains of calls that will be invoked in the future, using a dataflow
    style, like this::

     p = getPromiseForServer()
     d = p.getDatabase('db1')
     r = d.getRecord(name)
     def _print(record):
         print 'the record says', record
     def _oops(failure):
         print 'something failed:', failure
     r._then(_print)
     r._except(_oops)

    Or all collapsed in one sequence like::

     getPromiseForServer().getDatabase('db1').getRecord(name)._then(_print)

    The eventual-send will eventually invoke the method foo(args) on the
    promise's resolution. This will return a new Promise for the results of
    that method call.
    """

    # all our internal methods are private, to avoid a confusing lack of an
    # error message if someone tries to make a synchronous method call on us
    # with a name that happens to match an internal one.

    _state = EVENTUAL
    _useDataflowStyle = True # enables p.foo(args)

    def __init__(self):
        self._watchers = []
        self._pendingMethods = [] # list of (methname, args, kwargs, p)

    # _then and _except are our only public methods. All other access is
    # through normal (not underscore-prefixed) attribute names, which
    # indicate names of methods on the target object that should be called
    # later.
    def _then(self, cb, *args, **kwargs):
        d = self._wait_for_resolution()
        d.addCallback(cb, *args, **kwargs)
        d.addErrback(lambda ignore: None)
        return self

    def _except(self, cb, *args, **kwargs):
        d = self._wait_for_resolution()
        d.addErrback(cb, *args, **kwargs)
        return self

    # everything beyond here is private to this module

    def __repr__(self):
        return "<Promise %#x>" % id(self)

    def __getattr__(self, name):
        if not self._useDataflowStyle:
            raise AttributeError("no such attribute %s" % name)
        def newmethod(*args, **kwargs):
            return self._send(name, args, kwargs)
        return newmethod

    # _send and _sendOnly are used by send() and sendOnly(). _send is also
    # used by regular attribute access.

    def _send(self, methname, args, kwargs):
        """Return a Promise (for the result of the call) when the call is
        eventually made. The call is guaranteed to not fire in this turn."""
        # this is called by send()
        p, resolver = makePromise()
        if self._state in (EVENTUAL, CHAINED):
            self._pendingMethods.append((methname, args, kwargs, resolver))
        else:
            eventually(self._deliver, methname, args, kwargs, resolver)
        return p

    def _sendOnly(self, methname, args, kwargs):
        """Send a message like _send, but discard the result."""
        # this is called by sendOnly()
        if self._state in (EVENTUAL, CHAINED):
            self._pendingMethods.append((methname, args, kwargs, _ignore))
        else:
            eventually(self._deliver, methname, args, kwargs, _ignore)

    # _wait_for_resolution is used by when(), as well as _then and _except

    def _wait_for_resolution(self):
        """Return a Deferred that will fire (with whatever was passed to
        _resolve) when this Promise moves to a RESOLVED state (either NEAR or
        BROKEN)."""
        # this is called by when()
        if self._state in (EVENTUAL, CHAINED):
            d = defer.Deferred()
            self._watchers.append(d)
            return d
        if self._state == NEAR:
            return defer.succeed(self._target)
        # self._state == BROKEN
        return defer.fail(self._target)

    # _resolve is our resolver method, and is handed out by makePromise()

    def _resolve(self, target_or_failure):
        """Resolve this Promise to refer to the given target. If called with
        a Failure, the Promise is now BROKEN. _resolve may only be called
        once."""
        # E splits this method into two pieces resolve(result) and
        # smash(problem). It is easier for us to keep them in one piece,
        # because d.addBoth(p._resolve) is convenient.
        if self._state != EVENTUAL:
            raise UsageError("Promises may not be resolved multiple times")
        self._resolve2(target_or_failure)

    # the remaining methods are internal, for use by this class only

    def _resolve2(self, target_or_failure):
        # we may be called with a Promise, an immediate value, or a Failure
        if isinstance(target_or_failure, Promise):
            self._state = CHAINED
            when(target_or_failure).addBoth(self._resolve2)
            return
        if isinstance(target_or_failure, Failure):
            self._break(target_or_failure)
            return
        self._target = target_or_failure
        self._deliver_queued_messages()
        self._state = NEAR

    def _break(self, failure):
        # TODO: think about what you do to break a resolved promise. Once the
        # Promise is in the NEAR state, it can't be broken, but eventually
        # we're going to have a FAR state, which *can* be broken.
        """Put this Promise in the BROKEN state."""
        if not isinstance(failure, Failure):
            raise UsageError("Promises must be broken with a Failure")
        if self._state == BROKEN:
            raise UsageError("Broken Promises may not be re-broken")
        self._target = failure
        if self._state in (EVENTUAL, CHAINED):
            self._deliver_queued_messages()
        self._state == BROKEN

    def _invoke_method(self, name, args, kwargs):
        if isinstance(self._target, Failure):
            return self._target
        method = getattr(self._target, name)
        res = method(*args, **kwargs)
        return res

    def _deliverOneMethod(self, methname, args, kwargs):
        method = getattr(self._target, methname)
        return method(*args, **kwargs)

    def _deliver(self, methname, args, kwargs, resolver):
        # the resolver will be fired with both success and Failure
        t = self._target
        if isinstance(t, Promise):
            resolver(t._send(methname, args, kwargs))
        elif isinstance(t, Failure):
            resolver(t)
        else:
            d = defer.maybeDeferred(self._deliverOneMethod,
                                    methname, args, kwargs)
            d.addBoth(resolver)

    def _deliver_queued_messages(self):
        for (methname, args, kwargs, resolver) in self._pendingMethods:
            eventually(self._deliver, methname, args, kwargs, resolver)
        del self._pendingMethods
        # Q: what are the partial-ordering semantics between queued messages
        # and when() clauses that are waiting on this Promise to be resolved?
        for d in self._watchers:
            eventually(d.callback, self._target)
        del self._watchers

def resolvedPromise(resolution):
    p = Promise()
    p._resolve(resolution)
    return p

def makePromise():
    p = Promise()
    return p, p._resolve


class _MethodGetterWrapper:
    def __init__(self, callback):
        self.cb = [callback]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError("method %s is probably private" % name)
        cb = self.cb[0] # avoid bound-methodizing
        def newmethod(*args, **kwargs):
            return cb(name, args, kwargs)
        return newmethod


def send(o):
    """Make an eventual-send call on object C{o}. Use this as follows:

     p = send(o).foo(args)

    C{o} can either be a Promise or an immediate value. The arguments can
    either be promises or immediate values.

    send() always returns a Promise, and the o.foo(args) method invocation
    always takes place in a later reactor turn.

    Many thanks to Mark Miller for suggesting this syntax to me.
    """
    if isinstance(o, Promise):
        return _MethodGetterWrapper(o._send)
    p = resolvedPromise(o)
    return _MethodGetterWrapper(p._send)

def sendOnly(o):
    """Make an eventual-send call on object C{o}, and ignore the results.
    """

    if isinstance(o, Promise):
        return _MethodGetterWrapper(o._sendOnly)
    # this is a little bit heavyweight for a simple eventually(), but it
    # makes the code simpler
    p = resolvedPromise(o)
    return _MethodGetterWrapper(p._sendOnly)


def when(p):
    """Turn a Promise into a Deferred that will fire with the enclosed object
    when it is ready. Use this when you actually need to schedule something
    to happen in a synchronous fashion. Most of the time, you can just invoke
    methods on the Promise as if it were immediately available."""
    
    assert isinstance(p, Promise)
    return p._wait_for_resolution()
