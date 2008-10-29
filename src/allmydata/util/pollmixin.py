
import time
from twisted.internet import task

class TimeoutError(Exception):
    pass

class PollComplete(Exception):
    pass

class PollMixin:

    def poll(self, check_f, pollinterval=0.01, timeout=100):
        # Return a Deferred, then call check_f periodically until it returns
        # True, at which point the Deferred will fire.. If check_f raises an
        # exception, the Deferred will errback. If the check_f does not
        # indicate success within timeout= seconds, the Deferred will
        # errback. If timeout=None, no timeout will be enforced, and the loop
        # will poll forever (or really until Trial times out).
        cutoff = None
        if timeout is not None:
            cutoff = time.time() + timeout
        lc = task.LoopingCall(self._poll, check_f, cutoff)
        d = lc.start(pollinterval)
        def _convert_done(f):
            f.trap(PollComplete)
            return None
        d.addErrback(_convert_done)
        return d

    def _poll(self, check_f, cutoff):
        if cutoff is not None and time.time() > cutoff:
            raise TimeoutError("PollMixin never saw %s return True" % check_f)
        if check_f():
            raise PollComplete()

