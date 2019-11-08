from mock import Mock

from twisted.trial import unittest
from twisted.web.test.requesthelper import DummyRequest

from ...storage_client import NativeStorageServer
from ...web.root import Root
from ...util.connection_status import ConnectionStatus
from allmydata.web.root import URIHandler
from allmydata.web.common import WebError

from hypothesis import given
from hypothesis.strategies import text


from ..common import (
    EMPTY_CLIENT_CONFIG,
)

class FakeRoot(Root):
    def __init__(self):
        pass
    def now_fn(self):
        return 0


class FakeContext(object):
    def __init__(self):
        self.slots = {}
        self.tag = self
    def fillSlots(self, slotname, contents):
        self.slots[slotname] = contents


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
        with self.assertRaises(WebError):
            self.res.render_GET(self.request)

    @given(
        text()
    )
    def test_hypothesis_error_caps(self, cap):
        """
        Let hypothesis try a bunch of invalid capabilities
        """
        self.request.args[b"uri"] = [cap.encode('utf8')]
        with self.assertRaises(WebError):
            self.res.render_GET(self.request)


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
        s = NativeStorageServer("server_id", ann, None, {}, EMPTY_CLIENT_CONFIG)
        cs = ConnectionStatus(False, "summary", {}, 0, 0)
        s.get_connection_status = lambda: cs

        r = FakeRoot()
        ctx = FakeContext()
        res = r.render_service_row(ctx, s)
        self.assertIdentical(res, ctx)
        self.assertEqual(ctx.slots["version"], "")
        self.assertEqual(ctx.slots["nickname"], "")
