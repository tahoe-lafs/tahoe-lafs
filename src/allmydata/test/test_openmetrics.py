"""
Tests for ``/statistics?t=openmetrics``.

Ported to Python 3.
"""

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from future.utils import PY2

if PY2:
    # fmt: off
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
    # fmt: on

from prometheus_client.openmetrics import parser

from treq.testing import RequestTraversalAgent

from twisted.web.http import OK
from twisted.web.client import readBody
from twisted.web.resource import Resource

from testtools.twistedsupport import succeeded
from testtools.matchers import (
    AfterPreprocessing,
    Equals,
    MatchesAll,
    MatchesStructure,
    MatchesPredicate,
)
from testtools.content import text_content

from allmydata.web.status import Statistics
from allmydata.test.common import SyncTestCase


class FakeStatsProvider(object):
    """
    A stats provider that hands backed a canned collection of performance
    statistics.
    """

    def get_stats(self):
        # Parsed into a dict from a running tahoe's /statistics?t=json
        stats = {
            "stats": {
                "storage_server.latencies.get.99_9_percentile": None,
                "storage_server.latencies.close.10_0_percentile": 0.00021910667419433594,
                "storage_server.latencies.read.01_0_percentile": 2.8848648071289062e-05,
                "storage_server.latencies.writev.99_9_percentile": None,
                "storage_server.latencies.read.99_9_percentile": None,
                "storage_server.latencies.allocate.99_0_percentile": 0.000988006591796875,
                "storage_server.latencies.writev.mean": 0.00045332245070571654,
                "storage_server.latencies.close.99_9_percentile": None,
                "cpu_monitor.15min_avg": 0.00017592000079223033,
                "storage_server.disk_free_for_root": 103289454592,
                "storage_server.latencies.get.99_0_percentile": 0.000347137451171875,
                "storage_server.latencies.get.mean": 0.00021158285060171353,
                "storage_server.latencies.read.90_0_percentile": 8.893013000488281e-05,
                "storage_server.latencies.write.01_0_percentile": 3.600120544433594e-05,
                "storage_server.latencies.write.99_9_percentile": 0.00017690658569335938,
                "storage_server.latencies.close.90_0_percentile": 0.00033211708068847656,
                "storage_server.disk_total": 103497859072,
                "storage_server.latencies.close.95_0_percentile": 0.0003509521484375,
                "storage_server.latencies.readv.samplesize": 1000,
                "storage_server.disk_free_for_nonroot": 103289454592,
                "storage_server.latencies.close.mean": 0.0002715024480059103,
                "storage_server.latencies.writev.95_0_percentile": 0.0007410049438476562,
                "storage_server.latencies.readv.90_0_percentile": 0.0003781318664550781,
                "storage_server.latencies.readv.99_0_percentile": 0.0004050731658935547,
                "storage_server.latencies.allocate.mean": 0.0007128627429454784,
                "storage_server.latencies.close.samplesize": 326,
                "storage_server.latencies.get.50_0_percentile": 0.0001819133758544922,
                "storage_server.latencies.write.50_0_percentile": 4.482269287109375e-05,
                "storage_server.latencies.readv.01_0_percentile": 0.0002970695495605469,
                "storage_server.latencies.get.10_0_percentile": 0.00015687942504882812,
                "storage_server.latencies.allocate.90_0_percentile": 0.0008189678192138672,
                "storage_server.latencies.get.samplesize": 472,
                "storage_server.total_bucket_count": 393,
                "storage_server.latencies.read.mean": 5.936201880959903e-05,
                "storage_server.latencies.allocate.01_0_percentile": 0.0004208087921142578,
                "storage_server.latencies.allocate.99_9_percentile": None,
                "storage_server.latencies.readv.mean": 0.00034061360359191893,
                "storage_server.disk_used": 208404480,
                "storage_server.latencies.allocate.50_0_percentile": 0.0007410049438476562,
                "storage_server.latencies.read.99_0_percentile": 0.00011992454528808594,
                "node.uptime": 3805759.8545179367,
                "storage_server.latencies.writev.10_0_percentile": 0.00035190582275390625,
                "storage_server.latencies.writev.90_0_percentile": 0.0006821155548095703,
                "storage_server.latencies.close.01_0_percentile": 0.00021505355834960938,
                "storage_server.latencies.close.50_0_percentile": 0.0002579689025878906,
                "cpu_monitor.1min_avg": 0.0002130000000003444,
                "storage_server.latencies.writev.50_0_percentile": 0.0004138946533203125,
                "storage_server.latencies.read.95_0_percentile": 9.107589721679688e-05,
                "storage_server.latencies.readv.95_0_percentile": 0.0003859996795654297,
                "storage_server.latencies.write.10_0_percentile": 3.719329833984375e-05,
                "storage_server.accepting_immutable_shares": 1,
                "storage_server.latencies.writev.samplesize": 309,
                "storage_server.latencies.get.95_0_percentile": 0.0003190040588378906,
                "storage_server.latencies.readv.10_0_percentile": 0.00032210350036621094,
                "storage_server.latencies.get.90_0_percentile": 0.0002999305725097656,
                "storage_server.latencies.get.01_0_percentile": 0.0001239776611328125,
                "cpu_monitor.total": 641.4941180000001,
                "storage_server.latencies.write.samplesize": 1000,
                "storage_server.latencies.write.95_0_percentile": 9.489059448242188e-05,
                "storage_server.latencies.read.50_0_percentile": 6.890296936035156e-05,
                "storage_server.latencies.writev.01_0_percentile": 0.00033211708068847656,
                "storage_server.latencies.read.10_0_percentile": 3.0994415283203125e-05,
                "storage_server.latencies.allocate.10_0_percentile": 0.0004949569702148438,
                "storage_server.reserved_space": 0,
                "storage_server.disk_avail": 103289454592,
                "storage_server.latencies.write.99_0_percentile": 0.00011301040649414062,
                "storage_server.latencies.write.90_0_percentile": 9.083747863769531e-05,
                "cpu_monitor.5min_avg": 0.0002370666691157502,
                "storage_server.latencies.write.mean": 5.8008909225463864e-05,
                "storage_server.latencies.readv.50_0_percentile": 0.00033020973205566406,
                "storage_server.latencies.close.99_0_percentile": 0.0004038810729980469,
                "storage_server.allocated": 0,
                "storage_server.latencies.writev.99_0_percentile": 0.0007710456848144531,
                "storage_server.latencies.readv.99_9_percentile": 0.0004780292510986328,
                "storage_server.latencies.read.samplesize": 170,
                "storage_server.latencies.allocate.samplesize": 406,
                "storage_server.latencies.allocate.95_0_percentile": 0.0008411407470703125,
            },
            "counters": {
                "storage_server.writev": 309,
                "storage_server.bytes_added": 197836146,
                "storage_server.close": 326,
                "storage_server.readv": 14299,
                "storage_server.allocate": 406,
                "storage_server.read": 170,
                "storage_server.write": 3775,
                "storage_server.get": 472,
            },
        }
        return stats


