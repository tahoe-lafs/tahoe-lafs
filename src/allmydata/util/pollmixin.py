"""
Polling utility that returns Deferred.

Ported to Python 3.
"""

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import time

try:
    from typing import List
except ImportError:
    pass

from twisted.internet import task

class TimeoutError(Exception):
    pass

class PollComplete(Exception):
    pass

class PollMixin(object):
    _poll_should_ignore_these_errors = []  # type: List[Exception]

    def poll(self, check_f, pollinterval=0.01, timeout=1000):
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
        # since PollMixin is mostly used for unit tests, quit if we see any
        # logged errors. This should give us a nice fast failure, instead of
        # waiting for a timeout. Tests which use flushLoggedErrors() will
        # need to warn us by putting the error types they'll be ignoring in
        # self._poll_should_ignore_these_errors
        if hasattr(self, "_observer") and hasattr(self._observer, "getErrors"):
            errs = []
            for e in self._observer.getErrors():
                if not e.check(*self._poll_should_ignore_these_errors):
                    errs.append(e)
            if errs:
                print(errs)
                self.fail("Errors snooped, terminating early")

