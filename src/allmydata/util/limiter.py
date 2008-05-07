
from twisted.internet import defer
from foolscap.eventual import eventually

class ConcurrencyLimiter:
    """I implement a basic concurrency limiter. Add work to it in the form of
    (callable, args, kwargs) tuples. No more than LIMIT callables will be
    outstanding at any one time.
    """

    def __init__(self, limit=10):
        self.limit = 10
        self.pending = []
        self.active = 0

    def add(self, cb, *args, **kwargs):
        d = defer.Deferred()
        task = (cb, args, kwargs, d)
        self.pending.append(task)
        self.maybe_start_task()
        return d

    def maybe_start_task(self):
        if self.active >= self.limit:
            return
        if not self.pending:
            return
        (cb, args, kwargs, done_d) = self.pending.pop(0)
        self.active += 1
        d = defer.maybeDeferred(cb, *args, **kwargs)
        d.addBoth(self._done, done_d)

    def _done(self, res, done_d):
        self.active -= 1
        eventually(done_d.callback, res)
        self.maybe_start_task()
