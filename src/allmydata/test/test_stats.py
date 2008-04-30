
from twisted.trial import unittest
from twisted.application import service
from allmydata.stats import CPUUsageMonitor
from allmydata.util import testutil

class FasterMonitor(CPUUsageMonitor):
    POLL_INTERVAL = 0.1


class CPUUsage(unittest.TestCase, testutil.PollMixin, testutil.StallMixin):
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
        # pause one more second, to make sure that the history-trimming code
        # is exercised
        d.addCallback(self.stall, 1.0)
        def _check(res):
            s = m.get_stats()
            self.failUnless("cpu_monitor.1min_avg" in s)
            self.failUnless("cpu_monitor.5min_avg" in s)
            self.failUnless("cpu_monitor.15min_avg" in s)
            self.failUnless("cpu_monitor.total" in s)
        d.addCallback(_check)
        return d

