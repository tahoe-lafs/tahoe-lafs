"""
Common utilities that have been ported to Python 3.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2, native_str, native_bytes
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os
import time
import signal

from twisted.internet import defer, reactor
from twisted.python import failure


class TimezoneMixin(object):

    def setTimezone(self, timezone):
        def tzset_if_possible():
            # Windows doesn't have time.tzset().
            if hasattr(time, 'tzset'):
                time.tzset()

        unset = object()
        originalTimezone = os.environ.get('TZ', unset)
        def restoreTimezone():
            if originalTimezone is unset:
                del os.environ['TZ']
            else:
                os.environ['TZ'] = originalTimezone
            tzset_if_possible()

        os.environ['TZ'] = timezone
        self.addCleanup(restoreTimezone)
        tzset_if_possible()

    def have_working_tzset(self):
        return hasattr(time, 'tzset')


class SignalMixin(object):
    # This class is necessary for any code which wants to use Processes
    # outside the usual reactor.run() environment. It is copied from
    # Twisted's twisted.test.test_process . Note that Twisted-8.2.0 uses
    # something rather different.
    sigchldHandler = None

    def setUp(self):
        # make sure SIGCHLD handler is installed, as it should be on
        # reactor.run(). problem is reactor may not have been run when this
        # test runs.
        if hasattr(reactor, "_handleSigchld") and hasattr(signal, "SIGCHLD"):
            self.sigchldHandler = signal.signal(signal.SIGCHLD,
                                                reactor._handleSigchld)
        return super(SignalMixin, self).setUp()

    def tearDown(self):
        if self.sigchldHandler:
            signal.signal(signal.SIGCHLD, self.sigchldHandler)
        return super(SignalMixin, self).tearDown()


class ShouldFailMixin(object):

    def shouldFail(self, expected_failure, which, substring,
                   callable, *args, **kwargs):
        assert substring is None or isinstance(substring, (native_str, native_bytes))
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnless(substring in str(res),
                                    "%s: substring '%s' not in '%s'"
                                    % (which, substring, str(res)))
                # return the Failure for further analysis, but in a form that
                # doesn't make the Deferred chain think that we failed.
                return [res]
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d
