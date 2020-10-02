from mock import Mock

import time

from bs4 import BeautifulSoup

from twisted.trial import unittest
from twisted.web.template import Tag
from twisted.web.test.requesthelper import DummyRequest
from twisted.application import service

from ...storage_client import (
    NativeStorageServer,
    StorageFarmBroker,
)
from ...web.root import RootElement
from ...util.connection_status import ConnectionStatus
from allmydata.web.root import URIHandler
from allmydata.client import _Client

from hypothesis import given
from hypothesis.strategies import text

from .common import (
    assert_soup_has_tag_with_content,
)

from ..common import (
    EMPTY_CLIENT_CONFIG,
)

class RenderSlashUri(unittest.TestCase):
    """
    Ensure that URIs starting with /uri?uri= only accept valid
    capabilities
    """

    def setUp(self):
        self.request = DummyRequest(b"/uri")
        self.request.fields = {}

        def prepathURL():
            return b"http://127.0.0.1.99999/" + b"/".join(self.request.prepath)

        self.request.prePathURL = prepathURL
        self.client = Mock()
        self.res = URIHandler(self.client)

    def test_valid(self):
        """
        A valid capbility does not result in error
        """
        self.request.args[b"uri"] = [(
            b"URI:CHK:nt2xxmrccp7sursd6yh2thhcky:"
            b"mukesarwdjxiyqsjinbfiiro6q7kgmmekocxfjcngh23oxwyxtzq:2:5:5874882"
        )]
        self.res.render_GET(self.request)

    def test_invalid(self):
        """
        A (trivially) invalid capbility is an error
        """
        self.request.args[b"uri"] = [b"not a capability"]
        response_body = self.res.render_GET(self.request)

        soup = BeautifulSoup(response_body, 'html5lib')

        assert_soup_has_tag_with_content(
            self, soup, "title", "400 - Error",
        )
        assert_soup_has_tag_with_content(
            self, soup, "h1", "Error",
        )
        assert_soup_has_tag_with_content(
            self, soup, "p", "Invalid capability",
        )

    @given(
        text()
    )
    def test_hypothesis_error_caps(self, cap):
        """
        Let hypothesis try a bunch of invalid capabilities
        """
        self.request.args[b"uri"] = [cap.encode('utf8')]
        response_body = self.res.render_GET(self.request)

        soup = BeautifulSoup(response_body, 'html5lib')

        assert_soup_has_tag_with_content(
            self, soup, "title", "400 - Error",
        )
        assert_soup_has_tag_with_content(
            self, soup, "h1", "Error",
        )
        assert_soup_has_tag_with_content(
            self, soup, "p", "Invalid capability",
        )


class RenderServiceRow(unittest.TestCase):
    def test_missing(self):
        """
        minimally-defined static servers just need anonymous-storage-FURL
        and permutation-seed-base32. The WUI used to have problems
        rendering servers that lacked nickname and version. This tests that
        we can render such minimal servers.
        """
        ann = {"anonymous-storage-FURL": "pb://w2hqnbaa25yw4qgcvghl5psa3srpfgw3@tcp:127.0.0.1:51309/vucto2z4fxment3vfxbqecblbf6zyp6x",
               "permutation-seed-base32": "w2hqnbaa25yw4qgcvghl5psa3srpfgw3",
               }
        srv = NativeStorageServer("server_id", ann, None, {}, EMPTY_CLIENT_CONFIG)
        srv.get_connection_status = lambda: ConnectionStatus(False, "summary", {}, 0, 0)

        class FakeClient(_Client):
            def __init__(self):
                service.MultiService.__init__(self)
                self.storage_broker = StorageFarmBroker(
                    permute_peers=True,
                    tub_maker=None,
                    node_config=EMPTY_CLIENT_CONFIG,
                )
                self.storage_broker.test_add_server("test-srv", srv)

        root = RootElement(FakeClient(), time.time)
        req = DummyRequest(b"")
        tag = Tag(b"")

        # Pick all items from services table.
        items = root.services_table(req, tag).item(req, tag)

        # Coerce `items` to list and pick the first item from it.
        item = list(items)[0]

        self.assertEqual(item.slotData.get("version"), "")
        self.assertEqual(item.slotData.get("nickname"), "")
