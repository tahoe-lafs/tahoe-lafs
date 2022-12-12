"""
First attempt at benchmarking immutable uploads and downloads.

TODO Parameterization (pytest?)
- Foolscap vs not foolscap
- Number of nodes
- Data size
- Number of needed/happy/total shares.
"""

from twisted.trial.unittest import TestCase

from allmydata.util.deferredutil import async_to_deferred
from allmydata.util.consumer import MemoryConsumer
from allmydata.test.common_system import SystemTestMixin
from allmydata.immutable.upload import Data as UData


class ImmutableBenchmarks(SystemTestMixin, TestCase):
    """Benchmarks for immutables."""

    FORCE_FOOLSCAP_FOR_STORAGE = True

    @async_to_deferred
    async def test_upload_and_download(self):
        self.basedir = self.mktemp()
        
        DATA = b"Some data to upload\n" * 2000

        # 3 nodes
        await self.set_up_nodes(3)

        # 3 shares
        for c in self.clients:
            c.encoding_params["k"] = 3
            c.encoding_params["happy"] = 3
            c.encoding_params["n"] = 3

        # 1. Upload:
        uploader = self.clients[0].getServiceNamed("uploader")
        results = await uploader.upload(UData(DATA, convergence=None))

        # 2. Download:
        uri = results.get_uri()
        node = self.clients[1].create_node_from_uri(uri)
        mc = await node.read(MemoryConsumer(), 0, None)
        self.assertEqual(b"".join(mc.chunks), DATA)
