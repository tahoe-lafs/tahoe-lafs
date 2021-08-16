"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import time

from urllib.parse import (
    quote,
)

from bs4 import (
    BeautifulSoup,
)

from twisted.web.template import Tag
from twisted.web.test.requesthelper import DummyRequest
from twisted.application import service
from testtools.twistedsupport import succeeded
from twisted.internet.defer import inlineCallbacks

from ...storage_client import (
    NativeStorageServer,
    StorageFarmBroker,
)
from ...web.root import RootElement
from ...util.connection_status import ConnectionStatus
from allmydata.web.root import URIHandler
from allmydata.client import _Client

from .common import (
    assert_soup_has_tag_with_attributes,
)
from ..common_web import (
    render,
)
from ..common import (
    EMPTY_CLIENT_CONFIG,
)

from ..common import (
    SyncTestCase,
)

from testtools.matchers import (
    Equals,
    Contains,
    AfterPreprocessing,
)

class RenderSlashUri(SyncTestCase):
    """
    Ensure that URIs starting with /uri?uri= only accept valid
    capabilities
    """

    def setUp(self):
        self.client = object()
        self.res = URIHandler(self.client)
        super(RenderSlashUri, self).setUp()

    @inlineCallbacks
    def test_valid_query_redirect(self):
        """
        A syntactically valid capability given in the ``uri`` query argument
        results in a redirect.
        """
        cap = (
            b"URI:CHK:nt2xxmrccp7sursd6yh2thhcky:"
            b"mukesarwdjxiyqsjinbfiiro6q7kgmmekocxfjcngh23oxwyxtzq:2:5:5874882"
        )
        query_args = {b"uri": [cap]}
        response_body = yield render(self.res, query_args)
        soup = BeautifulSoup(response_body, 'html5lib')
        tag = assert_soup_has_tag_with_attributes(
            self,
            soup,
            u"meta",
            {u"http-equiv": "refresh"},
        )
        self.assertThat(
            tag.attrs.get(u"content"),
            Contains(quote(cap, safe="")),
        )

    def test_invalid(self):
        """
        A syntactically invalid capbility results in an error.
        """
        query_args = {b"uri": [b"not a capability"]}
        response_body = render(self.res, query_args)
        self.assertThat(
            response_body,
            succeeded(AfterPreprocessing(bytes, Equals(b"Invalid capability"))),
        )


class RenderServiceRow(SyncTestCase):
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
        srv = NativeStorageServer(b"server_id", ann, None, {}, EMPTY_CLIENT_CONFIG)
        srv.get_connection_status = lambda: ConnectionStatus(False, "summary", {}, 0, 0)

        class FakeClient(_Client):
            def __init__(self):
                service.MultiService.__init__(self)
                self.storage_broker = StorageFarmBroker(
                    permute_peers=True,
                    tub_maker=None,
                    node_config=EMPTY_CLIENT_CONFIG,
                )
                self.storage_broker.test_add_server(b"test-srv", srv)

        root = RootElement(FakeClient(), time.time)
        req = DummyRequest(b"")
        tag = Tag(b"")

        # Pick all items from services table.
        items = root.services_table(req, tag).item(req, tag)

        # Coerce `items` to list and pick the first item from it.
        item = list(items)[0]

        self.assertThat(item.slotData.get("version"), Equals(""))
        self.assertThat(item.slotData.get("nickname"), Equals(""))
