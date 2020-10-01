"""
Common utilities that have been ported to Python 3.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from past.builtins import unicode

import os
import time
import signal

from twisted.internet import reactor
from twisted.trial import unittest

from ..util.assertutil import precondition
from ..util.encodingutil import unicode_platform, get_filesystem_encoding


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


class ReallyEqualMixin(object):
    def failUnlessReallyEqual(self, a, b, msg=None):
        self.assertEqual(a, b, msg)
        self.assertEqual(type(a), type(b), "a :: %r (%s), b :: %r (%s), %r" % (a, type(a), b, type(b), msg))


def skip_if_cannot_represent_filename(u):
    precondition(isinstance(u, unicode))

    enc = get_filesystem_encoding()
    if not unicode_platform():
        try:
            u.encode(enc)
        except UnicodeEncodeError:
            raise unittest.SkipTest("A non-ASCII filename could not be encoded on this platform.")


class Marker(object):
    pass

class FakeCanary(object):
    """For use in storage tests.

    Can be moved back to test_storage.py once enough Python 3 porting has been
    done.
    """
    def __init__(self, ignore_disconnectors=False):
        self.ignore = ignore_disconnectors
        self.disconnectors = {}
    def notifyOnDisconnect(self, f, *args, **kwargs):
        if self.ignore:
            return
        m = Marker()
        self.disconnectors[m] = (f, args, kwargs)
        return m
    def dontNotifyOnDisconnect(self, marker):
        if self.ignore:
            return
        del self.disconnectors[marker]
    def getRemoteTubID(self):
        return None
    def getPeer(self):
        return "<fake>"
