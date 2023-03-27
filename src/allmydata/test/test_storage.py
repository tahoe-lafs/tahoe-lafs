"""
Tests for allmydata.storage.

Ported to Python 3.
"""

from __future__ import annotations
from future.utils import native_str, bytes_to_native_str, bchr
from six import ensure_str

from io import (
    BytesIO,
)
import time
import os.path
import platform
import stat
import struct
import shutil
from functools import partial
from uuid import uuid4

from testtools.matchers import (
    Equals,
    NotEquals,
    Contains,
    HasLength,
    IsInstance,
)

from twisted.trial import unittest

from twisted.internet import defer
from twisted.internet.task import Clock

from hypothesis import given, strategies, example

import itertools
from allmydata import interfaces
from allmydata.util import fileutil, hashutil, base32
from allmydata.storage.server import (
    StorageServer, DEFAULT_RENEWAL_TIME, FoolscapStorageServer,
)
from allmydata.storage.shares import get_share_file
from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.mutable_schema import (
    ALL_SCHEMAS as ALL_MUTABLE_SCHEMAS,
)
from allmydata.storage.immutable import (
    BucketWriter, BucketReader, ShareFile, FoolscapBucketWriter,
    FoolscapBucketReader,
)
from allmydata.storage.immutable_schema import (
    ALL_SCHEMAS as ALL_IMMUTABLE_SCHEMAS,
)
from allmydata.storage.common import storage_index_to_dir, \
     UnknownMutableContainerVersionError, UnknownImmutableContainerVersionError, \
     si_b2a, si_a2b
from allmydata.storage.lease import LeaseInfo
from allmydata.immutable.layout import WriteBucketProxy, WriteBucketProxy_v2, \
     ReadBucketProxy, _WriteBuffer
from allmydata.mutable.layout import MDMFSlotWriteProxy, MDMFSlotReadProxy, \
                                     LayoutInvalid, MDMFSIGNABLEHEADER, \
                                     SIGNED_PREFIX, MDMFHEADER, \
                                     MDMFOFFSETS, SDMFSlotWriteProxy, \
                                     PRIVATE_KEY_SIZE, \
                                     SIGNATURE_SIZE, \
                                     VERIFICATION_KEY_SIZE, \
                                     SHARE_HASH_CHAIN_SIZE
from allmydata.interfaces import (
    BadWriteEnablerError, DataTooLargeError, ConflictingWriteError,
)
from allmydata.test.no_network import NoNetworkServer
from allmydata.storage_client import (
    _StorageServer,
)
from .common import (
    LoggingServiceParent,
    ShouldFailMixin,
    FakeDisk,
    SyncTestCase,
    AsyncTestCase,
)

from .common_util import FakeCanary
from .common_storage import (
    upload_immutable,
    upload_mutable,
)
from .strategies import (
    offsets,
    lengths,
)


class UtilTests(SyncTestCase):
    """Tests for allmydata.storage.common and .shares."""

    def test_encoding(self):
        """b2a/a2b are the same as base32."""
        s = b"\xFF HELLO \xF3"
        result = si_b2a(s)
        self.assertThat(base32.b2a(s), Equals(result))
        self.assertThat(si_a2b(result), Equals(s))

    def test_storage_index_to_dir(self):
        """storage_index_to_dir creates a native string path."""
        s = b"\xFF HELLO \xF3"
        path = storage_index_to_dir(s)
        parts = os.path.split(path)
        self.assertThat(parts[0], Equals(parts[1][:2]))
        self.assertThat(path, IsInstance(native_str))

    def test_get_share_file_mutable(self):
        """A mutable share is identified by get_share_file()."""
        path = self.mktemp()
        msf = MutableShareFile(path)
        msf.create(b"12", b"abc")  # arbitrary values
        loaded = get_share_file(path)
        self.assertThat(loaded, IsInstance(MutableShareFile))
        self.assertThat(loaded.home, Equals(path))

    def test_get_share_file_immutable(self):
        """An immutable share is identified by get_share_file()."""
        path = self.mktemp()
        _ = ShareFile(path, max_size=1000, create=True)
        loaded = get_share_file(path)
        self.assertThat(loaded, IsInstance(ShareFile))
        self.assertThat(loaded.home, Equals(path))


class FakeStatsProvider(object):
    def count(self, name, delta=1):
        pass
    def register_producer(self, producer):
        pass


class Bucket(SyncTestCase):
    def make_workdir(self, name):
        basedir = os.path.join("storage", "Bucket", name)
        incoming = os.path.join(basedir, "tmp", "bucket")
        final = os.path.join(basedir, "bucket")
        fileutil.make_dirs(basedir)
        fileutil.make_dirs(os.path.join(basedir, "tmp"))
        return incoming, final

    def bucket_writer_closed(self, bw, consumed):
        pass
    def add_latency(self, category, latency):
        pass
    def count(self, name, delta=1):
        pass

    def make_lease(self):
        owner_num = 0
        renew_secret = os.urandom(32)
        cancel_secret = os.urandom(32)
        expiration_time = time.time() + 5000
        return LeaseInfo(owner_num, renew_secret, cancel_secret,
                         expiration_time, b"\x00" * 20)

    def test_create(self):
        incoming, final = self.make_workdir("test_create")
        bw = BucketWriter(self, incoming, final, 200, self.make_lease(), Clock())
        bw.write(0, b"a"*25)
        bw.write(25, b"b"*25)
        bw.write(50, b"c"*25)
        bw.write(75, b"d"*7)
        bw.close()

    def test_readwrite(self):
        incoming, final = self.make_workdir("test_readwrite")
        bw = BucketWriter(self, incoming, final, 200, self.make_lease(), Clock())
        bw.write(0, b"a"*25)
        bw.write(25, b"b"*25)
        bw.write(50, b"c"*7) # last block may be short
        bw.close()

        # now read from it
        br = BucketReader(self, bw.finalhome)
        self.assertThat(br.read(0, 25), Equals(b"a"*25))
        self.assertThat(br.read(25, 25), Equals(b"b"*25))
        self.assertThat(br.read(50, 7), Equals(b"c"*7))

    def test_write_past_size_errors(self):
        """Writing beyond the size of the bucket throws an exception."""
        for (i, (offset, length)) in enumerate([(0, 201), (10, 191), (202, 34)]):
            incoming, final = self.make_workdir(
                "test_write_past_size_errors-{}".format(i)
            )
            bw = BucketWriter(self, incoming, final, 200, self.make_lease(), Clock())
            with self.assertRaises(DataTooLargeError):
                bw.write(offset, b"a" * length)

    @given(
        maybe_overlapping_offset=strategies.integers(min_value=0, max_value=98),
        maybe_overlapping_length=strategies.integers(min_value=1, max_value=100),
    )
    def test_overlapping_writes_ok_if_matching(
            self, maybe_overlapping_offset, maybe_overlapping_length
    ):
        """
        Writes that overlap with previous writes are OK when the content is the
        same.
        """
        length = 100
        expected_data = b"".join(bchr(i) for i in range(100))
        incoming, final = self.make_workdir("overlapping_writes_{}".format(uuid4()))
        bw = BucketWriter(
            self, incoming, final, length, self.make_lease(), Clock()
        )
        # Three writes: 10-19, 30-39, 50-59. This allows for a bunch of holes.
        bw.write(10, expected_data[10:20])
        bw.write(30, expected_data[30:40])
        bw.write(50, expected_data[50:60])
        # Then, an overlapping write but with matching data:
        bw.write(
            maybe_overlapping_offset,
            expected_data[
                maybe_overlapping_offset:maybe_overlapping_offset + maybe_overlapping_length
            ]
        )
        # Now fill in the holes:
        bw.write(0, expected_data[0:10])
        bw.write(20, expected_data[20:30])
        bw.write(40, expected_data[40:50])
        bw.write(60, expected_data[60:])
        bw.close()

        br = BucketReader(self, bw.finalhome)
        self.assertEqual(br.read(0, length), expected_data)

    @given(
        maybe_overlapping_offset=strategies.integers(min_value=0, max_value=98),
        maybe_overlapping_length=strategies.integers(min_value=1, max_value=100),
    )
    def test_overlapping_writes_not_ok_if_different(
            self, maybe_overlapping_offset, maybe_overlapping_length
    ):
        """
        Writes that overlap with previous writes fail with an exception if the
        contents don't match.
        """
        length = 100
        incoming, final = self.make_workdir("overlapping_writes_{}".format(uuid4()))
        bw = BucketWriter(
            self, incoming, final, length, self.make_lease(), Clock()
        )
        # Three writes: 10-19, 30-39, 50-59. This allows for a bunch of holes.
        bw.write(10, b"1" * 10)
        bw.write(30, b"1" * 10)
        bw.write(50, b"1" * 10)
        # Then, write something that might overlap with some of them, but
        # conflicts. Then fill in holes left by first three writes. Conflict is
        # inevitable.
        with self.assertRaises(ConflictingWriteError):
            bw.write(
                maybe_overlapping_offset,
                b'X' * min(maybe_overlapping_length, length - maybe_overlapping_offset),
            )
            bw.write(0, b"1" * 10)
            bw.write(20, b"1" * 10)
            bw.write(40, b"1" * 10)
            bw.write(60, b"1" * 40)

    @given(
        offsets=strategies.lists(
            strategies.integers(min_value=0, max_value=99),
            min_size=20,
            max_size=20
        ),
    )
    @example(offsets=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10, 40, 70])
    def test_writes_return_when_finished(
            self, offsets
    ):
        """
        The ``BucketWriter.write()`` return true if and only if the maximum
        size has been reached via potentially overlapping writes.  The
        remaining ranges can be checked via ``BucketWriter.required_ranges()``.
        """
        incoming, final = self.make_workdir("overlapping_writes_{}".format(uuid4()))
        bw = BucketWriter(
            self, incoming, final, 100, self.make_lease(), Clock()
        )
        local_written = [0] * 100
        for offset in offsets:
            length = min(30, 100 - offset)
            data = b"1" * length
            for i in range(offset, offset+length):
                local_written[i] = 1
            finished = bw.write(offset, data)
            self.assertEqual(finished, sum(local_written) == 100)
            required_ranges = bw.required_ranges()
            for i in range(0, 100):
                self.assertEqual(local_written[i] == 1, required_ranges.get(i) is None)

    def test_read_past_end_of_share_data(self):
        # test vector for immutable files (hard-coded contents of an immutable share
        # file):

        # The following immutable share file content is identical to that
        # generated with storage.immutable.ShareFile from Tahoe-LAFS v1.8.2
        # with share data == 'a'. The total size of this content is 85
        # bytes.

        containerdata = struct.pack('>LLL', 1, 1, 1)

        # A Tahoe-LAFS storage client would send as the share_data a
        # complicated string involving hash trees and a URI Extension Block
        # -- see allmydata/immutable/layout.py . This test, which is
        # simulating a client, just sends 'a'.
        share_data = b'a'

        ownernumber = struct.pack('>L', 0)
        renewsecret  = b'THIS LETS ME RENEW YOUR FILE....'
        assert len(renewsecret) == 32
        cancelsecret = b'THIS LETS ME KILL YOUR FILE HAHA'
        assert len(cancelsecret) == 32
        expirationtime = struct.pack('>L', DEFAULT_RENEWAL_TIME) # 31 days in seconds

        lease_data = ownernumber + renewsecret + cancelsecret + expirationtime

        share_file_data = containerdata + share_data + lease_data

        incoming, final = self.make_workdir("test_read_past_end_of_share_data")

        fileutil.write(final, share_file_data)

        class MockStorageServer(object):
            def add_latency(self, category, latency):
                pass
            def count(self, name, delta=1):
                pass

        mockstorageserver = MockStorageServer()

        # Now read from it.
        br = BucketReader(mockstorageserver, final)

        self.assertThat(br.read(0, len(share_data)), Equals(share_data))

        # Read past the end of share data to get the cancel secret.
        read_length = len(share_data) + len(ownernumber) + len(renewsecret) + len(cancelsecret)

        result_of_read = br.read(0, read_length)
        self.assertThat(result_of_read, Equals(share_data))

        result_of_read = br.read(0, len(share_data)+1)
        self.assertThat(result_of_read, Equals(share_data))

    def _assert_timeout_only_after_30_minutes(self, clock, bw):
        """
        The ``BucketWriter`` times out and is closed after 30 minutes, but not
        sooner.
        """
        self.assertFalse(bw.closed)
        # 29 minutes pass. Everything is fine.
        for i in range(29):
            clock.advance(60)
            self.assertFalse(bw.closed, "Bucket closed after only %d minutes" % (i + 1,))
        # After the 30th minute, the bucket is closed due to lack of writes.
        clock.advance(60)
        self.assertTrue(bw.closed)

    def test_bucket_expires_if_no_writes_for_30_minutes(self):
        """
        If a ``BucketWriter`` receives no writes for 30 minutes, it is removed.
        """
        incoming, final = self.make_workdir("test_bucket_expires")
        clock = Clock()
        bw = BucketWriter(self, incoming, final, 200, self.make_lease(), clock)
        self._assert_timeout_only_after_30_minutes(clock, bw)

    def test_bucket_writes_delay_timeout(self):
        """
        So long as the ``BucketWriter`` receives writes, the the removal
        timeout is put off.
        """
        incoming, final = self.make_workdir("test_bucket_writes_delay_timeout")
        clock = Clock()
        bw = BucketWriter(self, incoming, final, 200, self.make_lease(), clock)
        # 29 minutes pass, getting close to the timeout...
        clock.advance(29 * 60)
        # .. but we receive a write! So that should delay the timeout again to
        # another 30 minutes.
        bw.write(0, b"hello")
        self._assert_timeout_only_after_30_minutes(clock, bw)

    def test_bucket_closing_cancels_timeout(self):
        """
        Closing cancels the ``BucketWriter`` timeout.
        """
        incoming, final = self.make_workdir("test_bucket_close_timeout")
        clock = Clock()
        bw = BucketWriter(self, incoming, final, 10, self.make_lease(), clock)
        self.assertTrue(clock.getDelayedCalls())
        bw.close()
        self.assertFalse(clock.getDelayedCalls())

    def test_bucket_aborting_cancels_timeout(self):
        """
        Closing cancels the ``BucketWriter`` timeout.
        """
        incoming, final = self.make_workdir("test_bucket_abort_timeout")
        clock = Clock()
        bw = BucketWriter(self, incoming, final, 10, self.make_lease(), clock)
        self.assertTrue(clock.getDelayedCalls())
        bw.abort()
        self.assertFalse(clock.getDelayedCalls())


class RemoteBucket(object):

    def __init__(self, target):
        self.target = target
        self.read_count = 0
        self.write_count = 0

    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self.target, "remote_" + methname)
            return meth(*args, **kwargs)

        if methname == "slot_readv":
            self.read_count += 1
        if "writev" in methname:
            self.write_count += 1

        return defer.maybeDeferred(_call)


