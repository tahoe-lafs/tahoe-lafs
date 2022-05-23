"""
Ported to Python 3.
"""
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os
from six.moves import cStringIO as StringIO

from twisted.internet import defer
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


    def do_upload_mdmf(self, data=None):
        if data is None:
            data = self.data
        d = self.nm.create_mutable_file(MutableData(data),
                                        version=MDMF_VERSION)
        def _then(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            self.assertThat(n._protocol_version, Equals(MDMF_VERSION))
            self.mdmf_node = n
            return n
        d.addCallback(_then)
        return d

    def do_upload_sdmf(self, data=None):
        if data is None:
            data = self.small_data
        d = self.nm.create_mutable_file(MutableData(data))
        def _then(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            self.assertThat(n._protocol_version, Equals(SDMF_VERSION))
            self.sdmf_node = n
            return n
        d.addCallback(_then)
        return d

    def do_upload_empty_sdmf(self):
        d = self.nm.create_mutable_file(MutableData(b""))
        def _then(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            self.sdmf_zero_length_node = n
            self.assertThat(n._protocol_version, Equals(SDMF_VERSION))
            return n
        d.addCallback(_then)
        return d

    def do_upload(self):
        d = self.do_upload_mdmf()
        d.addCallback(lambda ign: self.do_upload_sdmf())
        return d

    def test_debug(self):
        d = self.do_upload_mdmf()
        def _debug(n):
            fso = debug.FindSharesOptions()
            storage_index = base32.b2a(n.get_storage_index())
            fso.si_s = str(storage_index, "utf-8")  # command-line options are unicode on Python 3
            fso.nodedirs = [os.path.dirname(abspath_expanduser_unicode(str(storedir)))
                            for (i,ss,storedir)
                            in self.iterate_servers()]
            fso.stdout = StringIO()
            fso.stderr = StringIO()
            debug.find_shares(fso)
            sharefiles = fso.stdout.getvalue().splitlines()
            expected = self.nm.default_encoding_parameters["n"]
            self.assertThat(sharefiles, HasLength(expected))

            do = debug.DumpOptions()
            do["filename"] = sharefiles[0]
            do.stdout = StringIO()
            debug.dump_share(do)
            output = do.stdout.getvalue()
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
            cso.stdout = StringIO()
            cso.stderr = StringIO()
            debug.catalog_shares(cso)
            shares = cso.stdout.getvalue().splitlines()
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
        d.addCallback(_debug)
        return d

    def test_get_sequence_number(self):
        d = self.do_upload()
        d.addCallback(lambda ign: self.mdmf_node.get_best_readable_version())
        d.addCallback(lambda bv:
            self.assertThat(bv.get_sequence_number(), Equals(1)))
        d.addCallback(lambda ignored:
            self.sdmf_node.get_best_readable_version())
        d.addCallback(lambda bv:
            self.assertThat(bv.get_sequence_number(), Equals(1)))
        # Now update. The sequence number in both cases should be 1 in
        # both cases.
        def _do_update(ignored):
            new_data = MutableData(b"foo bar baz" * 100000)
            new_small_data = MutableData(b"foo bar baz" * 10)
            d1 = self.mdmf_node.overwrite(new_data)
            d2 = self.sdmf_node.overwrite(new_small_data)
            dl = gatherResults([d1, d2])
            return dl
        d.addCallback(_do_update)
        d.addCallback(lambda ignored:
            self.mdmf_node.get_best_readable_version())
        d.addCallback(lambda bv:
            self.assertThat(bv.get_sequence_number(), Equals(2)))
        d.addCallback(lambda ignored:
            self.sdmf_node.get_best_readable_version())
        d.addCallback(lambda bv:
            self.assertThat(bv.get_sequence_number(), Equals(2)))
        return d


    def test_cap_after_upload(self):
        # If we create a new mutable file and upload things to it, and
        # it's an MDMF file, we should get an MDMF cap back from that
        # file and should be able to use that.
        # That's essentially what MDMF node is, so just check that.
        d = self.do_upload_mdmf()
        def _then(ign):
            mdmf_uri = self.mdmf_node.get_uri()
            cap = uri.from_string(mdmf_uri)
            self.assertTrue(isinstance(cap, uri.WriteableMDMFFileURI))
            readonly_mdmf_uri = self.mdmf_node.get_readonly_uri()
            cap = uri.from_string(readonly_mdmf_uri)
            self.assertTrue(isinstance(cap, uri.ReadonlyMDMFFileURI))
        d.addCallback(_then)
        return d

    def test_mutable_version(self):
        # assert that getting parameters from the IMutableVersion object
        # gives us the same data as getting them from the filenode itself
        d = self.do_upload()
        d.addCallback(lambda ign: self.mdmf_node.get_best_mutable_version())
        def _check_mdmf(bv):
            n = self.mdmf_node
            self.assertThat(bv.get_writekey(), Equals(n.get_writekey()))
            self.assertThat(bv.get_storage_index(), Equals(n.get_storage_index()))
            self.assertFalse(bv.is_readonly())
        d.addCallback(_check_mdmf)
        d.addCallback(lambda ign: self.sdmf_node.get_best_mutable_version())
        def _check_sdmf(bv):
            n = self.sdmf_node
            self.assertThat(bv.get_writekey(), Equals(n.get_writekey()))
            self.assertThat(bv.get_storage_index(), Equals(n.get_storage_index()))
            self.assertFalse(bv.is_readonly())
        d.addCallback(_check_sdmf)
        return d


    def test_get_readonly_version(self):
        d = self.do_upload()
        d.addCallback(lambda ign: self.mdmf_node.get_best_readable_version())
        d.addCallback(lambda bv: self.assertTrue(bv.is_readonly()))

        # Attempting to get a mutable version of a mutable file from a
        # filenode initialized with a readcap should return a readonly
        # version of that same node.
        d.addCallback(lambda ign: self.mdmf_node.get_readonly())
        d.addCallback(lambda ro: ro.get_best_mutable_version())
        d.addCallback(lambda v: self.assertTrue(v.is_readonly()))

        d.addCallback(lambda ign: self.sdmf_node.get_best_readable_version())
        d.addCallback(lambda bv: self.assertTrue(bv.is_readonly()))

        d.addCallback(lambda ign: self.sdmf_node.get_readonly())
        d.addCallback(lambda ro: ro.get_best_mutable_version())
        d.addCallback(lambda v: self.assertTrue(v.is_readonly()))
        return d


    def test_toplevel_overwrite(self):
        new_data = MutableData(b"foo bar baz" * 100000)
        new_small_data = MutableData(b"foo bar baz" * 10)
        d = self.do_upload()
        d.addCallback(lambda ign: self.mdmf_node.overwrite(new_data))
        d.addCallback(lambda ignored:
            self.mdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.assertThat(data, Equals(b"foo bar baz" * 100000)))
        d.addCallback(lambda ignored:
            self.sdmf_node.overwrite(new_small_data))
        d.addCallback(lambda ignored:
            self.sdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.assertThat(data, Equals(b"foo bar baz" * 10)))
        return d


    def test_toplevel_modify(self):
        d = self.do_upload()
        def modifier(old_contents, servermap, first_time):
            return old_contents + b"modified"
        d.addCallback(lambda ign: self.mdmf_node.modify(modifier))
        d.addCallback(lambda ignored:
            self.mdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.assertThat(data, Contains(b"modified")))
        d.addCallback(lambda ignored:
            self.sdmf_node.modify(modifier))
        d.addCallback(lambda ignored:
            self.sdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.assertThat(data, Contains(b"modified")))
        return d


    def test_version_modify(self):
        # TODO: When we can publish multiple versions, alter this test
        # to modify a version other than the best usable version, then
        # test to see that the best recoverable version is that.
        d = self.do_upload()
        def modifier(old_contents, servermap, first_time):
            return old_contents + b"modified"
        d.addCallback(lambda ign: self.mdmf_node.modify(modifier))
        d.addCallback(lambda ignored:
            self.mdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.assertThat(data, Contains(b"modified")))
        d.addCallback(lambda ignored:
            self.sdmf_node.modify(modifier))
        d.addCallback(lambda ignored:
            self.sdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.assertThat(data, Contains(b"modified")))
        return d


    def test_download_version(self):
        d = self.publish_multiple()
        # We want to have two recoverable versions on the grid.
        d.addCallback(lambda res:
                      self._set_versions({0:0,2:0,4:0,6:0,8:0,
                                          1:1,3:1,5:1,7:1,9:1}))
        # Now try to download each version. We should get the plaintext
        # associated with that version.
        d.addCallback(lambda ignored:
            self._fn.get_servermap(mode=MODE_READ))
        def _got_servermap(smap):
            versions = smap.recoverable_versions()
            assert len(versions) == 2

            self.servermap = smap
            self.version1, self.version2 = versions
            assert self.version1 != self.version2

            self.version1_seqnum = self.version1[0]
            self.version2_seqnum = self.version2[0]
            self.version1_index = self.version1_seqnum - 1
            self.version2_index = self.version2_seqnum - 1

        d.addCallback(_got_servermap)
        d.addCallback(lambda ignored:
            self._fn.download_version(self.servermap, self.version1))
        d.addCallback(lambda results:
            self.assertThat(self.CONTENTS[self.version1_index],
                                 Equals(results)))
        d.addCallback(lambda ignored:
            self._fn.download_version(self.servermap, self.version2))
        d.addCallback(lambda results:
            self.assertThat(self.CONTENTS[self.version2_index],
                                 Equals(results)))
        return d


    def test_download_nonexistent_version(self):
        d = self.do_upload_mdmf()
        d.addCallback(lambda ign: self.mdmf_node.get_servermap(mode=MODE_WRITE))
        def _set_servermap(servermap):
            self.servermap = servermap
        d.addCallback(_set_servermap)
        d.addCallback(lambda ignored:
           self.shouldFail(UnrecoverableFileError, "nonexistent version",
                           None,
                           self.mdmf_node.download_version, self.servermap,
                           "not a version"))
        return d


    def _test_partial_read(self, node, expected, modes, step):
        d = node.get_best_readable_version()
        for (name, offset, length) in modes:
            d.addCallback(self._do_partial_read, name, expected, offset, length)
        # then read the whole thing, but only a few bytes at a time, and see
        # that the results are what we expect.
        def _read_data(version):
            c = consumer.MemoryConsumer()
            d2 = defer.succeed(None)
            for i in range(0, len(expected), step):
                d2.addCallback(lambda ignored, i=i: version.read(c, i, step))
            d2.addCallback(lambda ignored:
                self.assertThat(expected, Equals(b"".join(c.chunks))))
            return d2
        d.addCallback(_read_data)
        return d

    def _do_partial_read(self, version, name, expected, offset, length):
        c = consumer.MemoryConsumer()
        d = version.read(c, offset, length)
        if length is None:
            expected_range = expected[offset:]
        else:
            expected_range = expected[offset:offset+length]
        d.addCallback(lambda ignored: b"".join(c.chunks))
        def _check(results):
            if results != expected_range:
                print("read([%d]+%s) got %d bytes, not %d" % \
                      (offset, length, len(results), len(expected_range)))
                print("got: %s ... %s" % (results[:20], results[-20:]))
                print("exp: %s ... %s" % (expected_range[:20], expected_range[-20:]))
                self.fail("results[%s] != expected_range" % name)
            return version # daisy-chained to next call
        d.addCallback(_check)
        return d

    def test_partial_read_mdmf_0(self):
        data = b""
        d = self.do_upload_mdmf(data=data)
        modes = [("all1",    0,0),
                 ("all2",    0,None),
                 ]
        d.addCallback(self._test_partial_read, data, modes, 1)
        return d

    def test_partial_read_mdmf_large(self):
        segment_boundary = mathutil.next_multiple(128 * 1024, 3)
        modes = [("start_on_segment_boundary",              segment_boundary, 50),
                 ("ending_one_byte_after_segment_boundary", segment_boundary-50, 51),
                 ("zero_length_at_start",                   0, 0),
                 ("zero_length_in_middle",                  50, 0),
                 ("zero_length_at_segment_boundary",        segment_boundary, 0),
                 ("complete_file1",                         0, len(self.data)),
                 ("complete_file2",                         0, None),
                 ]
        d = self.do_upload_mdmf()
        d.addCallback(self._test_partial_read, self.data, modes, 10000)
        return d

    def test_partial_read_sdmf_0(self):
        data = b""
        modes = [("all1",    0,0),
                 ("all2",    0,None),
                 ]
        d = self.do_upload_sdmf(data=data)
        d.addCallback(self._test_partial_read, data, modes, 1)
        return d

    def test_partial_read_sdmf_2(self):
        data = b"hi"
        modes = [("one_byte",                  0, 1),
                 ("last_byte",                 1, 1),
                 ("last_byte2",                1, None),
                 ("complete_file",             0, 2),
                 ("complete_file2",            0, None),
                 ]
        d = self.do_upload_sdmf(data=data)
        d.addCallback(self._test_partial_read, data, modes, 1)
        return d

    def test_partial_read_sdmf_90(self):
        modes = [("start_at_middle",           50, 40),
                 ("start_at_middle2",          50, None),
                 ("zero_length_at_start",      0, 0),
                 ("zero_length_in_middle",     50, 0),
                 ("zero_length_at_end",        90, 0),
                 ("complete_file1",            0, None),
                 ("complete_file2",            0, 90),
                 ]
        d = self.do_upload_sdmf()
        d.addCallback(self._test_partial_read, self.small_data, modes, 10)
        return d

    def test_partial_read_sdmf_100(self):
        data = b"test data "*10
        modes = [("start_at_middle",           50, 50),
                 ("start_at_middle2",          50, None),
                 ("zero_length_at_start",      0, 0),
                 ("zero_length_in_middle",     50, 0),
                 ("complete_file1",            0, 100),
                 ("complete_file2",            0, None),
                 ]
        d = self.do_upload_sdmf(data=data)
        d.addCallback(self._test_partial_read, data, modes, 10)
        return d


    def _test_read_and_download(self, node, expected):
        d = node.get_best_readable_version()
        def _read_data(version):
            c = consumer.MemoryConsumer()
            c2 = consumer.MemoryConsumer()
            d2 = defer.succeed(None)
            d2.addCallback(lambda ignored: version.read(c))
            d2.addCallback(lambda ignored:
                self.assertThat(expected, Equals(b"".join(c.chunks))))

            d2.addCallback(lambda ignored: version.read(c2, offset=0,
                                                        size=len(expected)))
            d2.addCallback(lambda ignored:
                self.assertThat(expected, Equals(b"".join(c2.chunks))))
            return d2
        d.addCallback(_read_data)
        d.addCallback(lambda ignored: node.download_best_version())
        d.addCallback(lambda data: self.assertThat(expected, Equals(data)))
        return d

    def test_read_and_download_mdmf(self):
        d = self.do_upload_mdmf()
        d.addCallback(self._test_read_and_download, self.data)
        return d

    def test_read_and_download_sdmf(self):
        d = self.do_upload_sdmf()
        d.addCallback(self._test_read_and_download, self.small_data)
        return d

    def test_read_and_download_sdmf_zero_length(self):
        d = self.do_upload_empty_sdmf()
        d.addCallback(self._test_read_and_download, b"")
        return d
