"""
Tests for allmydata.monitor.
"""

from twisted.trial import unittest

from allmydata.monitor import Monitor, OperationCancelledError


class MonitorTests(unittest.TestCase):
    """Tests for the Monitor class."""

    def test_cancellation(self):
        """The monitor can be cancelled."""
        m = Monitor()
        self.assertFalse(m.is_cancelled())
        m.raise_if_cancelled()
        m.cancel()
        self.assertTrue(m.is_cancelled())
        with self.assertRaises(OperationCancelledError):
            m.raise_if_cancelled()

    def test_status(self):
        """The monitor can have its status set."""
        m = Monitor()
        self.assertEqual(m.get_status(), None)
        m.set_status("discombobulated")
        self.assertEqual(m.get_status(), "discombobulated")

    def test_finish(self):
        """The monitor can finish."""
        m = Monitor()
        self.assertFalse(m.is_finished())
        d = m.when_done()
        self.assertNoResult(d)

        result = m.finish(300)
        self.assertEqual(result, 300)
        self.assertEqual(m.get_status(), 300)
        self.assertTrue(m.is_finished())

        d.addBoth(self.assertEqual, 300)
        return d
