"""
Tests related to the way ``allmydata.mutable`` handles different versions
of data for an object.
"""

from io import StringIO
import os
from typing import Optional

from ..common import AsyncTestCase
from testtools.matchers import (
    Equals,
    IsInstance,
    HasLength,
    Contains,
)

from allmydata import uri
from allmydata.interfaces import SDMF_VERSION, MDMF_VERSION
from allmydata.util import base32, consumer, mathutil
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.deferredutil import gatherResults
from allmydata.mutable.filenode import MutableFileNode
from allmydata.mutable.common import MODE_WRITE, MODE_READ, UnrecoverableFileError
from allmydata.mutable.publish import MutableData
from allmydata.scripts import debug
from ..no_network import GridTestMixin
from .util import PublishMixin
from .. import common_util as testutil

class Version(GridTestMixin, AsyncTestCase, testutil.ShouldFailMixin, \
              PublishMixin):
    def setUp(self):
        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker
        self.data = b"test data" * 100000 # about 900 KiB; MDMF
        self.small_data = b"test data" * 10 # 90 B; SDMF


    async def do_upload_mdmf(self, data: Optional[bytes] = None) -> MutableFileNode:
        if data is None:
            data = self.data
        n = await self.nm.create_mutable_file(MutableData(data),
                                              version=MDMF_VERSION)
        self.assertThat(n, IsInstance(MutableFileNode))
        self.assertThat(n._protocol_version, Equals(MDMF_VERSION))
        self.mdmf_node = n
        return n

    async def do_upload_sdmf(self, data: Optional[bytes] = None) -> MutableFileNode:
        if data is None:
            data = self.small_data
        n = await self.nm.create_mutable_file(MutableData(data))
        self.assertThat(n, IsInstance(MutableFileNode))
        self.assertThat(n._protocol_version, Equals(SDMF_VERSION))
        self.sdmf_node = n
        return n

    async def do_upload_empty_sdmf(self) -> MutableFileNode:
        n = await self.nm.create_mutable_file(MutableData(b""))
        self.assertThat(n, IsInstance(MutableFileNode))
        self.sdmf_zero_length_node = n
        self.assertThat(n._protocol_version, Equals(SDMF_VERSION))
        return n

    async def do_upload(self) -> MutableFileNode:
        await self.do_upload_mdmf()
        return await self.do_upload_sdmf()

    async def test_debug(self) -> None:
        n = await self.do_upload_mdmf()
        fso = debug.FindSharesOptions()
        storage_index = base32.b2a(n.get_storage_index())
        fso.si_s = str(storage_index, "utf-8")  # command-line options are unicode on Python 3
        fso.nodedirs = [os.path.dirname(abspath_expanduser_unicode(str(storedir)))
                        for (i,ss,storedir)
                        in self.iterate_servers()]
        # This attribute isn't defined on FindSharesOptions but `find_shares()`
        # definitely expects it...
        fso.stdout = StringIO()  # type: ignore[attr-defined]
        debug.find_shares(fso)
        sharefiles = fso.stdout.getvalue().splitlines()  # type: ignore[attr-defined]
        expected = self.nm.default_encoding_parameters["n"]
        self.assertThat(sharefiles, HasLength(expected))

        # This attribute isn't defined on DebugOptions but `dump_share()`
        # definitely expects it...
        do = debug.DumpOptions()
        do["filename"] = sharefiles[0]
        do.stdout = StringIO()  # type: ignore[attr-defined]
        debug.dump_share(do)
        output = do.stdout.getvalue()  # type: ignore[attr-defined]
        lines = set(output.splitlines())
        self.assertTrue("Mutable slot found:" in lines, output)
        self.assertTrue(" share_type: MDMF" in lines, output)
        self.assertTrue(" num_extra_leases: 0" in lines, output)
        self.assertTrue(" MDMF contents:" in lines, output)
        self.assertTrue("  seqnum: 1" in lines, output)
        self.assertTrue("  required_shares: 3" in lines, output)
        self.assertTrue("  total_shares: 10" in lines, output)
        self.assertTrue("  segsize: 131073" in lines, output)
        self.assertTrue("  datalen: %d" % len(self.data) in lines, output)
        vcap = str(n.get_verify_cap().to_string(), "utf-8")
        self.assertTrue("  verify-cap: %s" % vcap in lines, output)
        cso = debug.CatalogSharesOptions()
        cso.nodedirs = fso.nodedirs
        # Definitely not options on CatalogSharesOptions, but the code does use
        # stdout and stderr...
        cso.stdout = StringIO()  # type: ignore[attr-defined]
        cso.stderr = StringIO()  # type: ignore[attr-defined]
        debug.catalog_shares(cso)
        shares = cso.stdout.getvalue().splitlines()  # type: ignore[attr-defined]
        oneshare = shares[0] # all shares should be MDMF
        self.failIf(oneshare.startswith("UNKNOWN"), oneshare)
        self.assertTrue(oneshare.startswith("MDMF"), oneshare)
        fields = oneshare.split()
        self.assertThat(fields[0], Equals("MDMF"))
        self.assertThat(fields[1].encode("ascii"), Equals(storage_index))
        self.assertThat(fields[2], Equals("3/10"))
        self.assertThat(fields[3], Equals("%d" % len(self.data)))
        self.assertTrue(fields[4].startswith("#1:"), fields[3])
        # the rest of fields[4] is the roothash, which depends upon
        # encryption salts and is not constant. fields[5] is the
        # remaining time on the longest lease, which is timing dependent.
        # The rest of the line is the quoted pathname to the share.

    async def test_get_sequence_number(self) -> None:
        await self.do_upload()
        bv = await self.mdmf_node.get_best_readable_version()
        self.assertThat(bv.get_sequence_number(), Equals(1))
        bv = await self.sdmf_node.get_best_readable_version()
        self.assertThat(bv.get_sequence_number(), Equals(1))

        # Now update. The sequence number in both cases should be 1 in
        # both cases.
        new_data = MutableData(b"foo bar baz" * 100000)
        new_small_data = MutableData(b"foo bar baz" * 10)
        d1 = self.mdmf_node.overwrite(new_data)
        d2 = self.sdmf_node.overwrite(new_small_data)
        await gatherResults([d1, d2])
        bv = await self.mdmf_node.get_best_readable_version()
        self.assertThat(bv.get_sequence_number(), Equals(2))
        bv = await self.sdmf_node.get_best_readable_version()
        self.assertThat(bv.get_sequence_number(), Equals(2))

    async def test_cap_after_upload(self) -> None:
        # If we create a new mutable file and upload things to it, and
        # it's an MDMF file, we should get an MDMF cap back from that
        # file and should be able to use that.
        # That's essentially what MDMF node is, so just check that.
        await self.do_upload_mdmf()
        mdmf_uri = self.mdmf_node.get_uri()
        cap = uri.from_string(mdmf_uri)
        self.assertTrue(isinstance(cap, uri.WriteableMDMFFileURI))
        readonly_mdmf_uri = self.mdmf_node.get_readonly_uri()
        cap = uri.from_string(readonly_mdmf_uri)
        self.assertTrue(isinstance(cap, uri.ReadonlyMDMFFileURI))

    async def test_mutable_version(self) -> None:
        # assert that getting parameters from the IMutableVersion object
        # gives us the same data as getting them from the filenode itself
        await self.do_upload()
        bv = await self.mdmf_node.get_best_mutable_version()
        n = self.mdmf_node
        self.assertThat(bv.get_writekey(), Equals(n.get_writekey()))
        self.assertThat(bv.get_storage_index(), Equals(n.get_storage_index()))
        self.assertFalse(bv.is_readonly())

        bv = await self.sdmf_node.get_best_mutable_version()
        n = self.sdmf_node
        self.assertThat(bv.get_writekey(), Equals(n.get_writekey()))
        self.assertThat(bv.get_storage_index(), Equals(n.get_storage_index()))
        self.assertFalse(bv.is_readonly())


    async def test_get_readonly_version(self) -> None:
        await self.do_upload()
        bv = await self.mdmf_node.get_best_readable_version()
        self.assertTrue(bv.is_readonly())

        # Attempting to get a mutable version of a mutable file from a
        # filenode initialized with a readcap should return a readonly
        # version of that same node.
        ro = self.mdmf_node.get_readonly()
        v = await ro.get_best_mutable_version()
        self.assertTrue(v.is_readonly())

        bv = await self.sdmf_node.get_best_readable_version()
        self.assertTrue(bv.is_readonly())

        ro = self.sdmf_node.get_readonly()
        v = await ro.get_best_mutable_version()
        self.assertTrue(v.is_readonly())


    async def test_toplevel_overwrite(self) -> None:
        new_data = MutableData(b"foo bar baz" * 100000)
        new_small_data = MutableData(b"foo bar baz" * 10)
        await self.do_upload()
        await self.mdmf_node.overwrite(new_data)
        data = await self.mdmf_node.download_best_version()
        self.assertThat(data, Equals(b"foo bar baz" * 100000))
        await self.sdmf_node.overwrite(new_small_data)
        data = await self.sdmf_node.download_best_version()
        self.assertThat(data, Equals(b"foo bar baz" * 10))


    async def test_toplevel_modify(self) -> None:
        await self.do_upload()
        def modifier(old_contents, servermap, first_time):
            return old_contents + b"modified"
        await self.mdmf_node.modify(modifier)
        data = await self.mdmf_node.download_best_version()
        self.assertThat(data, Contains(b"modified"))
        await self.sdmf_node.modify(modifier)
        data = await self.sdmf_node.download_best_version()
        self.assertThat(data, Contains(b"modified"))


    async def test_version_modify(self) -> None:
        # TODO: When we can publish multiple versions, alter this test
        # to modify a version other than the best usable version, then
        # test to see that the best recoverable version is that.
        await self.do_upload()
        def modifier(old_contents, servermap, first_time):
            return old_contents + b"modified"
        await self.mdmf_node.modify(modifier)
        data = await self.mdmf_node.download_best_version()
        self.assertThat(data, Contains(b"modified"))
        await self.sdmf_node.modify(modifier)
        data = await self.sdmf_node.download_best_version()
        self.assertThat(data, Contains(b"modified"))


    async def test_download_version(self) -> None:
        await self.publish_multiple()
        # We want to have two recoverable versions on the grid.
        self._set_versions({0:0,2:0,4:0,6:0,8:0,
                            1:1,3:1,5:1,7:1,9:1})
        # Now try to download each version. We should get the plaintext
        # associated with that version.
        smap = await self._fn.get_servermap(mode=MODE_READ)
        versions = smap.recoverable_versions()
        assert len(versions) == 2

        self.servermap = smap
        self.version1, self.version2 = versions
        assert self.version1 != self.version2

        self.version1_seqnum = self.version1[0]
        self.version2_seqnum = self.version2[0]
        self.version1_index = self.version1_seqnum - 1
        self.version2_index = self.version2_seqnum - 1

        results = await self._fn.download_version(self.servermap, self.version1)
        self.assertThat(self.CONTENTS[self.version1_index],
                        Equals(results))
        results = await self._fn.download_version(self.servermap, self.version2)
        self.assertThat(self.CONTENTS[self.version2_index],
                        Equals(results))


    async def test_download_nonexistent_version(self) -> None:
        await self.do_upload_mdmf()
        servermap = await self.mdmf_node.get_servermap(mode=MODE_WRITE)
        await self.shouldFail(UnrecoverableFileError, "nonexistent version",
                              None,
                              self.mdmf_node.download_version, servermap,
                              "not a version")


    async def _test_partial_read(self, node, expected, modes, step) -> None:
        version = await node.get_best_readable_version()
        for (name, offset, length) in modes:
            await self._do_partial_read(version, name, expected, offset, length)
        # then read the whole thing, but only a few bytes at a time, and see
        # that the results are what we expect.
        c = consumer.MemoryConsumer()
        for i in range(0, len(expected), step):
            await version.read(c, i, step)
        self.assertThat(expected, Equals(b"".join(c.chunks)))

    async def _do_partial_read(self, version, name, expected, offset, length) -> None:
        c = consumer.MemoryConsumer()
        await version.read(c, offset, length)
        if length is None:
            expected_range = expected[offset:]
        else:
            expected_range = expected[offset:offset+length]
        results = b"".join(c.chunks)
        if results != expected_range:
            print("read([%d]+%s) got %d bytes, not %d" % \
                  (offset, length, len(results), len(expected_range)))
            print("got: %r ... %r" % (results[:20], results[-20:]))
            print("exp: %r ... %r" % (expected_range[:20], expected_range[-20:]))
            self.fail("results[%s] != expected_range" % name)

    async def test_partial_read_mdmf_0(self) -> None:
        data = b""
        result = await self.do_upload_mdmf(data=data)
        modes = [("all1",    0,0),
                 ("all2",    0,None),
                 ]
        await self._test_partial_read(result, data, modes, 1)

    async def test_partial_read_mdmf_large(self) -> None:
        segment_boundary = mathutil.next_multiple(128 * 1024, 3)
        modes = [("start_on_segment_boundary",              segment_boundary, 50),
                 ("ending_one_byte_after_segment_boundary", segment_boundary-50, 51),
                 ("zero_length_at_start",                   0, 0),
                 ("zero_length_in_middle",                  50, 0),
                 ("zero_length_at_segment_boundary",        segment_boundary, 0),
                 ("complete_file1",                         0, len(self.data)),
                 ("complete_file2",                         0, None),
                 ]
        result = await self.do_upload_mdmf()
        await self._test_partial_read(result, self.data, modes, 10000)

    async def test_partial_read_sdmf_0(self) -> None:
        data = b""
        modes = [("all1",    0,0),
                 ("all2",    0,None),
                 ]
        result = await self.do_upload_sdmf(data=data)
        await self._test_partial_read(result, data, modes, 1)

    async def test_partial_read_sdmf_2(self) -> None:
        data = b"hi"
        modes = [("one_byte",                  0, 1),
                 ("last_byte",                 1, 1),
                 ("last_byte2",                1, None),
                 ("complete_file",             0, 2),
                 ("complete_file2",            0, None),
                 ]
        result = await self.do_upload_sdmf(data=data)
        await self._test_partial_read(result, data, modes, 1)

    async def test_partial_read_sdmf_90(self) -> None:
        modes = [("start_at_middle",           50, 40),
                 ("start_at_middle2",          50, None),
                 ("zero_length_at_start",      0, 0),
                 ("zero_length_in_middle",     50, 0),
                 ("zero_length_at_end",        90, 0),
                 ("complete_file1",            0, None),
                 ("complete_file2",            0, 90),
                 ]
        result = await self.do_upload_sdmf()
        await self._test_partial_read(result, self.small_data, modes, 10)

    async def test_partial_read_sdmf_100(self) -> None:
        data = b"test data "*10
        modes = [("start_at_middle",           50, 50),
                 ("start_at_middle2",          50, None),
                 ("zero_length_at_start",      0, 0),
                 ("zero_length_in_middle",     50, 0),
                 ("complete_file1",            0, 100),
                 ("complete_file2",            0, None),
                 ]
        result = await self.do_upload_sdmf(data=data)
        await self._test_partial_read(result, data, modes, 10)

    async def _test_read_and_download(self, node, expected) -> None:
        version = await node.get_best_readable_version()
        c = consumer.MemoryConsumer()
        await version.read(c)
        self.assertThat(expected, Equals(b"".join(c.chunks)))

        c2 = consumer.MemoryConsumer()
        await version.read(c2, offset=0, size=len(expected))
        self.assertThat(expected, Equals(b"".join(c2.chunks)))

        data = await node.download_best_version()
        self.assertThat(expected, Equals(data))

    async def test_read_and_download_mdmf(self) -> None:
        result = await self.do_upload_mdmf()
        await self._test_read_and_download(result, self.data)

    async def test_read_and_download_sdmf(self) -> None:
        result = await self.do_upload_sdmf()
        await self._test_read_and_download(result, self.small_data)

    async def test_read_and_download_sdmf_zero_length(self) -> None:
        result = await self.do_upload_empty_sdmf()
        await self._test_read_and_download(result, b"")
