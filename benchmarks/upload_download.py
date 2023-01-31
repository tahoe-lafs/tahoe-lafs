"""
First attempt at benchmarking uploads and downloads.

To run:

$ pytest benchmarks/upload_download.py -s -v -Wignore

To add latency of e.g. 60ms on Linux:

$ tc qdisc add dev lo root netem delay 30ms

To reset:

$ tc qdisc del dev lo root netem

TODO Parameterization (pytest?)

    - Foolscap vs not foolscap

    - Number of nodes

    - Data size

    - Number of needed/happy/total shares.

CAVEATS: The goal here isn't a realistic benchmark, or a benchmark that will be
measured over time, or is expected to be maintainable over time.  This is just
a quick and easy way to measure the speed of certain operations, compare HTTP
and Foolscap, and see the short-term impact of changes.

Eventually this will be replaced by a real benchmark suite that can be run over
time to measure something more meaningful.
"""

from time import time, process_time
from contextlib import contextmanager
from tempfile import mkdtemp
import os

from twisted.trial.unittest import TestCase

from allmydata.util.deferredutil import async_to_deferred
from allmydata.util.consumer import MemoryConsumer
from allmydata.test.common_system import SystemTestMixin
from allmydata.immutable.upload import Data as UData
from allmydata.mutable.publish import MutableData


@contextmanager
def timeit(name):
    start = time()
    start_cpu = process_time()
    try:
        yield
    finally:
        print(
            f"{name}: {time() - start:.3f} elapsed, {process_time() - start_cpu:.3f} CPU"
        )


class ImmutableBenchmarks(SystemTestMixin, TestCase):
    """Benchmarks for immutables."""

    # To use Foolscap, change to True:
    FORCE_FOOLSCAP_FOR_STORAGE = False

    @async_to_deferred
    async def setUp(self):
        SystemTestMixin.setUp(self)
        self.basedir = os.path.join(mkdtemp(), "nodes")

        # 2 nodes
        await self.set_up_nodes(2)

        # 1 share
        for c in self.clients:
            c.encoding_params["k"] = 1
            c.encoding_params["happy"] = 1
            c.encoding_params["n"] = 1

        print()

    @async_to_deferred
    async def test_upload_and_download_immutable(self):
        # To test larger files, change this:
        DATA = b"Some data to upload\n" * 10

        for i in range(5):
            # 1. Upload:
            with timeit("  upload"):
                uploader = self.clients[0].getServiceNamed("uploader")
                results = await uploader.upload(UData(DATA, convergence=None))

            # 2. Download:
            with timeit("download"):
                uri = results.get_uri()
                node = self.clients[1].create_node_from_uri(uri)
                mc = await node.read(MemoryConsumer(), 0, None)
                self.assertEqual(b"".join(mc.chunks), DATA)

    @async_to_deferred
    async def test_upload_and_download_mutable(self):
        # To test larger files, change this:
        DATA = b"Some data to upload\n" * 10

        for i in range(5):
            # 1. Upload:
            with timeit("  upload"):
                result = await self.clients[0].create_mutable_file(MutableData(DATA))

            # 2. Download:
            with timeit("download"):
                data = await result.download_best_version()
                self.assertEqual(data, DATA)
