
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
    file_size = 0
    servers_used = 0
    server_problems = {"s-1": "unknown problem"}
    servermap = dict()
    timings = dict()


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
        return FakeDownloadResults()


class StatusTests(TrialTestCase):

    def _render_download_status_element(self):
        elem = DownloadStatusElement(FakeDownloadStatus("si-1", 123))
        d = flattenString(None, elem)
        return self.successResultOf(d)

    def test_download_status_element(self):
        result = self._render_download_status_element()
        soup = BeautifulSoup(result, 'html5lib')

        assert_soup_has_text(self, soup, u'Tahoe-LAFS - File Download Status')
        assert_soup_has_favicon(self, soup)

        assert_soup_has_tag_with_content(self, soup, u'li', u'[omwtc]: unknown problem')