class BucketProxy(AsyncTestCase):
    def make_bucket(self, name, size):
        basedir = os.path.join("storage", "BucketProxy", name)
        incoming = os.path.join(basedir, "tmp", "bucket")
        final = os.path.join(basedir, "bucket")
        fileutil.make_dirs(basedir)
        fileutil.make_dirs(os.path.join(basedir, "tmp"))
        bw = BucketWriter(self, incoming, final, size, self.make_lease(), Clock())
        rb = RemoteBucket(FoolscapBucketWriter(bw))
        return bw, rb, final

    def make_lease(self):
        owner_num = 0
        renew_secret = os.urandom(32)
        cancel_secret = os.urandom(32)
        expiration_time = time.time() + 5000
        return LeaseInfo(owner_num, renew_secret, cancel_secret,
                         expiration_time, b"\x00" * 20)

    def bucket_writer_closed(self, bw, consumed):
        pass
    def add_latency(self, category, latency):
        pass
    def count(self, name, delta=1):
        pass

    def test_create(self):
        bw, rb, sharefname = self.make_bucket("test_create", 500)
        bp = WriteBucketProxy(rb, None,
                              data_size=300,
                              block_size=10,
                              num_segments=5,
                              num_share_hashes=3,
                              uri_extension_size=500)
        self.assertTrue(interfaces.IStorageBucketWriter.providedBy(bp), bp)

    def _do_test_readwrite(self, name, header_size, wbp_class, rbp_class):
        # Let's pretend each share has 100 bytes of data, and that there are
        # 4 segments (25 bytes each), and 8 shares total. So the two
        # per-segment merkle trees (crypttext_hash_tree,
        # block_hashes) will have 4 leaves and 7 nodes each. The per-share
        # merkle tree (share_hashes) has 8 leaves and 15 nodes, and we need 3
        # nodes. Furthermore, let's assume the uri_extension is 500 bytes
        # long. That should make the whole share:
        #
        # 0x24 + 100 + 7*32 + 7*32 + 7*32 + 3*(2+32) + 4+500 = 1414 bytes long
        # 0x44 + 100 + 7*32 + 7*32 + 7*32 + 3*(2+32) + 4+500 = 1446 bytes long

        sharesize = header_size + 100 + 7*32 + 7*32 + 7*32 + 3*(2+32) + 4+500

        crypttext_hashes = [hashutil.tagged_hash(b"crypt", b"bar%d" % i)
                            for i in range(7)]
        block_hashes = [hashutil.tagged_hash(b"block", b"bar%d" % i)
                        for i in range(7)]
        share_hashes = [(i, hashutil.tagged_hash(b"share", b"bar%d" % i))
                        for i in (1,9,13)]
        uri_extension = b"s" + b"E"*498 + b"e"

        bw, rb, sharefname = self.make_bucket(name, sharesize)
        bp = wbp_class(rb, None,
                       data_size=95,
                       block_size=25,
                       num_segments=4,
                       num_share_hashes=3,
                       uri_extension_size=len(uri_extension))

        d = bp.put_header()
        d.addCallback(lambda res: bp.put_block(0, b"a"*25))
        d.addCallback(lambda res: bp.put_block(1, b"b"*25))
        d.addCallback(lambda res: bp.put_block(2, b"c"*25))
        d.addCallback(lambda res: bp.put_block(3, b"d"*20))
        d.addCallback(lambda res: bp.put_crypttext_hashes(crypttext_hashes))
        d.addCallback(lambda res: bp.put_block_hashes(block_hashes))
        d.addCallback(lambda res: bp.put_share_hashes(share_hashes))
        d.addCallback(lambda res: bp.put_uri_extension(uri_extension))
        d.addCallback(lambda res: bp.close())

        # now read everything back
        def _start_reading(res):
            br = BucketReader(self, sharefname)
            rb = RemoteBucket(FoolscapBucketReader(br))
            server = NoNetworkServer(b"abc", None)
            rbp = rbp_class(rb, server, storage_index=b"")
            self.assertThat(repr(rbp), Contains("to peer"))
            self.assertTrue(interfaces.IStorageBucketReader.providedBy(rbp), rbp)

            d1 = rbp.get_block_data(0, 25, 25)
            d1.addCallback(lambda res: self.failUnlessEqual(res, b"a"*25))
            d1.addCallback(lambda res: rbp.get_block_data(1, 25, 25))
            d1.addCallback(lambda res: self.failUnlessEqual(res, b"b"*25))
            d1.addCallback(lambda res: rbp.get_block_data(2, 25, 25))
            d1.addCallback(lambda res: self.failUnlessEqual(res, b"c"*25))
            d1.addCallback(lambda res: rbp.get_block_data(3, 25, 20))
            d1.addCallback(lambda res: self.failUnlessEqual(res, b"d"*20))

            d1.addCallback(lambda res: rbp.get_crypttext_hashes())
            d1.addCallback(lambda res:
                           self.failUnlessEqual(res, crypttext_hashes))
            d1.addCallback(lambda res: rbp.get_block_hashes(set(range(4))))
            d1.addCallback(lambda res: self.failUnlessEqual(res, block_hashes))
            d1.addCallback(lambda res: rbp.get_share_hashes())
            d1.addCallback(lambda res: self.failUnlessEqual(res, share_hashes))
            d1.addCallback(lambda res: rbp.get_uri_extension())
            d1.addCallback(lambda res:
                           self.failUnlessEqual(res, uri_extension))

            return d1

        d.addCallback(_start_reading)

        return d

    def test_readwrite_v1(self):
        return self._do_test_readwrite("test_readwrite_v1",
                                       0x24, WriteBucketProxy, ReadBucketProxy)

    def test_readwrite_v2(self):
        return self._do_test_readwrite("test_readwrite_v2",
                                       0x44, WriteBucketProxy_v2, ReadBucketProxy)

