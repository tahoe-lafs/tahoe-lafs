import mock
from twisted.trial import unittest
from allmydata.web.status import Statistics

class FakeStatsProvider(object):
    def get_stats(self):
        stats = {'stats': {}, 'counters': {}}
        return stats

class OpenMetrics(unittest.TestCase):
    def test_header(self):
        req = mock.Mock()
        stats = mock.Mock()
        stats._provider = FakeStatsProvider()
        metrics = Statistics.render_OPENMETRICS(stats, req)
        req.setHeader.assert_called_with("content-type", "application/openmetrics-text; version=1.0.0; charset=utf-8")

    def test_spec_compliance(self):
        req = mock.Mock()
        stats = mock.Mock()
        stats._provider = FakeStatsProvider()
        metrics = Statistics.render_OPENMETRICS(stats, req)
        # TODO test that output adheres to spec
        # TODO add more realistic stats, incl. missing (None) values