class HackItResource(Resource, object):
    """
    A bridge between ``RequestTraversalAgent`` and ``MultiFormatResource``
    (used by ``Statistics``).  ``MultiFormatResource`` expects the request
    object to have a ``fields`` attribute but Twisted's ``IRequest`` has no
    such attribute.  Create it here.
    """

    def getChildWithDefault(self, path, request):
        request.fields = None
        return Resource.getChildWithDefault(self, path, request)


class OpenMetrics(SyncTestCase):
    """
    Tests for ``/statistics?t=openmetrics``.
    """

    def test_spec_compliance(self):
        """
        Does our output adhere to the `OpenMetrics <https://openmetrics.io/>` spec?
        https://github.com/OpenObservability/OpenMetrics/
        https://prometheus.io/docs/instrumenting/exposition_formats/
        """
        root = HackItResource()
        root.putChild(b"", Statistics(FakeStatsProvider()))
        rta = RequestTraversalAgent(root)
        d = rta.request(b"GET", b"http://localhost/?t=openmetrics")
        self.assertThat(d, succeeded(matches_stats(self)))


def matches_stats(testcase):
    """
    Create a matcher that matches a response that confirms to the OpenMetrics
    specification.

    * The ``Content-Type`` is **application/openmetrics-text; version=1.0.0; charset=utf-8**.
    * The status is **OK**.
    * The body can be parsed by an OpenMetrics parser.
    * The metric families in the body are grouped and sorted.
    * At least one of the expected families appears in the body.

    :param testtools.TestCase testcase: The case to which to add detail about the matching process.

    :return: A matcher.
    """
    return MatchesAll(
        MatchesStructure(
            code=Equals(OK),
            # "The content type MUST be..."
            headers=has_header(
                "content-type",
                "application/openmetrics-text; version=1.0.0; charset=utf-8",
            ),
        ),
        AfterPreprocessing(
            readBodyText,
            succeeded(
                MatchesAll(
                    MatchesPredicate(add_detail(testcase, "response body"), "%s dummy"),
                    parses_as_openmetrics(),
                )
            ),
        ),
    )


def add_detail(testcase, name):
    """
    Create a matcher that always matches and as a side-effect adds the matched
    value as detail to the testcase.

    :param testtools.TestCase testcase: The case to which to add the detail.

    :return: A matcher.
    """

    def predicate(value):
        testcase.addDetail(name, text_content(value))
        return True

    return predicate


def readBodyText(response):
    """
    Read the response body and decode it using UTF-8.

    :param twisted.web.iweb.IResponse response: The response from which to
        read the body.

    :return: A ``Deferred`` that fires with the ``str`` body.
    """
    d = readBody(response)
    d.addCallback(lambda body: body.decode("utf-8"))
    return d


def has_header(name, value):
    """
    Create a matcher that matches a response object that includes the given
    name / value pair.

    :param str name: The name of the item in the HTTP header to match.
    :param str value: The value of the item in the HTTP header to match by equality.

    :return: A matcher.
    """
    return AfterPreprocessing(
        lambda headers: headers.getRawHeaders(name),
        Equals([value]),
    )


def parses_as_openmetrics():
    """
    Create a matcher that matches a ``str`` string that can be parsed as an
    OpenMetrics response and includes a certain well-known value expected by
    the tests.

    :return: A matcher.
    """
    # The parser throws if it does not like its input.
    # Wrapped in a list() to drain the generator.
    return AfterPreprocessing(
        lambda body: list(parser.text_string_to_metric_families(body)),
        AfterPreprocessing(
            lambda families: families[-1].name,
            Equals("tahoe_stats_storage_server_total_bucket_count"),
        ),
    )
