"""
Tests for ```allmydata.web.status```.
"""

from bs4 import BeautifulSoup
from twisted.web.template import flattenString

from allmydata.web.status import (
    Status,
    StatusElement,
)

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
