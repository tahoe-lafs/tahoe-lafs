from twisted.trial import unittest

class FakeStatsProvider(object):
    def get_stats(self):
        stats = {'stats': {}, 'counters': {}}
        return stats

class OpenMetrics(unittest.TestCase):
    def test_spec_compliance(self):
        self.assertEqual('1', '2')