class Server(AsyncTestCase):

    def setUp(self):
        super(Server, self).setUp()
        self.sparent = LoggingServiceParent()
        self.sparent.startService()
        self._lease_secret = itertools.count()
        self.addCleanup(self.sparent.stopService)

    def workdir(self, name):
        basedir = os.path.join("storage", "Server", name)
        return basedir

    def create(self, name, reserved_space=0, klass=StorageServer, clock=None):
        if clock is None:
            clock = Clock()
        workdir = self.workdir(name)
        ss = klass(workdir, b"\x00" * 20, reserved_space=reserved_space,
                   stats_provider=FakeStatsProvider(),
                   clock=clock)
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        self.create("test_create")

    def test_declares_fixed_1528(self):
        ss = self.create("test_declares_fixed_1528")
        ver = ss.get_version()
        sv1 = ver[b'http://allmydata.org/tahoe/protocols/storage/v1']
        self.assertTrue(sv1.get(b'prevents-read-past-end-of-share-data'), sv1)

    def test_declares_maximum_share_sizes(self):
        ss = self.create("test_declares_maximum_share_sizes")
        ver = ss.get_version()
        sv1 = ver[b'http://allmydata.org/tahoe/protocols/storage/v1']
        self.assertThat(sv1, Contains(b'maximum-immutable-share-size'))
        self.assertThat(sv1, Contains(b'maximum-mutable-share-size'))

    def test_declares_available_space(self):
        ss = self.create("test_declares_available_space")
        ver = ss.get_version()
        sv1 = ver[b'http://allmydata.org/tahoe/protocols/storage/v1']
        self.assertThat(sv1, Contains(b'available-space'))

    def allocate(self, ss, storage_index, sharenums, size, renew_leases=True):
        """
        Call directly into the storage server's allocate_buckets implementation,
        skipping the Foolscap layer.
        """
        renew_secret = hashutil.my_renewal_secret_hash(b"%d" % next(self._lease_secret))
        cancel_secret = hashutil.my_cancel_secret_hash(b"%d" % next(self._lease_secret))
        if isinstance(ss, FoolscapStorageServer):
            ss = ss._server
        return ss.allocate_buckets(
            storage_index,
            renew_secret, cancel_secret,
            sharenums, size,
            renew_leases=renew_leases,
        )

    def test_large_share(self):
        syslow = platform.system().lower()
        if 'cygwin' in syslow or 'windows' in syslow or 'darwin' in syslow:
            raise unittest.SkipTest("If your filesystem doesn't support efficient sparse files then it is very expensive (Mac OS X and Windows don't support efficient sparse files).")

        avail = fileutil.get_available_space('.', 512*2**20)
        if avail <= 4*2**30:
            raise unittest.SkipTest("This test will spuriously fail if you have less than 4 GiB free on your filesystem.")

        ss = self.create("test_large_share")

        already,writers = self.allocate(ss, b"allocate", [0], 2**32+2)
        self.assertThat(set(), Equals(already))
        self.assertThat(set([0]), Equals(set(writers.keys())))

        shnum, bucket = list(writers.items())[0]
        # This test is going to hammer your filesystem if it doesn't make a sparse file for this.  :-(
        bucket.write(2**32, b"ab")
        bucket.close()

        readers = ss.get_buckets(b"allocate")
        reader = readers[shnum]
        self.assertThat(b"ab", Equals(reader.read(2**32, 2)))

    def test_dont_overfill_dirs(self):
        """
        This test asserts that if you add a second share whose storage index
        share lots of leading bits with an extant share (but isn't the exact
        same storage index), this won't add an entry to the share directory.
        """
        ss = self.create("test_dont_overfill_dirs")
        already, writers = self.allocate(ss, b"storageindex", [0], 10)
        for i, wb in writers.items():
            wb.write(0, b"%10d" % i)
            wb.close()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")
        children_of_storedir = set(os.listdir(storedir))

        # Now store another one under another storageindex that has leading
        # chars the same as the first storageindex.
        already, writers = self.allocate(ss, b"storageindey", [0], 10)
        for i, wb in writers.items():
            wb.write(0, b"%10d" % i)
            wb.close()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")
        new_children_of_storedir = set(os.listdir(storedir))
        self.assertThat(new_children_of_storedir, Equals(children_of_storedir))

    def test_remove_incoming(self):
        ss = self.create("test_remove_incoming")
        already, writers = self.allocate(ss, b"vid", list(range(3)), 10)
        for i,wb in writers.items():
            wb.write(0, b"%10d" % i)
            wb.close()
        incoming_share_dir = wb.incominghome
        incoming_bucket_dir = os.path.dirname(incoming_share_dir)
        incoming_prefix_dir = os.path.dirname(incoming_bucket_dir)
        incoming_dir = os.path.dirname(incoming_prefix_dir)
        self.assertFalse(os.path.exists(incoming_bucket_dir), incoming_bucket_dir)
        self.assertFalse(os.path.exists(incoming_prefix_dir), incoming_prefix_dir)
        self.assertTrue(os.path.exists(incoming_dir), incoming_dir)

    def test_abort(self):
        # remote_abort, when called on a writer, should make sure that
        # the allocated size of the bucket is not counted by the storage
        # server when accounting for space.
        ss = self.create("test_abort")
        already, writers = self.allocate(ss, b"allocate", [0, 1, 2], 150)
        self.assertThat(ss.allocated_size(), NotEquals(0))

        # Now abort the writers.
        for writer in writers.values():
            writer.abort()
        self.assertThat(ss.allocated_size(), Equals(0))

    def test_immutable_length(self):
        """
        ``get_immutable_share_length()`` returns the length of an immutable
        share, as does ``BucketWriter.get_length()``..
        """
        ss = self.create("test_immutable_length")
        _, writers = self.allocate(ss, b"allocate", [22], 75)
        bucket = writers[22]
        bucket.write(0, b"X" * 75)
        bucket.close()
        self.assertThat(ss.get_immutable_share_length(b"allocate", 22), Equals(75))
        self.assertThat(ss.get_buckets(b"allocate")[22].get_length(), Equals(75))

    def test_allocate(self):
        ss = self.create("test_allocate")

        self.assertThat(ss.get_buckets(b"allocate"), Equals({}))

        already,writers = self.allocate(ss, b"allocate", [0,1,2], 75)
        self.assertThat(already, Equals(set()))
        self.assertThat(set(writers.keys()), Equals(set([0,1,2])))

        # while the buckets are open, they should not count as readable
        self.assertThat(ss.get_buckets(b"allocate"), Equals({}))

        # close the buckets
        for i,wb in writers.items():
            wb.write(0, b"%25d" % i)
            wb.close()
            # aborting a bucket that was already closed is a no-op
            wb.abort()

        # now they should be readable
        b = ss.get_buckets(b"allocate")
        self.assertThat(set(b.keys()), Equals(set([0,1,2])))
        self.assertThat(b[0].read(0, 25), Equals(b"%25d" % 0))
        b_str = str(b[0])
        self.assertThat(b_str, Contains("BucketReader"))
        self.assertThat(b_str, Contains("mfwgy33dmf2g 0"))

        # now if we ask about writing again, the server should offer those
        # three buckets as already present. It should offer them even if we
        # don't ask about those specific ones.
        already,writers = self.allocate(ss, b"allocate", [2,3,4], 75)
        self.assertThat(already, Equals(set([0,1,2])))
        self.assertThat(set(writers.keys()), Equals(set([3,4])))

        # while those two buckets are open for writing, the server should
        # refuse to offer them to uploaders

        already2,writers2 = self.allocate(ss, b"allocate", [2,3,4,5], 75)
        self.assertThat(already2, Equals(set([0,1,2])))
        self.assertThat(set(writers2.keys()), Equals(set([5])))

        # aborting the writes should remove the tempfiles
        for i,wb in writers2.items():
            wb.abort()
        already2,writers2 = self.allocate(ss, b"allocate", [2,3,4,5], 75)
        self.assertThat(already2, Equals(set([0,1,2])))
        self.assertThat(set(writers2.keys()), Equals(set([5])))

        for i,wb in writers2.items():
            wb.abort()
        for i,wb in writers.items():
            wb.abort()

    def test_allocate_without_lease_renewal(self):
        """
        ``StorageServer._allocate_buckets`` does not renew leases on existing
        shares if ``renew_leases`` is ``False``.
        """
        first_lease = 456
        second_lease = 543
        storage_index = b"allocate"

        clock = Clock()
        clock.advance(first_lease)
        ss = self.create(
            "test_allocate_without_lease_renewal",
            clock=clock,
        )

        # Put a share on there
        already, writers = self.allocate(
            ss, storage_index, [0], 1, renew_leases=False,
        )
        (writer,) = writers.values()
        writer.write(0, b"x")
        writer.close()

        # It should have a lease granted at the current time.
        shares = dict(ss.get_shares(storage_index))
        self.assertEqual(
            [first_lease],
            list(
                lease.get_grant_renew_time_time()
                for lease
                in ShareFile(shares[0]).get_leases()
            ),
        )

        # Let some time pass so we can tell if the lease on share 0 is
        # renewed.
        clock.advance(second_lease)

        # Put another share on there.
        already, writers = self.allocate(
            ss, storage_index, [1], 1, renew_leases=False,
        )
        (writer,) = writers.values()
        writer.write(0, b"x")
        writer.close()

        # The first share's lease expiration time is unchanged.
        shares = dict(ss.get_shares(storage_index))
        self.assertThat(
            [first_lease],
            Equals(list(
                lease.get_grant_renew_time_time()
                for lease
                in ShareFile(shares[0]).get_leases()
            )),
        )

    def test_bad_container_version(self):
        ss = self.create("test_bad_container_version")
        a,w = self.allocate(ss, b"si1", [0], 10)
        w[0].write(0, b"\xff"*10)
        w[0].close()

        fn = os.path.join(ss.sharedir, storage_index_to_dir(b"si1"), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write(struct.pack(">L", 0)) # this is invalid: minimum used is v1
        f.close()

        ss.get_buckets(b"allocate")

        e = self.failUnlessRaises(UnknownImmutableContainerVersionError,
                                  ss.get_buckets, b"si1")
        self.assertThat(e.filename, Equals(fn))
        self.assertThat(e.version, Equals(0))
        self.assertThat(str(e), Contains("had unexpected version 0"))

    def test_disconnect(self):
        # simulate a disconnection
        ss = FoolscapStorageServer(self.create("test_disconnect"))
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        canary = FakeCanary()
        already,writers = ss.remote_allocate_buckets(
            b"disconnect",
            renew_secret,
            cancel_secret,
            sharenums=[0,1,2],
            allocated_size=75,
            canary=canary,
        )
        self.assertThat(already, Equals(set()))
        self.assertThat(set(writers.keys()), Equals(set([0,1,2])))
        for (f,args,kwargs) in list(canary.disconnectors.values()):
            f(*args, **kwargs)
        del already
        del writers

        # that ought to delete the incoming shares
        already,writers = self.allocate(ss, b"disconnect", [0,1,2], 75)
        self.assertThat(already, Equals(set()))
        self.assertThat(set(writers.keys()), Equals(set([0,1,2])))

    def test_reserved_space_immutable_lease(self):
        """
        If there is not enough available space to store an additional lease on an
        immutable share then ``remote_add_lease`` fails with ``NoSpace`` when
        an attempt is made to use it to create a new lease.
        """
        disk = FakeDisk(total=1024, used=0)
        self.patch(fileutil, "get_disk_stats", disk.get_disk_stats)

        ss = self.create("test_reserved_space_immutable_lease")

        storage_index = b"x" * 16
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        shares = {0: b"y" * 500}
        upload_immutable(ss, storage_index, renew_secret, cancel_secret, shares)

        # use up all the available space
        disk.use(disk.available)

        # Different secrets to produce a different lease, not a renewal.
        renew_secret = b"R" * 32
        cancel_secret = b"C" * 32
        with self.assertRaises(interfaces.NoSpace):
            ss.add_lease(storage_index, renew_secret, cancel_secret)

    def test_reserved_space_mutable_lease(self):
        """
        If there is not enough available space to store an additional lease on a
        mutable share then ``remote_add_lease`` fails with ``NoSpace`` when an
        attempt is made to use it to create a new lease.
        """
        disk = FakeDisk(total=1024, used=0)
        self.patch(fileutil, "get_disk_stats", disk.get_disk_stats)

        ss = self.create("test_reserved_space_mutable_lease")

        renew_secrets = iter(
            "{}{}".format("r" * 31, i).encode("ascii")
            for i
            in range(5)
        )

        storage_index = b"x" * 16
        write_enabler = b"w" * 32
        cancel_secret = b"c" * 32
        secrets = (write_enabler, next(renew_secrets), cancel_secret)
        shares = {0: b"y" * 500}
        upload_mutable(ss, storage_index, secrets, shares)

        # use up all the available space
        disk.use(disk.available)

        # The upload created one lease.  There is room for three more leases
        # in the share header.  Even if we're out of disk space, on a boring
        # enough filesystem we can write these.
        for i in range(3):
            ss.add_lease(storage_index, next(renew_secrets), cancel_secret)

        # Having used all of the space for leases in the header, we would have
        # to allocate storage for the next lease.  Since there is no space
        # available, this must fail instead.
        with self.assertRaises(interfaces.NoSpace):
            ss.add_lease(storage_index, next(renew_secrets), cancel_secret)


    def test_reserved_space(self):
        reserved = 10000
        allocated = 0

        def call_get_disk_stats(whichdir, reserved_space=0):
            self.failUnlessEqual(reserved_space, reserved)
            return {
              'free_for_nonroot': 15000 - allocated,
              'avail': max(15000 - allocated - reserved_space, 0),
            }
        self.patch(fileutil, 'get_disk_stats', call_get_disk_stats)

        ss = FoolscapStorageServer(self.create("test_reserved_space", reserved_space=reserved))
        # 15k available, 10k reserved, leaves 5k for shares

        # a newly created and filled share incurs this much overhead, beyond
        # the size we request.
        OVERHEAD = 3*4
        LEASE_SIZE = 4+32+32+4
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        canary = FakeCanary()
        already, writers = ss.remote_allocate_buckets(
            b"vid1",
            renew_secret,
            cancel_secret,
            sharenums=[0,1,2],
            allocated_size=1000,
            canary=canary,
        )
        self.assertThat(writers, HasLength(3))
        # now the StorageServer should have 3000 bytes provisionally
        # allocated, allowing only 2000 more to be claimed
        self.assertThat(ss._server._bucket_writers, HasLength(3))

        # allocating 1001-byte shares only leaves room for one
        canary2 = FakeCanary()
        already2, writers2 = self.allocate(ss, b"vid2", [0,1,2], 1001, canary2)
        self.assertThat(writers2, HasLength(1))
        self.assertThat(ss._server._bucket_writers, HasLength(4))

        # we abandon the first set, so their provisional allocation should be
        # returned
        canary.disconnected()

        self.assertThat(ss._server._bucket_writers, HasLength(1))
        # now we have a provisional allocation of 1001 bytes

        # and we close the second set, so their provisional allocation should
        # become real, long-term allocation, and grows to include the
        # overhead.
        for bw in writers2.values():
            bw.write(0, b"a"*25)
            bw.close()
        self.assertThat(ss._server._bucket_writers, HasLength(0))

        # this also changes the amount reported as available by call_get_disk_stats
        allocated = 1001 + OVERHEAD + LEASE_SIZE

        # now there should be ALLOCATED=1001+12+72=1085 bytes allocated, and
        # 5000-1085=3915 free, therefore we can fit 39 100byte shares
        canary3 = FakeCanary()
        already3, writers3 = ss.remote_allocate_buckets(
            b"vid3",
            renew_secret,
            cancel_secret,
            sharenums=list(range(100)),
            allocated_size=100,
            canary=canary3,
        )
        self.assertThat(writers3, HasLength(39))
        self.assertThat(ss._server._bucket_writers, HasLength(39))

        canary3.disconnected()

        self.assertThat(ss._server._bucket_writers, HasLength(0))
        ss._server.disownServiceParent()
        del ss

    def test_seek(self):
        basedir = self.workdir("test_seek_behavior")
        fileutil.make_dirs(basedir)
        filename = os.path.join(basedir, "testfile")
        f = open(filename, "wb")
        f.write(b"start")
        f.close()
        # mode="w" allows seeking-to-create-holes, but truncates pre-existing
        # files. mode="a" preserves previous contents but does not allow
        # seeking-to-create-holes. mode="r+" allows both.
        f = open(filename, "rb+")
        f.seek(100)
        f.write(b"100")
        f.close()
        filelen = os.stat(filename)[stat.ST_SIZE]
        self.assertThat(filelen, Equals(100+3))
        f2 = open(filename, "rb")
        self.assertThat(f2.read(5), Equals(b"start"))

    def create_bucket_5_shares(
            self, ss, storage_index, expected_already=0, expected_writers=5
    ):
        """
        Given a StorageServer, create a bucket with 5 shares and return renewal
        and cancellation secrets.
        """
        sharenums = list(range(5))
        size = 100

        # Creating a bucket also creates a lease:
        rs, cs  = (hashutil.my_renewal_secret_hash(b"%d" % next(self._lease_secret)),
                   hashutil.my_cancel_secret_hash(b"%d" % next(self._lease_secret)))
        already, writers = ss.allocate_buckets(storage_index, rs, cs,
                                               sharenums, size)
        self.assertThat(already, HasLength(expected_already))
        self.assertThat(writers, HasLength(expected_writers))
        for wb in writers.values():
            wb.close()
        return rs, cs

    def test_leases(self):
        ss = self.create("test_leases")
        sharenums = list(range(5))
        size = 100

        # Create a bucket:
        rs0, cs0 = self.create_bucket_5_shares(ss, b"si0")

        # Upload of an immutable implies creation of a single lease with the
        # supplied secrets.
        (lease,) = ss.get_leases(b"si0")
        self.assertTrue(lease.is_renew_secret(rs0))

        rs1, cs1 = self.create_bucket_5_shares(ss, b"si1")

        # take out a second lease on si1
        rs2, cs2 = self.create_bucket_5_shares(ss, b"si1", 5, 0)
        (lease1, lease2) = ss.get_leases(b"si1")
        self.assertTrue(lease1.is_renew_secret(rs1))
        self.assertTrue(lease2.is_renew_secret(rs2))

        # and a third lease, using add-lease
        rs2a,cs2a = (hashutil.my_renewal_secret_hash(b"%d" % next(self._lease_secret)),
                     hashutil.my_cancel_secret_hash(b"%d" % next(self._lease_secret)))
        ss.add_lease(b"si1", rs2a, cs2a)
        (lease1, lease2, lease3) = ss.get_leases(b"si1")
        self.assertTrue(lease1.is_renew_secret(rs1))
        self.assertTrue(lease2.is_renew_secret(rs2))
        self.assertTrue(lease3.is_renew_secret(rs2a))

        # add-lease on a missing storage index is silently ignored
        self.assertThat(ss.add_lease(b"si18", b"", b""), Equals(None))

        # check that si0 is readable
        readers = ss.get_buckets(b"si0")
        self.assertThat(readers, HasLength(5))

        # renew the first lease. Only the proper renew_secret should work
        ss.renew_lease(b"si0", rs0)
        self.failUnlessRaises(IndexError, ss.renew_lease, b"si0", cs0)
        self.failUnlessRaises(IndexError, ss.renew_lease, b"si0", rs1)

        # check that si0 is still readable
        readers = ss.get_buckets(b"si0")
        self.assertThat(readers, HasLength(5))

        # There is no such method as remote_cancel_lease for now -- see
        # ticket #1528.
        self.assertFalse(hasattr(FoolscapStorageServer(ss), 'remote_cancel_lease'), \
                    "ss should not have a 'remote_cancel_lease' method/attribute")

        # test overlapping uploads
        rs3,cs3 = (hashutil.my_renewal_secret_hash(b"%d" % next(self._lease_secret)),
                   hashutil.my_cancel_secret_hash(b"%d" % next(self._lease_secret)))
        rs4,cs4 = (hashutil.my_renewal_secret_hash(b"%d" % next(self._lease_secret)),
                   hashutil.my_cancel_secret_hash(b"%d" % next(self._lease_secret)))
        already,writers = ss.allocate_buckets(b"si3", rs3, cs3,
                                              sharenums, size)
        self.assertThat(already, HasLength(0))
        self.assertThat(writers, HasLength(5))
        already2,writers2 = ss.allocate_buckets(b"si3", rs4, cs4,
                                                sharenums, size)
        self.assertThat(already2, HasLength(0))
        self.assertThat(writers2, HasLength(0))
        for wb in writers.values():
            wb.close()

        leases = list(ss.get_leases(b"si3"))
        self.assertThat(leases, HasLength(1))

        already3,writers3 = ss.allocate_buckets(b"si3", rs4, cs4,
                                                sharenums, size)
        self.assertThat(already3, HasLength(5))
        self.assertThat(writers3, HasLength(0))

        leases = list(ss.get_leases(b"si3"))
        self.assertThat(leases, HasLength(2))

    def test_immutable_add_lease_renews(self):
        """
        Adding a lease on an already leased immutable with the same secret just
        renews it.
        """
        clock = Clock()
        clock.advance(123)
        ss = self.create("test_immutable_add_lease_renews", clock=clock)

        # Start out with single lease created with bucket:
        renewal_secret, cancel_secret = self.create_bucket_5_shares(ss, b"si0")
        [lease] = ss.get_leases(b"si0")
        self.assertThat(lease.get_expiration_time(), Equals(123 + DEFAULT_RENEWAL_TIME))

        # Time passes:
        clock.advance(123456)

        # Adding a lease with matching renewal secret just renews it:
        ss.add_lease(b"si0", renewal_secret, cancel_secret)
        [lease] = ss.get_leases(b"si0")
        self.assertThat(lease.get_expiration_time(), Equals(123 + 123456 + DEFAULT_RENEWAL_TIME))

    def test_have_shares(self):
        """By default the StorageServer has no shares."""
        workdir = self.workdir("test_have_shares")
        ss = StorageServer(workdir, b"\x00" * 20, readonly_storage=True)
        self.assertFalse(ss.have_shares())

    def test_readonly(self):
        workdir = self.workdir("test_readonly")
        ss = StorageServer(workdir, b"\x00" * 20, readonly_storage=True)
        ss.setServiceParent(self.sparent)

        already,writers = self.allocate(ss, b"vid", [0,1,2], 75)
        self.assertThat(already, Equals(set()))
        self.assertThat(writers, Equals({}))

        stats = ss.get_stats()
        self.assertThat(stats["storage_server.accepting_immutable_shares"], Equals(0))
        if "storage_server.disk_avail" in stats:
            # Some platforms may not have an API to get disk stats.
            # But if there are stats, readonly_storage means disk_avail=0
            self.assertThat(stats["storage_server.disk_avail"], Equals(0))

    def test_discard(self):
        # discard is really only used for other tests, but we test it anyways
        workdir = self.workdir("test_discard")
        ss = StorageServer(workdir, b"\x00" * 20, discard_storage=True)
        ss.setServiceParent(self.sparent)

        already,writers = self.allocate(ss, b"vid", [0,1,2], 75)
        self.assertThat(already, Equals(set()))
        self.assertThat(set(writers.keys()), Equals(set([0,1,2])))
        for i,wb in writers.items():
            wb.write(0, b"%25d" % i)
            wb.close()
        # since we discard the data, the shares should be present but sparse.
        # Since we write with some seeks, the data we read back will be all
        # zeros.
        b = ss.get_buckets(b"vid")
        self.assertThat(set(b.keys()), Equals(set([0,1,2])))
        self.assertThat(b[0].read(0, 25), Equals(b"\x00" * 25))

    def test_reserved_space_advise_corruption(self):
        """
        If there is no available space then ``remote_advise_corrupt_share`` does
        not write a corruption report.
        """
        disk = FakeDisk(total=1024, used=1024)
        self.patch(fileutil, "get_disk_stats", disk.get_disk_stats)

        workdir = self.workdir("test_reserved_space_advise_corruption")
        ss = StorageServer(workdir, b"\x00" * 20, discard_storage=True)
        ss.setServiceParent(self.sparent)

        upload_immutable(ss, b"si0", b"r" * 32, b"c" * 32, {0: b""})
        ss.advise_corrupt_share(b"immutable", b"si0", 0,
                                b"This share smells funny.\n")

        self.assertThat(
            [],
            Equals(os.listdir(ss.corruption_advisory_dir)),
        )

    def test_advise_corruption(self):
        workdir = self.workdir("test_advise_corruption")
        ss = StorageServer(workdir, b"\x00" * 20, discard_storage=True)
        ss.setServiceParent(self.sparent)

        si0_s = base32.b2a(b"si0")
        upload_immutable(ss, b"si0", b"r" * 32, b"c" * 32, {0: b""})
        ss.advise_corrupt_share(b"immutable", b"si0", 0,
                                b"This share smells funny.\n")
        reportdir = os.path.join(workdir, "corruption-advisories")
        reports = os.listdir(reportdir)
        self.assertThat(reports, HasLength(1))
        report_si0 = reports[0]
        self.assertThat(report_si0, Contains(ensure_str(si0_s)))
        f = open(os.path.join(reportdir, report_si0), "rb")
        report = f.read()
        f.close()
        self.assertThat(report, Contains(b"type: immutable"))
        self.assertThat(report, Contains(b"storage_index: %s" % si0_s))
        self.assertThat(report, Contains(b"share_number: 0"))
        self.assertThat(report, Contains(b"This share smells funny."))

        # test the RIBucketWriter version too
        si1_s = base32.b2a(b"si1")
        already,writers = self.allocate(ss, b"si1", [1], 75)
        self.assertThat(already, Equals(set()))
        self.assertThat(set(writers.keys()), Equals(set([1])))
        writers[1].write(0, b"data")
        writers[1].close()

        b = ss.get_buckets(b"si1")
        self.assertThat(set(b.keys()), Equals(set([1])))
        b[1].advise_corrupt_share(b"This share tastes like dust.\n")

        reports = os.listdir(reportdir)
        self.assertThat(reports, HasLength(2))
        report_si1 = [r for r in reports if bytes_to_native_str(si1_s) in r][0]
        f = open(os.path.join(reportdir, report_si1), "rb")
        report = f.read()
        f.close()
        self.assertThat(report, Contains(b"type: immutable"))
        self.assertThat(report, Contains(b"storage_index: %s" % si1_s))
        self.assertThat(report, Contains(b"share_number: 1"))
        self.assertThat(report, Contains(b"This share tastes like dust."))

    def test_advise_corruption_missing(self):
        """
        If a corruption advisory is received for a share that is not present on
        this server then it is not persisted.
        """
        workdir = self.workdir("test_advise_corruption_missing")
        ss = StorageServer(workdir, b"\x00" * 20, discard_storage=True)
        ss.setServiceParent(self.sparent)

        # Upload one share for this storage index
        upload_immutable(ss, b"si0", b"r" * 32, b"c" * 32, {0: b""})

        # And try to submit a corruption advisory about a different share
        ss.advise_corrupt_share(b"immutable", b"si0", 1,
                                b"This share smells funny.\n")

        self.assertThat(
            [],
            Equals(os.listdir(ss.corruption_advisory_dir)),
        )


class MutableServer(SyncTestCase):

    def setUp(self):
        super(MutableServer, self).setUp()
        self.sparent = LoggingServiceParent()
        self._lease_secret = itertools.count()
        self.addCleanup(self.sparent.stopService)

    def workdir(self, name):
        basedir = os.path.join("storage", "MutableServer", name)
        return basedir

    def create(self, name, clock=None):
        workdir = self.workdir(name)
        if clock is None:
            clock = Clock()
        ss = StorageServer(workdir, b"\x00" * 20,
                           clock=clock)
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        self.create("test_create")

    def write_enabler(self, we_tag):
        return hashutil.tagged_hash(b"we_blah", we_tag)

    def renew_secret(self, tag):
        if isinstance(tag, int):
            tag = b"%d" % (tag,)
        self.assertThat(tag, IsInstance(bytes))
        return hashutil.tagged_hash(b"renew_blah", tag)

    def cancel_secret(self, tag):
        if isinstance(tag, int):
            tag = b"%d" % (tag,)
        self.assertThat(tag, IsInstance(bytes))
        return hashutil.tagged_hash(b"cancel_blah", tag)

    def allocate(self, ss, storage_index, we_tag, lease_tag, sharenums, size):
        write_enabler = self.write_enabler(we_tag)
        renew_secret = self.renew_secret(lease_tag)
        cancel_secret = self.cancel_secret(lease_tag)
        rstaraw = ss.slot_testv_and_readv_and_writev
        testandwritev = dict( [ (shnum, ([], [], None) )
                         for shnum in sharenums ] )
        readv = []
        rc = rstaraw(storage_index,
                     (write_enabler, renew_secret, cancel_secret),
                     testandwritev,
                     readv)
        (did_write, readv_data) = rc
        self.assertTrue(did_write)
        self.assertThat(readv_data, IsInstance(dict))
        self.assertThat(readv_data, HasLength(0))

    def test_enumerate_mutable_shares(self):
        """
        ``StorageServer.enumerate_mutable_shares()`` returns a set of share
        numbers for the given storage index, or an empty set if it does not
        exist at all.
        """
        ss = self.create("test_enumerate_mutable_shares")

        # Initially, nothing exists:
        empty = ss.enumerate_mutable_shares(b"si1")

        self.allocate(ss, b"si1", b"we1", b"le1", [0, 1, 4, 2], 12)
        shares0_1_2_4 = ss.enumerate_mutable_shares(b"si1")

        # Remove share 2, by setting size to 0:
        secrets = (self.write_enabler(b"we1"),
                   self.renew_secret(b"le1"),
                   self.cancel_secret(b"le1"))
        ss.slot_testv_and_readv_and_writev(b"si1", secrets, {2: ([], [], 0)}, [])
        shares0_1_4 = ss.enumerate_mutable_shares(b"si1")
        self.assertThat(
            (empty, shares0_1_2_4, shares0_1_4),
            Equals((set(), {0, 1, 2, 4}, {0, 1, 4}))
        )

    def test_mutable_share_length(self):
        """``get_mutable_share_length()`` returns the length of the share."""
        ss = self.create("test_mutable_share_length")
        self.allocate(ss, b"si1", b"we1", b"le1", [16], 23)
        ss.slot_testv_and_readv_and_writev(
            b"si1", (self.write_enabler(b"we1"),
                     self.renew_secret(b"le1"),
                     self.cancel_secret(b"le1")),
            {16: ([], [(0, b"x" * 23)], None)},
            []
        )
        self.assertThat(ss.get_mutable_share_length(b"si1", 16), Equals(23))

    def test_mutable_share_length_unknown(self):
        """
        ``get_mutable_share_length()`` raises a ``KeyError`` on unknown shares.
        """
        ss = self.create("test_mutable_share_length_unknown")
        self.allocate(ss, b"si1", b"we1", b"le1", [16], 23)
        ss.slot_testv_and_readv_and_writev(
            b"si1", (self.write_enabler(b"we1"),
                     self.renew_secret(b"le1"),
                     self.cancel_secret(b"le1")),
            {16: ([], [(0, b"x" * 23)], None)},
            []
        )
        with self.assertRaises(KeyError):
            # Wrong share number.
            ss.get_mutable_share_length(b"si1", 17)
        with self.assertRaises(KeyError):
            # Wrong storage index
            ss.get_mutable_share_length(b"unknown", 16)

    def test_bad_magic(self):
        ss = self.create("test_bad_magic")
        self.allocate(ss, b"si1", b"we1", next(self._lease_secret), set([0]), 10)
        fn = os.path.join(ss.sharedir, storage_index_to_dir(b"si1"), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write(b"BAD MAGIC")
        f.close()
        read = ss.slot_readv
        e = self.failUnlessRaises(UnknownMutableContainerVersionError,
                                  read, b"si1", [0], [(0,10)])
        self.assertThat(e.filename, Equals(fn))
        self.assertTrue(e.version.startswith(b"BAD MAGIC"))
        self.assertThat(str(e), Contains("had unexpected version"))
        self.assertThat(str(e), Contains("BAD MAGIC"))

    def test_container_size(self):
        ss = self.create("test_container_size")
        self.allocate(ss, b"si1", b"we1", next(self._lease_secret),
                      set([0,1,2]), 100)
        read = ss.slot_readv
        rstaraw = ss.slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [(0,data)], len(data)+12)},
                         [])
        self.assertThat(answer, Equals((True, {0:[],1:[],2:[]})))

        # Trying to make the container too large (by sending a write vector
        # whose offset is too high) will raise an exception.
        TOOBIG = MutableShareFile.MAX_SIZE + 10
        self.failUnlessRaises(DataTooLargeError,
                              rstaraw, b"si1", secrets,
                              {0: ([], [(TOOBIG,data)], None)},
                              [])

        answer = rstaraw(b"si1", secrets,
                         {0: ([], [(0,data)], None)},
                         [])
        self.assertThat(answer, Equals((True, {0:[],1:[],2:[]})))

        read_answer = read(b"si1", [0], [(0,10)])
        self.assertThat(read_answer, Equals({0: [data[:10]]}))

        # Sending a new_length shorter than the current length truncates the
        # data.
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [], 9)},
                         [])
        read_answer = read(b"si1", [0], [(0,10)])
        self.assertThat(read_answer, Equals({0: [data[:9]]}))

        # Sending a new_length longer than the current length doesn't change
        # the data.
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [], 20)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        read_answer = read(b"si1", [0], [(0, 20)])
        self.assertThat(read_answer, Equals({0: [data[:9]]}))

        # Sending a write vector whose start is after the end of the current
        # data doesn't reveal "whatever was there last time" (palimpsest),
        # but instead fills with zeroes.

        # To test this, we fill the data area with a recognizable pattern.
        pattern = u''.join([chr(i) for i in range(100)]).encode("utf-8")
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [(0, pattern)], None)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        # Then truncate the data...
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [], 20)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        # Just confirm that you get an empty string if you try to read from
        # past the (new) endpoint now.
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [], None)},
                         [(20, 1980)])
        self.assertThat(answer, Equals((True, {0:[b''],1:[b''],2:[b'']})))

        # Then the extend the file by writing a vector which starts out past
        # the end...
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [(50, b'hellothere')], None)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        # Now if you read the stuff between 20 (where we earlier truncated)
        # and 50, it had better be all zeroes.
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [], None)},
                         [(20, 30)])
        self.assertThat(answer, Equals((True, {0:[b'\x00'*30],1:[b''],2:[b'']})))

        # Also see if the server explicitly declares that it supports this
        # feature.
        ver = ss.get_version()
        storage_v1_ver = ver[b"http://allmydata.org/tahoe/protocols/storage/v1"]
        self.assertTrue(storage_v1_ver.get(b"fills-holes-with-zero-bytes"))

        # If the size is dropped to zero the share is deleted.
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [(0,data)], 0)},
                         [])
        self.assertThat(answer, Equals((True, {0:[],1:[],2:[]})))

        read_answer = read(b"si1", [0], [(0,10)])
        self.assertThat(read_answer, Equals({}))

    def test_allocate(self):
        ss = self.create("test_allocate")
        self.allocate(ss, b"si1", b"we1", next(self._lease_secret),
                      set([0,1,2]), 100)

        read = ss.slot_readv
        self.assertThat(read(b"si1", [0], [(0, 10)]),
                             Equals({0: [b""]}))
        self.assertThat(read(b"si1", [], [(0, 10)]),
                             Equals({0: [b""], 1: [b""], 2: [b""]}))
        self.assertThat(read(b"si1", [0], [(100, 10)]),
                             Equals({0: [b""]}))

        # try writing to one
        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.slot_testv_and_readv_and_writev
        answer = write(b"si1", secrets,
                       {0: ([], [(0,data)], None)},
                       [])
        self.assertThat(answer, Equals((True, {0:[],1:[],2:[]})))

        self.assertThat(read(b"si1", [0], [(0,20)]),
                             Equals({0: [b"00000000001111111111"]}))
        self.assertThat(read(b"si1", [0], [(95,10)]),
                             Equals({0: [b"99999"]}))
        #self.failUnlessEqual(s0.get_length(), 100)

        bad_secrets = (b"bad write enabler", secrets[1], secrets[2])
        f = self.failUnlessRaises(BadWriteEnablerError,
                                  write, b"si1", bad_secrets,
                                  {}, [])
        self.assertThat(str(f), Contains("The write enabler was recorded by nodeid 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'."))

        # this testv should fail
        answer = write(b"si1", secrets,
                       {0: ([(0, 12, b"eq", b"444444444444"),
                             (20, 5, b"eq", b"22222"),
                             ],
                            [(0, b"x"*100)],
                            None),
                        },
                       [(0,12), (20,5)],
                       )
        self.assertThat(answer, Equals((False,
                                      {0: [b"000000000011", b"22222"],
                                       1: [b"", b""],
                                       2: [b"", b""],
                                       })))
        self.assertThat(read(b"si1", [0], [(0,100)]), Equals({0: [data]}))

    def test_operators(self):
        # test operators, the data we're comparing is '11111' in all cases.
        # test both fail+pass, reset data after each one.
        ss = self.create("test_operators")

        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.slot_testv_and_readv_and_writev
        read = ss.slot_readv

        def reset():
            write(b"si1", secrets,
                  {0: ([], [(0,data)], None)},
                  [])

        reset()

        #  eq
        answer = write(b"si1", secrets, {0: ([(10, 5, b"eq", b"11112"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.assertThat(answer, Equals((False, {0: [b"11111"]})))
        self.assertThat(read(b"si1", [0], [(0,100)]), Equals({0: [data]}))
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"eq", b"11111"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.assertThat(answer, Equals((True, {0: [b"11111"]})))
        self.assertThat(read(b"si1", [0], [(0,100)]), Equals({0: [b"y"*100]}))
        reset()

        # finally, test some operators against empty shares
        answer = write(b"si1", secrets, {1: ([(10, 5, b"eq", b"11112"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.assertThat(answer, Equals((False, {0: [b"11111"]})))
        self.assertThat(read(b"si1", [0], [(0,100)]), Equals({0: [data]}))
        reset()

    def test_readv(self):
        ss = self.create("test_readv")
        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.slot_testv_and_readv_and_writev
        read = ss.slot_readv
        data = [(b"%d" % i) * 100 for i in range(3)]
        rc = write(b"si1", secrets,
                   {0: ([], [(0,data[0])], None),
                    1: ([], [(0,data[1])], None),
                    2: ([], [(0,data[2])], None),
                    }, [])
        self.assertThat(rc, Equals((True, {})))

        answer = read(b"si1", [], [(0, 10)])
        self.assertThat(answer, Equals({0: [b"0"*10],
                                      1: [b"1"*10],
                                      2: [b"2"*10]}))

    def compare_leases_without_timestamps(self, leases_a, leases_b):
        """
        Assert that, except for expiration times, ``leases_a`` contains the same
        lease information as ``leases_b``.
        """
        for a, b in zip(leases_a, leases_b):
            # The leases aren't always of the same type (though of course
            # corresponding elements in the two lists should be of the same
            # type as each other) so it's inconvenient to just reach in and
            # normalize the expiration timestamp.  We don't want to call
            # `renew` on both objects to normalize the expiration timestamp in
            # case `renew` is broken and gives us back equal outputs from
            # non-equal inputs (expiration timestamp aside).  It seems
            # reasonably safe to use `renew` to make _one_ of the timestamps
            # equal to the other though.
            self.assertThat(
                a.renew(b.get_expiration_time()),
                Equals(b),
            )
        self.assertThat(len(leases_a), Equals(len(leases_b)))

    def test_leases(self):
        ss = self.create("test_leases")
        def secrets(n):
            return ( self.write_enabler(b"we1"),
                     self.renew_secret(b"we1-%d" % n),
                     self.cancel_secret(b"we1-%d" % n) )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.slot_testv_and_readv_and_writev
        read = ss.slot_readv
        rc = write(b"si1", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.assertThat(rc, Equals((True, {})))

        # create a random non-numeric file in the bucket directory, to
        # exercise the code that's supposed to ignore those.
        bucket_dir = os.path.join(self.workdir("test_leases"),
                                  "shares", storage_index_to_dir(b"si1"))
        f = open(os.path.join(bucket_dir, "ignore_me.txt"), "w")
        f.write("you ought to be ignoring me\n")
        f.close()

        s0 = MutableShareFile(os.path.join(bucket_dir, "0"))
        self.assertThat(list(s0.get_leases()), HasLength(1))

        # add-lease on a missing storage index is silently ignored
        self.assertThat(ss.add_lease(b"si18", b"", b""), Equals(None))

        # re-allocate the slots and use the same secrets, that should update
        # the lease
        write(b"si1", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.assertThat(list(s0.get_leases()), HasLength(1))

        # renew it directly
        ss.renew_lease(b"si1", secrets(0)[1])
        self.assertThat(list(s0.get_leases()), HasLength(1))

        # now allocate them with a bunch of different secrets, to trigger the
        # extended lease code. Use add_lease for one of them.
        write(b"si1", secrets(1), {0: ([], [(0,data)], None)}, [])
        self.assertThat(list(s0.get_leases()), HasLength(2))
        secrets2 = secrets(2)
        ss.add_lease(b"si1", secrets2[1], secrets2[2])
        self.assertThat(list(s0.get_leases()), HasLength(3))
        write(b"si1", secrets(3), {0: ([], [(0,data)], None)}, [])
        write(b"si1", secrets(4), {0: ([], [(0,data)], None)}, [])
        write(b"si1", secrets(5), {0: ([], [(0,data)], None)}, [])

        self.assertThat(list(s0.get_leases()), HasLength(6))

        all_leases = list(s0.get_leases())
        # and write enough data to expand the container, forcing the server
        # to move the leases
        write(b"si1", secrets(0),
              {0: ([], [(0,data)], 200), },
              [])

        # read back the leases, make sure they're still intact.
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

        ss.renew_lease(b"si1", secrets(0)[1])
        ss.renew_lease(b"si1", secrets(1)[1])
        ss.renew_lease(b"si1", secrets(2)[1])
        ss.renew_lease(b"si1", secrets(3)[1])
        ss.renew_lease(b"si1", secrets(4)[1])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))
        # get a new copy of the leases, with the current timestamps. Reading
        # data and failing to renew/cancel leases should leave the timestamps
        # alone.
        all_leases = list(s0.get_leases())
        # renewing with a bogus token should prompt an error message

        # examine the exception thus raised, make sure the old nodeid is
        # present, to provide for share migration
        e = self.failUnlessRaises(IndexError,
                                  ss.renew_lease, b"si1",
                                  secrets(20)[1])
        e_s = str(e)
        self.assertThat(e_s, Contains("Unable to renew non-existent lease"))
        self.assertThat(e_s, Contains("I have leases accepted by nodeids:"))
        self.assertThat(e_s, Contains("nodeids: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ."))

        self.assertThat(all_leases, Equals(list(s0.get_leases())))

        # reading shares should not modify the timestamp
        read(b"si1", [], [(0,200)])
        self.assertThat(all_leases, Equals(list(s0.get_leases())))

        write(b"si1", secrets(0),
              {0: ([], [(200, b"make me bigger")], None)}, [])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

        write(b"si1", secrets(0),
              {0: ([], [(500, b"make me really bigger")], None)}, [])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

    def test_mutable_add_lease_renews(self):
        """
        Adding a lease on an already leased mutable with the same secret just
        renews it.
        """
        clock = Clock()
        clock.advance(235)
        ss = self.create("test_mutable_add_lease_renews",
                         clock=clock)
        def secrets(n):
            return ( self.write_enabler(b"we1"),
                     self.renew_secret(b"we1-%d" % n),
                     self.cancel_secret(b"we1-%d" % n) )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.slot_testv_and_readv_and_writev
        write_enabler, renew_secret, cancel_secret = secrets(0)
        rc = write(b"si1", (write_enabler, renew_secret, cancel_secret),
                   {0: ([], [(0,data)], None)}, [])
        self.assertThat(rc, Equals((True, {})))

        bucket_dir = os.path.join(self.workdir("test_mutable_add_lease_renews"),
                                  "shares", storage_index_to_dir(b"si1"))
        s0 = MutableShareFile(os.path.join(bucket_dir, "0"))
        [lease] = s0.get_leases()
        self.assertThat(lease.get_expiration_time(), Equals(235 + DEFAULT_RENEWAL_TIME))

        # Time passes...
        clock.advance(835)

        # Adding a lease renews it:
        ss.add_lease(b"si1", renew_secret, cancel_secret)
        [lease] = s0.get_leases()
        self.assertThat(lease.get_expiration_time(),
                         Equals(235 + 835 + DEFAULT_RENEWAL_TIME))

    def test_remove(self):
        ss = self.create("test_remove")
        self.allocate(ss, b"si1", b"we1", next(self._lease_secret),
                      set([0,1,2]), 100)
        readv = ss.slot_readv
        writev = ss.slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        # delete sh0 by setting its size to zero
        answer = writev(b"si1", secrets,
                        {0: ([], [], 0)},
                        [])
        # the answer should mention all the shares that existed before the
        # write
        self.assertThat(answer, Equals((True, {0:[],1:[],2:[]})))
        # but a new read should show only sh1 and sh2
        self.assertThat(readv(b"si1", [], [(0,10)]),
                             Equals({1: [b""], 2: [b""]}))

        # delete sh1 by setting its size to zero
        answer = writev(b"si1", secrets,
                        {1: ([], [], 0)},
                        [])
        self.assertThat(answer, Equals((True, {1:[],2:[]})))
        self.assertThat(readv(b"si1", [], [(0,10)]),
                             Equals({2: [b""]}))

        # delete sh2 by setting its size to zero
        answer = writev(b"si1", secrets,
                        {2: ([], [], 0)},
                        [])
        self.assertThat(answer, Equals((True, {2:[]})))
        self.assertThat(readv(b"si1", [], [(0,10)]),
                             Equals({}))
        # and the bucket directory should now be gone
        si = base32.b2a(b"si1")
        # note: this is a detail of the storage server implementation, and
        # may change in the future
        si = bytes_to_native_str(si)  # filesystem paths are native strings
        prefix = si[:2]
        prefixdir = os.path.join(self.workdir("test_remove"), "shares", prefix)
        bucketdir = os.path.join(prefixdir, si)
        self.assertTrue(os.path.exists(prefixdir), prefixdir)
        self.assertFalse(os.path.exists(bucketdir), bucketdir)

    def test_writev_without_renew_lease(self):
        """
        The helper method ``slot_testv_and_readv_and_writev`` does not renew
        leases if ``False`` is passed for the ``renew_leases`` parameter.
        """
        ss = self.create("test_writev_without_renew_lease")

        storage_index = b"si2"
        secrets = (
            self.write_enabler(storage_index),
            self.renew_secret(storage_index),
            self.cancel_secret(storage_index),
        )

        sharenum = 3
        datav = [(0, b"Hello, world")]

        ss.slot_testv_and_readv_and_writev(
            storage_index=storage_index,
            secrets=secrets,
            test_and_write_vectors={
                sharenum: ([], datav, None),
            },
            read_vector=[],
            renew_leases=False,
        )
        leases = list(ss.get_slot_leases(storage_index))
        self.assertThat([], Equals(leases))

    def test_get_slot_leases_empty_slot(self):
        """
        When ``get_slot_leases`` is called for a slot for which the server has no
        shares, it returns an empty iterable.
        """
        ss = self.create("test_get_slot_leases_empty_slot")
        self.assertThat(
            list(ss.get_slot_leases(b"si1")),
            Equals([]),
        )

    def test_remove_non_present(self):
        """
        A write vector which would remove a share completely is applied as a no-op
        by a server which does not have the share.
        """
        ss = self.create("test_remove_non_present")

        storage_index = b"si1"
        secrets = (
            self.write_enabler(storage_index),
            self.renew_secret(storage_index),
            self.cancel_secret(storage_index),
        )

        sharenum = 3
        testv = []
        datav = []
        new_length = 0
        read_vector = []

        # We don't even need to create any shares to exercise this
        # functionality.  Just go straight to sending a truncate-to-zero
        # write.
        testv_is_good, read_data = ss.slot_testv_and_readv_and_writev(
            storage_index=storage_index,
            secrets=secrets,
            test_and_write_vectors={
                sharenum: (testv, datav, new_length),
            },
            read_vector=read_vector,
        )

        self.assertTrue(testv_is_good)
        self.assertThat({}, Equals(read_data))


class MDMFProxies(AsyncTestCase, ShouldFailMixin):
    def setUp(self):
        super(MDMFProxies, self).setUp()
        self.sparent = LoggingServiceParent()
        self._lease_secret = itertools.count()
        self.ss = self.create("MDMFProxies storage test server")
        self.rref = RemoteBucket(FoolscapStorageServer(self.ss))
        self.storage_server = _StorageServer(lambda: self.rref)
        self.secrets = (self.write_enabler(b"we_secret"),
                        self.renew_secret(b"renew_secret"),
                        self.cancel_secret(b"cancel_secret"))
        self.segment = b"aaaaaa"
        self.block = b"aa"
        self.salt = b"a" * 16
        self.block_hash = b"a" * 32
        self.block_hash_tree = [self.block_hash for i in range(6)]
        self.share_hash = self.block_hash
        self.share_hash_chain = dict([(i, self.share_hash) for i in range(6)])
        self.signature = b"foobarbaz"
        self.verification_key = b"vvvvvv"
        self.encprivkey = b"private"
        self.root_hash = self.block_hash
        self.salt_hash = self.root_hash
        self.salt_hash_tree = [self.salt_hash for i in range(6)]
        self.block_hash_tree_s = self.serialize_blockhashes(self.block_hash_tree)
        self.share_hash_chain_s = self.serialize_sharehashes(self.share_hash_chain)
        # blockhashes and salt hashes are serialized in the same way,
        # only we lop off the first element and store that in the
        # header.
        self.salt_hash_tree_s = self.serialize_blockhashes(self.salt_hash_tree[1:])


    def tearDown(self):
        super(MDMFProxies, self).tearDown()
        self.sparent.stopService()
        shutil.rmtree(self.workdir("MDMFProxies storage test server"))


    def write_enabler(self, we_tag):
        return hashutil.tagged_hash(b"we_blah", we_tag)


    def renew_secret(self, tag):
        if isinstance(tag, int):
            tag = b"%d" % tag
        return hashutil.tagged_hash(b"renew_blah", tag)


    def cancel_secret(self, tag):
        if isinstance(tag, int):
            tag = b"%d" % tag
        return hashutil.tagged_hash(b"cancel_blah", tag)


    def workdir(self, name):
        basedir = os.path.join("storage", "MutableServer", name)
        return basedir


    def create(self, name):
        workdir = self.workdir(name)
        ss = StorageServer(workdir, b"\x00" * 20)
        ss.setServiceParent(self.sparent)
        return ss


    def build_test_mdmf_share(self, tail_segment=False, empty=False):
        # Start with the checkstring
        data = struct.pack(">BQ32s",
                           1,
                           0,
                           self.root_hash)
        self.checkstring = data
        # Next, the encoding parameters
        if tail_segment:
            data += struct.pack(">BBQQ",
                                3,
                                10,
                                6,
                                33)
        elif empty:
            data += struct.pack(">BBQQ",
                                3,
                                10,
                                0,
                                0)
        else:
            data += struct.pack(">BBQQ",
                                3,
                                10,
                                6,
                                36)
        # Now we'll build the offsets.
        sharedata = b""
        if not tail_segment and not empty:
            for i in range(6):
                sharedata += self.salt + self.block
        elif tail_segment:
            for i in range(5):
                sharedata += self.salt + self.block
            sharedata += self.salt + b"a"

        # The encrypted private key comes after the shares + salts
        offset_size = struct.calcsize(MDMFOFFSETS)
        encrypted_private_key_offset = len(data) + offset_size
        # The share has chain comes after the private key
        sharehashes_offset = encrypted_private_key_offset + \
            len(self.encprivkey)

        # The signature comes after the share hash chain.
        signature_offset = sharehashes_offset + len(self.share_hash_chain_s)

        verification_key_offset = signature_offset + len(self.signature)
        verification_key_end = verification_key_offset + \
            len(self.verification_key)

        share_data_offset = offset_size
        share_data_offset += PRIVATE_KEY_SIZE
        share_data_offset += SIGNATURE_SIZE
        share_data_offset += VERIFICATION_KEY_SIZE
        share_data_offset += SHARE_HASH_CHAIN_SIZE

        blockhashes_offset = share_data_offset + len(sharedata)
        eof_offset = blockhashes_offset + len(self.block_hash_tree_s)

        data += struct.pack(MDMFOFFSETS,
                            encrypted_private_key_offset,
                            sharehashes_offset,
                            signature_offset,
                            verification_key_offset,
                            verification_key_end,
                            share_data_offset,
                            blockhashes_offset,
                            eof_offset)

        self.offsets = {}
        self.offsets['enc_privkey'] = encrypted_private_key_offset
        self.offsets['block_hash_tree'] = blockhashes_offset
        self.offsets['share_hash_chain'] = sharehashes_offset
        self.offsets['signature'] = signature_offset
        self.offsets['verification_key'] = verification_key_offset
        self.offsets['share_data'] = share_data_offset
        self.offsets['verification_key_end'] = verification_key_end
        self.offsets['EOF'] = eof_offset

        # the private key,
        data += self.encprivkey
        # the sharehashes
        data += self.share_hash_chain_s
        # the signature,
        data += self.signature
        # and the verification key
        data += self.verification_key
        # Then we'll add in gibberish until we get to the right point.
        nulls = b"".join([b" " for i in range(len(data), share_data_offset)])
        data += nulls

        # Then the share data
        data += sharedata
        # the blockhashes
        data += self.block_hash_tree_s
        return data


    def write_test_share_to_server(self,
                                   storage_index,
                                   tail_segment=False,
                                   empty=False):
        """
        I write some data for the read tests to read to self.ss

        If tail_segment=True, then I will write a share that has a
        smaller tail segment than other segments.
        """
        write = self.ss.slot_testv_and_readv_and_writev
        data = self.build_test_mdmf_share(tail_segment, empty)
        # Finally, we write the whole thing to the storage server in one
        # pass.
        testvs = [(0, 1, b"eq", b"")]
        tws = {}
        tws[0] = (testvs, [(0, data)], None)
        readv = [(0, 1)]
        results = write(storage_index, self.secrets, tws, readv)
        self.assertTrue(results[0])


    def build_test_sdmf_share(self, empty=False):
        if empty:
            sharedata = b""
        else:
            sharedata = self.segment * 6
        self.sharedata = sharedata
        blocksize = len(sharedata) // 3
        block = sharedata[:blocksize]
        self.blockdata = block
        prefix = struct.pack(">BQ32s16s BBQQ",
                             0, # version,
                             0,
                             self.root_hash,
                             self.salt,
                             3,
                             10,
                             len(sharedata),
                             len(sharedata),
                            )
        post_offset = struct.calcsize(">BQ32s16sBBQQLLLLQQ")
        signature_offset = post_offset + len(self.verification_key)
        sharehashes_offset = signature_offset + len(self.signature)
        blockhashes_offset = sharehashes_offset + len(self.share_hash_chain_s)
        sharedata_offset = blockhashes_offset + len(self.block_hash_tree_s)
        encprivkey_offset = sharedata_offset + len(block)
        eof_offset = encprivkey_offset + len(self.encprivkey)
        offsets = struct.pack(">LLLLQQ",
                              signature_offset,
                              sharehashes_offset,
                              blockhashes_offset,
                              sharedata_offset,
                              encprivkey_offset,
                              eof_offset)
        final_share = b"".join([prefix,
                                offsets,
                                self.verification_key,
                                self.signature,
                                self.share_hash_chain_s,
                                self.block_hash_tree_s,
                                block,
                                self.encprivkey])
        self.offsets = {}
        self.offsets['signature'] = signature_offset
        self.offsets['share_hash_chain'] = sharehashes_offset
        self.offsets['block_hash_tree'] = blockhashes_offset
        self.offsets['share_data'] = sharedata_offset
        self.offsets['enc_privkey'] = encprivkey_offset
        self.offsets['EOF'] = eof_offset
        return final_share


    def write_sdmf_share_to_server(self,
                                   storage_index,
                                   empty=False):
        # Some tests need SDMF shares to verify that we can still
        # read them. This method writes one, which resembles but is not
        write = self.ss.slot_testv_and_readv_and_writev
        share = self.build_test_sdmf_share(empty)
        testvs = [(0, 1, b"eq", b"")]
        tws = {}
        tws[0] = (testvs, [(0, share)], None)
        readv = []
        results = write(storage_index, self.secrets, tws, readv)
        self.assertTrue(results[0])


    def test_read(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        # Check that every method equals what we expect it to.
        d = defer.succeed(None)
        def _check_block_and_salt(block_and_salt):
            (block, salt) = block_and_salt
            self.assertThat(block, Equals(self.block))
            self.assertThat(salt, Equals(self.salt))

        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mr.get_block_and_salt(i))
            d.addCallback(_check_block_and_salt)

        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.assertThat(self.encprivkey, Equals(encprivkey)))

        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.assertThat(self.block_hash_tree, Equals(blockhashes)))

        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.assertThat(self.share_hash_chain, Equals(sharehashes)))

        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.assertThat(signature, Equals(self.signature)))

        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.assertThat(verification_key, Equals(self.verification_key)))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.assertThat(seqnum, Equals(0)))

        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.assertThat(self.root_hash, Equals(root_hash)))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.assertThat(seqnum, Equals(0)))

        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters(args):
            (k, n, segsize, datalen) = args
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            self.assertThat(segsize, Equals(6))
            self.assertThat(datalen, Equals(36))
        d.addCallback(_check_encoding_parameters)

        d.addCallback(lambda ignored:
            mr.get_checkstring())
        d.addCallback(lambda checkstring:
            self.assertThat(checkstring, Equals(checkstring)))
        return d


    def test_read_with_different_tail_segment_size(self):
        self.write_test_share_to_server(b"si1", tail_segment=True)
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_block_and_salt(5)
        def _check_tail_segment(results):
            block, salt = results
            self.assertThat(block, HasLength(1))
            self.assertThat(block, Equals(b"a"))
        d.addCallback(_check_tail_segment)
        return d


    def test_get_block_with_invalid_segnum(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test invalid segnum",
                            None,
                            mr.get_block_and_salt, 7))
        return d


    def test_get_encoding_parameters_first(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_encoding_parameters()
        def _check_encoding_parameters(args):
            (k, n, segment_size, datalen) = args
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            self.assertThat(segment_size, Equals(6))
            self.assertThat(datalen, Equals(36))
        d.addCallback(_check_encoding_parameters)
        return d


    def test_get_seqnum_first(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_seqnum()
        d.addCallback(lambda seqnum:
            self.assertThat(seqnum, Equals(0)))
        return d


    def test_get_root_hash_first(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_root_hash()
        d.addCallback(lambda root_hash:
            self.assertThat(root_hash, Equals(self.root_hash)))
        return d


    def test_get_checkstring_first(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_checkstring()
        d.addCallback(lambda checkstring:
            self.assertThat(checkstring, Equals(self.checkstring)))
        return d


    def test_write_read_vectors(self):
        # When writing for us, the storage server will return to us a
        # read vector, along with its result. If a write fails because
        # the test vectors failed, this read vector can help us to
        # diagnose the problem. This test ensures that the read vector
        # is working appropriately.
        mw = self._make_new_mw(b"si1", 0)

        for i in range(6):
            mw.put_block(self.block, i, self.salt)
        mw.put_encprivkey(self.encprivkey)
        mw.put_blockhashes(self.block_hash_tree)
        mw.put_sharehashes(self.share_hash_chain)
        mw.put_root_hash(self.root_hash)
        mw.put_signature(self.signature)
        mw.put_verification_key(self.verification_key)
        d = mw.finish_publishing()
        def _then(results):
            self.assertThat(results, HasLength(2))
            result, readv = results
            self.assertTrue(result)
            self.assertFalse(readv)
            self.old_checkstring = mw.get_checkstring()
            mw.set_checkstring(b"")
        d.addCallback(_then)
        d.addCallback(lambda ignored:
            mw.finish_publishing())
        def _then_again(results):
            self.assertThat(results, HasLength(2))
            result, readvs = results
            self.assertFalse(result)
            self.assertThat(readvs, Contains(0))
            readv = readvs[0][0]
            self.assertThat(readv, Equals(self.old_checkstring))
        d.addCallback(_then_again)
        # The checkstring remains the same for the rest of the process.
        return d


    def test_private_key_after_share_hash_chain(self):
        mw = self._make_new_mw(b"si1", 0)
        d = defer.succeed(None)
        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            mw.put_encprivkey(self.encprivkey))
        d.addCallback(lambda ignored:
            mw.put_sharehashes(self.share_hash_chain))

        # Now try to put the private key again.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test repeat private key",
                            None,
                            mw.put_encprivkey, self.encprivkey))
        return d


    def test_signature_after_verification_key(self):
        mw = self._make_new_mw(b"si1", 0)
        d = defer.succeed(None)
        # Put everything up to and including the verification key.
        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            mw.put_encprivkey(self.encprivkey))
        d.addCallback(lambda ignored:
            mw.put_blockhashes(self.block_hash_tree))
        d.addCallback(lambda ignored:
            mw.put_sharehashes(self.share_hash_chain))
        d.addCallback(lambda ignored:
            mw.put_root_hash(self.root_hash))
        d.addCallback(lambda ignored:
            mw.put_signature(self.signature))
        d.addCallback(lambda ignored:
            mw.put_verification_key(self.verification_key))
        # Now try to put the signature again. This should fail
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "signature after verification",
                            None,
                            mw.put_signature, self.signature))
        return d


    def test_uncoordinated_write(self):
        # Make two mutable writers, both pointing to the same storage
        # server, both at the same storage index, and try writing to the
        # same share.
        mw1 = self._make_new_mw(b"si1", 0)
        mw2 = self._make_new_mw(b"si1", 0)

        def _check_success(results):
            result, readvs = results
            self.assertTrue(result)

        def _check_failure(results):
            result, readvs = results
            self.assertFalse(result)

        def _write_share(mw):
            for i in range(6):
                mw.put_block(self.block, i, self.salt)
            mw.put_encprivkey(self.encprivkey)
            mw.put_blockhashes(self.block_hash_tree)
            mw.put_sharehashes(self.share_hash_chain)
            mw.put_root_hash(self.root_hash)
            mw.put_signature(self.signature)
            mw.put_verification_key(self.verification_key)
            return mw.finish_publishing()
        d = _write_share(mw1)
        d.addCallback(_check_success)
        d.addCallback(lambda ignored:
            _write_share(mw2))
        d.addCallback(_check_failure)
        return d


    def test_invalid_salt_size(self):
        # Salts need to be 16 bytes in size. Writes that attempt to
        # write more or less than this should be rejected.
        mw = self._make_new_mw(b"si1", 0)
        invalid_salt = b"a" * 17 # 17 bytes
        another_invalid_salt = b"b" * 15 # 15 bytes
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "salt too big",
                            None,
                            mw.put_block, self.block, 0, invalid_salt))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "salt too small",
                            None,
                            mw.put_block, self.block, 0,
                            another_invalid_salt))
        return d


    def test_write_test_vectors(self):
        # If we give the write proxy a bogus test vector at
        # any point during the process, it should fail to write when we
        # tell it to write.
        def _check_failure(results):
            self.assertThat(results, HasLength(2))
            res, d = results
            self.assertFalse(res)

        def _check_success(results):
            self.assertThat(results, HasLength(2))
            res, d = results
            self.assertTrue(results)

        mw = self._make_new_mw(b"si1", 0)
        mw.set_checkstring(b"this is a lie")
        for i in range(6):
            mw.put_block(self.block, i, self.salt)
        mw.put_encprivkey(self.encprivkey)
        mw.put_blockhashes(self.block_hash_tree)
        mw.put_sharehashes(self.share_hash_chain)
        mw.put_root_hash(self.root_hash)
        mw.put_signature(self.signature)
        mw.put_verification_key(self.verification_key)
        d = mw.finish_publishing()
        d.addCallback(_check_failure)
        d.addCallback(lambda ignored:
            mw.set_checkstring(b""))
        d.addCallback(lambda ignored:
            mw.finish_publishing())
        d.addCallback(_check_success)
        return d


    def serialize_blockhashes(self, blockhashes):
        return b"".join(blockhashes)


    def serialize_sharehashes(self, sharehashes):
        ret = b"".join([struct.pack(">H32s", i, sharehashes[i])
                        for i in sorted(sharehashes.keys())])
        return ret


    def test_write(self):
        # This translates to a file with 6 6-byte segments, and with 2-byte
        # blocks.
        mw = self._make_new_mw(b"si1", 0)
        # Test writing some blocks.
        read = self.ss.slot_readv
        expected_private_key_offset = struct.calcsize(MDMFHEADER)
        expected_sharedata_offset = struct.calcsize(MDMFHEADER) + \
                                    PRIVATE_KEY_SIZE + \
                                    SIGNATURE_SIZE + \
                                    VERIFICATION_KEY_SIZE + \
                                    SHARE_HASH_CHAIN_SIZE
        written_block_size = 2 + len(self.salt)
        written_block = self.block + self.salt
        for i in range(6):
            mw.put_block(self.block, i, self.salt)

        mw.put_encprivkey(self.encprivkey)
        mw.put_blockhashes(self.block_hash_tree)
        mw.put_sharehashes(self.share_hash_chain)
        mw.put_root_hash(self.root_hash)
        mw.put_signature(self.signature)
        mw.put_verification_key(self.verification_key)
        d = mw.finish_publishing()
        def _check_publish(results):
            self.assertThat(results, HasLength(2))
            result, ign = results
            self.assertTrue(result, "publish failed")
            for i in range(6):
                self.assertThat(read(b"si1", [0], [(expected_sharedata_offset + (i * written_block_size), written_block_size)]),
                                Equals({0: [written_block]}))

            self.assertThat(self.encprivkey, HasLength(7))
            self.assertThat(read(b"si1", [0], [(expected_private_key_offset, 7)]),
                                 Equals({0: [self.encprivkey]}))

            expected_block_hash_offset = expected_sharedata_offset + \
                        (6 * written_block_size)
            self.assertThat(self.block_hash_tree_s, HasLength(32 * 6))
            self.assertThat(read(b"si1", [0], [(expected_block_hash_offset, 32 * 6)]),
                                 Equals({0: [self.block_hash_tree_s]}))

            expected_share_hash_offset = expected_private_key_offset + len(self.encprivkey)
            self.assertThat(read(b"si1", [0],[(expected_share_hash_offset, (32 + 2) * 6)]),
                                 Equals({0: [self.share_hash_chain_s]}))

            self.assertThat(read(b"si1", [0], [(9, 32)]),
                                 Equals({0: [self.root_hash]}))
            expected_signature_offset = expected_share_hash_offset + \
                len(self.share_hash_chain_s)
            self.assertThat(self.signature, HasLength(9))
            self.assertThat(read(b"si1", [0], [(expected_signature_offset, 9)]),
                                 Equals({0: [self.signature]}))

            expected_verification_key_offset = expected_signature_offset + len(self.signature)
            self.assertThat(self.verification_key, HasLength(6))
            self.assertThat(read(b"si1", [0], [(expected_verification_key_offset, 6)]),
                                 Equals({0: [self.verification_key]}))

            signable = mw.get_signable()
            verno, seq, roothash, k, n, segsize, datalen = \
                                            struct.unpack(">BQ32sBBQQ",
                                                          signable)
            self.assertThat(verno, Equals(1))
            self.assertThat(seq, Equals(0))
            self.assertThat(roothash, Equals(self.root_hash))
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            self.assertThat(segsize, Equals(6))
            self.assertThat(datalen, Equals(36))
            expected_eof_offset = expected_block_hash_offset + \
                len(self.block_hash_tree_s)

            # Check the version number to make sure that it is correct.
            expected_version_number = struct.pack(">B", 1)
            self.assertThat(read(b"si1", [0], [(0, 1)]),
                                 Equals({0: [expected_version_number]}))
            # Check the sequence number to make sure that it is correct
            expected_sequence_number = struct.pack(">Q", 0)
            self.assertThat(read(b"si1", [0], [(1, 8)]),
                                 Equals({0: [expected_sequence_number]}))
            # Check that the encoding parameters (k, N, segement size, data
            # length) are what they should be. These are  3, 10, 6, 36
            expected_k = struct.pack(">B", 3)
            self.assertThat(read(b"si1", [0], [(41, 1)]),
                                 Equals({0: [expected_k]}))
            expected_n = struct.pack(">B", 10)
            self.assertThat(read(b"si1", [0], [(42, 1)]),
                                 Equals({0: [expected_n]}))
            expected_segment_size = struct.pack(">Q", 6)
            self.assertThat(read(b"si1", [0], [(43, 8)]),
                                 Equals({0: [expected_segment_size]}))
            expected_data_length = struct.pack(">Q", 36)
            self.assertThat(read(b"si1", [0], [(51, 8)]),
                                 Equals({0: [expected_data_length]}))
            expected_offset = struct.pack(">Q", expected_private_key_offset)
            self.assertThat(read(b"si1", [0], [(59, 8)]),
                                 Equals({0: [expected_offset]}))
            expected_offset = struct.pack(">Q", expected_share_hash_offset)
            self.assertThat(read(b"si1", [0], [(67, 8)]),
                                 Equals({0: [expected_offset]}))
            expected_offset = struct.pack(">Q", expected_signature_offset)
            self.assertThat(read(b"si1", [0], [(75, 8)]),
                                 Equals({0: [expected_offset]}))
            expected_offset = struct.pack(">Q", expected_verification_key_offset)
            self.assertThat(read(b"si1", [0], [(83, 8)]),
                                 Equals({0: [expected_offset]}))
            expected_offset = struct.pack(">Q", expected_verification_key_offset + len(self.verification_key))
            self.assertThat(read(b"si1", [0], [(91, 8)]),
                                 Equals({0: [expected_offset]}))
            expected_offset = struct.pack(">Q", expected_sharedata_offset)
            self.assertThat(read(b"si1", [0], [(99, 8)]),
                                 Equals({0: [expected_offset]}))
            expected_offset = struct.pack(">Q", expected_block_hash_offset)
            self.assertThat(read(b"si1", [0], [(107, 8)]),
                                 Equals({0: [expected_offset]}))
            expected_offset = struct.pack(">Q", expected_eof_offset)
            self.assertThat(read(b"si1", [0], [(115, 8)]),
                                 Equals({0: [expected_offset]}))
        d.addCallback(_check_publish)
        return d

    def _make_new_mw(self, si, share, datalength=36):
        # This is a file of size 36 bytes. Since it has a segment
        # size of 6, we know that it has 6 byte segments, which will
        # be split into blocks of 2 bytes because our FEC k
        # parameter is 3.
        mw = MDMFSlotWriteProxy(share, self.storage_server, si, self.secrets, 0, 3, 10,
                                6, datalength)
        return mw


    def test_write_rejected_with_too_many_blocks(self):
        mw = self._make_new_mw(b"si0", 0)

        # Try writing too many blocks. We should not be able to write
        # more than 6
        # blocks into each share.
        d = defer.succeed(None)
        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "too many blocks",
                            None,
                            mw.put_block, self.block, 7, self.salt))
        return d


    def test_write_rejected_with_invalid_salt(self):
        # Try writing an invalid salt. Salts are 16 bytes -- any more or
        # less should cause an error.
        mw = self._make_new_mw(b"si1", 0)
        bad_salt = b"a" * 17 # 17 bytes
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test_invalid_salt",
                            None, mw.put_block, self.block, 7, bad_salt))
        return d


    def test_write_rejected_with_invalid_root_hash(self):
        # Try writing an invalid root hash. This should be SHA256d, and
        # 32 bytes long as a result.
        mw = self._make_new_mw(b"si2", 0)
        # 17 bytes != 32 bytes
        invalid_root_hash = b"a" * 17
        d = defer.succeed(None)
        # Before this test can work, we need to put some blocks + salts,
        # a block hash tree, and a share hash tree. Otherwise, we'll see
        # failures that match what we are looking for, but are caused by
        # the constraints imposed on operation ordering.
        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            mw.put_encprivkey(self.encprivkey))
        d.addCallback(lambda ignored:
            mw.put_blockhashes(self.block_hash_tree))
        d.addCallback(lambda ignored:
            mw.put_sharehashes(self.share_hash_chain))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "invalid root hash",
                            None, mw.put_root_hash, invalid_root_hash))
        return d


    def test_write_rejected_with_invalid_blocksize(self):
        # The blocksize implied by the writer that we get from
        # _make_new_mw is 2bytes -- any more or any less than this
        # should be cause for failure, unless it is the tail segment, in
        # which case it may not be failure.
        invalid_block = b"a"
        mw = self._make_new_mw(b"si3", 0, 33) # implies a tail segment with
                                             # one byte blocks
        # 1 bytes != 2 bytes
        d = defer.succeed(None)
        d.addCallback(lambda ignored, invalid_block=invalid_block:
            self.shouldFail(LayoutInvalid, "test blocksize too small",
                            None, mw.put_block, invalid_block, 0,
                            self.salt))
        invalid_block = invalid_block * 3
        # 3 bytes != 2 bytes
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test blocksize too large",
                            None,
                            mw.put_block, invalid_block, 0, self.salt))
        for i in range(5):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        # Try to put an invalid tail segment
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test invalid tail segment",
                            None,
                            mw.put_block, self.block, 5, self.salt))
        valid_block = b"a"
        d.addCallback(lambda ignored:
            mw.put_block(valid_block, 5, self.salt))
        return d


    def test_write_enforces_order_constraints(self):
        # We require that the MDMFSlotWriteProxy be interacted with in a
        # specific way.
        # That way is:
        # 0: __init__
        # 1: write blocks and salts
        # 2: Write the encrypted private key
        # 3: Write the block hashes
        # 4: Write the share hashes
        # 5: Write the root hash and salt hash
        # 6: Write the signature and verification key
        # 7: Write the file.
        #
        # Some of these can be performed out-of-order, and some can't.
        # The dependencies that I want to test here are:
        #  - Private key before block hashes
        #  - share hashes and block hashes before root hash
        #  - root hash before signature
        #  - signature before verification key
        mw0 = self._make_new_mw(b"si0", 0)
        # Write some shares
        d = defer.succeed(None)
        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mw0.put_block(self.block, i, self.salt))

        # Try to write the share hash chain without writing the
        # encrypted private key
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "share hash chain before "
                                           "private key",
                            None,
                            mw0.put_sharehashes, self.share_hash_chain))
        # Write the private key.
        d.addCallback(lambda ignored:
            mw0.put_encprivkey(self.encprivkey))

        # Now write the block hashes and try again
        d.addCallback(lambda ignored:
            mw0.put_blockhashes(self.block_hash_tree))

        # We haven't yet put the root hash on the share, so we shouldn't
        # be able to sign it.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "signature before root hash",
                            None, mw0.put_signature, self.signature))

        d.addCallback(lambda ignored:
            self.failUnlessRaises(LayoutInvalid, mw0.get_signable))

        # ..and, since that fails, we also shouldn't be able to put the
        # verification key.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "key before signature",
                            None, mw0.put_verification_key,
                            self.verification_key))

        # Now write the share hashes.
        d.addCallback(lambda ignored:
            mw0.put_sharehashes(self.share_hash_chain))
        # We should be able to write the root hash now too
        d.addCallback(lambda ignored:
            mw0.put_root_hash(self.root_hash))

        # We should still be unable to put the verification key
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "key before signature",
                            None, mw0.put_verification_key,
                            self.verification_key))

        d.addCallback(lambda ignored:
            mw0.put_signature(self.signature))

        # We shouldn't be able to write the offsets to the remote server
        # until the offset table is finished; IOW, until we have written
        # the verification key.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "offsets before verification key",
                            None,
                            mw0.finish_publishing))

        d.addCallback(lambda ignored:
            mw0.put_verification_key(self.verification_key))
        return d


    def test_end_to_end(self):
        mw = self._make_new_mw(b"si1", 0)
        # Write a share using the mutable writer, and make sure that the
        # reader knows how to read everything back to us.
        d = defer.succeed(None)
        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            mw.put_encprivkey(self.encprivkey))
        d.addCallback(lambda ignored:
            mw.put_blockhashes(self.block_hash_tree))
        d.addCallback(lambda ignored:
            mw.put_sharehashes(self.share_hash_chain))
        d.addCallback(lambda ignored:
            mw.put_root_hash(self.root_hash))
        d.addCallback(lambda ignored:
            mw.put_signature(self.signature))
        d.addCallback(lambda ignored:
            mw.put_verification_key(self.verification_key))
        d.addCallback(lambda ignored:
            mw.finish_publishing())

        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        def _check_block_and_salt(block_and_salt):
            (block, salt) = block_and_salt
            self.assertThat(block, Equals(self.block))
            self.assertThat(salt, Equals(self.salt))

        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mr.get_block_and_salt(i))
            d.addCallback(_check_block_and_salt)

        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.assertThat(self.encprivkey, Equals(encprivkey)))

        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.assertThat(self.block_hash_tree, Equals(blockhashes)))

        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.assertThat(self.share_hash_chain, Equals(sharehashes)))

        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.assertThat(signature, Equals(self.signature)))

        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.assertThat(verification_key, Equals(self.verification_key)))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.assertThat(seqnum, Equals(0)))

        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.assertThat(self.root_hash, Equals(root_hash)))

        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters(args):
            (k, n, segsize, datalen) = args
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            self.assertThat(segsize, Equals(6))
            self.assertThat(datalen, Equals(36))
        d.addCallback(_check_encoding_parameters)

        d.addCallback(lambda ignored:
            mr.get_checkstring())
        d.addCallback(lambda checkstring:
            self.assertThat(checkstring, Equals(mw.get_checkstring())))
        return d


    def test_is_sdmf(self):
        # The MDMFSlotReadProxy should also know how to read SDMF files,
        # since it will encounter them on the grid. Callers use the
        # is_sdmf method to test this.
        self.write_sdmf_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.is_sdmf()
        d.addCallback(lambda issdmf:
            self.assertTrue(issdmf))
        return d


    def test_reads_sdmf(self):
        # The slot read proxy should, naturally, know how to tell us
        # about data in the SDMF format
        self.write_sdmf_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            mr.is_sdmf())
        d.addCallback(lambda issdmf:
            self.assertTrue(issdmf))

        # What do we need to read?
        #  - The sharedata
        #  - The salt
        d.addCallback(lambda ignored:
            mr.get_block_and_salt(0))
        def _check_block_and_salt(results):
            block, salt = results
            # Our original file is 36 bytes long. Then each share is 12
            # bytes in size. The share is composed entirely of the
            # letter a. self.block contains 2 as, so 6 * self.block is
            # what we are looking for.
            self.assertThat(block, Equals(self.block * 6))
            self.assertThat(salt, Equals(self.salt))
        d.addCallback(_check_block_and_salt)

        #  - The blockhashes
        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.assertThat(self.block_hash_tree,
                                 Equals(blockhashes),
                                 blockhashes))
        #  - The sharehashes
        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.assertThat(self.share_hash_chain,
                                 Equals(sharehashes)))
        #  - The keys
        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.assertThat(encprivkey, Equals(self.encprivkey), encprivkey))
        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.assertThat(verification_key,
                                 Equals(self.verification_key),
                                 verification_key))
        #  - The signature
        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.assertThat(signature, Equals(self.signature), signature))

        #  - The sequence number
        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.assertThat(seqnum, Equals(0), seqnum))

        #  - The root hash
        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.assertThat(root_hash, Equals(self.root_hash), root_hash))
        return d


    def test_only_reads_one_segment_sdmf(self):
        # SDMF shares have only one segment, so it doesn't make sense to
        # read more segments than that. The reader should know this and
        # complain if we try to do that.
        self.write_sdmf_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            mr.is_sdmf())
        d.addCallback(lambda issdmf:
            self.assertTrue(issdmf))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test bad segment",
                            None,
                            mr.get_block_and_salt, 1))
        return d


    def test_read_with_prefetched_mdmf_data(self):
        # The MDMFSlotReadProxy will prefill certain fields if you pass
        # it data that you have already fetched. This is useful for
        # cases like the Servermap, which prefetches ~2kb of data while
        # finding out which shares are on the remote peer so that it
        # doesn't waste round trips.
        mdmf_data = self.build_test_mdmf_share()
        self.write_test_share_to_server(b"si1")
        def _make_mr(ignored, length):
            mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0, mdmf_data[:length])
            return mr

        d = defer.succeed(None)
        # This should be enough to fill in both the encoding parameters
        # and the table of offsets, which will complete the version
        # information tuple.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.assertTrue(verinfo)
            self.assertThat(verinfo, HasLength(9))
            (seqnum,
             root_hash,
             salt_hash,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.assertThat(seqnum, Equals(0))
            self.assertThat(root_hash, Equals(self.root_hash))
            self.assertThat(segsize, Equals(6))
            self.assertThat(datalen, Equals(36))
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            expected_prefix = struct.pack(MDMFSIGNABLEHEADER,
                                          1,
                                          seqnum,
                                          root_hash,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.assertThat(expected_prefix, Equals(prefix))
            self.assertThat(self.rref.read_count, Equals(0))
        d.addCallback(_check_verinfo)
        # This is not enough data to read a block and a share, so the
        # wrapper should attempt to read this from the remote server.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        def _check_block_and_salt(block_and_salt):
            (block, salt) = block_and_salt
            self.assertThat(block, Equals(self.block))
            self.assertThat(salt, Equals(self.salt))
            self.assertThat(self.rref.read_count, Equals(1))
        # This should be enough data to read one block.
        d.addCallback(_make_mr, 123 + PRIVATE_KEY_SIZE + SIGNATURE_SIZE + VERIFICATION_KEY_SIZE + SHARE_HASH_CHAIN_SIZE + 140)
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        d.addCallback(_check_block_and_salt)
        return d


    def test_read_with_prefetched_sdmf_data(self):
        sdmf_data = self.build_test_sdmf_share()
        self.write_sdmf_share_to_server(b"si1")
        def _make_mr(ignored, length):
            mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0, sdmf_data[:length])
            return mr

        d = defer.succeed(None)
        # This should be enough to get us the encoding parameters,
        # offset table, and everything else we need to build a verinfo
        # string.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.assertTrue(verinfo)
            self.assertThat(verinfo, HasLength(9))
            (seqnum,
             root_hash,
             salt,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.assertThat(seqnum, Equals(0))
            self.assertThat(root_hash, Equals(self.root_hash))
            self.assertThat(salt, Equals(self.salt))
            self.assertThat(segsize, Equals(36))
            self.assertThat(datalen, Equals(36))
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            expected_prefix = struct.pack(SIGNED_PREFIX,
                                          0,
                                          seqnum,
                                          root_hash,
                                          salt,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.assertThat(expected_prefix, Equals(prefix))
            self.assertThat(self.rref.read_count, Equals(0))
        d.addCallback(_check_verinfo)
        # This shouldn't be enough to read any share data.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        def _check_block_and_salt(block_and_salt):
            (block, salt) = block_and_salt
            self.assertThat(block, Equals(self.block * 6))
            self.assertThat(salt, Equals(self.salt))
            # TODO: Fix the read routine so that it reads only the data
            #       that it has cached if it can't read all of it.
            self.assertThat(self.rref.read_count, Equals(2))

        # This should be enough to read share data.
        d.addCallback(_make_mr, self.offsets['share_data'])
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        d.addCallback(_check_block_and_salt)
        return d


    def test_read_with_empty_mdmf_file(self):
        # Some tests upload a file with no contents to test things
        # unrelated to the actual handling of the content of the file.
        # The reader should behave intelligently in these cases.
        self.write_test_share_to_server(b"si1", empty=True)
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        # We should be able to get the encoding parameters, and they
        # should be correct.
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters(params):
            self.assertThat(params, HasLength(4))
            k, n, segsize, datalen = params
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            self.assertThat(segsize, Equals(0))
            self.assertThat(datalen, Equals(0))
        d.addCallback(_check_encoding_parameters)

        # We should not be able to fetch a block, since there are no
        # blocks to fetch
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "get block on empty file",
                            None,
                            mr.get_block_and_salt, 0))
        return d


    def test_read_with_empty_sdmf_file(self):
        self.write_sdmf_share_to_server(b"si1", empty=True)
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        # We should be able to get the encoding parameters, and they
        # should be correct
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters(params):
            self.assertThat(params, HasLength(4))
            k, n, segsize, datalen = params
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            self.assertThat(segsize, Equals(0))
            self.assertThat(datalen, Equals(0))
        d.addCallback(_check_encoding_parameters)

        # It does not make sense to get a block in this format, so we
        # should not be able to.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "get block on an empty file",
                            None,
                            mr.get_block_and_salt, 0))
        return d


    def test_verinfo_with_sdmf_file(self):
        self.write_sdmf_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        # We should be able to get the version information.
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.assertTrue(verinfo)
            self.assertThat(verinfo, HasLength(9))
            (seqnum,
             root_hash,
             salt,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.assertThat(seqnum, Equals(0))
            self.assertThat(root_hash, Equals(self.root_hash))
            self.assertThat(salt, Equals(self.salt))
            self.assertThat(segsize, Equals(36))
            self.assertThat(datalen, Equals(36))
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            expected_prefix = struct.pack(">BQ32s16s BBQQ",
                                          0,
                                          seqnum,
                                          root_hash,
                                          salt,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.assertThat(prefix, Equals(expected_prefix))
            self.assertThat(offsets, Equals(self.offsets))
        d.addCallback(_check_verinfo)
        return d


    def test_verinfo_with_mdmf_file(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.assertTrue(verinfo)
            self.assertThat(verinfo, HasLength(9))
            (seqnum,
             root_hash,
             IV,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.assertThat(seqnum, Equals(0))
            self.assertThat(root_hash, Equals(self.root_hash))
            self.assertFalse(IV)
            self.assertThat(segsize, Equals(6))
            self.assertThat(datalen, Equals(36))
            self.assertThat(k, Equals(3))
            self.assertThat(n, Equals(10))
            expected_prefix = struct.pack(">BQ32s BBQQ",
                                          1,
                                          seqnum,
                                          root_hash,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.assertThat(prefix, Equals(expected_prefix))
            self.assertThat(offsets, Equals(self.offsets))
        d.addCallback(_check_verinfo)
        return d


    def test_sdmf_writer(self):
        # Go through the motions of writing an SDMF share to the storage
        # server. Then read the storage server to see that the share got
        # written in the way that we think it should have.

        # We do this first so that the necessary instance variables get
        # set the way we want them for the tests below.
        data = self.build_test_sdmf_share()
        sdmfr = SDMFSlotWriteProxy(0,
                                   self.storage_server,
                                   b"si1",
                                   self.secrets,
                                   0, 3, 10, 36, 36)
        # Put the block and salt.
        sdmfr.put_block(self.blockdata, 0, self.salt)

        # Put the encprivkey
        sdmfr.put_encprivkey(self.encprivkey)

        # Put the block and share hash chains
        sdmfr.put_blockhashes(self.block_hash_tree)
        sdmfr.put_sharehashes(self.share_hash_chain)
        sdmfr.put_root_hash(self.root_hash)

        # Put the signature
        sdmfr.put_signature(self.signature)

        # Put the verification key
        sdmfr.put_verification_key(self.verification_key)

        # Now check to make sure that nothing has been written yet.
        self.assertThat(self.rref.write_count, Equals(0))

        # Now finish publishing
        d = sdmfr.finish_publishing()
        def _then(ignored):
            self.assertThat(self.rref.write_count, Equals(1))
            read = self.ss.slot_readv
            self.assertThat(read(b"si1", [0], [(0, len(data))]),
                                 Equals({0: [data]}))
        d.addCallback(_then)
        return d


    def test_sdmf_writer_preexisting_share(self):
        data = self.build_test_sdmf_share()
        self.write_sdmf_share_to_server(b"si1")

        # Now there is a share on the storage server. To successfully
        # write, we need to set the checkstring correctly. When we
        # don't, no write should occur.
        sdmfw = SDMFSlotWriteProxy(0,
                                   self.storage_server,
                                   b"si1",
                                   self.secrets,
                                   1, 3, 10, 36, 36)
        sdmfw.put_block(self.blockdata, 0, self.salt)

        # Put the encprivkey
        sdmfw.put_encprivkey(self.encprivkey)

        # Put the block and share hash chains
        sdmfw.put_blockhashes(self.block_hash_tree)
        sdmfw.put_sharehashes(self.share_hash_chain)

        # Put the root hash
        sdmfw.put_root_hash(self.root_hash)

        # Put the signature
        sdmfw.put_signature(self.signature)

        # Put the verification key
        sdmfw.put_verification_key(self.verification_key)

        # We shouldn't have a checkstring yet
        self.assertThat(sdmfw.get_checkstring(), Equals(b""))

        d = sdmfw.finish_publishing()
        def _then(results):
            self.assertFalse(results[0])
            # this is the correct checkstring
            self._expected_checkstring = results[1][0][0]
            return self._expected_checkstring

        d.addCallback(_then)
        d.addCallback(sdmfw.set_checkstring)
        d.addCallback(lambda ignored:
            sdmfw.get_checkstring())
        d.addCallback(lambda checkstring:
            self.assertThat(checkstring, Equals(self._expected_checkstring)))
        d.addCallback(lambda ignored:
            sdmfw.finish_publishing())
        def _then_again(results):
            self.assertTrue(results[0])
            read = self.ss.slot_readv
            self.assertThat(read(b"si1", [0], [(1, 8)]),
                                 Equals({0: [struct.pack(">Q", 1)]}))
            self.assertThat(read(b"si1", [0], [(9, len(data) - 9)]),
                                 Equals({0: [data[9:]]}))
        d.addCallback(_then_again)
        return d


class Stats(SyncTestCase):

    def setUp(self):
        super(Stats, self).setUp()
        self.sparent = LoggingServiceParent()
        self._lease_secret = itertools.count()
        self.addCleanup(self.sparent.stopService)

    def workdir(self, name):
        basedir = os.path.join("storage", "Server", name)
        return basedir

    def create(self, name):
        workdir = self.workdir(name)
        ss = StorageServer(workdir, b"\x00" * 20)
        ss.setServiceParent(self.sparent)
        return ss

    def test_latencies(self):
        ss = self.create("test_latencies")
        for i in range(10000):
            ss.add_latency("allocate", 1.0 * i)
        for i in range(1000):
            ss.add_latency("renew", 1.0 * i)
        for i in range(20):
            ss.add_latency("write", 1.0 * i)
        for i in range(10):
            ss.add_latency("cancel", 2.0 * i)
        ss.add_latency("get", 5.0)

        output = ss.get_latencies()

        self.assertThat(sorted(output.keys()),
                             Equals(sorted(["allocate", "renew", "cancel", "write", "get"])))
        self.assertThat(ss.latencies["allocate"], HasLength(1000))
        self.assertTrue(abs(output["allocate"]["mean"] - 9500) < 1, output)
        self.assertTrue(abs(output["allocate"]["01_0_percentile"] - 9010) < 1, output)
        self.assertTrue(abs(output["allocate"]["10_0_percentile"] - 9100) < 1, output)
        self.assertTrue(abs(output["allocate"]["50_0_percentile"] - 9500) < 1, output)
        self.assertTrue(abs(output["allocate"]["90_0_percentile"] - 9900) < 1, output)
        self.assertTrue(abs(output["allocate"]["95_0_percentile"] - 9950) < 1, output)
        self.assertTrue(abs(output["allocate"]["99_0_percentile"] - 9990) < 1, output)
        self.assertTrue(abs(output["allocate"]["99_9_percentile"] - 9999) < 1, output)

        self.assertThat(ss.latencies["renew"], HasLength(1000))
        self.assertTrue(abs(output["renew"]["mean"] - 500) < 1, output)
        self.assertTrue(abs(output["renew"]["01_0_percentile"] -  10) < 1, output)
        self.assertTrue(abs(output["renew"]["10_0_percentile"] - 100) < 1, output)
        self.assertTrue(abs(output["renew"]["50_0_percentile"] - 500) < 1, output)
        self.assertTrue(abs(output["renew"]["90_0_percentile"] - 900) < 1, output)
        self.assertTrue(abs(output["renew"]["95_0_percentile"] - 950) < 1, output)
        self.assertTrue(abs(output["renew"]["99_0_percentile"] - 990) < 1, output)
        self.assertTrue(abs(output["renew"]["99_9_percentile"] - 999) < 1, output)

        self.assertThat(ss.latencies["write"], HasLength(20))
        self.assertTrue(abs(output["write"]["mean"] - 9) < 1, output)
        self.assertTrue(output["write"]["01_0_percentile"] is None, output)
        self.assertTrue(abs(output["write"]["10_0_percentile"] -  2) < 1, output)
        self.assertTrue(abs(output["write"]["50_0_percentile"] - 10) < 1, output)
        self.assertTrue(abs(output["write"]["90_0_percentile"] - 18) < 1, output)
        self.assertTrue(abs(output["write"]["95_0_percentile"] - 19) < 1, output)
        self.assertTrue(output["write"]["99_0_percentile"] is None, output)
        self.assertTrue(output["write"]["99_9_percentile"] is None, output)

        self.assertThat(ss.latencies["cancel"], HasLength(10))
        self.assertTrue(abs(output["cancel"]["mean"] - 9) < 1, output)
        self.assertTrue(output["cancel"]["01_0_percentile"] is None, output)
        self.assertTrue(abs(output["cancel"]["10_0_percentile"] -  2) < 1, output)
        self.assertTrue(abs(output["cancel"]["50_0_percentile"] - 10) < 1, output)
        self.assertTrue(abs(output["cancel"]["90_0_percentile"] - 18) < 1, output)
        self.assertTrue(output["cancel"]["95_0_percentile"] is None, output)
        self.assertTrue(output["cancel"]["99_0_percentile"] is None, output)
        self.assertTrue(output["cancel"]["99_9_percentile"] is None, output)

        self.assertThat(ss.latencies["get"], HasLength(1))
        self.assertTrue(output["get"]["mean"] is None, output)
        self.assertTrue(output["get"]["01_0_percentile"] is None, output)
        self.assertTrue(output["get"]["10_0_percentile"] is None, output)
        self.assertTrue(output["get"]["50_0_percentile"] is None, output)
        self.assertTrue(output["get"]["90_0_percentile"] is None, output)
        self.assertTrue(output["get"]["95_0_percentile"] is None, output)
        self.assertTrue(output["get"]["99_0_percentile"] is None, output)
        self.assertTrue(output["get"]["99_9_percentile"] is None, output)

immutable_schemas = strategies.sampled_from(list(ALL_IMMUTABLE_SCHEMAS))

class ShareFileTests(SyncTestCase):
    """Tests for allmydata.storage.immutable.ShareFile."""

    def get_sharefile(self, **kwargs):
        sf = ShareFile(self.mktemp(), max_size=1000, create=True, **kwargs)
        sf.write_share_data(0, b"abc")
        sf.write_share_data(2, b"DEF")
        # Should be b'abDEF' now.
        return sf

    @given(immutable_schemas)
    def test_read_write(self, schema):
        """Basic writes can be read."""
        sf = self.get_sharefile(schema=schema)
        self.assertEqual(sf.read_share_data(0, 3), b"abD")
        self.assertEqual(sf.read_share_data(1, 4), b"bDEF")

    @given(immutable_schemas)
    def test_reads_beyond_file_end(self, schema):
        """Reads beyond the file size are truncated."""
        sf = self.get_sharefile(schema=schema)
        self.assertEqual(sf.read_share_data(0, 10), b"abDEF")
        self.assertEqual(sf.read_share_data(5, 10), b"")

    @given(immutable_schemas)
    def test_too_large_write(self, schema):
        """Can't do write larger than file size."""
        sf = self.get_sharefile(schema=schema)
        with self.assertRaises(DataTooLargeError):
            sf.write_share_data(0, b"x" * 3000)

    @given(immutable_schemas)
    def test_no_leases_cancelled(self, schema):
        """If no leases were cancelled, IndexError is raised."""
        sf = self.get_sharefile(schema=schema)
        with self.assertRaises(IndexError):
            sf.cancel_lease(b"garbage")

    @given(immutable_schemas)
    def test_long_lease_count_format(self, schema):
        """
        ``ShareFile.__init__`` raises ``ValueError`` if the lease count format
        given is longer than one character.
        """
        with self.assertRaises(ValueError):
            self.get_sharefile(schema=schema, lease_count_format="BB")

    @given(immutable_schemas)
    def test_large_lease_count_format(self, schema):
        """
        ``ShareFile.__init__`` raises ``ValueError`` if the lease count format
        encodes to a size larger than 8 bytes.
        """
        with self.assertRaises(ValueError):
            self.get_sharefile(schema=schema, lease_count_format="Q")

    @given(immutable_schemas)
    def test_avoid_lease_overflow(self, schema):
        """
        If the share file already has the maximum number of leases supported then
        ``ShareFile.add_lease`` raises ``struct.error`` and makes no changes
        to the share file contents.
        """
        make_lease = partial(
            LeaseInfo,
            renew_secret=b"r" * 32,
            cancel_secret=b"c" * 32,
            expiration_time=2 ** 31,
        )
        # Make it a little easier to reach the condition by limiting the
        # number of leases to only 255.
        sf = self.get_sharefile(schema=schema, lease_count_format="B")

        # Add the leases.
        for i in range(2 ** 8 - 1):
            lease = make_lease(owner_num=i)
            sf.add_lease(lease)

        # Capture the state of the share file at this point so we can
        # determine whether the next operation modifies it or not.
        with open(sf.home, "rb") as f:
            before_data = f.read()

        # It is not possible to add a 256th lease.
        lease = make_lease(owner_num=256)
        with self.assertRaises(struct.error):
            sf.add_lease(lease)

        # Compare the share file state to what we captured earlier.  Any
        # change is a bug.
        with open(sf.home, "rb") as f:
            after_data = f.read()

        self.assertEqual(before_data, after_data)

    @given(immutable_schemas)
    def test_renew_secret(self, schema):
        """
        A lease loaded from an immutable share file at any schema version can have
        its renew secret verified.
        """
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        expiration_time = 2 ** 31

        sf = self.get_sharefile(schema=schema)
        lease = LeaseInfo(
            owner_num=0,
            renew_secret=renew_secret,
            cancel_secret=cancel_secret,
            expiration_time=expiration_time,
        )
        sf.add_lease(lease)
        (loaded_lease,) = sf.get_leases()
        self.assertTrue(loaded_lease.is_renew_secret(renew_secret))

    @given(immutable_schemas)
    def test_cancel_secret(self, schema):
        """
        A lease loaded from an immutable share file at any schema version can have
        its cancel secret verified.
        """
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        expiration_time = 2 ** 31

        sf = self.get_sharefile(schema=schema)
        lease = LeaseInfo(
            owner_num=0,
            renew_secret=renew_secret,
            cancel_secret=cancel_secret,
            expiration_time=expiration_time,
        )
        sf.add_lease(lease)
        (loaded_lease,) = sf.get_leases()
        self.assertTrue(loaded_lease.is_cancel_secret(cancel_secret))

mutable_schemas = strategies.sampled_from(list(ALL_MUTABLE_SCHEMAS))

class MutableShareFileTests(SyncTestCase):
    """
    Tests for allmydata.storage.mutable.MutableShareFile.
    """
    def get_sharefile(self, **kwargs):
        return MutableShareFile(self.mktemp(), **kwargs)

    @given(
        schema=mutable_schemas,
        nodeid=strategies.just(b"x" * 20),
        write_enabler=strategies.just(b"y" * 32),
        datav=strategies.lists(
            # Limit the max size of these so we don't write *crazy* amounts of
            # data to disk.
            strategies.tuples(offsets(), strategies.binary(max_size=2 ** 8)),
            max_size=2 ** 8,
        ),
        new_length=offsets(),
    )
    def test_readv_reads_share_data(self, schema, nodeid, write_enabler, datav, new_length):
        """
        ``MutableShareFile.readv`` returns bytes from the share data portion
        of the share file.
        """
        sf = self.get_sharefile(schema=schema)
        sf.create(my_nodeid=nodeid, write_enabler=write_enabler)
        sf.writev(datav=datav, new_length=new_length)

        # Apply all of the writes to a simple in-memory buffer so we can
        # resolve the final state of the share data.  In particular, this
        # helps deal with overlapping writes which otherwise make it tricky to
        # figure out what data to expect to be able to read back.
        buf = BytesIO()
        for (offset, data) in datav:
            buf.seek(offset)
            buf.write(data)
        buf.truncate(new_length)

        # Using that buffer, determine the expected result of a readv for all
        # of the data just written.
        def read_from_buf(offset, length):
            buf.seek(offset)
            return buf.read(length)
        expected_data = list(
            read_from_buf(offset, len(data))
            for (offset, data)
            in datav
        )

        # Perform a read that gives back all of the data written to the share
        # file.
        read_vectors = list((offset, len(data)) for (offset, data) in datav)
        read_data = sf.readv(read_vectors)

        # Make sure the read reproduces the value we computed using our local
        # buffer.
        self.assertEqual(expected_data, read_data)

    @given(
        schema=mutable_schemas,
        nodeid=strategies.just(b"x" * 20),
        write_enabler=strategies.just(b"y" * 32),
        readv=strategies.lists(strategies.tuples(offsets(), lengths()), min_size=1),
        random=strategies.randoms(),
    )
    def test_readv_rejects_negative_length(self, schema, nodeid, write_enabler, readv, random):
        """
        If a negative length is given to ``MutableShareFile.readv`` in a read
        vector then ``AssertionError`` is raised.
        """
        # Pick a read vector to break with a negative value
        readv_index = random.randrange(len(readv))
        # Decide on whether we're breaking offset or length
        offset_or_length = random.randrange(2)

        # A helper function that will take a valid offset and length and break
        # one of them.
        def corrupt(break_length, offset, length):
            if break_length:
                # length must not be 0 or flipping the sign does nothing
                # length must not be negative or flipping the sign *fixes* it
                assert length > 0
                return (offset, -length)
            else:
                if offset > 0:
                    # We can break offset just by flipping the sign.
                    return (-offset, length)
                else:
                    # Otherwise it has to be zero.  If it was negative, what's
                    # going on?
                    assert offset == 0
                    # Since we can't just flip the sign on 0 to break things,
                    # replace a 0 offset with a simple negative value.  All
                    # other negative values will be tested by the `offset > 0`
                    # case above.
                    return (-1, length)

        # Break the read vector very slightly!
        broken_readv = readv[:]
        broken_readv[readv_index] = corrupt(
            offset_or_length,
            *broken_readv[readv_index]
        )

        sf = self.get_sharefile(schema=schema)
        sf.create(my_nodeid=nodeid, write_enabler=write_enabler)

        # A read with a broken read vector is an error.
        with self.assertRaises(AssertionError):
            sf.readv(broken_readv)


class LeaseInfoTests(SyncTestCase):
    """
    Tests for ``allmydata.storage.lease.LeaseInfo``.
    """
    def test_is_renew_secret(self):
        """
        ``LeaseInfo.is_renew_secret`` returns ``True`` if the value given is the
        renew secret.
        """
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        lease = LeaseInfo(
            owner_num=1,
            renew_secret=renew_secret,
            cancel_secret=cancel_secret,
        )
        self.assertTrue(lease.is_renew_secret(renew_secret))

    def test_is_not_renew_secret(self):
        """
        ``LeaseInfo.is_renew_secret`` returns ``False`` if the value given is not
        the renew secret.
        """
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        lease = LeaseInfo(
            owner_num=1,
            renew_secret=renew_secret,
            cancel_secret=cancel_secret,
        )
        self.assertFalse(lease.is_renew_secret(cancel_secret))

    def test_is_cancel_secret(self):
        """
        ``LeaseInfo.is_cancel_secret`` returns ``True`` if the value given is the
        cancel secret.
        """
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        lease = LeaseInfo(
            owner_num=1,
            renew_secret=renew_secret,
            cancel_secret=cancel_secret,
        )
        self.assertTrue(lease.is_cancel_secret(cancel_secret))

    def test_is_not_cancel_secret(self):
        """
        ``LeaseInfo.is_cancel_secret`` returns ``False`` if the value given is not
        the cancel secret.
        """
        renew_secret = b"r" * 32
        cancel_secret = b"c" * 32
        lease = LeaseInfo(
            owner_num=1,
            renew_secret=renew_secret,
            cancel_secret=cancel_secret,
        )
        self.assertFalse(lease.is_cancel_secret(renew_secret))

    @given(
        strategies.tuples(
            strategies.integers(min_value=0, max_value=2 ** 31 - 1),
            strategies.binary(min_size=32, max_size=32),
            strategies.binary(min_size=32, max_size=32),
            strategies.integers(min_value=0, max_value=2 ** 31 - 1),
            strategies.binary(min_size=20, max_size=20),
        ),
    )
    def test_immutable_size(self, initializer_args):
        """
        ``LeaseInfo.immutable_size`` returns the length of the result of
        ``LeaseInfo.to_immutable_data``.

        ``LeaseInfo.mutable_size`` returns the length of the result of
        ``LeaseInfo.to_mutable_data``.
        """
        info = LeaseInfo(*initializer_args)
        self.expectThat(
            info.to_immutable_data(),
            HasLength(info.immutable_size()),
        )
        self.expectThat(
            info.to_mutable_data(),
            HasLength(info.mutable_size()),
        )


class WriteBufferTests(SyncTestCase):
    """Tests for ``_WriteBuffer``."""

    @given(
        small_writes=strategies.lists(
            strategies.binary(min_size=1, max_size=20),
            min_size=10, max_size=20),
        batch_size=strategies.integers(min_value=5, max_value=10)
    )
    def test_write_buffer(self, small_writes: list[bytes], batch_size: int):
        """
        ``_WriteBuffer`` coalesces small writes into bigger writes based on
        the batch size.
        """
        wb = _WriteBuffer(batch_size)
        result = b""
        for data in small_writes:
            should_flush = wb.queue_write(data)
            if should_flush:
                flushed_offset, flushed_data = wb.flush()
                self.assertEqual(flushed_offset, len(result))
                # The flushed data is in batch sizes, or closest approximation
                # given queued inputs:
                self.assertTrue(batch_size <= len(flushed_data) < batch_size + len(data))
                result += flushed_data

        # Final flush:
        remaining_length = wb.get_queued_bytes()
        flushed_offset, flushed_data = wb.flush()
        self.assertEqual(remaining_length, len(flushed_data))
        self.assertEqual(flushed_offset, len(result))
        result += flushed_data

        self.assertEqual(result, b"".join(small_writes))
