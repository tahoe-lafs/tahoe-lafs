"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.trial import unittest
from twisted.application import service
from allmydata.stats import CPUUsageMonitor
from allmydata.util import pollmixin
import allmydata.test.common_util as testutil

class FasterMonitor(CPUUsageMonitor):
    POLL_INTERVAL = 0.01


class CPUUsage(unittest.TestCase, pollmixin.PollMixin, testutil.StallMixin):
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()

    def tearDown(self):
        return self.s.stopService()

    def test_monitor(self):
        m = FasterMonitor()
        s = m.get_stats() # before it has been started
        self.failIf("cpu_monitor.1min_avg" in s)
        m.setServiceParent(self.s)
        def _poller():
            return bool(len(m.samples) == m.HISTORY_LENGTH+1)
        d = self.poll(_poller)
        # pause a couple more intervals, to make sure that the history-trimming
        # code is exercised
        d.addCallback(self.stall, FasterMonitor.POLL_INTERVAL * 2)
        def _check(res):
            s = m.get_stats()
            self.failUnless("cpu_monitor.1min_avg" in s)
            self.failUnless("cpu_monitor.5min_avg" in s)
            self.failUnless("cpu_monitor.15min_avg" in s)
            self.failUnless("cpu_monitor.total" in s)
        d.addCallback(_check)
        return d

