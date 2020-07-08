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

    def __init__(self, file_size):
    	self.file_size = file_size
    	self.servers_used = ["s-1", "s-2", "s-3"]
    	self.server_problems = {"s-1": "unknown problem"}
    	self.servermap = {"s-1": [1,2,3], "s-2": [2,3,4], "s-3": [0,1,3]}
    	self.timings = { "fetch_per_server": {"s-1": [1,2,3], "s-2": [2], "s-3": [3]}}


@implementer(IDownloadStatus)
class FakeDownloadStatus(object):

    def __init__(self, storage_index, size):
        self.storage_index = storage_index
        self.size = size
        self.dyhb_requests = []
        self.read_events = []
        self.segment_events = []
        self.block_requests = []

    def get_started(self):
        return None

    def get_storage_index(self):
        return self.storage_index

    def get_size(self):
        return self.size

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
        return FakeDownloadResults(self.size)

# Tests for code in allmydata.web.status.DownloadStatusElement
class DownloadStatusElementTests(TrialTestCase):

    def _render_download_status_element(self):
        elem = DownloadStatusElement(FakeDownloadStatus("si-1", 123))
        d = flattenString(None, elem)
        return self.successResultOf(d)

    def test_download_status_element(self):
        result = self._render_download_status_element()
        soup = BeautifulSoup(result, 'html5lib')

        assert_soup_has_text(self, soup, u"Tahoe-LAFS - File Download Status")
        assert_soup_has_favicon(self, soup)

        assert_soup_has_tag_with_content(self, soup, u"li", u"File Size: 123 bytes")
        assert_soup_has_tag_with_content(self, soup, u"li", u"Progress: 0.0%")

        assert_soup_has_tag_with_content(self, soup, u"li", u"Servers Used: [omwtc], [omwte], [omwtg]")

        assert_soup_has_tag_with_content(self, soup, u"li", u"Server Problems:")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtc]: unknown problem")

        assert_soup_has_tag_with_content(self, soup, u"li", u"Servermap:")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtc] has shares: #1,#2,#3")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwte] has shares: #2,#3,#4")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtg] has shares: #0,#1,#3")

        assert_soup_has_tag_with_content(self, soup, u"li", u"Per-Server Segment Fetch Response Times:")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtc]: 1.00s, 2.00s, 3.00s")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwte]: 2.00s")
        assert_soup_has_tag_with_content(self, soup, u"li", u"[omwtg]: 3.00s")
