"""
Tests for ```allmydata.web.status```.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from bs4 import BeautifulSoup
from twisted.web.template import flattenString

from allmydata.web.status import (
    Status,
    StatusElement,
)

from zope.interface import implementer

from allmydata.interfaces import IDownloadResults
from allmydata.web.status import DownloadStatusElement
from allmydata.immutable.downloader.status import DownloadStatus

from .common import (
    assert_soup_has_favicon,
    assert_soup_has_tag_with_content,
)
from ..common import TrialTestCase

from .test_web import FakeHistory

# Test that status.StatusElement can render HTML.
class StatusTests(TrialTestCase):

    def _render_status_page(self, active, recent):
        elem = StatusElement(active, recent)
        d = flattenString(None, elem)
        return self.successResultOf(d)

    def test_status_page(self):
        status = Status(FakeHistory())
        doc = self._render_status_page(
            status._get_active_operations(),
            status._get_recent_operations()
        )
        soup = BeautifulSoup(doc, 'html5lib')

        assert_soup_has_favicon(self, soup)

        assert_soup_has_tag_with_content(
            self, soup, u"title",
            u"Tahoe-LAFS - Recent and Active Operations"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"Active Operations:"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"td",
            u"retrieve"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"td",
            u"publish"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"td",
            u"download"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"td",
            u"upload"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            "Recent Operations:"
        )


@implementer(IDownloadResults)
class FakeDownloadResults(object):

    def __init__(self,
                 file_size=0,
                 servers_used=None,
                 server_problems=None,
                 servermap=None,
                 timings=None):
        """
        See IDownloadResults for parameters.
        """
        self.file_size = file_size
        self.servers_used = servers_used
        self.server_problems = server_problems
        self.servermap = servermap
        self.timings = timings


class FakeDownloadStatus(DownloadStatus):

    def __init__(self,
                 storage_index = None,
                 file_size = 0,
                 servers_used = None,
                 server_problems = None,
                 servermap = None,
                 timings = None):
        """
        See IDownloadStatus and IDownloadResults for parameters.
        """
        super(FakeDownloadStatus, self).__init__(storage_index, file_size)

        self.servers_used = servers_used
        self.server_problems = server_problems
        self.servermap = servermap
        self.timings = timings

    def get_results(self):
        return FakeDownloadResults(self.size,
                                   self.servers_used,
                                   self.server_problems,
                                   self.servermap,
                                   self.timings)


class DownloadStatusElementTests(TrialTestCase):
    """
    Tests for ```allmydata.web.status.DownloadStatusElement```.
    """

    def _render_download_status_element(self, status):
        """
        :param IDownloadStatus status:
        :return: HTML string rendered by DownloadStatusElement
        """
        elem = DownloadStatusElement(status)
        d = flattenString(None, elem)
        return self.successResultOf(d)

    def test_download_status_element(self):
        """
        See if we can render the page almost fully.
        """
        status = FakeDownloadStatus(
            b"si-1", 123,
            [b"s-1", b"s-2", b"s-3"],
            {b"s-1": "unknown problem"},
            {b"s-1": [1], b"s-2": [1,2], b"s-3": [2,3]},
            {"fetch_per_server":
             {b"s-1": [1], b"s-2": [2,3], b"s-3": [3,2]}}
        )

        result = self._render_download_status_element(status)
        soup = BeautifulSoup(result, 'html5lib')

        assert_soup_has_favicon(self, soup)

        assert_soup_has_tag_with_content(
            self, soup, u"title", u"Tahoe-LAFS - File Download Status"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"File Size: 123 bytes"
        )
        assert_soup_has_tag_with_content(
            self, soup, u"li", u"Progress: 0.0%"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"Servers Used: [omwtc], [omwte], [omwtg]"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"Server Problems:"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"[omwtc]: unknown problem"
        )

        assert_soup_has_tag_with_content(self, soup, u"li", u"Servermap:")

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"[omwtc] has share: #1"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"[omwte] has shares: #1,#2"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"[omwtg] has shares: #2,#3"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"Per-Server Segment Fetch Response Times:"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"[omwtc]: 1.00s"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"[omwte]: 2.00s, 3.00s"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"[omwtg]: 3.00s, 2.00s"
        )

    def test_download_status_element_partial(self):
        """
        See if we can render the page with incomplete download status.
        """
        status = FakeDownloadStatus()
        result = self._render_download_status_element(status)
        soup = BeautifulSoup(result, 'html5lib')

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"Servermap: None"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"File Size: 0 bytes"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li", u"Total: None (None)"
        )
