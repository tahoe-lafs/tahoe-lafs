# Tests for code in allmydata.web.status

from bs4 import BeautifulSoup
from twisted.web.template import flattenString
from zope.interface import implementer

from allmydata.interfaces import (
    IDownloadResults,
    IDownloadStatus,
)
from allmydata.web.status import DownloadStatusElement

from .common import (
    assert_soup_has_favicon,
    assert_soup_has_text,
    assert_soup_has_tag_with_content,
)
from ..common import TrialTestCase


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


@implementer(IDownloadStatus)
class FakeDownloadStatus(object):

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
        self.storage_index = storage_index
        self.file_size = file_size
        self.dyhb_requests = []
        self.read_events = []
        self.segment_events = []
        self.block_requests = []

        self.servers_used = servers_used
        self.server_problems = server_problems
        self.servermap = servermap
        self.timings = timings

    def get_started(self):
        return None

    def get_storage_index(self):
        return self.storage_index

    def get_size(self):
        return self.file_size

    def using_helper(self):
        return False

    def get_status(self):
        return "FakeDownloadStatus"

    def get_progress(self):
        return 0

    def get_active():
        return False

    def get_counter():
        return 0

    def get_results(self):
        return FakeDownloadResults(self.file_size,
                                   self.servers_used,
                                   self.server_problems,
                                   self.servermap,
                                   self.timings)

# Tests for code in allmydata.web.status.DownloadStatusElement
class DownloadStatusElementTests(TrialTestCase):

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
        status = FakeDownloadStatus("si-1", 123,
                                    ["s-1", "s-2", "s-3"],
                                    {"s-1": "unknown problem"},
                                    {"s-1": [1], "s-2": [1,2], "s-3": [2,3]},
                                    {"fetch_per_server": {"s-1": [1], "s-2": [2,3], "s-3": [3,2]}})

        result = self._render_download_status_element(status)
        soup = BeautifulSoup(result, 'html5lib')

        assert_soup_has_text(self, soup, u"Tahoe-LAFS - File Download Status")
        assert_soup_has_favicon(self, soup)

        assert_soup_has_tag_with_content(self, soup, u"li", u"File Size: 123 bytes")
        assert_soup_has_tag_with_content(self, soup, u"li", u"Progress: 0.0%")

        assert_soup_has_tag_with_content(self, soup, u"li", u"Servers Used: [omwtc], [omwte], [omwtg]")

        assert_soup_has_tag_with_content(self, soup, u"li", u"Server Problems:")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtc]: unknown problem")

        assert_soup_has_tag_with_content(self, soup, u"li", u"Servermap:")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtc] has share: #1")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwte] has shares: #1,#2")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtg] has shares: #2,#3")

        assert_soup_has_tag_with_content(self, soup, u"li", u"Per-Server Segment Fetch Response Times:")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtc]: 1.00s")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwte]: 2.00s, 3.00s")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtg]: 3.00s, 2.00s")
