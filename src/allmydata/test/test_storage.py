"""
Tests for allmydata.storage.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import native_str, PY2, bytes_to_native_str
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401


import time
import os.path
import platform
import stat
import struct
import shutil
import gc

from twisted.trial import unittest

from twisted.internet import defer

import itertools
from allmydata import interfaces
from allmydata.util import fileutil, hashutil, base32
from allmydata.storage.server import StorageServer
from allmydata.storage.shares import get_share_file
from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.immutable import BucketWriter, BucketReader, ShareFile
from allmydata.storage.common import DataTooLargeError, storage_index_to_dir, \
     UnknownMutableContainerVersionError, UnknownImmutableContainerVersionError, \
     si_b2a, si_a2b
from allmydata.storage.lease import LeaseInfo
from allmydata.immutable.layout import WriteBucketProxy, WriteBucketProxy_v2, \
     ReadBucketProxy
from allmydata.mutable.layout import MDMFSlotWriteProxy, MDMFSlotReadProxy, \
                                     LayoutInvalid, MDMFSIGNABLEHEADER, \
                                     SIGNED_PREFIX, MDMFHEADER, \
                                     MDMFOFFSETS, SDMFSlotWriteProxy, \
                                     PRIVATE_KEY_SIZE, \
                                     SIGNATURE_SIZE, \
                                     VERIFICATION_KEY_SIZE, \
                                     SHARE_HASH_CHAIN_SIZE
from allmydata.interfaces import BadWriteEnablerError
from allmydata.test.no_network import NoNetworkServer
from allmydata.storage_client import (
    _StorageServer,
)
from .common import LoggingServiceParent, ShouldFailMixin
from .common_util import FakeCanary


class UtilTests(unittest.TestCase):
    """Tests for allmydata.storage.common and .shares."""

    def test_encoding(self):
        """b2a/a2b are the same as base32."""
        s = b"\xFF HELLO \xF3"
        result = si_b2a(s)
        self.assertEqual(base32.b2a(s), result)
        self.assertEqual(si_a2b(result), s)

    def test_storage_index_to_dir(self):
        """storage_index_to_dir creates a native string path."""
        s = b"\xFF HELLO \xF3"
        path = storage_index_to_dir(s)
        parts = os.path.split(path)
        self.assertEqual(parts[0], parts[1][:2])
        self.assertIsInstance(path, native_str)

    def test_get_share_file_mutable(self):
        """A mutable share is identified by get_share_file()."""
        path = self.mktemp()
        msf = MutableShareFile(path)
        msf.create(b"12", b"abc")  # arbitrary values
        loaded = get_share_file(path)
        self.assertIsInstance(loaded, MutableShareFile)
        self.assertEqual(loaded.home, path)

    def test_get_share_file_immutable(self):
        """An immutable share is identified by get_share_file()."""
        path = self.mktemp()
        _ = ShareFile(path, max_size=1000, create=True)
        loaded = get_share_file(path)
        self.assertIsInstance(loaded, ShareFile)
        self.assertEqual(loaded.home, path)


class FakeStatsProvider(object):
    def count(self, name, delta=1):
        pass
    def register_producer(self, producer):
        pass

class Bucket(unittest.TestCase):
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
        bw = BucketWriter(self, incoming, final, 200, self.make_lease(),
                          FakeCanary())
        bw.remote_write(0, b"a"*25)
        bw.remote_write(25, b"b"*25)
        bw.remote_write(50, b"c"*25)
        bw.remote_write(75, b"d"*7)
        bw.remote_close()

    def test_readwrite(self):
        incoming, final = self.make_workdir("test_readwrite")
        bw = BucketWriter(self, incoming, final, 200, self.make_lease(),
                          FakeCanary())
        bw.remote_write(0, b"a"*25)
        bw.remote_write(25, b"b"*25)
        bw.remote_write(50, b"c"*7) # last block may be short
        bw.remote_close()

        # now read from it
        br = BucketReader(self, bw.finalhome)
        self.failUnlessEqual(br.remote_read(0, 25), b"a"*25)
        self.failUnlessEqual(br.remote_read(25, 25), b"b"*25)
        self.failUnlessEqual(br.remote_read(50, 7), b"c"*7)

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
        expirationtime = struct.pack('>L', 60*60*24*31) # 31 days in seconds

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

        self.failUnlessEqual(br.remote_read(0, len(share_data)), share_data)

        # Read past the end of share data to get the cancel secret.
        read_length = len(share_data) + len(ownernumber) + len(renewsecret) + len(cancelsecret)

        result_of_read = br.remote_read(0, read_length)
        self.failUnlessEqual(result_of_read, share_data)

        result_of_read = br.remote_read(0, len(share_data)+1)
        self.failUnlessEqual(result_of_read, share_data)

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


class BucketProxy(unittest.TestCase):
    def make_bucket(self, name, size):
        basedir = os.path.join("storage", "BucketProxy", name)
        incoming = os.path.join(basedir, "tmp", "bucket")
        final = os.path.join(basedir, "bucket")
        fileutil.make_dirs(basedir)
        fileutil.make_dirs(os.path.join(basedir, "tmp"))
        bw = BucketWriter(self, incoming, final, size, self.make_lease(),
                          FakeCanary())
        rb = RemoteBucket(bw)
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
                              uri_extension_size_max=500)
        self.failUnless(interfaces.IStorageBucketWriter.providedBy(bp), bp)

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
                       uri_extension_size_max=len(uri_extension))

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
            rb = RemoteBucket(br)
            server = NoNetworkServer(b"abc", None)
            rbp = rbp_class(rb, server, storage_index=b"")
            self.failUnlessIn("to peer", repr(rbp))
            self.failUnless(interfaces.IStorageBucketReader.providedBy(rbp), rbp)

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

class Server(unittest.TestCase):

    def setUp(self):
        self.sparent = LoggingServiceParent()
        self.sparent.startService()
        self._lease_secret = itertools.count()
    def tearDown(self):
        return self.sparent.stopService()

    def workdir(self, name):
        basedir = os.path.join("storage", "Server", name)
        return basedir

    def create(self, name, reserved_space=0, klass=StorageServer):
        workdir = self.workdir(name)
        ss = klass(workdir, b"\x00" * 20, reserved_space=reserved_space,
                   stats_provider=FakeStatsProvider())
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        self.create("test_create")

    def test_declares_fixed_1528(self):
        ss = self.create("test_declares_fixed_1528")
        ver = ss.remote_get_version()
        sv1 = ver[b'http://allmydata.org/tahoe/protocols/storage/v1']
        self.failUnless(sv1.get(b'prevents-read-past-end-of-share-data'), sv1)

    def test_declares_maximum_share_sizes(self):
        ss = self.create("test_declares_maximum_share_sizes")
        ver = ss.remote_get_version()
        sv1 = ver[b'http://allmydata.org/tahoe/protocols/storage/v1']
        self.failUnlessIn(b'maximum-immutable-share-size', sv1)
        self.failUnlessIn(b'maximum-mutable-share-size', sv1)

    def test_declares_available_space(self):
        ss = self.create("test_declares_available_space")
        ver = ss.remote_get_version()
        sv1 = ver[b'http://allmydata.org/tahoe/protocols/storage/v1']
        self.failUnlessIn(b'available-space', sv1)

    def allocate(self, ss, storage_index, sharenums, size, canary=None):
        renew_secret = hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret))
        cancel_secret = hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret))
        if not canary:
            canary = FakeCanary()
        return ss.remote_allocate_buckets(storage_index,
                                          renew_secret, cancel_secret,
                                          sharenums, size, canary)

    def test_large_share(self):
        syslow = platform.system().lower()
        if 'cygwin' in syslow or 'windows' in syslow or 'darwin' in syslow:
            raise unittest.SkipTest("If your filesystem doesn't support efficient sparse files then it is very expensive (Mac OS X and Windows don't support efficient sparse files).")

        avail = fileutil.get_available_space('.', 512*2**20)
        if avail <= 4*2**30:
            raise unittest.SkipTest("This test will spuriously fail if you have less than 4 GiB free on your filesystem.")

        ss = self.create("test_large_share")

        already,writers = self.allocate(ss, b"allocate", [0], 2**32+2)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0]))

        shnum, bucket = list(writers.items())[0]
        # This test is going to hammer your filesystem if it doesn't make a sparse file for this.  :-(
        bucket.remote_write(2**32, b"ab")
        bucket.remote_close()

        readers = ss.remote_get_buckets(b"allocate")
        reader = readers[shnum]
        self.failUnlessEqual(reader.remote_read(2**32, 2), b"ab")

    def test_dont_overfill_dirs(self):
        """
        This test asserts that if you add a second share whose storage index
        share lots of leading bits with an extant share (but isn't the exact
        same storage index), this won't add an entry to the share directory.
        """
        ss = self.create("test_dont_overfill_dirs")
        already, writers = self.allocate(ss, b"storageindex", [0], 10)
        for i, wb in writers.items():
            wb.remote_write(0, b"%10d" % i)
            wb.remote_close()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")
        children_of_storedir = set(os.listdir(storedir))

        # Now store another one under another storageindex that has leading
        # chars the same as the first storageindex.
        already, writers = self.allocate(ss, b"storageindey", [0], 10)
        for i, wb in writers.items():
            wb.remote_write(0, b"%10d" % i)
            wb.remote_close()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")
        new_children_of_storedir = set(os.listdir(storedir))
        self.failUnlessEqual(children_of_storedir, new_children_of_storedir)

    def test_remove_incoming(self):
        ss = self.create("test_remove_incoming")
        already, writers = self.allocate(ss, b"vid", list(range(3)), 10)
        for i,wb in writers.items():
            wb.remote_write(0, b"%10d" % i)
            wb.remote_close()
        incoming_share_dir = wb.incominghome
        incoming_bucket_dir = os.path.dirname(incoming_share_dir)
        incoming_prefix_dir = os.path.dirname(incoming_bucket_dir)
        incoming_dir = os.path.dirname(incoming_prefix_dir)
        self.failIf(os.path.exists(incoming_bucket_dir), incoming_bucket_dir)
        self.failIf(os.path.exists(incoming_prefix_dir), incoming_prefix_dir)
        self.failUnless(os.path.exists(incoming_dir), incoming_dir)

    def test_abort(self):
        # remote_abort, when called on a writer, should make sure that
        # the allocated size of the bucket is not counted by the storage
        # server when accounting for space.
        ss = self.create("test_abort")
        already, writers = self.allocate(ss, b"allocate", [0, 1, 2], 150)
        self.failIfEqual(ss.allocated_size(), 0)

        # Now abort the writers.
        for writer in writers.values():
            writer.remote_abort()
        self.failUnlessEqual(ss.allocated_size(), 0)


    def test_allocate(self):
        ss = self.create("test_allocate")

        self.failUnlessEqual(ss.remote_get_buckets(b"allocate"), {})

        already,writers = self.allocate(ss, b"allocate", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

        # while the buckets are open, they should not count as readable
        self.failUnlessEqual(ss.remote_get_buckets(b"allocate"), {})

        # close the buckets
        for i,wb in writers.items():
            wb.remote_write(0, b"%25d" % i)
            wb.remote_close()
            # aborting a bucket that was already closed is a no-op
            wb.remote_abort()

        # now they should be readable
        b = ss.remote_get_buckets(b"allocate")
        self.failUnlessEqual(set(b.keys()), set([0,1,2]))
        self.failUnlessEqual(b[0].remote_read(0, 25), b"%25d" % 0)
        b_str = str(b[0])
        self.failUnlessIn("BucketReader", b_str)
        self.failUnlessIn("mfwgy33dmf2g 0", b_str)

        # now if we ask about writing again, the server should offer those
        # three buckets as already present. It should offer them even if we
        # don't ask about those specific ones.
        already,writers = self.allocate(ss, b"allocate", [2,3,4], 75)
        self.failUnlessEqual(already, set([0,1,2]))
        self.failUnlessEqual(set(writers.keys()), set([3,4]))

        # while those two buckets are open for writing, the server should
        # refuse to offer them to uploaders

        already2,writers2 = self.allocate(ss, b"allocate", [2,3,4,5], 75)
        self.failUnlessEqual(already2, set([0,1,2]))
        self.failUnlessEqual(set(writers2.keys()), set([5]))

        # aborting the writes should remove the tempfiles
        for i,wb in writers2.items():
            wb.remote_abort()
        already2,writers2 = self.allocate(ss, b"allocate", [2,3,4,5], 75)
        self.failUnlessEqual(already2, set([0,1,2]))
        self.failUnlessEqual(set(writers2.keys()), set([5]))

        for i,wb in writers2.items():
            wb.remote_abort()
        for i,wb in writers.items():
            wb.remote_abort()

    def test_bad_container_version(self):
        ss = self.create("test_bad_container_version")
        a,w = self.allocate(ss, b"si1", [0], 10)
        w[0].remote_write(0, b"\xff"*10)
        w[0].remote_close()

        fn = os.path.join(ss.sharedir, storage_index_to_dir(b"si1"), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write(struct.pack(">L", 0)) # this is invalid: minimum used is v1
        f.close()

        ss.remote_get_buckets(b"allocate")

        e = self.failUnlessRaises(UnknownImmutableContainerVersionError,
                                  ss.remote_get_buckets, b"si1")
        self.failUnlessIn(" had version 0 but we wanted 1", str(e))

    def test_disconnect(self):
        # simulate a disconnection
        ss = self.create("test_disconnect")
        canary = FakeCanary()
        already,writers = self.allocate(ss, b"disconnect", [0,1,2], 75, canary)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))
        for (f,args,kwargs) in list(canary.disconnectors.values()):
            f(*args, **kwargs)
        del already
        del writers

        # that ought to delete the incoming shares
        already,writers = self.allocate(ss, b"disconnect", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

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

        ss = self.create("test_reserved_space", reserved_space=reserved)
        # 15k available, 10k reserved, leaves 5k for shares

        # a newly created and filled share incurs this much overhead, beyond
        # the size we request.
        OVERHEAD = 3*4
        LEASE_SIZE = 4+32+32+4
        canary = FakeCanary(True)
        already, writers = self.allocate(ss, b"vid1", [0,1,2], 1000, canary)
        self.failUnlessEqual(len(writers), 3)
        # now the StorageServer should have 3000 bytes provisionally
        # allocated, allowing only 2000 more to be claimed
        self.failUnlessEqual(len(ss._active_writers), 3)

        # allocating 1001-byte shares only leaves room for one
        already2, writers2 = self.allocate(ss, b"vid2", [0,1,2], 1001, canary)
        self.failUnlessEqual(len(writers2), 1)
        self.failUnlessEqual(len(ss._active_writers), 4)

        # we abandon the first set, so their provisional allocation should be
        # returned

        del already
        del writers
        gc.collect()

        self.failUnlessEqual(len(ss._active_writers), 1)
        # now we have a provisional allocation of 1001 bytes

        # and we close the second set, so their provisional allocation should
        # become real, long-term allocation, and grows to include the
        # overhead.
        for bw in writers2.values():
            bw.remote_write(0, b"a"*25)
            bw.remote_close()
        del already2
        del writers2
        del bw
        self.failUnlessEqual(len(ss._active_writers), 0)

        # this also changes the amount reported as available by call_get_disk_stats
        allocated = 1001 + OVERHEAD + LEASE_SIZE

        # now there should be ALLOCATED=1001+12+72=1085 bytes allocated, and
        # 5000-1085=3915 free, therefore we can fit 39 100byte shares
        already3, writers3 = self.allocate(ss, b"vid3", list(range(100)), 100, canary)
        self.failUnlessEqual(len(writers3), 39)
        self.failUnlessEqual(len(ss._active_writers), 39)

        del already3
        del writers3
        gc.collect()

        self.failUnlessEqual(len(ss._active_writers), 0)
        ss.disownServiceParent()
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
        self.failUnlessEqual(filelen, 100+3)
        f2 = open(filename, "rb")
        self.failUnlessEqual(f2.read(5), b"start")


    def test_leases(self):
        ss = self.create("test_leases")
        canary = FakeCanary()
        sharenums = list(range(5))
        size = 100

        rs0,cs0 = (hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)),
                   hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)))
        already,writers = ss.remote_allocate_buckets(b"si0", rs0, cs0,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 0)
        self.failUnlessEqual(len(writers), 5)
        for wb in writers.values():
            wb.remote_close()

        leases = list(ss.get_leases(b"si0"))
        self.failUnlessEqual(len(leases), 1)
        self.failUnlessEqual(set([l.renew_secret for l in leases]), set([rs0]))

        rs1,cs1 = (hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)),
                   hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)))
        already,writers = ss.remote_allocate_buckets(b"si1", rs1, cs1,
                                                     sharenums, size, canary)
        for wb in writers.values():
            wb.remote_close()

        # take out a second lease on si1
        rs2,cs2 = (hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)),
                   hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)))
        already,writers = ss.remote_allocate_buckets(b"si1", rs2, cs2,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 5)
        self.failUnlessEqual(len(writers), 0)

        leases = list(ss.get_leases(b"si1"))
        self.failUnlessEqual(len(leases), 2)
        self.failUnlessEqual(set([l.renew_secret for l in leases]), set([rs1, rs2]))

        # and a third lease, using add-lease
        rs2a,cs2a = (hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)),
                     hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)))
        ss.remote_add_lease(b"si1", rs2a, cs2a)
        leases = list(ss.get_leases(b"si1"))
        self.failUnlessEqual(len(leases), 3)
        self.failUnlessEqual(set([l.renew_secret for l in leases]), set([rs1, rs2, rs2a]))

        # add-lease on a missing storage index is silently ignored
        self.failUnlessEqual(ss.remote_add_lease(b"si18", b"", b""), None)

        # check that si0 is readable
        readers = ss.remote_get_buckets(b"si0")
        self.failUnlessEqual(len(readers), 5)

        # renew the first lease. Only the proper renew_secret should work
        ss.remote_renew_lease(b"si0", rs0)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, b"si0", cs0)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, b"si0", rs1)

        # check that si0 is still readable
        readers = ss.remote_get_buckets(b"si0")
        self.failUnlessEqual(len(readers), 5)

        # There is no such method as remote_cancel_lease for now -- see
        # ticket #1528.
        self.failIf(hasattr(ss, 'remote_cancel_lease'), \
                        "ss should not have a 'remote_cancel_lease' method/attribute")

        # test overlapping uploads
        rs3,cs3 = (hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)),
                   hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)))
        rs4,cs4 = (hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)),
                   hashutil.tagged_hash(b"blah", b"%d" % next(self._lease_secret)))
        already,writers = ss.remote_allocate_buckets(b"si3", rs3, cs3,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 0)
        self.failUnlessEqual(len(writers), 5)
        already2,writers2 = ss.remote_allocate_buckets(b"si3", rs4, cs4,
                                                       sharenums, size, canary)
        self.failUnlessEqual(len(already2), 0)
        self.failUnlessEqual(len(writers2), 0)
        for wb in writers.values():
            wb.remote_close()

        leases = list(ss.get_leases(b"si3"))
        self.failUnlessEqual(len(leases), 1)

        already3,writers3 = ss.remote_allocate_buckets(b"si3", rs4, cs4,
                                                       sharenums, size, canary)
        self.failUnlessEqual(len(already3), 5)
        self.failUnlessEqual(len(writers3), 0)

        leases = list(ss.get_leases(b"si3"))
        self.failUnlessEqual(len(leases), 2)

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
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(writers, {})

        stats = ss.get_stats()
        self.failUnlessEqual(stats["storage_server.accepting_immutable_shares"], 0)
        if "storage_server.disk_avail" in stats:
            # Some platforms may not have an API to get disk stats.
            # But if there are stats, readonly_storage means disk_avail=0
            self.failUnlessEqual(stats["storage_server.disk_avail"], 0)

    def test_discard(self):
        # discard is really only used for other tests, but we test it anyways
        workdir = self.workdir("test_discard")
        ss = StorageServer(workdir, b"\x00" * 20, discard_storage=True)
        ss.setServiceParent(self.sparent)

        already,writers = self.allocate(ss, b"vid", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))
        for i,wb in writers.items():
            wb.remote_write(0, b"%25d" % i)
            wb.remote_close()
        # since we discard the data, the shares should be present but sparse.
        # Since we write with some seeks, the data we read back will be all
        # zeros.
        b = ss.remote_get_buckets(b"vid")
        self.failUnlessEqual(set(b.keys()), set([0,1,2]))
        self.failUnlessEqual(b[0].remote_read(0, 25), b"\x00" * 25)

    def test_advise_corruption(self):
        workdir = self.workdir("test_advise_corruption")
        ss = StorageServer(workdir, b"\x00" * 20, discard_storage=True)
        ss.setServiceParent(self.sparent)

        si0_s = base32.b2a(b"si0")
        ss.remote_advise_corrupt_share(b"immutable", b"si0", 0,
                                       b"This share smells funny.\n")
        reportdir = os.path.join(workdir, "corruption-advisories")
        reports = os.listdir(reportdir)
        self.failUnlessEqual(len(reports), 1)
        report_si0 = reports[0]
        self.failUnlessIn(native_str(si0_s), report_si0)
        f = open(os.path.join(reportdir, report_si0), "rb")
        report = f.read()
        f.close()
        self.failUnlessIn(b"type: immutable", report)
        self.failUnlessIn(b"storage_index: %s" % si0_s, report)
        self.failUnlessIn(b"share_number: 0", report)
        self.failUnlessIn(b"This share smells funny.", report)

        # test the RIBucketWriter version too
        si1_s = base32.b2a(b"si1")
        already,writers = self.allocate(ss, b"si1", [1], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([1]))
        writers[1].remote_write(0, b"data")
        writers[1].remote_close()

        b = ss.remote_get_buckets(b"si1")
        self.failUnlessEqual(set(b.keys()), set([1]))
        b[1].remote_advise_corrupt_share(b"This share tastes like dust.\n")

        reports = os.listdir(reportdir)
        self.failUnlessEqual(len(reports), 2)
        report_si1 = [r for r in reports if bytes_to_native_str(si1_s) in r][0]
        f = open(os.path.join(reportdir, report_si1), "rb")
        report = f.read()
        f.close()
        self.failUnlessIn(b"type: immutable", report)
        self.failUnlessIn(b"storage_index: %s" % si1_s, report)
        self.failUnlessIn(b"share_number: 1", report)
        self.failUnlessIn(b"This share tastes like dust.", report)



class MutableServer(unittest.TestCase):

    def setUp(self):
        self.sparent = LoggingServiceParent()
        self._lease_secret = itertools.count()
    def tearDown(self):
        return self.sparent.stopService()

    def workdir(self, name):
        basedir = os.path.join("storage", "MutableServer", name)
        return basedir

    def create(self, name):
        workdir = self.workdir(name)
        ss = StorageServer(workdir, b"\x00" * 20)
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        self.create("test_create")

    def write_enabler(self, we_tag):
        return hashutil.tagged_hash(b"we_blah", we_tag)

    def renew_secret(self, tag):
        if isinstance(tag, int):
            tag = b"%d" % (tag,)
        assert isinstance(tag, bytes)
        return hashutil.tagged_hash(b"renew_blah", tag)

    def cancel_secret(self, tag):
        if isinstance(tag, int):
            tag = b"%d" % (tag,)
        assert isinstance(tag, bytes)
        return hashutil.tagged_hash(b"cancel_blah", tag)

    def allocate(self, ss, storage_index, we_tag, lease_tag, sharenums, size):
        write_enabler = self.write_enabler(we_tag)
        renew_secret = self.renew_secret(lease_tag)
        cancel_secret = self.cancel_secret(lease_tag)
        rstaraw = ss.remote_slot_testv_and_readv_and_writev
        testandwritev = dict( [ (shnum, ([], [], None) )
                         for shnum in sharenums ] )
        readv = []
        rc = rstaraw(storage_index,
                     (write_enabler, renew_secret, cancel_secret),
                     testandwritev,
                     readv)
        (did_write, readv_data) = rc
        self.failUnless(did_write)
        self.failUnless(isinstance(readv_data, dict))
        self.failUnlessEqual(len(readv_data), 0)

    def test_bad_magic(self):
        ss = self.create("test_bad_magic")
        self.allocate(ss, b"si1", b"we1", next(self._lease_secret), set([0]), 10)
        fn = os.path.join(ss.sharedir, storage_index_to_dir(b"si1"), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write(b"BAD MAGIC")
        f.close()
        read = ss.remote_slot_readv
        e = self.failUnlessRaises(UnknownMutableContainerVersionError,
                                  read, b"si1", [0], [(0,10)])
        self.failUnlessIn(" had magic ", str(e))
        self.failUnlessIn(" but we wanted ", str(e))

    def test_container_size(self):
        ss = self.create("test_container_size")
        self.allocate(ss, b"si1", b"we1", next(self._lease_secret),
                      set([0,1,2]), 100)
        read = ss.remote_slot_readv
        rstaraw = ss.remote_slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [(0,data)], len(data)+12)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

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
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        read_answer = read(b"si1", [0], [(0,10)])
        self.failUnlessEqual(read_answer, {0: [data[:10]]})

        # Sending a new_length shorter than the current length truncates the
        # data.
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [], 9)},
                         [])
        read_answer = read(b"si1", [0], [(0,10)])
        self.failUnlessEqual(read_answer, {0: [data[:9]]})

        # Sending a new_length longer than the current length doesn't change
        # the data.
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [], 20)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        read_answer = read(b"si1", [0], [(0, 20)])
        self.failUnlessEqual(read_answer, {0: [data[:9]]})

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
        self.failUnlessEqual(answer, (True, {0:[b''],1:[b''],2:[b'']}))

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
        self.failUnlessEqual(answer, (True, {0:[b'\x00'*30],1:[b''],2:[b'']}))

        # Also see if the server explicitly declares that it supports this
        # feature.
        ver = ss.remote_get_version()
        storage_v1_ver = ver[b"http://allmydata.org/tahoe/protocols/storage/v1"]
        self.failUnless(storage_v1_ver.get(b"fills-holes-with-zero-bytes"))

        # If the size is dropped to zero the share is deleted.
        answer = rstaraw(b"si1", secrets,
                         {0: ([], [(0,data)], 0)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        read_answer = read(b"si1", [0], [(0,10)])
        self.failUnlessEqual(read_answer, {})

    def test_allocate(self):
        ss = self.create("test_allocate")
        self.allocate(ss, b"si1", b"we1", next(self._lease_secret),
                      set([0,1,2]), 100)

        read = ss.remote_slot_readv
        self.failUnlessEqual(read(b"si1", [0], [(0, 10)]),
                             {0: [b""]})
        self.failUnlessEqual(read(b"si1", [], [(0, 10)]),
                             {0: [b""], 1: [b""], 2: [b""]})
        self.failUnlessEqual(read(b"si1", [0], [(100, 10)]),
                             {0: [b""]})

        # try writing to one
        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.remote_slot_testv_and_readv_and_writev
        answer = write(b"si1", secrets,
                       {0: ([], [(0,data)], None)},
                       [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        self.failUnlessEqual(read(b"si1", [0], [(0,20)]),
                             {0: [b"00000000001111111111"]})
        self.failUnlessEqual(read(b"si1", [0], [(95,10)]),
                             {0: [b"99999"]})
        #self.failUnlessEqual(s0.remote_get_length(), 100)

        bad_secrets = (b"bad write enabler", secrets[1], secrets[2])
        f = self.failUnlessRaises(BadWriteEnablerError,
                                  write, b"si1", bad_secrets,
                                  {}, [])
        self.failUnlessIn("The write enabler was recorded by nodeid 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'.", str(f))

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
        self.failUnlessEqual(answer, (False,
                                      {0: [b"000000000011", b"22222"],
                                       1: [b"", b""],
                                       2: [b"", b""],
                                       }))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})

        # as should this one
        answer = write(b"si1", secrets,
                       {0: ([(10, 5, b"lt", b"11111"),
                             ],
                            [(0, b"x"*100)],
                            None),
                        },
                       [(10,5)],
                       )
        self.failUnlessEqual(answer, (False,
                                      {0: [b"11111"],
                                       1: [b""],
                                       2: [b""]},
                                      ))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})


    def test_operators(self):
        # test operators, the data we're comparing is '11111' in all cases.
        # test both fail+pass, reset data after each one.
        ss = self.create("test_operators")

        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.remote_slot_testv_and_readv_and_writev
        read = ss.remote_slot_readv

        def reset():
            write(b"si1", secrets,
                  {0: ([], [(0,data)], None)},
                  [])

        reset()

        #  lt
        answer = write(b"si1", secrets, {0: ([(10, 5, b"lt", b"11110"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        self.failUnlessEqual(read(b"si1", [], [(0,100)]), {0: [data]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"lt", b"11111"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"lt", b"11112"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [b"y"*100]})
        reset()

        #  le
        answer = write(b"si1", secrets, {0: ([(10, 5, b"le", b"11110"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"le", b"11111"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [b"y"*100]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"le", b"11112"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [b"y"*100]})
        reset()

        #  eq
        answer = write(b"si1", secrets, {0: ([(10, 5, b"eq", b"11112"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"eq", b"11111"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [b"y"*100]})
        reset()

        #  ne
        answer = write(b"si1", secrets, {0: ([(10, 5, b"ne", b"11111"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"ne", b"11112"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [b"y"*100]})
        reset()

        #  ge
        answer = write(b"si1", secrets, {0: ([(10, 5, b"ge", b"11110"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [b"y"*100]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"ge", b"11111"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [b"y"*100]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"ge", b"11112"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        reset()

        #  gt
        answer = write(b"si1", secrets, {0: ([(10, 5, b"gt", b"11110"),
                                             ],
                                            [(0, b"y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [b"y"*100]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"gt", b"11111"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write(b"si1", secrets, {0: ([(10, 5, b"gt", b"11112"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        reset()

        # finally, test some operators against empty shares
        answer = write(b"si1", secrets, {1: ([(10, 5, b"eq", b"11112"),
                                             ],
                                            [(0, b"x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: [b"11111"]}))
        self.failUnlessEqual(read(b"si1", [0], [(0,100)]), {0: [data]})
        reset()

    def test_readv(self):
        ss = self.create("test_readv")
        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.remote_slot_testv_and_readv_and_writev
        read = ss.remote_slot_readv
        data = [(b"%d" % i) * 100 for i in range(3)]
        rc = write(b"si1", secrets,
                   {0: ([], [(0,data[0])], None),
                    1: ([], [(0,data[1])], None),
                    2: ([], [(0,data[2])], None),
                    }, [])
        self.failUnlessEqual(rc, (True, {}))

        answer = read(b"si1", [], [(0, 10)])
        self.failUnlessEqual(answer, {0: [b"0"*10],
                                      1: [b"1"*10],
                                      2: [b"2"*10]})

    def compare_leases_without_timestamps(self, leases_a, leases_b):
        self.failUnlessEqual(len(leases_a), len(leases_b))
        for i in range(len(leases_a)):
            a = leases_a[i]
            b = leases_b[i]
            self.failUnlessEqual(a.owner_num,       b.owner_num)
            self.failUnlessEqual(a.renew_secret,    b.renew_secret)
            self.failUnlessEqual(a.cancel_secret,   b.cancel_secret)
            self.failUnlessEqual(a.nodeid,          b.nodeid)

    def compare_leases(self, leases_a, leases_b):
        self.failUnlessEqual(len(leases_a), len(leases_b))
        for i in range(len(leases_a)):
            a = leases_a[i]
            b = leases_b[i]
            self.failUnlessEqual(a.owner_num,       b.owner_num)
            self.failUnlessEqual(a.renew_secret,    b.renew_secret)
            self.failUnlessEqual(a.cancel_secret,   b.cancel_secret)
            self.failUnlessEqual(a.nodeid,          b.nodeid)
            self.failUnlessEqual(a.expiration_time, b.expiration_time)

    def test_leases(self):
        ss = self.create("test_leases")
        def secrets(n):
            return ( self.write_enabler(b"we1"),
                     self.renew_secret(b"we1-%d" % n),
                     self.cancel_secret(b"we1-%d" % n) )
        data = b"".join([ (b"%d" % i) * 10 for i in range(10) ])
        write = ss.remote_slot_testv_and_readv_and_writev
        read = ss.remote_slot_readv
        rc = write(b"si1", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(rc, (True, {}))

        # create a random non-numeric file in the bucket directory, to
        # exercise the code that's supposed to ignore those.
        bucket_dir = os.path.join(self.workdir("test_leases"),
                                  "shares", storage_index_to_dir(b"si1"))
        f = open(os.path.join(bucket_dir, "ignore_me.txt"), "w")
        f.write("you ought to be ignoring me\n")
        f.close()

        s0 = MutableShareFile(os.path.join(bucket_dir, "0"))
        self.failUnlessEqual(len(list(s0.get_leases())), 1)

        # add-lease on a missing storage index is silently ignored
        self.failUnlessEqual(ss.remote_add_lease(b"si18", b"", b""), None)

        # re-allocate the slots and use the same secrets, that should update
        # the lease
        write(b"si1", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(len(list(s0.get_leases())), 1)

        # renew it directly
        ss.remote_renew_lease(b"si1", secrets(0)[1])
        self.failUnlessEqual(len(list(s0.get_leases())), 1)

        # now allocate them with a bunch of different secrets, to trigger the
        # extended lease code. Use add_lease for one of them.
        write(b"si1", secrets(1), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(len(list(s0.get_leases())), 2)
        secrets2 = secrets(2)
        ss.remote_add_lease(b"si1", secrets2[1], secrets2[2])
        self.failUnlessEqual(len(list(s0.get_leases())), 3)
        write(b"si1", secrets(3), {0: ([], [(0,data)], None)}, [])
        write(b"si1", secrets(4), {0: ([], [(0,data)], None)}, [])
        write(b"si1", secrets(5), {0: ([], [(0,data)], None)}, [])

        self.failUnlessEqual(len(list(s0.get_leases())), 6)

        all_leases = list(s0.get_leases())
        # and write enough data to expand the container, forcing the server
        # to move the leases
        write(b"si1", secrets(0),
              {0: ([], [(0,data)], 200), },
              [])

        # read back the leases, make sure they're still intact.
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

        ss.remote_renew_lease(b"si1", secrets(0)[1])
        ss.remote_renew_lease(b"si1", secrets(1)[1])
        ss.remote_renew_lease(b"si1", secrets(2)[1])
        ss.remote_renew_lease(b"si1", secrets(3)[1])
        ss.remote_renew_lease(b"si1", secrets(4)[1])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))
        # get a new copy of the leases, with the current timestamps. Reading
        # data and failing to renew/cancel leases should leave the timestamps
        # alone.
        all_leases = list(s0.get_leases())
        # renewing with a bogus token should prompt an error message

        # examine the exception thus raised, make sure the old nodeid is
        # present, to provide for share migration
        e = self.failUnlessRaises(IndexError,
                                  ss.remote_renew_lease, b"si1",
                                  secrets(20)[1])
        e_s = str(e)
        self.failUnlessIn("Unable to renew non-existent lease", e_s)
        self.failUnlessIn("I have leases accepted by nodeids:", e_s)
        self.failUnlessIn("nodeids: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' .", e_s)

        self.compare_leases(all_leases, list(s0.get_leases()))

        # reading shares should not modify the timestamp
        read(b"si1", [], [(0,200)])
        self.compare_leases(all_leases, list(s0.get_leases()))

        write(b"si1", secrets(0),
              {0: ([], [(200, b"make me bigger")], None)}, [])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

        write(b"si1", secrets(0),
              {0: ([], [(500, b"make me really bigger")], None)}, [])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

    def test_remove(self):
        ss = self.create("test_remove")
        self.allocate(ss, b"si1", b"we1", next(self._lease_secret),
                      set([0,1,2]), 100)
        readv = ss.remote_slot_readv
        writev = ss.remote_slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler(b"we1"),
                    self.renew_secret(b"we1"),
                    self.cancel_secret(b"we1") )
        # delete sh0 by setting its size to zero
        answer = writev(b"si1", secrets,
                        {0: ([], [], 0)},
                        [])
        # the answer should mention all the shares that existed before the
        # write
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )
        # but a new read should show only sh1 and sh2
        self.failUnlessEqual(readv(b"si1", [], [(0,10)]),
                             {1: [b""], 2: [b""]})

        # delete sh1 by setting its size to zero
        answer = writev(b"si1", secrets,
                        {1: ([], [], 0)},
                        [])
        self.failUnlessEqual(answer, (True, {1:[],2:[]}) )
        self.failUnlessEqual(readv(b"si1", [], [(0,10)]),
                             {2: [b""]})

        # delete sh2 by setting its size to zero
        answer = writev(b"si1", secrets,
                        {2: ([], [], 0)},
                        [])
        self.failUnlessEqual(answer, (True, {2:[]}) )
        self.failUnlessEqual(readv(b"si1", [], [(0,10)]),
                             {})
        # and the bucket directory should now be gone
        si = base32.b2a(b"si1")
        # note: this is a detail of the storage server implementation, and
        # may change in the future
        si = bytes_to_native_str(si)  # filesystem paths are native strings
        prefix = si[:2]
        prefixdir = os.path.join(self.workdir("test_remove"), "shares", prefix)
        bucketdir = os.path.join(prefixdir, si)
        self.failUnless(os.path.exists(prefixdir), prefixdir)
        self.failIf(os.path.exists(bucketdir), bucketdir)

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
        self.assertEqual([], leases)

    def test_get_slot_leases_empty_slot(self):
        """
        When ``get_slot_leases`` is called for a slot for which the server has no
        shares, it returns an empty iterable.
        """
        ss = self.create("test_get_slot_leases_empty_slot")
        self.assertEqual(
            list(ss.get_slot_leases(b"si1")),
            [],
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
        testv_is_good, read_data = ss.remote_slot_testv_and_readv_and_writev(
            storage_index=storage_index,
            secrets=secrets,
            test_and_write_vectors={
                sharenum: (testv, datav, new_length),
            },
            read_vector=read_vector,
        )

        self.assertTrue(testv_is_good)
        self.assertEqual({}, read_data)


class MDMFProxies(unittest.TestCase, ShouldFailMixin):
    def setUp(self):
        self.sparent = LoggingServiceParent()
        self._lease_secret = itertools.count()
        self.ss = self.create("MDMFProxies storage test server")
        self.rref = RemoteBucket(self.ss)
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
        write = self.ss.remote_slot_testv_and_readv_and_writev
        data = self.build_test_mdmf_share(tail_segment, empty)
        # Finally, we write the whole thing to the storage server in one
        # pass.
        testvs = [(0, 1, b"eq", b"")]
        tws = {}
        tws[0] = (testvs, [(0, data)], None)
        readv = [(0, 1)]
        results = write(storage_index, self.secrets, tws, readv)
        self.failUnless(results[0])


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
        write = self.ss.remote_slot_testv_and_readv_and_writev
        share = self.build_test_sdmf_share(empty)
        testvs = [(0, 1, b"eq", b"")]
        tws = {}
        tws[0] = (testvs, [(0, share)], None)
        readv = []
        results = write(storage_index, self.secrets, tws, readv)
        self.failUnless(results[0])


    def test_read(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        # Check that every method equals what we expect it to.
        d = defer.succeed(None)
        def _check_block_and_salt(block_and_salt):
            (block, salt) = block_and_salt
            self.failUnlessEqual(block, self.block)
            self.failUnlessEqual(salt, self.salt)

        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mr.get_block_and_salt(i))
            d.addCallback(_check_block_and_salt)

        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.failUnlessEqual(self.encprivkey, encprivkey))

        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.failUnlessEqual(self.block_hash_tree, blockhashes))

        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.failUnlessEqual(self.share_hash_chain, sharehashes))

        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.failUnlessEqual(signature, self.signature))

        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.failUnlessEqual(verification_key, self.verification_key))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0))

        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(self.root_hash, root_hash))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(0, seqnum))

        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters(args):
            (k, n, segsize, datalen) = args
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
        d.addCallback(_check_encoding_parameters)

        d.addCallback(lambda ignored:
            mr.get_checkstring())
        d.addCallback(lambda checkstring:
            self.failUnlessEqual(checkstring, checkstring))
        return d


    def test_read_with_different_tail_segment_size(self):
        self.write_test_share_to_server(b"si1", tail_segment=True)
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_block_and_salt(5)
        def _check_tail_segment(results):
            block, salt = results
            self.failUnlessEqual(len(block), 1)
            self.failUnlessEqual(block, b"a")
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
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segment_size, 6)
            self.failUnlessEqual(datalen, 36)
        d.addCallback(_check_encoding_parameters)
        return d


    def test_get_seqnum_first(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_seqnum()
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0))
        return d


    def test_get_root_hash_first(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_root_hash()
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(root_hash, self.root_hash))
        return d


    def test_get_checkstring_first(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.get_checkstring()
        d.addCallback(lambda checkstring:
            self.failUnlessEqual(checkstring, self.checkstring))
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
            self.failUnless(len(results), 2)
            result, readv = results
            self.failUnless(result)
            self.failIf(readv)
            self.old_checkstring = mw.get_checkstring()
            mw.set_checkstring(b"")
        d.addCallback(_then)
        d.addCallback(lambda ignored:
            mw.finish_publishing())
        def _then_again(results):
            self.failUnlessEqual(len(results), 2)
            result, readvs = results
            self.failIf(result)
            self.failUnlessIn(0, readvs)
            readv = readvs[0][0]
            self.failUnlessEqual(readv, self.old_checkstring)
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
            self.failUnless(result)

        def _check_failure(results):
            result, readvs = results
            self.failIf(result)

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
            self.failUnlessEqual(len(results), 2)
            res, d = results
            self.failIf(res)

        def _check_success(results):
            self.failUnlessEqual(len(results), 2)
            res, d = results
            self.failUnless(results)

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
        read = self.ss.remote_slot_readv
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
            self.failUnlessEqual(len(results), 2)
            result, ign = results
            self.failUnless(result, "publish failed")
            for i in range(6):
                self.failUnlessEqual(read(b"si1", [0], [(expected_sharedata_offset + (i * written_block_size), written_block_size)]),
                                {0: [written_block]})

            self.failUnlessEqual(len(self.encprivkey), 7)
            self.failUnlessEqual(read(b"si1", [0], [(expected_private_key_offset, 7)]),
                                 {0: [self.encprivkey]})

            expected_block_hash_offset = expected_sharedata_offset + \
                        (6 * written_block_size)
            self.failUnlessEqual(len(self.block_hash_tree_s), 32 * 6)
            self.failUnlessEqual(read(b"si1", [0], [(expected_block_hash_offset, 32 * 6)]),
                                 {0: [self.block_hash_tree_s]})

            expected_share_hash_offset = expected_private_key_offset + len(self.encprivkey)
            self.failUnlessEqual(read(b"si1", [0],[(expected_share_hash_offset, (32 + 2) * 6)]),
                                 {0: [self.share_hash_chain_s]})

            self.failUnlessEqual(read(b"si1", [0], [(9, 32)]),
                                 {0: [self.root_hash]})
            expected_signature_offset = expected_share_hash_offset + \
                len(self.share_hash_chain_s)
            self.failUnlessEqual(len(self.signature), 9)
            self.failUnlessEqual(read(b"si1", [0], [(expected_signature_offset, 9)]),
                                 {0: [self.signature]})

            expected_verification_key_offset = expected_signature_offset + len(self.signature)
            self.failUnlessEqual(len(self.verification_key), 6)
            self.failUnlessEqual(read(b"si1", [0], [(expected_verification_key_offset, 6)]),
                                 {0: [self.verification_key]})

            signable = mw.get_signable()
            verno, seq, roothash, k, n, segsize, datalen = \
                                            struct.unpack(">BQ32sBBQQ",
                                                          signable)
            self.failUnlessEqual(verno, 1)
            self.failUnlessEqual(seq, 0)
            self.failUnlessEqual(roothash, self.root_hash)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
            expected_eof_offset = expected_block_hash_offset + \
                len(self.block_hash_tree_s)

            # Check the version number to make sure that it is correct.
            expected_version_number = struct.pack(">B", 1)
            self.failUnlessEqual(read(b"si1", [0], [(0, 1)]),
                                 {0: [expected_version_number]})
            # Check the sequence number to make sure that it is correct
            expected_sequence_number = struct.pack(">Q", 0)
            self.failUnlessEqual(read(b"si1", [0], [(1, 8)]),
                                 {0: [expected_sequence_number]})
            # Check that the encoding parameters (k, N, segement size, data
            # length) are what they should be. These are  3, 10, 6, 36
            expected_k = struct.pack(">B", 3)
            self.failUnlessEqual(read(b"si1", [0], [(41, 1)]),
                                 {0: [expected_k]})
            expected_n = struct.pack(">B", 10)
            self.failUnlessEqual(read(b"si1", [0], [(42, 1)]),
                                 {0: [expected_n]})
            expected_segment_size = struct.pack(">Q", 6)
            self.failUnlessEqual(read(b"si1", [0], [(43, 8)]),
                                 {0: [expected_segment_size]})
            expected_data_length = struct.pack(">Q", 36)
            self.failUnlessEqual(read(b"si1", [0], [(51, 8)]),
                                 {0: [expected_data_length]})
            expected_offset = struct.pack(">Q", expected_private_key_offset)
            self.failUnlessEqual(read(b"si1", [0], [(59, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_share_hash_offset)
            self.failUnlessEqual(read(b"si1", [0], [(67, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_signature_offset)
            self.failUnlessEqual(read(b"si1", [0], [(75, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_verification_key_offset)
            self.failUnlessEqual(read(b"si1", [0], [(83, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_verification_key_offset + len(self.verification_key))
            self.failUnlessEqual(read(b"si1", [0], [(91, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_sharedata_offset)
            self.failUnlessEqual(read(b"si1", [0], [(99, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_block_hash_offset)
            self.failUnlessEqual(read(b"si1", [0], [(107, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_eof_offset)
            self.failUnlessEqual(read(b"si1", [0], [(115, 8)]),
                                 {0: [expected_offset]})
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
            self.failUnlessEqual(block, self.block)
            self.failUnlessEqual(salt, self.salt)

        for i in range(6):
            d.addCallback(lambda ignored, i=i:
                mr.get_block_and_salt(i))
            d.addCallback(_check_block_and_salt)

        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.failUnlessEqual(self.encprivkey, encprivkey))

        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.failUnlessEqual(self.block_hash_tree, blockhashes))

        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.failUnlessEqual(self.share_hash_chain, sharehashes))

        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.failUnlessEqual(signature, self.signature))

        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.failUnlessEqual(verification_key, self.verification_key))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0))

        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(self.root_hash, root_hash))

        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters(args):
            (k, n, segsize, datalen) = args
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
        d.addCallback(_check_encoding_parameters)

        d.addCallback(lambda ignored:
            mr.get_checkstring())
        d.addCallback(lambda checkstring:
            self.failUnlessEqual(checkstring, mw.get_checkstring()))
        return d


    def test_is_sdmf(self):
        # The MDMFSlotReadProxy should also know how to read SDMF files,
        # since it will encounter them on the grid. Callers use the
        # is_sdmf method to test this.
        self.write_sdmf_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = mr.is_sdmf()
        d.addCallback(lambda issdmf:
            self.failUnless(issdmf))
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
            self.failUnless(issdmf))

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
            self.failUnlessEqual(block, self.block * 6)
            self.failUnlessEqual(salt, self.salt)
        d.addCallback(_check_block_and_salt)

        #  - The blockhashes
        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.failUnlessEqual(self.block_hash_tree,
                                 blockhashes,
                                 blockhashes))
        #  - The sharehashes
        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.failUnlessEqual(self.share_hash_chain,
                                 sharehashes))
        #  - The keys
        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.failUnlessEqual(encprivkey, self.encprivkey, encprivkey))
        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.failUnlessEqual(verification_key,
                                 self.verification_key,
                                 verification_key))
        #  - The signature
        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.failUnlessEqual(signature, self.signature, signature))

        #  - The sequence number
        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0, seqnum))

        #  - The root hash
        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(root_hash, self.root_hash, root_hash))
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
            self.failUnless(issdmf))
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
            self.failUnless(verinfo)
            self.failUnlessEqual(len(verinfo), 9)
            (seqnum,
             root_hash,
             salt_hash,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.failUnlessEqual(seqnum, 0)
            self.failUnlessEqual(root_hash, self.root_hash)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            expected_prefix = struct.pack(MDMFSIGNABLEHEADER,
                                          1,
                                          seqnum,
                                          root_hash,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.failUnlessEqual(expected_prefix, prefix)
            self.failUnlessEqual(self.rref.read_count, 0)
        d.addCallback(_check_verinfo)
        # This is not enough data to read a block and a share, so the
        # wrapper should attempt to read this from the remote server.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        def _check_block_and_salt(block_and_salt):
            (block, salt) = block_and_salt
            self.failUnlessEqual(block, self.block)
            self.failUnlessEqual(salt, self.salt)
            self.failUnlessEqual(self.rref.read_count, 1)
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
            self.failUnless(verinfo)
            self.failUnlessEqual(len(verinfo), 9)
            (seqnum,
             root_hash,
             salt,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.failUnlessEqual(seqnum, 0)
            self.failUnlessEqual(root_hash, self.root_hash)
            self.failUnlessEqual(salt, self.salt)
            self.failUnlessEqual(segsize, 36)
            self.failUnlessEqual(datalen, 36)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            expected_prefix = struct.pack(SIGNED_PREFIX,
                                          0,
                                          seqnum,
                                          root_hash,
                                          salt,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.failUnlessEqual(expected_prefix, prefix)
            self.failUnlessEqual(self.rref.read_count, 0)
        d.addCallback(_check_verinfo)
        # This shouldn't be enough to read any share data.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        def _check_block_and_salt(block_and_salt):
            (block, salt) = block_and_salt
            self.failUnlessEqual(block, self.block * 6)
            self.failUnlessEqual(salt, self.salt)
            # TODO: Fix the read routine so that it reads only the data
            #       that it has cached if it can't read all of it.
            self.failUnlessEqual(self.rref.read_count, 2)

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
            self.failUnlessEqual(len(params), 4)
            k, n, segsize, datalen = params
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 0)
            self.failUnlessEqual(datalen, 0)
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
            self.failUnlessEqual(len(params), 4)
            k, n, segsize, datalen = params
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 0)
            self.failUnlessEqual(datalen, 0)
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
            self.failUnless(verinfo)
            self.failUnlessEqual(len(verinfo), 9)
            (seqnum,
             root_hash,
             salt,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.failUnlessEqual(seqnum, 0)
            self.failUnlessEqual(root_hash, self.root_hash)
            self.failUnlessEqual(salt, self.salt)
            self.failUnlessEqual(segsize, 36)
            self.failUnlessEqual(datalen, 36)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            expected_prefix = struct.pack(">BQ32s16s BBQQ",
                                          0,
                                          seqnum,
                                          root_hash,
                                          salt,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.failUnlessEqual(prefix, expected_prefix)
            self.failUnlessEqual(offsets, self.offsets)
        d.addCallback(_check_verinfo)
        return d


    def test_verinfo_with_mdmf_file(self):
        self.write_test_share_to_server(b"si1")
        mr = MDMFSlotReadProxy(self.storage_server, b"si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.failUnless(verinfo)
            self.failUnlessEqual(len(verinfo), 9)
            (seqnum,
             root_hash,
             IV,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.failUnlessEqual(seqnum, 0)
            self.failUnlessEqual(root_hash, self.root_hash)
            self.failIf(IV)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            expected_prefix = struct.pack(">BQ32s BBQQ",
                                          1,
                                          seqnum,
                                          root_hash,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.failUnlessEqual(prefix, expected_prefix)
            self.failUnlessEqual(offsets, self.offsets)
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
        self.failUnlessEqual(self.rref.write_count, 0)

        # Now finish publishing
        d = sdmfr.finish_publishing()
        def _then(ignored):
            self.failUnlessEqual(self.rref.write_count, 1)
            read = self.ss.remote_slot_readv
            self.failUnlessEqual(read(b"si1", [0], [(0, len(data))]),
                                 {0: [data]})
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
        self.failUnlessEqual(sdmfw.get_checkstring(), b"")

        d = sdmfw.finish_publishing()
        def _then(results):
            self.failIf(results[0])
            # this is the correct checkstring
            self._expected_checkstring = results[1][0][0]
            return self._expected_checkstring

        d.addCallback(_then)
        d.addCallback(sdmfw.set_checkstring)
        d.addCallback(lambda ignored:
            sdmfw.get_checkstring())
        d.addCallback(lambda checkstring:
            self.failUnlessEqual(checkstring, self._expected_checkstring))
        d.addCallback(lambda ignored:
            sdmfw.finish_publishing())
        def _then_again(results):
            self.failUnless(results[0])
            read = self.ss.remote_slot_readv
            self.failUnlessEqual(read(b"si1", [0], [(1, 8)]),
                                 {0: [struct.pack(">Q", 1)]})
            self.failUnlessEqual(read(b"si1", [0], [(9, len(data) - 9)]),
                                 {0: [data[9:]]})
        d.addCallback(_then_again)
        return d


class Stats(unittest.TestCase):

    def setUp(self):
        self.sparent = LoggingServiceParent()
        self._lease_secret = itertools.count()
    def tearDown(self):
        return self.sparent.stopService()

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

        self.failUnlessEqual(sorted(output.keys()),
                             sorted(["allocate", "renew", "cancel", "write", "get"]))
        self.failUnlessEqual(len(ss.latencies["allocate"]), 1000)
        self.failUnless(abs(output["allocate"]["mean"] - 9500) < 1, output)
        self.failUnless(abs(output["allocate"]["01_0_percentile"] - 9010) < 1, output)
        self.failUnless(abs(output["allocate"]["10_0_percentile"] - 9100) < 1, output)
        self.failUnless(abs(output["allocate"]["50_0_percentile"] - 9500) < 1, output)
        self.failUnless(abs(output["allocate"]["90_0_percentile"] - 9900) < 1, output)
        self.failUnless(abs(output["allocate"]["95_0_percentile"] - 9950) < 1, output)
        self.failUnless(abs(output["allocate"]["99_0_percentile"] - 9990) < 1, output)
        self.failUnless(abs(output["allocate"]["99_9_percentile"] - 9999) < 1, output)

        self.failUnlessEqual(len(ss.latencies["renew"]), 1000)
        self.failUnless(abs(output["renew"]["mean"] - 500) < 1, output)
        self.failUnless(abs(output["renew"]["01_0_percentile"] -  10) < 1, output)
        self.failUnless(abs(output["renew"]["10_0_percentile"] - 100) < 1, output)
        self.failUnless(abs(output["renew"]["50_0_percentile"] - 500) < 1, output)
        self.failUnless(abs(output["renew"]["90_0_percentile"] - 900) < 1, output)
        self.failUnless(abs(output["renew"]["95_0_percentile"] - 950) < 1, output)
        self.failUnless(abs(output["renew"]["99_0_percentile"] - 990) < 1, output)
        self.failUnless(abs(output["renew"]["99_9_percentile"] - 999) < 1, output)

        self.failUnlessEqual(len(ss.latencies["write"]), 20)
        self.failUnless(abs(output["write"]["mean"] - 9) < 1, output)
        self.failUnless(output["write"]["01_0_percentile"] is None, output)
        self.failUnless(abs(output["write"]["10_0_percentile"] -  2) < 1, output)
        self.failUnless(abs(output["write"]["50_0_percentile"] - 10) < 1, output)
        self.failUnless(abs(output["write"]["90_0_percentile"] - 18) < 1, output)
        self.failUnless(abs(output["write"]["95_0_percentile"] - 19) < 1, output)
        self.failUnless(output["write"]["99_0_percentile"] is None, output)
        self.failUnless(output["write"]["99_9_percentile"] is None, output)

        self.failUnlessEqual(len(ss.latencies["cancel"]), 10)
        self.failUnless(abs(output["cancel"]["mean"] - 9) < 1, output)
        self.failUnless(output["cancel"]["01_0_percentile"] is None, output)
        self.failUnless(abs(output["cancel"]["10_0_percentile"] -  2) < 1, output)
        self.failUnless(abs(output["cancel"]["50_0_percentile"] - 10) < 1, output)
        self.failUnless(abs(output["cancel"]["90_0_percentile"] - 18) < 1, output)
        self.failUnless(output["cancel"]["95_0_percentile"] is None, output)
        self.failUnless(output["cancel"]["99_0_percentile"] is None, output)
        self.failUnless(output["cancel"]["99_9_percentile"] is None, output)

        self.failUnlessEqual(len(ss.latencies["get"]), 1)
        self.failUnless(output["get"]["mean"] is None, output)
        self.failUnless(output["get"]["01_0_percentile"] is None, output)
        self.failUnless(output["get"]["10_0_percentile"] is None, output)
        self.failUnless(output["get"]["50_0_percentile"] is None, output)
        self.failUnless(output["get"]["90_0_percentile"] is None, output)
        self.failUnless(output["get"]["95_0_percentile"] is None, output)
        self.failUnless(output["get"]["99_0_percentile"] is None, output)
        self.failUnless(output["get"]["99_9_percentile"] is None, output)


class ShareFileTests(unittest.TestCase):
    """Tests for allmydata.storage.immutable.ShareFile."""

    def get_sharefile(self):
        sf = ShareFile(self.mktemp(), max_size=1000, create=True)
        sf.write_share_data(0, b"abc")
        sf.write_share_data(2, b"DEF")
        # Should be b'abDEF' now.
        return sf

    def test_read_write(self):
        """Basic writes can be read."""
        sf = self.get_sharefile()
        self.assertEqual(sf.read_share_data(0, 3), b"abD")
        self.assertEqual(sf.read_share_data(1, 4), b"bDEF")

    def test_reads_beyond_file_end(self):
        """Reads beyond the file size are truncated."""
        sf = self.get_sharefile()
        self.assertEqual(sf.read_share_data(0, 10), b"abDEF")
        self.assertEqual(sf.read_share_data(5, 10), b"")

    def test_too_large_write(self):
        """Can't do write larger than file size."""
        sf = self.get_sharefile()
        with self.assertRaises(DataTooLargeError):
            sf.write_share_data(0, b"x" * 3000)

    def test_no_leases_cancelled(self):
        """If no leases were cancelled, IndexError is raised."""
        sf = self.get_sharefile()
        with self.assertRaises(IndexError):
            sf.cancel_lease(b"garbage")
