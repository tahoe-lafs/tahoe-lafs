# -*- test-case-name: allmydata.test.test_observer -*-

import weakref
from twisted.internet import defer
from foolscap.api import eventually

"""The idiom we use is for the observed object to offer a method named
'when_something', which returns a deferred.  That deferred will be fired when
something happens.  The way this is typically implemented is that the observed
has an ObserverList whose when_fired method is called in the observed's
'when_something'."""

class OneShotObserverList:
    """A one-shot event distributor."""
    def __init__(self):
        self._fired = False
        self._result = None
        self._watchers = []
        self.__repr__ = self._unfired_repr

    def _unfired_repr(self):
        return "<OneShotObserverList [%s]>" % (self._watchers, )

    def _fired_repr(self):
        return "<OneShotObserverList -> %s>" % (self._result, )

    def _get_result(self):
        return self._result

    def when_fired(self):
        if self._fired:
            return defer.succeed(self._get_result())
        d = defer.Deferred()
        self._watchers.append(d)
        return d

    def fire(self, result):
        assert not self._fired
        self._fired = True
        self._result = result
        self._fire(result)

    def _fire(self, result):
        for w in self._watchers:
            eventually(w.callback, result)
        del self._watchers
        self.__repr__ = self._fired_repr

    def fire_if_not_fired(self, result):
        if not self._fired:
            self.fire(result)

class LazyOneShotObserverList(OneShotObserverList):
    """
    a variant of OneShotObserverList which does not retain
    the result it handles, but rather retains a callable()
    through which is retrieves the data if and when needed.
    """
    def __init__(self):
        OneShotObserverList.__init__(self)

    def _get_result(self):
        return self._result_producer()

    def fire(self, result_producer):
        """
        @param result_producer: a no-arg callable which
        returns the data which is to be considered the
        'result' for this observer list.  note that this
        function may be called multiple times - once
        upon initial firing, and potentially once more
        for each subsequent when_fired() deferred created
        """
        assert not self._fired
        self._fired = True
        self._result_producer = result_producer
        if self._watchers: # if not, don't call result_producer
            self._fire(self._get_result())

class ObserverList:
    """A simple class to distribute events to a number of subscribers."""

    def __init__(self):
        self._watchers = []

    def subscribe(self, observer):
        self._watchers.append(observer)

    def unsubscribe(self, observer):
        self._watchers.remove(observer)

    def notify(self, *args, **kwargs):
        for o in self._watchers:
            eventually(o, *args, **kwargs)

class EventStreamObserver:
    """A simple class to distribute multiple events to a single subscriber.
    It accepts arbitrary kwargs, but no posargs."""
    def __init__(self):
        self._watcher = None
        self._undelivered_results = []
        self._canceler = None

    def set_canceler(self, c, methname):
        """I will call c.METHNAME(self) when somebody cancels me."""
        # we use a weakref to avoid creating a cycle between us and the thing
        # we're observing: they'll be holding a reference to us to compare
        # against the value we pass to their canceler function. However,
        # since bound methods are first-class objects (and not kept alive by
        # the object they're bound to), we can't just stash a weakref to the
        # bound cancel method. Instead, we must hold a weakref to the actual
        # object, and obtain its cancel method later.
        # http://code.activestate.com/recipes/81253-weakmethod/ has an
        # alternative.
        self._canceler = (weakref.ref(c), methname)

    def subscribe(self, observer, **watcher_kwargs):
        self._watcher = (observer, watcher_kwargs)
        while self._undelivered_results:
            self._notify(self._undelivered_results.pop(0))

    def notify(self, **result_kwargs):
        if self._watcher:
            self._notify(result_kwargs)
        else:
            self._undelivered_results.append(result_kwargs)

    def _notify(self, result_kwargs):
        o, watcher_kwargs = self._watcher
        kwargs = dict(result_kwargs)
        kwargs.update(watcher_kwargs)
        eventually(o, **kwargs)

    def cancel(self):
        wr,methname = self._canceler
        o = wr()
        if o:
            getattr(o,methname)(self)
