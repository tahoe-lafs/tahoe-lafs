import mock
from prometheus_client.openmetrics import parser
from twisted.trial import unittest
from allmydata.web.status import Statistics

class FakeStatsProvider(object):
    def get_stats(self):
        stats = {'stats': {}, 'counters': {}}
        return stats

class OpenMetrics(unittest.TestCase):
    def test_spec_compliance(self):
        """
        Does our output adhere to the OpenMetrics spec?
        https://github.com/OpenObservability/OpenMetrics/blob/main/specification/OpenMetrics.md
        """
        req = mock.Mock()
        stats = mock.Mock()
        stats._provider = FakeStatsProvider()
        metrics = Statistics.render_OPENMETRICS(stats, req)

        # "The content type MUST be..."
        req.setHeader.assert_called_with("content-type", "application/openmetrics-text; version=1.0.0; charset=utf-8")

        # The parser throws if it can't parse.
        # Wrap in a list() to drain the generator.
        families = list(parser.text_string_to_metric_families(metrics))
        # TODO add more realistic stats, incl. missing (None) values

