from twisted.trial import unittest

from ...storage_client import NativeStorageServer
from ...web.root import Root
from ...util.connection_status import ConnectionStatus

class FakeRoot(Root):
    def __init__(self):
        pass
    def now_fn(self):
        return 0

class FakeContext:
    def __init__(self):
        self.slots = {}
        self.tag = self
    def fillSlots(self, slotname, contents):
        self.slots[slotname] = contents

class RenderServiceRow(unittest.TestCase):
    def test_missing(self):
        # minimally-defined static servers just need anonymous-storage-FURL
        # and permutation-seed-base32. The WUI used to have problems
        # rendering servers that lacked nickname and version. This tests that
        # we can render such minimal servers.
        ann = {"anonymous-storage-FURL": "pb://w2hqnbaa25yw4qgcvghl5psa3srpfgw3@tcp:127.0.0.1:51309/vucto2z4fxment3vfxbqecblbf6zyp6x",
               "permutation-seed-base32": "w2hqnbaa25yw4qgcvghl5psa3srpfgw3",
               }
        s = NativeStorageServer("server_id", ann, None, {})
        cs = ConnectionStatus(False, "summary", {}, 0, 0)
        s.get_connection_status = lambda: cs

        r = FakeRoot()
        ctx = FakeContext()
        res = r.render_service_row(ctx, s)
        self.assertIdentical(res, ctx)
        self.assertEqual(ctx.slots["version"], "")
        self.assertEqual(ctx.slots["nickname"], "")
