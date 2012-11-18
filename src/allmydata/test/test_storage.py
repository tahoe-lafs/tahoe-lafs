import time, os.path, platform, stat, re, simplejson, struct, shutil

import mock

from twisted.trial import unittest

from twisted.internet import defer, reactor
from twisted.application import service
from foolscap.api import fireEventually
import itertools

from allmydata import interfaces
from allmydata.util import fileutil, hashutil, base32, time_format
from allmydata.storage.server import StorageServer
from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.immutable import BucketWriter, BucketReader, ShareFile
from allmydata.storage.common import DataTooLargeError, storage_index_to_dir, \
     UnknownMutableContainerVersionError, UnknownImmutableContainerVersionError
from allmydata.storage.leasedb import SHARETYPE_IMMUTABLE, SHARETYPE_MUTABLE
from allmydata.storage.expiration import ExpirationPolicy
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
from allmydata.test.common import LoggingServiceParent, ShouldFailMixin, CrawlerTestMixin
from allmydata.test.common_util import ReallyEqualMixin
from allmydata.test.common_web import WebRenderingMixin
from allmydata.test.no_network import NoNetworkServer
from allmydata.web.storage import StorageStatus, remove_prefix

class Marker:
    pass

class FakeAccount:
    def add_share(self, storage_index, shnum, used_space, sharetype, commit=True):
        pass
    def add_or_renew_default_lease(self, storage_index, shnum, commit=True):
        pass
    def mark_share_as_stable(self, storage_index, shnum, used_space, commit=True):
        pass

class FakeCanary:
    def __init__(self, ignore_disconnectors=False):
        self.ignore = ignore_disconnectors
        self.disconnectors = {}
    def notifyOnDisconnect(self, f, *args, **kwargs):
        if self.ignore:
            return
        m = Marker()
        self.disconnectors[m] = (f, args, kwargs)
        return m
    def dontNotifyOnDisconnect(self, marker):
        if self.ignore:
            return
        del self.disconnectors[marker]

class FakeStatsProvider:
    def count(self, name, delta=1):
        pass
    def register_producer(self, producer):
        pass


class BucketTestMixin:
    def bucket_writer_closed(self, bw, consumed):
        pass
    def add_latency(self, category, latency):
        pass
    def count(self, name, delta=1):
        pass


class Bucket(BucketTestMixin, unittest.TestCase):
    def make_workdir(self, name):
        basedir = os.path.join("storage", "Bucket", name)
        incoming = os.path.join(basedir, "tmp", "bucket")
        final = os.path.join(basedir, "bucket")
        fileutil.make_dirs(basedir)
        fileutil.make_dirs(os.path.join(basedir, "tmp"))
        return incoming, final

    def test_create(self):
        incoming, final = self.make_workdir("test_create")
        bw = BucketWriter(self, FakeAccount(), "si1", 0, incoming, final, 200, FakeCanary())
        bw.remote_write(0,  "a"*25)
        bw.remote_write(25, "b"*25)
        bw.remote_write(50, "c"*25)
        bw.remote_write(75, "d"*7)
        bw.remote_close()

    def test_readwrite(self):
        incoming, final = self.make_workdir("test_readwrite")
        bw = BucketWriter(self, FakeAccount(), "si1", 0, incoming, final, 200, FakeCanary())
        bw.remote_write(0,  "a"*25)
        bw.remote_write(25, "b"*25)
        bw.remote_write(50, "c"*7) # last block may be short
        bw.remote_close()

        # now read from it
        br = BucketReader(self, bw.finalhome)
        self.failUnlessEqual(br.remote_read(0,  25), "a"*25)
        self.failUnlessEqual(br.remote_read(25, 25), "b"*25)
        self.failUnlessEqual(br.remote_read(50, 7 ), "c"*7 )

    def test_read_past_end_of_share_data(self):
        # test vector for immutable files (hard-coded contents of an immutable share
        # file):

        containerdata = struct.pack('>LLL', 1, 1, 1)

        # A Tahoe-LAFS storage client would send as the share_data a
        # complicated string involving hash trees and a URI Extension Block
        # -- see allmydata/immutable/layout.py . This test, which is
        # simulating a client, just sends 'a'.
        share_data = 'a'
        extra_data = 'b' * ShareFile.LEASE_SIZE
        share_file_data = containerdata + share_data + extra_data

        incoming, final = self.make_workdir("test_read_past_end_of_share_data")

        fileutil.write(final, share_file_data)

        mockstorageserver = mock.Mock()

        # Now read from it.
        br = BucketReader(mockstorageserver, final)

        self.failUnlessEqual(br.remote_read(0, len(share_data)), share_data)

        # Read past the end of share data by 1 byte.
        result_of_read = br.remote_read(0, len(share_data)+1)
        self.failUnlessEqual(result_of_read, share_data)


class RemoteBucket:
    def __init__(self):
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


class BucketProxy(BucketTestMixin, unittest.TestCase):
    def make_bucket(self, name, size):
        basedir = os.path.join("storage", "BucketProxy", name)
        incoming = os.path.join(basedir, "tmp", "bucket")
        final = os.path.join(basedir, "bucket")
        fileutil.make_dirs(basedir)
        fileutil.make_dirs(os.path.join(basedir, "tmp"))
        si = "si1"
        bw = BucketWriter(self, FakeAccount(), si, 0, incoming, final, size, FakeCanary())
        rb = RemoteBucket()
        rb.target = bw
        return bw, rb, final

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

        crypttext_hashes = [hashutil.tagged_hash("crypt", "bar%d" % i)
                            for i in range(7)]
        block_hashes = [hashutil.tagged_hash("block", "bar%d" % i)
                        for i in range(7)]
        share_hashes = [(i, hashutil.tagged_hash("share", "bar%d" % i))
                        for i in (1,9,13)]
        uri_extension = "s" + "E"*498 + "e"

        bw, rb, sharefname = self.make_bucket(name, sharesize)
        bp = wbp_class(rb, None,
                       data_size=95,
                       block_size=25,
                       num_segments=4,
                       num_share_hashes=3,
                       uri_extension_size_max=len(uri_extension))

        d = bp.put_header()
        d.addCallback(lambda res: bp.put_block(0, "a"*25))
        d.addCallback(lambda res: bp.put_block(1, "b"*25))
        d.addCallback(lambda res: bp.put_block(2, "c"*25))
        d.addCallback(lambda res: bp.put_block(3, "d"*20))
        d.addCallback(lambda res: bp.put_crypttext_hashes(crypttext_hashes))
        d.addCallback(lambda res: bp.put_block_hashes(block_hashes))
        d.addCallback(lambda res: bp.put_share_hashes(share_hashes))
        d.addCallback(lambda res: bp.put_uri_extension(uri_extension))
        d.addCallback(lambda res: bp.close())

        # now read everything back
        def _start_reading(res):
            br = BucketReader(self, sharefname)
            rb = RemoteBucket()
            rb.target = br
            server = NoNetworkServer("abc", None)
            rbp = rbp_class(rb, server, storage_index="")
            self.failUnlessIn("to peer", repr(rbp))
            self.failUnless(interfaces.IStorageBucketReader.providedBy(rbp), rbp)

            d1 = rbp.get_block_data(0, 25, 25)
            d1.addCallback(lambda res: self.failUnlessEqual(res, "a"*25))
            d1.addCallback(lambda res: rbp.get_block_data(1, 25, 25))
            d1.addCallback(lambda res: self.failUnlessEqual(res, "b"*25))
            d1.addCallback(lambda res: rbp.get_block_data(2, 25, 25))
            d1.addCallback(lambda res: self.failUnlessEqual(res, "c"*25))
            d1.addCallback(lambda res: rbp.get_block_data(3, 25, 20))
            d1.addCallback(lambda res: self.failUnlessEqual(res, "d"*20))

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
        server = klass(workdir, "\x00" * 20, reserved_space=reserved_space,
                       stats_provider=FakeStatsProvider())
        server.setServiceParent(self.sparent)
        return server


    def test_create(self):
        self.create("test_create")

    def test_declares_fixed_1528(self):
        server = self.create("test_declares_fixed_1528")
        aa = server.get_accountant().get_anonymous_account()

        ver = aa.remote_get_version()
        sv1 = ver['http://allmydata.org/tahoe/protocols/storage/v1']
        self.failUnless(sv1.get('prevents-read-past-end-of-share-data'), sv1)

    def test_declares_maximum_share_sizes(self):
        server = self.create("test_declares_maximum_share_sizes")
        aa = server.get_accountant().get_anonymous_account()

        ver = aa.remote_get_version()
        sv1 = ver['http://allmydata.org/tahoe/protocols/storage/v1']
        self.failUnlessIn('maximum-immutable-share-size', sv1)
        self.failUnlessIn('maximum-mutable-share-size', sv1)

    def allocate(self, aa, storage_index, sharenums, size, canary=None):
        renew_secret = hashutil.tagged_hash("blah", "%d" % self._lease_secret.next())
        cancel_secret = hashutil.tagged_hash("blah", "%d" % self._lease_secret.next())
        if not canary:
            canary = FakeCanary()
        return aa.remote_allocate_buckets(storage_index,
                                          renew_secret, cancel_secret,
                                          sharenums, size, canary)

    def test_large_share(self):
        syslow = platform.system().lower()
        if 'cygwin' in syslow or 'windows' in syslow or 'darwin' in syslow:
            raise unittest.SkipTest("If your filesystem doesn't support efficient sparse files then it is very expensive (Mac OS X and Windows don't support efficient sparse files).")

        avail = fileutil.get_available_space('.', 512*2**20)
        if avail <= 4*2**30:
            raise unittest.SkipTest("This test will spuriously fail if you have less than 4 GiB free on your filesystem.")

        server = self.create("test_large_share")
        aa = server.get_accountant().get_anonymous_account()

        already,writers = self.allocate(aa, "allocate", [0], 2**32+2)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0]))

        shnum, bucket = writers.items()[0]
        # This test is going to hammer your filesystem if it doesn't make a sparse file for this.  :-(
        bucket.remote_write(2**32, "ab")
        bucket.remote_close()

        readers = aa.remote_get_buckets("allocate")
        reader = readers[shnum]
        self.failUnlessEqual(reader.remote_read(2**32, 2), "ab")

    def test_dont_overfill_dirs(self):
        """
        This test asserts that if you add a second share whose storage index
        share lots of leading bits with an extant share (but isn't the exact
        same storage index), this won't add an entry to the share directory.
        """
        server = self.create("test_dont_overfill_dirs")
        aa = server.get_accountant().get_anonymous_account()

        already, writers = self.allocate(aa, "storageindex", [0], 10)
        for i, wb in writers.items():
            wb.remote_write(0, "%10d" % i)
            wb.remote_close()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")
        children_of_storedir = set(os.listdir(storedir))

        # Now store another one under another storageindex that has leading
        # chars the same as the first storageindex.
        already, writers = self.allocate(aa, "storageindey", [0], 10)
        for i, wb in writers.items():
            wb.remote_write(0, "%10d" % i)
            wb.remote_close()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")
        new_children_of_storedir = set(os.listdir(storedir))
        self.failUnlessEqual(children_of_storedir, new_children_of_storedir)

    def test_remove_incoming(self):
        server = self.create("test_remove_incoming")
        aa = server.get_accountant().get_anonymous_account()

        already, writers = self.allocate(aa, "vid", range(3), 10)
        for i,wb in writers.items():
            wb.remote_write(0, "%10d" % i)
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
        server = self.create("test_abort")
        aa = server.get_accountant().get_anonymous_account()

        already, writers = self.allocate(aa, "allocate", [0, 1, 2], 150)
        self.failIfEqual(server.allocated_size(), 0)

        # Now abort the writers.
        for writer in writers.itervalues():
            writer.remote_abort()
        self.failUnlessEqual(server.allocated_size(), 0)

    def test_allocate(self):
        server = self.create("test_allocate")
        aa = server.get_accountant().get_anonymous_account()

        self.failUnlessEqual(aa.remote_get_buckets("allocate"), {})

        already,writers = self.allocate(aa, "allocate", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

        # while the buckets are open, they should not count as readable
        self.failUnlessEqual(aa.remote_get_buckets("allocate"), {})

        # close the buckets
        for i,wb in writers.items():
            wb.remote_write(0, "%25d" % i)
            wb.remote_close()
            # aborting a bucket that was already closed is a no-op
            wb.remote_abort()

        # now they should be readable
        b = aa.remote_get_buckets("allocate")
        self.failUnlessEqual(set(b.keys()), set([0,1,2]))
        self.failUnlessEqual(b[0].remote_read(0, 25), "%25d" % 0)
        b_str = str(b[0])
        self.failUnlessIn("BucketReader", b_str)
        self.failUnlessIn("mfwgy33dmf2g 0", b_str)

        # now if we ask about writing again, the server should offer those
        # three buckets as already present. It should offer them even if we
        # don't ask about those specific ones.
        already,writers = self.allocate(aa, "allocate", [2,3,4], 75)
        self.failUnlessEqual(already, set([0,1,2]))
        self.failUnlessEqual(set(writers.keys()), set([3,4]))

        # while those two buckets are open for writing, the server should
        # refuse to offer them to uploaders

        already2,writers2 = self.allocate(aa, "allocate", [2,3,4,5], 75)
        self.failUnlessEqual(already2, set([0,1,2]))
        self.failUnlessEqual(set(writers2.keys()), set([5]))

        # aborting the writes should remove the tempfiles
        for i,wb in writers2.items():
            wb.remote_abort()
        already2,writers2 = self.allocate(aa, "allocate", [2,3,4,5], 75)
        self.failUnlessEqual(already2, set([0,1,2]))
        self.failUnlessEqual(set(writers2.keys()), set([5]))

        for i,wb in writers2.items():
            wb.remote_abort()
        for i,wb in writers.items():
            wb.remote_abort()

    def test_bad_container_version(self):
        server = self.create("test_bad_container_version")
        aa = server.get_accountant().get_anonymous_account()

        a,w = self.allocate(aa, "si1", [0], 10)
        w[0].remote_write(0, "\xff"*10)
        w[0].remote_close()

        fn = os.path.join(server.sharedir, storage_index_to_dir("si1"), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write(struct.pack(">L", 0)) # this is invalid: minimum used is v1
        f.close()

        aa.remote_get_buckets("allocate")

        e = self.failUnlessRaises(UnknownImmutableContainerVersionError,
                                  aa.remote_get_buckets, "si1")
        self.failUnlessIn(" had version 0 but we wanted 1", str(e))

    def test_disconnect(self):
        # simulate a disconnection
        server = self.create("test_disconnect")
        aa = server.get_accountant().get_anonymous_account()

        canary = FakeCanary()
        already,writers = self.allocate(aa, "disconnect", [0,1,2], 75, canary)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))
        for (f,args,kwargs) in canary.disconnectors.values():
            f(*args, **kwargs)
        del already
        del writers

        # that ought to delete the incoming shares
        already,writers = self.allocate(aa, "disconnect", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

    @mock.patch('allmydata.util.fileutil.get_disk_stats')
    def test_reserved_space(self, mock_get_disk_stats):
        reserved_space=10000
        mock_get_disk_stats.return_value = {
            'free_for_nonroot': 15000,
            'avail': max(15000 - reserved_space, 0),
            }

        server = self.create("test_reserved_space", reserved_space=reserved_space)
        aa = server.get_accountant().get_anonymous_account()

        # 15k available, 10k reserved, leaves 5k for shares

        # a newly created and filled share incurs this much overhead, beyond
        # the size we request.
        OVERHEAD = 3*4
        LEASE_SIZE = 4+32+32+4
        canary = FakeCanary(True)
        already,writers = self.allocate(aa, "vid1", [0,1,2], 1000, canary)
        self.failUnlessEqual(len(writers), 3)
        # now the StorageServer should have 3000 bytes provisionally
        # allocated, allowing only 2000 more to be claimed
        self.failUnlessEqual(len(server._active_writers), 3)

        # allocating 1001-byte shares only leaves room for one
        already2,writers2 = self.allocate(aa, "vid2", [0,1,2], 1001, canary)
        self.failUnlessEqual(len(writers2), 1)
        self.failUnlessEqual(len(server._active_writers), 4)

        # we abandon the first set, so their provisional allocation should be
        # returned
        del already
        del writers
        self.failUnlessEqual(len(server._active_writers), 1)
        # now we have a provisional allocation of 1001 bytes

        # and we close the second set, so their provisional allocation should
        # become real, long-term allocation, and grows to include the
        # overhead.
        for bw in writers2.values():
            bw.remote_write(0, "a"*25)
            bw.remote_close()
        del already2
        del writers2
        del bw
        self.failUnlessEqual(len(server._active_writers), 0)

        allocated = 1001 + OVERHEAD + LEASE_SIZE

        # we have to manually increase available, since we're not doing real
        # disk measurements
        mock_get_disk_stats.return_value = {
            'free_for_nonroot': 15000 - allocated,
            'avail': max(15000 - allocated - reserved_space, 0),
            }

        # now there should be ALLOCATED=1001+12+72=1085 bytes allocated, and
        # 5000-1085=3915 free, therefore we can fit 39 100byte shares
        already3,writers3 = self.allocate(aa, "vid3", range(100), 100, canary)
        self.failUnlessEqual(len(writers3), 39)
        self.failUnlessEqual(len(server._active_writers), 39)

        del already3
        del writers3
        self.failUnlessEqual(len(server._active_writers), 0)
        server.disownServiceParent()
        del server

    def test_seek(self):
        basedir = self.workdir("test_seek_behavior")
        fileutil.make_dirs(basedir)
        filename = os.path.join(basedir, "testfile")
        fileutil.write(filename, "start")

        # mode="w" allows seeking-to-create-holes, but truncates pre-existing
        # files. mode="a" preserves previous contents but does not allow
        # seeking-to-create-holes. mode="r+" allows both.
        f = open(filename, "rb+")
        f.seek(100)
        f.write("100")
        f.close()
        filelen = os.stat(filename)[stat.ST_SIZE]
        self.failUnlessEqual(filelen, 100+3)
        f2 = open(filename, "rb")
        self.failUnlessEqual(f2.read(5), "start")

    def compare_leases(self, leases_a, leases_b, with_timestamps=True):
        self.failUnlessEqual(len(leases_a), len(leases_b))
        for i in range(len(leases_a)):
            a = leases_a[i]
            b = leases_b[i]
            self.failUnlessEqual(a.owner_num, b.owner_num)
            if with_timestamps:
                self.failUnlessEqual(a.renewal_time, b.renewal_time)
                self.failUnlessEqual(a.expiration_time, b.expiration_time)

    def test_leases(self):
        server = self.create("test_leases")
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        canary = FakeCanary()
        sharenums = range(5)
        size = 100

        # create a random non-numeric file in the bucket directory, to
        # exercise the code that's supposed to ignore those.
        bucket_dir = os.path.join(self.workdir("test_leases"),
                                  "shares", storage_index_to_dir("six"))
        os.makedirs(bucket_dir)
        fileutil.write(os.path.join(bucket_dir, "ignore_me.txt"),
                       "you ought to be ignoring me\n")

        already,writers = aa.remote_allocate_buckets("si1", "", "",
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 0)
        self.failUnlessEqual(len(writers), 5)
        for wb in writers.values():
            wb.remote_close()

        leases = aa.get_leases("si1")
        self.failUnlessEqual(len(leases), 5)

        aa.add_share("six", 0, 0, SHARETYPE_IMMUTABLE)
        # adding a share does not immediately add a lease
        self.failUnlessEqual(len(aa.get_leases("six")), 0)

        aa.add_or_renew_default_lease("six", 0)
        self.failUnlessEqual(len(aa.get_leases("six")), 1)

        # add-lease on a missing storage index is silently ignored
        self.failUnlessEqual(aa.remote_add_lease("si18", "", ""), None)
        self.failUnlessEqual(len(aa.get_leases("si18")), 0)

        all_leases = aa.get_leases("si1")

        # renew the lease directly
        aa.remote_renew_lease("si1", "")
        self.failUnlessEqual(len(aa.get_leases("si1")), 5)
        self.compare_leases(all_leases, aa.get_leases("si1"), with_timestamps=False)

        # Now allocate more leases using a different account.
        # A new lease should be allocated for every share in the shareset.
        sa.remote_renew_lease("si1", "")
        self.failUnlessEqual(len(aa.get_leases("si1")), 5)
        self.failUnlessEqual(len(sa.get_leases("si1")), 5)

        all_leases2 = sa.get_leases("si1")

        sa.remote_renew_lease("si1", "")
        self.compare_leases(all_leases2, sa.get_leases("si1"), with_timestamps=False)

    def test_readonly(self):
        workdir = self.workdir("test_readonly")
        server = StorageServer(workdir, "\x00" * 20, readonly_storage=True)
        server.setServiceParent(self.sparent)
        aa = server.get_accountant().get_anonymous_account()

        already,writers = self.allocate(aa, "vid", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(writers, {})

        stats = server.get_stats()
        self.failUnlessEqual(stats["storage_server.accepting_immutable_shares"], 0)
        if "storage_server.disk_avail" in stats:
            # Some platforms may not have an API to get disk stats.
            # But if there are stats, readonly_storage means disk_avail=0
            self.failUnlessEqual(stats["storage_server.disk_avail"], 0)

    def test_advise_corruption(self):
        workdir = self.workdir("test_advise_corruption")
        server = StorageServer(workdir, "\x00" * 20)
        server.setServiceParent(self.sparent)
        aa = server.get_accountant().get_anonymous_account()

        si0_s = base32.b2a("si0")
        aa.remote_advise_corrupt_share("immutable", "si0", 0,
                                       "This share smells funny.\n")
        reportdir = os.path.join(workdir, "corruption-advisories")
        reports = os.listdir(reportdir)
        self.failUnlessEqual(len(reports), 1)
        report_si0 = reports[0]
        self.failUnlessIn(si0_s, report_si0)
        f = open(os.path.join(reportdir, report_si0), "r")
        report = f.read()
        f.close()
        self.failUnlessIn("type: immutable", report)
        self.failUnlessIn("storage_index: %s" % si0_s, report)
        self.failUnlessIn("share_number: 0", report)
        self.failUnlessIn("This share smells funny.", report)

        # test the RIBucketWriter version too
        si1_s = base32.b2a("si1")
        already,writers = self.allocate(aa, "si1", [1], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([1]))
        writers[1].remote_write(0, "data")
        writers[1].remote_close()

        b = aa.remote_get_buckets("si1")
        self.failUnlessEqual(set(b.keys()), set([1]))
        b[1].remote_advise_corrupt_share("This share tastes like dust.\n")

        reports = os.listdir(reportdir)
        self.failUnlessEqual(len(reports), 2)
        report_si1 = [r for r in reports if si1_s in r][0]
        f = open(os.path.join(reportdir, report_si1), "r")
        report = f.read()
        f.close()
        self.failUnlessIn("type: immutable", report)
        self.failUnlessIn("storage_index: %s" % si1_s, report)
        self.failUnlessIn("share_number: 1", report)
        self.failUnlessIn("This share tastes like dust.", report)


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
        server = StorageServer(workdir, "\x00" * 20)
        server.setServiceParent(self.sparent)
        return server

    def test_create(self):
        self.create("test_create")


    def write_enabler(self, we_tag):
        return hashutil.tagged_hash("we_blah", we_tag)

    def renew_secret(self, tag):
        return hashutil.tagged_hash("renew_blah", str(tag))

    def cancel_secret(self, tag):
        return hashutil.tagged_hash("cancel_blah", str(tag))

    def allocate(self, aa, storage_index, we_tag, lease_tag, sharenums, size):
        write_enabler = self.write_enabler(we_tag)
        renew_secret = self.renew_secret(lease_tag)
        cancel_secret = self.cancel_secret(lease_tag)
        rstaraw = aa.remote_slot_testv_and_readv_and_writev
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
        server = self.create("test_bad_magic")
        aa = server.get_accountant().get_anonymous_account()

        self.allocate(aa, "si1", "we1", self._lease_secret.next(), set([0]), 10)
        fn = os.path.join(server.sharedir, storage_index_to_dir("si1"), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write("BAD MAGIC")
        f.close()
        read = aa.remote_slot_readv
        e = self.failUnlessRaises(UnknownMutableContainerVersionError,
                                  read, "si1", [0], [(0,10)])
        self.failUnlessIn(" had magic ", str(e))
        self.failUnlessIn(" but we wanted ", str(e))

    def test_container_size(self):
        server = self.create("test_container_size")
        aa = server.get_accountant().get_anonymous_account()

        self.allocate(aa, "si1", "we1", self._lease_secret.next(),
                      set([0,1,2]), 100)
        read = aa.remote_slot_readv
        rstaraw = aa.remote_slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        answer = rstaraw("si1", secrets,
                         {0: ([], [(0,data)], len(data)+12)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        # Trying to make the container too large (by sending a write vector
        # whose offset is too high) will raise an exception.
        TOOBIG = MutableShareFile.MAX_SIZE + 10
        self.failUnlessRaises(DataTooLargeError,
                              rstaraw, "si1", secrets,
                              {0: ([], [(TOOBIG,data)], None)},
                              [])

        answer = rstaraw("si1", secrets,
                         {0: ([], [(0,data)], None)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        read_answer = read("si1", [0], [(0,10)])
        self.failUnlessEqual(read_answer, {0: [data[:10]]})

        # Sending a new_length shorter than the current length truncates the
        # data.
        answer = rstaraw("si1", secrets,
                         {0: ([], [], 9)},
                         [])
        read_answer = read("si1", [0], [(0,10)])
        self.failUnlessEqual(read_answer, {0: [data[:9]]})

        # Sending a new_length longer than the current length doesn't change
        # the data.
        answer = rstaraw("si1", secrets,
                         {0: ([], [], 20)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        read_answer = read("si1", [0], [(0, 20)])
        self.failUnlessEqual(read_answer, {0: [data[:9]]})

        # Sending a write vector whose start is after the end of the current
        # data doesn't reveal "whatever was there last time" (palimpsest),
        # but instead fills with zeroes.

        # To test this, we fill the data area with a recognizable pattern.
        pattern = ''.join([chr(i) for i in range(100)])
        answer = rstaraw("si1", secrets,
                         {0: ([], [(0, pattern)], None)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        # Then truncate the data...
        answer = rstaraw("si1", secrets,
                         {0: ([], [], 20)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        # Just confirm that you get an empty string if you try to read from
        # past the (new) endpoint now.
        answer = rstaraw("si1", secrets,
                         {0: ([], [], None)},
                         [(20, 1980)])
        self.failUnlessEqual(answer, (True, {0:[''],1:[''],2:['']}))

        # Then the extend the file by writing a vector which starts out past
        # the end...
        answer = rstaraw("si1", secrets,
                         {0: ([], [(50, 'hellothere')], None)},
                         [])
        assert answer == (True, {0:[],1:[],2:[]})
        # Now if you read the stuff between 20 (where we earlier truncated)
        # and 50, it had better be all zeroes.
        answer = rstaraw("si1", secrets,
                         {0: ([], [], None)},
                         [(20, 30)])
        self.failUnlessEqual(answer, (True, {0:['\x00'*30],1:[''],2:['']}))

        # Also see if the server explicitly declares that it supports this
        # feature.
        ver = aa.remote_get_version()
        storage_v1_ver = ver["http://allmydata.org/tahoe/protocols/storage/v1"]
        self.failUnless(storage_v1_ver.get("fills-holes-with-zero-bytes"))

        # If the size is dropped to zero the share is deleted.
        answer = rstaraw("si1", secrets,
                         {0: ([], [(0,data)], 0)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        read_answer = read("si1", [0], [(0,10)])
        self.failUnlessEqual(read_answer, {})

    def test_allocate(self):
        server = self.create("test_allocate")
        aa = server.get_accountant().get_anonymous_account()

        self.allocate(aa, "si1", "we1", self._lease_secret.next(),
                      set([0,1,2]), 100)

        read = aa.remote_slot_readv
        self.failUnlessEqual(read("si1", [0], [(0, 10)]),
                             {0: [""]})
        self.failUnlessEqual(read("si1", [], [(0, 10)]),
                             {0: [""], 1: [""], 2: [""]})
        self.failUnlessEqual(read("si1", [0], [(100, 10)]),
                             {0: [""]})

        # try writing to one
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = aa.remote_slot_testv_and_readv_and_writev
        answer = write("si1", secrets,
                       {0: ([], [(0,data)], None)},
                       [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        self.failUnlessEqual(read("si1", [0], [(0,20)]),
                             {0: ["00000000001111111111"]})
        self.failUnlessEqual(read("si1", [0], [(95,10)]),
                             {0: ["99999"]})
        #self.failUnlessEqual(s0.remote_get_length(), 100)

        bad_secrets = ("bad write enabler", secrets[1], secrets[2])
        f = self.failUnlessRaises(BadWriteEnablerError,
                                  write, "si1", bad_secrets,
                                  {}, [])
        self.failUnlessIn("The write enabler was recorded by nodeid 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'.", f)

        # this testv should fail
        answer = write("si1", secrets,
                       {0: ([(0, 12, "eq", "444444444444"),
                             (20, 5, "eq", "22222"),
                             ],
                            [(0, "x"*100)],
                            None),
                        },
                       [(0,12), (20,5)],
                       )
        self.failUnlessEqual(answer, (False,
                                      {0: ["000000000011", "22222"],
                                       1: ["", ""],
                                       2: ["", ""],
                                       }))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})

        # as should this one
        answer = write("si1", secrets,
                       {0: ([(10, 5, "lt", "11111"),
                             ],
                            [(0, "x"*100)],
                            None),
                        },
                       [(10,5)],
                       )
        self.failUnlessEqual(answer, (False,
                                      {0: ["11111"],
                                       1: [""],
                                       2: [""]},
                                      ))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})

    def test_operators(self):
        # test operators, the data we're comparing is '11111' in all cases.
        # test both fail+pass, reset data after each one.
        server = self.create("test_operators")
        aa = server.get_accountant().get_anonymous_account()

        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = aa.remote_slot_testv_and_readv_and_writev
        read = aa.remote_slot_readv

        def reset():
            write("si1", secrets,
                  {0: ([], [(0,data)], None)},
                  [])

        reset()

        #  lt
        answer = write("si1", secrets, {0: ([(10, 5, "lt", "11110"),
                                             ],
                                            [(0, "x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        self.failUnlessEqual(read("si1", [], [(0,100)]), {0: [data]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "lt", "11111"),
                                             ],
                                            [(0, "x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "lt", "11112"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: ["y"*100]})
        reset()

        #  le
        answer = write("si1", secrets, {0: ([(10, 5, "le", "11110"),
                                             ],
                                            [(0, "x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "le", "11111"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: ["y"*100]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "le", "11112"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: ["y"*100]})
        reset()

        #  eq
        answer = write("si1", secrets, {0: ([(10, 5, "eq", "11112"),
                                             ],
                                            [(0, "x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "eq", "11111"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: ["y"*100]})
        reset()

        #  ne
        answer = write("si1", secrets, {0: ([(10, 5, "ne", "11111"),
                                             ],
                                            [(0, "x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "ne", "11112"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: ["y"*100]})
        reset()

        #  ge
        answer = write("si1", secrets, {0: ([(10, 5, "ge", "11110"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: ["y"*100]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "ge", "11111"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: ["y"*100]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "ge", "11112"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        reset()

        #  gt
        answer = write("si1", secrets, {0: ([(10, 5, "gt", "11110"),
                                             ],
                                            [(0, "y"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (True, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: ["y"*100]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "gt", "11111"),
                                             ],
                                            [(0, "x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        reset()

        answer = write("si1", secrets, {0: ([(10, 5, "gt", "11112"),
                                             ],
                                            [(0, "x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        reset()

        # finally, test some operators against empty shares
        answer = write("si1", secrets, {1: ([(10, 5, "eq", "11112"),
                                             ],
                                            [(0, "x"*100)],
                                            None,
                                            )}, [(10,5)])
        self.failUnlessEqual(answer, (False, {0: ["11111"]}))
        self.failUnlessEqual(read("si1", [0], [(0,100)]), {0: [data]})
        reset()

    def test_readv(self):
        server = self.create("test_readv")
        aa = server.get_accountant().get_anonymous_account()

        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = aa.remote_slot_testv_and_readv_and_writev
        read = aa.remote_slot_readv
        data = [("%d" % i) * 100 for i in range(3)]
        rc = write("si1", secrets,
                   {0: ([], [(0,data[0])], None),
                    1: ([], [(0,data[1])], None),
                    2: ([], [(0,data[2])], None),
                    }, [])
        self.failUnlessEqual(rc, (True, {}))

        answer = read("si1", [], [(0, 10)])
        self.failUnlessEqual(answer, {0: ["0"*10],
                                      1: ["1"*10],
                                      2: ["2"*10]})

    def compare_leases(self, leases_a, leases_b, with_timestamps=True):
        self.failUnlessEqual(len(leases_a), len(leases_b))
        for i in range(len(leases_a)):
            a = leases_a[i]
            b = leases_b[i]
            self.failUnlessEqual(a.owner_num, b.owner_num)
            if with_timestamps:
                self.failUnlessEqual(a.renewal_time, b.renewal_time)
                self.failUnlessEqual(a.expiration_time, b.expiration_time)

    def test_leases(self):
        server = self.create("test_leases")
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        def secrets(n):
            return ( self.write_enabler("we1"),
                     self.renew_secret("we1-%d" % n),
                     self.cancel_secret("we1-%d" % n) )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = aa.remote_slot_testv_and_readv_and_writev
        write2 = sa.remote_slot_testv_and_readv_and_writev
        read = aa.remote_slot_readv
        rc = write("si0", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(rc, (True, {}))

        # create a random non-numeric file in the bucket directory, to
        # exercise the code that's supposed to ignore those.
        bucket_dir = os.path.join(self.workdir("test_leases"),
                                  "shares", storage_index_to_dir("six"))
        os.makedirs(bucket_dir)
        fileutil.write(os.path.join(bucket_dir, "ignore_me.txt"),
                       "you ought to be ignoring me\n")

        s0 = MutableShareFile(os.path.join(bucket_dir, "0"))
        s0.create("nodeid", secrets(0)[0])

        aa.add_share("six", 0, 0, SHARETYPE_MUTABLE)
        # adding a share does not immediately add a lease
        self.failUnlessEqual(len(aa.get_leases("six")), 0)

        aa.add_or_renew_default_lease("six", 0)
        self.failUnlessEqual(len(aa.get_leases("six")), 1)

        # add-lease on a missing storage index is silently ignored
        self.failUnlessEqual(aa.remote_add_lease("si18", "", ""), None)
        self.failUnlessEqual(len(aa.get_leases("si18")), 0)

        # update the lease by writing
        write("si1", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(len(aa.get_leases("si1")), 1)

        # renew it directly
        aa.remote_renew_lease("si1", secrets(0)[1])
        self.failUnlessEqual(len(aa.get_leases("si1")), 1)

        # now allocate another lease using a different account
        write2("si1", secrets(1), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(len(aa.get_leases("si1")), 1)
        self.failUnlessEqual(len(sa.get_leases("si1")), 1)

        aa_leases = aa.get_leases("si1")
        sa_leases = sa.get_leases("si1")

        aa.remote_renew_lease("si1", secrets(0)[1])
        self.compare_leases(aa_leases, aa.get_leases("si1"), with_timestamps=False)

        sa.remote_renew_lease("si1", secrets(1)[1])
        self.compare_leases(sa_leases, sa.get_leases("si1"), with_timestamps=False)

        # get a new copy of the leases, with the current timestamps. Reading
        # data should leave the timestamps alone.
        aa_leases = aa.get_leases("si1")

        # reading shares should not modify the timestamp
        read("si1", [], [(0,200)])
        self.compare_leases(aa_leases, aa.get_leases("si1"))

        write("si1", secrets(0),
              {0: ([], [(200, "make me bigger")], None)}, [])
        self.compare_leases(aa_leases, aa.get_leases("si1"), with_timestamps=False)

        write("si1", secrets(0),
              {0: ([], [(500, "make me really bigger")], None)}, [])
        self.compare_leases(aa_leases, aa.get_leases("si1"), with_timestamps=False)

    def test_remove(self):
        server = self.create("test_remove")
        aa = server.get_accountant().get_anonymous_account()

        self.allocate(aa, "si1", "we1", self._lease_secret.next(),
                      set([0,1,2]), 100)
        readv = aa.remote_slot_readv
        writev = aa.remote_slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        # delete sh0 by setting its size to zero
        answer = writev("si1", secrets,
                        {0: ([], [], 0)},
                        [])
        # the answer should mention all the shares that existed before the
        # write
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )
        # but a new read should show only sh1 and sh2
        self.failUnlessEqual(readv("si1", [], [(0,10)]),
                             {1: [""], 2: [""]})

        # delete sh1 by setting its size to zero
        answer = writev("si1", secrets,
                        {1: ([], [], 0)},
                        [])
        self.failUnlessEqual(answer, (True, {1:[],2:[]}) )
        self.failUnlessEqual(readv("si1", [], [(0,10)]),
                             {2: [""]})

        # delete sh2 by setting its size to zero
        answer = writev("si1", secrets,
                        {2: ([], [], 0)},
                        [])
        self.failUnlessEqual(answer, (True, {2:[]}) )
        self.failUnlessEqual(readv("si1", [], [(0,10)]),
                             {})
        # and the bucket directory should now be gone
        si = base32.b2a("si1")
        # note: this is a detail of the storage server implementation, and
        # may change in the future
        prefix = si[:2]
        prefixdir = os.path.join(self.workdir("test_remove"), "shares", prefix)
        bucketdir = os.path.join(prefixdir, si)
        self.failUnless(os.path.exists(prefixdir), prefixdir)
        self.failIf(os.path.exists(bucketdir), bucketdir)


class MDMFProxies(unittest.TestCase, ShouldFailMixin):
    def setUp(self):
        self.sparent = LoggingServiceParent()
        self._lease_secret = itertools.count()
        self.aa = self.create("MDMFProxies storage test server")
        self.rref = RemoteBucket()
        self.rref.target = self.aa
        self.secrets = (self.write_enabler("we_secret"),
                        self.renew_secret("renew_secret"),
                        self.cancel_secret("cancel_secret"))
        self.segment = "aaaaaa"
        self.block = "aa"
        self.salt = "a" * 16
        self.block_hash = "a" * 32
        self.block_hash_tree = [self.block_hash for i in xrange(6)]
        self.share_hash = self.block_hash
        self.share_hash_chain = dict([(i, self.share_hash) for i in xrange(6)])
        self.signature = "foobarbaz"
        self.verification_key = "vvvvvv"
        self.encprivkey = "private"
        self.root_hash = self.block_hash
        self.salt_hash = self.root_hash
        self.salt_hash_tree = [self.salt_hash for i in xrange(6)]
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
        return hashutil.tagged_hash("we_blah", we_tag)

    def renew_secret(self, tag):
        return hashutil.tagged_hash("renew_blah", str(tag))

    def cancel_secret(self, tag):
        return hashutil.tagged_hash("cancel_blah", str(tag))

    def workdir(self, name):
        basedir = os.path.join("storage", "MutableServer", name)
        return basedir

    def create(self, name):
        workdir = self.workdir(name)
        server = StorageServer(workdir, "\x00" * 20)
        server.setServiceParent(self.sparent)
        return server.get_accountant().get_anonymous_account()

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
        sharedata = ""
        if not tail_segment and not empty:
            for i in xrange(6):
                sharedata += self.salt + self.block
        elif tail_segment:
            for i in xrange(5):
                sharedata += self.salt + self.block
            sharedata += self.salt + "a"

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
        nulls = "".join([" " for i in xrange(len(data), share_data_offset)])
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
        I write some data for the read tests to read to self.aa

        If tail_segment=True, then I will write a share that has a
        smaller tail segment than other segments.
        """
        write = self.aa.remote_slot_testv_and_readv_and_writev
        data = self.build_test_mdmf_share(tail_segment, empty)
        # Finally, we write the whole thing to the storage server in one
        # pass.
        testvs = [(0, 1, "eq", "")]
        tws = {}
        tws[0] = (testvs, [(0, data)], None)
        readv = [(0, 1)]
        results = write(storage_index, self.secrets, tws, readv)
        self.failUnless(results[0])

    def build_test_sdmf_share(self, empty=False):
        if empty:
            sharedata = ""
        else:
            sharedata = self.segment * 6
        self.sharedata = sharedata
        blocksize = len(sharedata) / 3
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
        final_share = "".join([prefix,
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
        assert self.rref
        write = self.aa.remote_slot_testv_and_readv_and_writev
        share = self.build_test_sdmf_share(empty)
        testvs = [(0, 1, "eq", "")]
        tws = {}
        tws[0] = (testvs, [(0, share)], None)
        readv = []
        results = write(storage_index, self.secrets, tws, readv)
        self.failUnless(results[0])


    def test_read(self):
        self.write_test_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        # Check that every method equals what we expect it to.
        d = defer.succeed(None)
        def _check_block_and_salt((block, salt)):
            self.failUnlessEqual(block, self.block)
            self.failUnlessEqual(salt, self.salt)

        for i in xrange(6):
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
        def _check_encoding_parameters((k, n, segsize, datalen)):
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
        self.write_test_share_to_server("si1", tail_segment=True)
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = mr.get_block_and_salt(5)
        def _check_tail_segment(results):
            block, salt = results
            self.failUnlessEqual(len(block), 1)
            self.failUnlessEqual(block, "a")
        d.addCallback(_check_tail_segment)
        return d

    def test_get_block_with_invalid_segnum(self):
        self.write_test_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test invalid segnum",
                            None,
                            mr.get_block_and_salt, 7))
        return d

    def test_get_encoding_parameters_first(self):
        self.write_test_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = mr.get_encoding_parameters()
        def _check_encoding_parameters((k, n, segment_size, datalen)):
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segment_size, 6)
            self.failUnlessEqual(datalen, 36)
        d.addCallback(_check_encoding_parameters)
        return d

    def test_get_seqnum_first(self):
        self.write_test_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = mr.get_seqnum()
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0))
        return d

    def test_get_root_hash_first(self):
        self.write_test_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = mr.get_root_hash()
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(root_hash, self.root_hash))
        return d

    def test_get_checkstring_first(self):
        self.write_test_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
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
        mw = self._make_new_mw("si1", 0)

        for i in xrange(6):
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
            mw.set_checkstring("")
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
        mw = self._make_new_mw("si1", 0)
        d = defer.succeed(None)
        for i in xrange(6):
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
        mw = self._make_new_mw("si1", 0)
        d = defer.succeed(None)
        # Put everything up to and including the verification key.
        for i in xrange(6):
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
        mw1 = self._make_new_mw("si1", 0)
        mw2 = self._make_new_mw("si1", 0)

        def _check_success(results):
            result, readvs = results
            self.failUnless(result)

        def _check_failure(results):
            result, readvs = results
            self.failIf(result)

        def _write_share(mw):
            for i in xrange(6):
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
        mw = self._make_new_mw("si1", 0)
        invalid_salt = "a" * 17 # 17 bytes
        another_invalid_salt = "b" * 15 # 15 bytes
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

        mw = self._make_new_mw("si1", 0)
        mw.set_checkstring("this is a lie")
        for i in xrange(6):
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
            mw.set_checkstring(""))
        d.addCallback(lambda ignored:
            mw.finish_publishing())
        d.addCallback(_check_success)
        return d


    def serialize_blockhashes(self, blockhashes):
        return "".join(blockhashes)

    def serialize_sharehashes(self, sharehashes):
        ret = "".join([struct.pack(">H32s", i, sharehashes[i])
                        for i in sorted(sharehashes.keys())])
        return ret


    def test_write(self):
        # This translates to a file with 6 6-byte segments, and with 2-byte
        # blocks.
        mw = self._make_new_mw("si1", 0)
        # Test writing some blocks.
        read = self.aa.remote_slot_readv
        expected_private_key_offset = struct.calcsize(MDMFHEADER)
        expected_sharedata_offset = struct.calcsize(MDMFHEADER) + \
                                    PRIVATE_KEY_SIZE + \
                                    SIGNATURE_SIZE + \
                                    VERIFICATION_KEY_SIZE + \
                                    SHARE_HASH_CHAIN_SIZE
        written_block_size = 2 + len(self.salt)
        written_block = self.block + self.salt
        for i in xrange(6):
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
            for i in xrange(6):
                self.failUnlessEqual(read("si1", [0], [(expected_sharedata_offset + (i * written_block_size), written_block_size)]),
                                {0: [written_block]})

            self.failUnlessEqual(len(self.encprivkey), 7)
            self.failUnlessEqual(read("si1", [0], [(expected_private_key_offset, 7)]),
                                 {0: [self.encprivkey]})

            expected_block_hash_offset = expected_sharedata_offset + \
                        (6 * written_block_size)
            self.failUnlessEqual(len(self.block_hash_tree_s), 32 * 6)
            self.failUnlessEqual(read("si1", [0], [(expected_block_hash_offset, 32 * 6)]),
                                 {0: [self.block_hash_tree_s]})

            expected_share_hash_offset = expected_private_key_offset + len(self.encprivkey)
            self.failUnlessEqual(read("si1", [0],[(expected_share_hash_offset, (32 + 2) * 6)]),
                                 {0: [self.share_hash_chain_s]})

            self.failUnlessEqual(read("si1", [0], [(9, 32)]),
                                 {0: [self.root_hash]})
            expected_signature_offset = expected_share_hash_offset + \
                len(self.share_hash_chain_s)
            self.failUnlessEqual(len(self.signature), 9)
            self.failUnlessEqual(read("si1", [0], [(expected_signature_offset, 9)]),
                                 {0: [self.signature]})

            expected_verification_key_offset = expected_signature_offset + len(self.signature)
            self.failUnlessEqual(len(self.verification_key), 6)
            self.failUnlessEqual(read("si1", [0], [(expected_verification_key_offset, 6)]),
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
            self.failUnlessEqual(read("si1", [0], [(0, 1)]),
                                 {0: [expected_version_number]})
            # Check the sequence number to make sure that it is correct
            expected_sequence_number = struct.pack(">Q", 0)
            self.failUnlessEqual(read("si1", [0], [(1, 8)]),
                                 {0: [expected_sequence_number]})
            # Check that the encoding parameters (k, N, segement size, data
            # length) are what they should be. These are  3, 10, 6, 36
            expected_k = struct.pack(">B", 3)
            self.failUnlessEqual(read("si1", [0], [(41, 1)]),
                                 {0: [expected_k]})
            expected_n = struct.pack(">B", 10)
            self.failUnlessEqual(read("si1", [0], [(42, 1)]),
                                 {0: [expected_n]})
            expected_segment_size = struct.pack(">Q", 6)
            self.failUnlessEqual(read("si1", [0], [(43, 8)]),
                                 {0: [expected_segment_size]})
            expected_data_length = struct.pack(">Q", 36)
            self.failUnlessEqual(read("si1", [0], [(51, 8)]),
                                 {0: [expected_data_length]})
            expected_offset = struct.pack(">Q", expected_private_key_offset)
            self.failUnlessEqual(read("si1", [0], [(59, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_share_hash_offset)
            self.failUnlessEqual(read("si1", [0], [(67, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_signature_offset)
            self.failUnlessEqual(read("si1", [0], [(75, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_verification_key_offset)
            self.failUnlessEqual(read("si1", [0], [(83, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_verification_key_offset + len(self.verification_key))
            self.failUnlessEqual(read("si1", [0], [(91, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_sharedata_offset)
            self.failUnlessEqual(read("si1", [0], [(99, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_block_hash_offset)
            self.failUnlessEqual(read("si1", [0], [(107, 8)]),
                                 {0: [expected_offset]})
            expected_offset = struct.pack(">Q", expected_eof_offset)
            self.failUnlessEqual(read("si1", [0], [(115, 8)]),
                                 {0: [expected_offset]})
        d.addCallback(_check_publish)
        return d


    def _make_new_mw(self, si, share, datalength=36):
        # This is a file of size 36 bytes. Since it has a segment
        # size of 6, we know that it has 6 byte segments, which will
        # be split into blocks of 2 bytes because our FEC k
        # parameter is 3.
        mw = MDMFSlotWriteProxy(share, self.rref, si, self.secrets, 0, 3, 10,
                                6, datalength)
        return mw

    def test_write_rejected_with_too_many_blocks(self):
        mw = self._make_new_mw("si0", 0)

        # Try writing too many blocks. We should not be able to write
        # more than 6
        # blocks into each share.
        d = defer.succeed(None)
        for i in xrange(6):
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
        mw = self._make_new_mw("si1", 0)
        bad_salt = "a" * 17 # 17 bytes
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test_invalid_salt",
                            None, mw.put_block, self.block, 7, bad_salt))
        return d

    def test_write_rejected_with_invalid_root_hash(self):
        # Try writing an invalid root hash. This should be SHA256d, and
        # 32 bytes long as a result.
        mw = self._make_new_mw("si2", 0)
        # 17 bytes != 32 bytes
        invalid_root_hash = "a" * 17
        d = defer.succeed(None)
        # Before this test can work, we need to put some blocks + salts,
        # a block hash tree, and a share hash tree. Otherwise, we'll see
        # failures that match what we are looking for, but are caused by
        # the constraints imposed on operation ordering.
        for i in xrange(6):
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
        invalid_block = "a"
        mw = self._make_new_mw("si3", 0, 33) # implies a tail segment with
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
        for i in xrange(5):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        # Try to put an invalid tail segment
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test invalid tail segment",
                            None,
                            mw.put_block, self.block, 5, self.salt))
        valid_block = "a"
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
        mw0 = self._make_new_mw("si0", 0)
        # Write some shares
        d = defer.succeed(None)
        for i in xrange(6):
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
        mw = self._make_new_mw("si1", 0)
        # Write a share using the mutable writer, and make sure that the
        # reader knows how to read everything back to us.
        d = defer.succeed(None)
        for i in xrange(6):
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

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        def _check_block_and_salt((block, salt)):
            self.failUnlessEqual(block, self.block)
            self.failUnlessEqual(salt, self.salt)

        for i in xrange(6):
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
        def _check_encoding_parameters((k, n, segsize, datalen)):
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
        self.write_sdmf_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = mr.is_sdmf()
        d.addCallback(lambda issdmf:
            self.failUnless(issdmf))
        return d

    def test_reads_sdmf(self):
        # The slot read proxy should, naturally, know how to tell us
        # about data in the SDMF format
        self.write_sdmf_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
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
        self.write_sdmf_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
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
        self.write_test_share_to_server("si1")
        def _make_mr(ignored, length):
            mr = MDMFSlotReadProxy(self.rref, "si1", 0, mdmf_data[:length])
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
        def _check_block_and_salt((block, salt)):
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
        self.write_sdmf_share_to_server("si1")
        def _make_mr(ignored, length):
            mr = MDMFSlotReadProxy(self.rref, "si1", 0, sdmf_data[:length])
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
        def _check_block_and_salt((block, salt)):
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
        self.write_test_share_to_server("si1", empty=True)
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
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
        self.write_sdmf_share_to_server("si1", empty=True)
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
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
        self.write_sdmf_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
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
        self.write_test_share_to_server("si1")
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
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
                                   self.rref,
                                   "si1",
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
            read = self.aa.remote_slot_readv
            self.failUnlessEqual(read("si1", [0], [(0, len(data))]),
                                 {0: [data]})
        d.addCallback(_then)
        return d

    def test_sdmf_writer_preexisting_share(self):
        data = self.build_test_sdmf_share()
        self.write_sdmf_share_to_server("si1")

        # Now there is a share on the storage server. To successfully
        # write, we need to set the checkstring correctly. When we
        # don't, no write should occur.
        sdmfw = SDMFSlotWriteProxy(0,
                                   self.rref,
                                   "si1",
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
        self.failUnlessEqual(sdmfw.get_checkstring(), "")

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
            read = self.aa.remote_slot_readv
            self.failUnlessEqual(read("si1", [0], [(1, 8)]),
                                 {0: [struct.pack(">Q", 1)]})
            self.failUnlessEqual(read("si1", [0], [(9, len(data) - 9)]),
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
        server = StorageServer(workdir, "\x00" * 20)
        server.setServiceParent(self.sparent)
        return server


    def test_latencies(self):
        server = self.create("test_latencies")
        for i in range(10000):
            server.add_latency("allocate", 1.0 * i)
        for i in range(1000):
            server.add_latency("renew", 1.0 * i)
        for i in range(20):
            server.add_latency("write", 1.0 * i)
        for i in range(10):
            server.add_latency("cancel", 2.0 * i)
        server.add_latency("get", 5.0)

        output = server.get_latencies()

        self.failUnlessEqual(sorted(output.keys()),
                             sorted(["allocate", "renew", "cancel", "write", "get"]))
        self.failUnlessEqual(len(server.latencies["allocate"]), 1000)
        self.failUnless(abs(output["allocate"]["mean"] - 9500) < 1, output)
        self.failUnless(abs(output["allocate"]["01_0_percentile"] - 9010) < 1, output)
        self.failUnless(abs(output["allocate"]["10_0_percentile"] - 9100) < 1, output)
        self.failUnless(abs(output["allocate"]["50_0_percentile"] - 9500) < 1, output)
        self.failUnless(abs(output["allocate"]["90_0_percentile"] - 9900) < 1, output)
        self.failUnless(abs(output["allocate"]["95_0_percentile"] - 9950) < 1, output)
        self.failUnless(abs(output["allocate"]["99_0_percentile"] - 9990) < 1, output)
        self.failUnless(abs(output["allocate"]["99_9_percentile"] - 9999) < 1, output)

        self.failUnlessEqual(len(server.latencies["renew"]), 1000)
        self.failUnless(abs(output["renew"]["mean"] - 500) < 1, output)
        self.failUnless(abs(output["renew"]["01_0_percentile"] -  10) < 1, output)
        self.failUnless(abs(output["renew"]["10_0_percentile"] - 100) < 1, output)
        self.failUnless(abs(output["renew"]["50_0_percentile"] - 500) < 1, output)
        self.failUnless(abs(output["renew"]["90_0_percentile"] - 900) < 1, output)
        self.failUnless(abs(output["renew"]["95_0_percentile"] - 950) < 1, output)
        self.failUnless(abs(output["renew"]["99_0_percentile"] - 990) < 1, output)
        self.failUnless(abs(output["renew"]["99_9_percentile"] - 999) < 1, output)

        self.failUnlessEqual(len(server.latencies["write"]), 20)
        self.failUnless(abs(output["write"]["mean"] - 9) < 1, output)
        self.failUnless(output["write"]["01_0_percentile"] is None, output)
        self.failUnless(abs(output["write"]["10_0_percentile"] -  2) < 1, output)
        self.failUnless(abs(output["write"]["50_0_percentile"] - 10) < 1, output)
        self.failUnless(abs(output["write"]["90_0_percentile"] - 18) < 1, output)
        self.failUnless(abs(output["write"]["95_0_percentile"] - 19) < 1, output)
        self.failUnless(output["write"]["99_0_percentile"] is None, output)
        self.failUnless(output["write"]["99_9_percentile"] is None, output)

        self.failUnlessEqual(len(server.latencies["cancel"]), 10)
        self.failUnless(abs(output["cancel"]["mean"] - 9) < 1, output)
        self.failUnless(output["cancel"]["01_0_percentile"] is None, output)
        self.failUnless(abs(output["cancel"]["10_0_percentile"] -  2) < 1, output)
        self.failUnless(abs(output["cancel"]["50_0_percentile"] - 10) < 1, output)
        self.failUnless(abs(output["cancel"]["90_0_percentile"] - 18) < 1, output)
        self.failUnless(output["cancel"]["95_0_percentile"] is None, output)
        self.failUnless(output["cancel"]["99_0_percentile"] is None, output)
        self.failUnless(output["cancel"]["99_9_percentile"] is None, output)

        self.failUnlessEqual(len(server.latencies["get"]), 1)
        self.failUnless(output["get"]["mean"] is None, output)
        self.failUnless(output["get"]["01_0_percentile"] is None, output)
        self.failUnless(output["get"]["10_0_percentile"] is None, output)
        self.failUnless(output["get"]["50_0_percentile"] is None, output)
        self.failUnless(output["get"]["90_0_percentile"] is None, output)
        self.failUnless(output["get"]["95_0_percentile"] is None, output)
        self.failUnless(output["get"]["99_0_percentile"] is None, output)
        self.failUnless(output["get"]["99_9_percentile"] is None, output)


def remove_tags(s):
    s = re.sub(r'<[^>]*>', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


class BucketCounterTest(unittest.TestCase, CrawlerTestMixin, ReallyEqualMixin):
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()

    def tearDown(self):
        return self.s.stopService()


    def test_bucket_counter(self):
        basedir = "storage/BucketCounter/bucket_counter"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20)
        bucket_counter = server.bucket_counter

        # finish as fast as possible
        bucket_counter.slow_start = 0
        bucket_counter.cpu_slice = 100.0

        d = server.bucket_counter.set_hook('after_prefix')

        server.setServiceParent(self.s)

        w = StorageStatus(server)

        # this sample is before the crawler has started doing anything
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: Yes", s)
        self.failUnlessIn("Reserved space: - 0 B (0)", s)
        self.failUnlessIn("Total sharesets: Not computed yet", s)
        self.failUnlessIn("Next crawl in", s)

        def _after_first_prefix(prefix):
            server.bucket_counter.save_state()
            state = bucket_counter.get_state()
            self.failUnlessEqual(prefix, state["last-complete-prefix"])
            self.failUnlessEqual(prefix, bucket_counter.prefixes[0])

            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn(" Current crawl ", s)
            self.failUnlessIn(" (next work in ", s)

            return bucket_counter.set_hook('after_cycle')
        d.addCallback(_after_first_prefix)

        def _after_first_cycle(cycle):
            self.failUnlessEqual(cycle, 0)
            progress = bucket_counter.get_progress()
            self.failUnlessReallyEqual(progress["cycle-in-progress"], False)
        d.addCallback(_after_first_cycle)
        d.addBoth(self._wait_for_yield, bucket_counter)

        def _after_yield(ign):
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("Total sharesets: 0 (the number of", s)
            self.failUnless("Next crawl in 59 minutes" in s or "Next crawl in 60 minutes" in s, s)
        d.addCallback(_after_yield)
        return d

    def test_bucket_counter_cleanup(self):
        basedir = "storage/BucketCounter/bucket_counter_cleanup"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20)
        bucket_counter = server.bucket_counter

        # finish as fast as possible
        bucket_counter.slow_start = 0
        bucket_counter.cpu_slice = 100.0

        d = bucket_counter.set_hook('after_prefix')

        server.setServiceParent(self.s)

        def _after_first_prefix(prefix):
            bucket_counter.save_state()
            state = bucket_counter.state
            self.failUnlessEqual(prefix, state["last-complete-prefix"])
            self.failUnlessEqual(prefix, bucket_counter.prefixes[0])

            # now sneak in and mess with its state, to make sure it cleans up
            # properly at the end of the cycle
            state["bucket-counts"][-12] = {}
            state["storage-index-samples"]["bogusprefix!"] = (-12, [])
            bucket_counter.save_state()

            return bucket_counter.set_hook('after_cycle')
        d.addCallback(_after_first_prefix)

        def _after_first_cycle(cycle):
            self.failUnlessEqual(cycle, 0)
            progress = bucket_counter.get_progress()
            self.failUnlessReallyEqual(progress["cycle-in-progress"], False)

            s = bucket_counter.get_state()
            self.failIf(-12 in s["bucket-counts"], s["bucket-counts"].keys())
            self.failIf("bogusprefix!" in s["storage-index-samples"],
                        s["storage-index-samples"].keys())
        d.addCallback(_after_first_cycle)
        d.addBoth(self._wait_for_yield, bucket_counter)
        return d

    def test_bucket_counter_eta(self):
        basedir = "storage/BucketCounter/bucket_counter_eta"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20)
        bucket_counter = server.bucket_counter

        # finish as fast as possible
        bucket_counter.slow_start = 0
        bucket_counter.cpu_slice = 100.0

        d = bucket_counter.set_hook('after_prefix')

        server.setServiceParent(self.s)

        w = StorageStatus(server)

        def _check_1(prefix1):
            # no ETA is available yet
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("complete (next work", s)

            return bucket_counter.set_hook('after_prefix')
        d.addCallback(_check_1)

        def _check_2(prefix2):
            # an ETA based upon elapsed time should be available.
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("complete (ETA ", s)
        d.addCallback(_check_2)
        d.addBoth(self._wait_for_yield, bucket_counter)
        return d


class AccountingCrawlerTest(unittest.TestCase, CrawlerTestMixin, WebRenderingMixin, ReallyEqualMixin):
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()

    def tearDown(self):
        return self.s.stopService()


    def make_shares(self, server):
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        def make(si):
            return (si, hashutil.tagged_hash("renew", si),
                    hashutil.tagged_hash("cancel", si))
        def make_mutable(si):
            return (si, hashutil.tagged_hash("renew", si),
                    hashutil.tagged_hash("cancel", si),
                    hashutil.tagged_hash("write-enabler", si))
        def make_extra_lease(si, num):
            return (hashutil.tagged_hash("renew-%d" % num, si),
                    hashutil.tagged_hash("cancel-%d" % num, si))

        immutable_si_0, rs0, cs0 = make("\x00" * 16)
        immutable_si_1, rs1, cs1 = make("\x01" * 16)
        rs1a, cs1a = make_extra_lease(immutable_si_1, 1)
        mutable_si_2, rs2, cs2, we2 = make_mutable("\x02" * 16)
        mutable_si_3, rs3, cs3, we3 = make_mutable("\x03" * 16)
        rs3a, cs3a = make_extra_lease(mutable_si_3, 1)
        sharenums = [0]
        canary = FakeCanary()
        # note: 'tahoe debug dump-share' will not handle this file, since the
        # inner contents are not a valid CHK share
        data = "\xff" * 1000

        a,w = aa.remote_allocate_buckets(immutable_si_0, rs0, cs0, sharenums,
                                         1000, canary)
        w[0].remote_write(0, data)
        w[0].remote_close()

        a,w = aa.remote_allocate_buckets(immutable_si_1, rs1, cs1, sharenums,
                                         1000, canary)
        w[0].remote_write(0, data)
        w[0].remote_close()
        sa.remote_add_lease(immutable_si_1, rs1a, cs1a)

        writev = aa.remote_slot_testv_and_readv_and_writev
        writev(mutable_si_2, (we2, rs2, cs2),
               {0: ([], [(0,data)], len(data))}, [])
        writev(mutable_si_3, (we3, rs3, cs3),
               {0: ([], [(0,data)], len(data))}, [])
        sa.remote_add_lease(mutable_si_3, rs3a, cs3a)

        self.sis = [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3]
        self.renew_secrets = [rs0, rs1, rs1a, rs2, rs3, rs3a]
        self.cancel_secrets = [cs0, cs1, cs1a, cs2, cs3, cs3a]

    def test_basic(self):
        basedir = "storage/AccountingCrawler/basic"
        fileutil.make_dirs(basedir)
        ep = ExpirationPolicy(enabled=False)
        server = StorageServer(basedir, "\x00" * 20, expiration_policy=ep)
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        # finish as fast as possible
        ac = server.get_accounting_crawler()
        ac.slow_start = 0
        ac.cpu_slice = 500

        webstatus = StorageStatus(server)

        # create a few shares, with some leases on them
        self.make_shares(server)
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

        # add a non-sharefile to exercise another code path
        fn = os.path.join(server.sharedir,
                          storage_index_to_dir(immutable_si_0),
                          "not-a-share")
        fileutil.write(fn, "I am not a share.\n")

        # this is before the crawl has started, so we're not in a cycle yet
        initial_state = ac.get_state()
        self.failIf(ac.get_progress()["cycle-in-progress"])
        self.failIfIn("cycle-to-date", initial_state)
        self.failIfIn("estimated-remaining-cycle", initial_state)
        self.failIfIn("estimated-current-cycle", initial_state)
        self.failUnlessIn("history", initial_state)
        self.failUnlessEqual(initial_state["history"], {})

        server.setServiceParent(self.s)

        DAY = 24*60*60

        # now examine the state right after the 'aa' prefix has been processed.
        d = self._after_prefix(None, 'aa', ac)
        def _after_aa_prefix(state):
            self.failUnlessIn("cycle-to-date", state)
            self.failUnlessIn("estimated-remaining-cycle", state)
            self.failUnlessIn("estimated-current-cycle", state)
            self.failUnlessIn("history", state)
            self.failUnlessEqual(state["history"], {})

            so_far = state["cycle-to-date"]
            self.failUnlessEqual(so_far["expiration-enabled"], False)
            self.failUnlessIn("configured-expiration-mode", so_far)
            self.failUnlessIn("lease-age-histogram", so_far)
            lah = so_far["lease-age-histogram"]
            self.failUnlessEqual(type(lah), list)
            self.failUnlessEqual(len(lah), 1)
            self.failUnlessEqual(lah, [ (0.0, DAY, 1) ] )
            self.failUnlessEqual(so_far["corrupt-shares"], [])
            sr1 = so_far["space-recovered"]
            self.failUnlessEqual(sr1["examined-buckets"], 1)
            self.failUnlessEqual(sr1["examined-shares"], 1)
            self.failUnlessEqual(sr1["actual-shares"], 0)
            left = state["estimated-remaining-cycle"]
            sr2 = left["space-recovered"]
            self.failUnless(sr2["examined-buckets"] > 0, sr2["examined-buckets"])
            self.failUnless(sr2["examined-shares"] > 0, sr2["examined-shares"])
            self.failIfEqual(sr2["actual-shares"], None)
        d.addCallback(_after_aa_prefix)

        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_in_cycle(html):
            s = remove_tags(html)
            self.failUnlessIn("So far, this cycle has examined "
                              "1 shares in 1 sharesets (0 mutable / 1 immutable) ", s)
            self.failUnlessIn("and has recovered: "
                              "0 shares, 0 sharesets (0 mutable / 0 immutable), "
                              "0 B (0 B / 0 B)", s)

            return ac.set_hook('after_cycle')
        d.addCallback(_check_html_in_cycle)

        def _after_first_cycle(cycle):
            # After the first cycle, nothing should have been removed.
            self.failUnlessEqual(cycle, 0)
            progress = ac.get_progress()
            self.failUnlessReallyEqual(progress["cycle-in-progress"], False)

            s = ac.get_state()
            self.failIf("cycle-to-date" in s)
            self.failIf("estimated-remaining-cycle" in s)
            self.failIf("estimated-current-cycle" in s)
            last = s["history"][0]
            self.failUnlessEqual(type(last), dict, repr(last))
            self.failUnlessIn("cycle-start-finish-times", last)
            self.failUnlessEqual(type(last["cycle-start-finish-times"]), list, repr(last))
            self.failUnlessEqual(last["expiration-enabled"], False)
            self.failUnlessIn("configured-expiration-mode", last)

            self.failUnlessIn("lease-age-histogram", last)
            lah = last["lease-age-histogram"]
            self.failUnlessEqual(type(lah), list)
            self.failUnlessEqual(len(lah), 1)
            self.failUnlessEqual(tuple(lah[0]), (0.0, DAY, 6) )

            self.failUnlessEqual(last["corrupt-shares"], [])

            rec = last["space-recovered"]
            self.failUnlessEqual(rec["examined-buckets"], 4)
            self.failUnlessEqual(rec["examined-shares"], 4)
            self.failUnlessEqual(rec["actual-buckets"], 0)
            self.failUnlessEqual(rec["actual-shares"], 0)
            self.failUnlessEqual(rec["actual-diskbytes"], 0)

            def count_leases(si):
                return (len(aa.get_leases(si)), len(sa.get_leases(si)))
            self.failUnlessEqual(count_leases(immutable_si_0), (1, 0))
            self.failUnlessEqual(count_leases(immutable_si_1), (1, 1))
            self.failUnlessEqual(count_leases(mutable_si_2), (1, 0))
            self.failUnlessEqual(count_leases(mutable_si_3), (1, 1))
        d.addCallback(_after_first_cycle)

        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_after_cycle(html):
            s = remove_tags(html)
            self.failUnlessIn("recovered: 0 shares, 0 sharesets "
                              "(0 mutable / 0 immutable), 0 B (0 B / 0 B) ", s)
            self.failUnlessIn("and saw a total of 4 shares, 4 sharesets "
                              "(2 mutable / 2 immutable),", s)
            self.failUnlessIn("but expiration was not enabled", s)
        d.addCallback(_check_html_after_cycle)

        d.addCallback(lambda ign: self.render_json(webstatus))
        def _check_json_after_cycle(json):
            data = simplejson.loads(json)
            self.failUnlessIn("lease-checker", data)
            self.failUnlessIn("lease-checker-progress", data)
        d.addCallback(_check_json_after_cycle)
        d.addBoth(self._wait_for_yield, ac)
        return d

    def test_expire_age(self):
        basedir = "storage/AccountingCrawler/expire_age"
        fileutil.make_dirs(basedir)
        # setting expiration_time to 2000 means that any lease which is more
        # than 2000s old will be expired.
        now = time.time()
        ep = ExpirationPolicy(enabled=True, mode="age", override_lease_duration=2000)
        server = StorageServer(basedir, "\x00" * 20, expiration_policy=ep)
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        # finish as fast as possible
        ac = server.get_accounting_crawler()
        ac.slow_start = 0
        ac.cpu_slice = 500

        webstatus = StorageStatus(server)

        # create a few shares, with some leases on them
        self.make_shares(server)
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

        def count_shares(si):
            return len(list(server._iter_share_files(si)))
        def _get_sharefile(si):
            return list(server._iter_share_files(si))[0]
        def count_leases(si):
            return (len(aa.get_leases(si)), len(sa.get_leases(si)))

        self.failUnlessEqual(count_shares(immutable_si_0), 1)
        self.failUnlessEqual(count_leases(immutable_si_0), (1, 0))
        self.failUnlessEqual(count_shares(immutable_si_1), 1)
        self.failUnlessEqual(count_leases(immutable_si_1), (1, 1))
        self.failUnlessEqual(count_shares(mutable_si_2), 1)
        self.failUnlessEqual(count_leases(mutable_si_2), (1, 0))
        self.failUnlessEqual(count_shares(mutable_si_3), 1)
        self.failUnlessEqual(count_leases(mutable_si_3), (1, 1))

        # artificially crank back the renewal time on the first lease of each
        # share to 3000s ago, and set the expiration time to 31 days later.
        new_renewal_time = now - 3000
        new_expiration_time = new_renewal_time + 31*24*60*60

        # Some shares have an extra lease which is set to expire at the
        # default time in 31 days from now (age=31days). We then run the
        # crawler, which will expire the first lease, making some shares get
        # deleted and others stay alive (with one remaining lease)

        aa.add_or_renew_lease(immutable_si_0, 0, new_renewal_time, new_expiration_time)

        # immutable_si_1 gets an extra lease
        sa.add_or_renew_lease(immutable_si_1, 0, new_renewal_time, new_expiration_time)

        aa.add_or_renew_lease(mutable_si_2,   0, new_renewal_time, new_expiration_time)

        # mutable_si_3 gets an extra lease
        sa.add_or_renew_lease(mutable_si_3,   0, new_renewal_time, new_expiration_time)

        server.setServiceParent(self.s)

        # now examine the web status right after the 'aa' prefix has been processed.
        d = self._after_prefix(None, 'aa', ac)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_in_cycle(html):
            s = remove_tags(html)
            # the first shareset encountered gets deleted, and its prefix
            # happens to be about 1/5th of the way through the ring, so the
            # predictor thinks we'll have 5 shares and that we'll delete them
            # all. This part of the test depends upon the SIs landing right
            # where they do now.
            self.failUnlessIn("The remainder of this cycle is expected to "
                              "recover: 4 shares, 4 sharesets", s)
            self.failUnlessIn("The whole cycle is expected to examine "
                              "5 shares in 5 sharesets and to recover: "
                              "5 shares, 5 sharesets", s)

            return ac.set_hook('after_cycle')
        d.addCallback(_check_html_in_cycle)

        def _after_first_cycle(ignored):
            self.failUnlessEqual(count_shares(immutable_si_0), 0)
            self.failUnlessEqual(count_shares(immutable_si_1), 1)
            self.failUnlessEqual(count_leases(immutable_si_1), (1, 0))
            self.failUnlessEqual(count_shares(mutable_si_2), 0)
            self.failUnlessEqual(count_shares(mutable_si_3), 1)
            self.failUnlessEqual(count_leases(mutable_si_3), (1, 0))

            s = ac.get_state()
            last = s["history"][0]

            self.failUnlessEqual(last["expiration-enabled"], True)
            cem = last["configured-expiration-mode"]
            self.failUnlessEqual(cem[0], "age")
            self.failUnlessEqual(cem[1], 2000)
            self.failUnlessEqual(cem[2], None)
            self.failUnlessEqual(cem[3][0], "mutable")
            self.failUnlessEqual(cem[3][1], "immutable")

            rec = last["space-recovered"]
            self.failUnlessEqual(rec["examined-buckets"], 4)
            self.failUnlessEqual(rec["examined-shares"], 4)
            self.failUnlessEqual(rec["actual-buckets"], 2)
            self.failUnlessEqual(rec["actual-shares"], 2)
            # different platforms have different notions of "blocks used by
            # this file", so merely assert that it's a number
            self.failUnless(rec["actual-diskbytes"] >= 0,
                            rec["actual-diskbytes"])
        d.addCallback(_after_first_cycle)

        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_after_cycle(html):
            s = remove_tags(html)
            self.failUnlessIn("Expiration Enabled: expired leases will be removed", s)
            self.failUnlessIn("Leases created or last renewed more than 33 minutes ago will be considered expired.", s)
            self.failUnlessIn(" recovered: 2 shares, 2 sharesets (1 mutable / 1 immutable), ", s)
        d.addCallback(_check_html_after_cycle)
        d.addBoth(self._wait_for_yield, ac)
        return d

    def test_expire_cutoff_date(self):
        basedir = "storage/AccountingCrawler/expire_cutoff_date"
        fileutil.make_dirs(basedir)
        # setting cutoff-date to 2000 seconds ago means that any lease which
        # is more than 2000s old will be expired.
        now = time.time()
        then = int(now - 2000)
        ep = ExpirationPolicy(enabled=True, mode="cutoff-date", cutoff_date=then)
        server = StorageServer(basedir, "\x00" * 20, expiration_policy=ep)
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        # finish as fast as possible
        ac = server.get_accounting_crawler()
        ac.slow_start = 0
        ac.cpu_slice = 500

        webstatus = StorageStatus(server)

        # create a few shares, with some leases on them
        self.make_shares(server)
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

        def count_shares(si):
            return len(list(server._iter_share_files(si)))
        def _get_sharefile(si):
            return list(server._iter_share_files(si))[0]
        def count_leases(si):
            return (len(aa.get_leases(si)), len(sa.get_leases(si)))

        self.failUnlessEqual(count_shares(immutable_si_0), 1)
        self.failUnlessEqual(count_leases(immutable_si_0), (1, 0))
        self.failUnlessEqual(count_shares(immutable_si_1), 1)
        self.failUnlessEqual(count_leases(immutable_si_1), (1, 1))
        self.failUnlessEqual(count_shares(mutable_si_2), 1)
        self.failUnlessEqual(count_leases(mutable_si_2), (1, 0))
        self.failUnlessEqual(count_shares(mutable_si_3), 1)
        self.failUnlessEqual(count_leases(mutable_si_3), (1, 1))

        # artificially crank back the renewal time on the first lease of each
        # share to 3000s ago, and set the expiration time to 31 days later.
        new_renewal_time = now - 3000
        new_expiration_time = new_renewal_time + 31*24*60*60

        # Some shares have an extra lease which is set to expire at the
        # default time in 31 days from now (age=31days). We then run the
        # crawler, which will expire the first lease, making some shares get
        # deleted and others stay alive (with one remaining lease)

        aa.add_or_renew_lease(immutable_si_0, 0, new_renewal_time, new_expiration_time)

        # immutable_si_1 gets an extra lease
        sa.add_or_renew_lease(immutable_si_1, 0, new_renewal_time, new_expiration_time)

        aa.add_or_renew_lease(mutable_si_2,   0, new_renewal_time, new_expiration_time)

        # mutable_si_3 gets an extra lease
        sa.add_or_renew_lease(mutable_si_3,   0, new_renewal_time, new_expiration_time)

        server.setServiceParent(self.s)

        # now examine the web status right after the 'aa' prefix has been processed.
        d = self._after_prefix(None, 'aa', ac)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_in_cycle(html):
            s = remove_tags(html)
            # the first bucket encountered gets deleted, and its prefix
            # happens to be about 1/5th of the way through the ring, so the
            # predictor thinks we'll have 5 shares and that we'll delete them
            # all. This part of the test depends upon the SIs landing right
            # where they do now.
            self.failUnlessIn("The remainder of this cycle is expected to "
                              "recover: 4 shares, 4 sharesets", s)
            self.failUnlessIn("The whole cycle is expected to examine "
                              "5 shares in 5 sharesets and to recover: "
                              "5 shares, 5 sharesets", s)

            return ac.set_hook('after_cycle')
        d.addCallback(_check_html_in_cycle)

        def _after_first_cycle(ignored):
            self.failUnlessEqual(count_shares(immutable_si_0), 0)
            self.failUnlessEqual(count_shares(immutable_si_1), 1)
            self.failUnlessEqual(count_leases(immutable_si_1), (1, 0))
            self.failUnlessEqual(count_shares(mutable_si_2), 0)
            self.failUnlessEqual(count_shares(mutable_si_3), 1)
            self.failUnlessEqual(count_leases(mutable_si_3), (1, 0))

            s = ac.get_state()
            last = s["history"][0]

            self.failUnlessEqual(last["expiration-enabled"], True)
            cem = last["configured-expiration-mode"]
            self.failUnlessEqual(cem[0], "cutoff-date")
            self.failUnlessEqual(cem[1], None)
            self.failUnlessEqual(cem[2], then)
            self.failUnlessEqual(cem[3][0], "mutable")
            self.failUnlessEqual(cem[3][1], "immutable")

            rec = last["space-recovered"]
            self.failUnlessEqual(rec["examined-buckets"], 4)
            self.failUnlessEqual(rec["examined-shares"], 4)
            self.failUnlessEqual(rec["actual-buckets"], 2)
            self.failUnlessEqual(rec["actual-shares"], 2)
            # different platforms have different notions of "blocks used by
            # this file", so merely assert that it's a number
            self.failUnless(rec["actual-diskbytes"] >= 0,
                            rec["actual-diskbytes"])
        d.addCallback(_after_first_cycle)

        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_after_cycle(html):
            s = remove_tags(html)
            self.failUnlessIn("Expiration Enabled:"
                              " expired leases will be removed", s)
            date = time.strftime("%Y-%m-%d (%d-%b-%Y) UTC", time.gmtime(then))
            substr = "Leases created or last renewed before %s will be considered expired." % date
            self.failUnlessIn(substr, s)
            self.failUnlessIn(" recovered: 2 shares, 2 sharesets (1 mutable / 1 immutable), ", s)
        d.addCallback(_check_html_after_cycle)
        d.addBoth(self._wait_for_yield, ac)
        return d

    def test_bad_mode(self):
        e = self.failUnlessRaises(AssertionError,
                                  ExpirationPolicy, enabled=True, mode="bogus")
        self.failUnlessIn("GC mode 'bogus' must be 'age' or 'cutoff-date'", str(e))

    def test_parse_duration(self):
        DAY = 24*60*60
        MONTH = 31*DAY
        YEAR = 365*DAY
        p = time_format.parse_duration
        self.failUnlessEqual(p("7days"), 7*DAY)
        self.failUnlessEqual(p("31day"), 31*DAY)
        self.failUnlessEqual(p("60 days"), 60*DAY)
        self.failUnlessEqual(p("2mo"), 2*MONTH)
        self.failUnlessEqual(p("3 month"), 3*MONTH)
        self.failUnlessEqual(p("2years"), 2*YEAR)
        e = self.failUnlessRaises(ValueError, p, "2kumquats")
        self.failUnlessIn("no unit (like day, month, or year) in '2kumquats'", str(e))

    def test_parse_date(self):
        p = time_format.parse_date
        self.failUnless(isinstance(p("2009-03-18"), int), p("2009-03-18"))
        self.failUnlessEqual(p("2009-03-18"), 1237334400)

    def test_limited_history(self):
        basedir = "storage/AccountingCrawler/limited_history"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20)

        # finish as fast as possible
        RETAINED = 2
        CYCLES = 4
        ac = server.get_accounting_crawler()
        ac._leasedb.retained_history_entries = RETAINED
        ac.slow_start = 0
        ac.cpu_slice = 500
        ac.allowed_cpu_proportion = 1.0
        ac.minimum_cycle_time = 0

        # create a few shares, with some leases on them
        self.make_shares(server)

        server.setServiceParent(self.s)

        d = ac.set_hook('after_cycle')
        def _after_cycle(cycle):
            if cycle < CYCLES:
                return ac.set_hook('after_cycle').addCallback(_after_cycle)

            state = ac.get_state()
            self.failUnlessIn("history", state)
            h = state["history"]
            self.failUnlessEqual(len(h), RETAINED)
            self.failUnlessEqual(max(h.keys()), CYCLES)
            self.failUnlessEqual(min(h.keys()), CYCLES-RETAINED+1)
        d.addCallback(_after_cycle)
        d.addBoth(self._wait_for_yield, ac)
        return d

    def OFF_test_unpredictable_future(self):
        basedir = "storage/AccountingCrawler/unpredictable_future"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20)

        # make it start sooner than usual.
        ac = server.get_accounting_crawler()
        ac.slow_start = 0
        ac.cpu_slice = -1.0 # stop quickly

        self.make_shares(server)

        server.setServiceParent(self.s)

        d = fireEventually()
        def _check(ignored):
            # this should fire after the first bucket is complete, but before
            # the first prefix is complete, so the progress-measurer won't
            # think we've gotten far enough to raise our percent-complete
            # above 0%, triggering the cannot-predict-the-future code in
            # expirer.py . This will have to change if/when the
            # progress-measurer gets smart enough to count buckets (we'll
            # have to interrupt it even earlier, before it's finished the
            # first bucket).
            s = ac.get_state()
            if "cycle-to-date" not in s:
                return reactor.callLater(0.2, _check)
            self.failUnlessIn("cycle-to-date", s)
            self.failUnlessIn("estimated-remaining-cycle", s)
            self.failUnlessIn("estimated-current-cycle", s)

            left = s["estimated-remaining-cycle"]["space-recovered"]
            self.failUnlessEqual(left["actual-buckets"], None)
            self.failUnlessEqual(left["actual-shares"], None)
            self.failUnlessEqual(left["actual-diskbytes"], None)

            full = s["estimated-remaining-cycle"]["space-recovered"]
            self.failUnlessEqual(full["actual-buckets"], None)
            self.failUnlessEqual(full["actual-shares"], None)
            self.failUnlessEqual(full["actual-diskbytes"], None)

        d.addCallback(_check)
        return d

    def render_json(self, page):
        d = self.render1(page, args={"t": ["json"]})
        return d


class WebStatus(unittest.TestCase, WebRenderingMixin):
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()

    def tearDown(self):
        return self.s.stopService()


    def test_no_server(self):
        w = StorageStatus(None)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>No Storage Server Running</h1>", html)

    def test_status(self):
        basedir = "storage/WebStatus/status"
        fileutil.make_dirs(basedir)
        nodeid = "\x00" * 20
        server = StorageServer(basedir, nodeid)
        server.setServiceParent(self.s)
        w = StorageStatus(server, "nickname")
        d = self.render1(w)
        def _check_html(html):
            self.failUnlessIn("<h1>Storage Server Status</h1>", html)
            s = remove_tags(html)
            self.failUnlessIn("Server Nickname: nickname", s)
            self.failUnlessIn("Server Nodeid: %s"  % base32.b2a(nodeid), s)
            self.failUnlessIn("Accepting new shares: Yes", s)
            self.failUnlessIn("Reserved space: - 0 B (0)", s)
        d.addCallback(_check_html)
        d.addCallback(lambda ign: self.render_json(w))
        def _check_json(json):
            data = simplejson.loads(json)
            s = data["stats"]
            self.failUnlessEqual(s["storage_server.accepting_immutable_shares"], 1)
            self.failUnlessEqual(s["storage_server.reserved_space"], 0)
            self.failUnlessIn("bucket-counter", data)
            self.failUnlessIn("lease-checker", data)
        d.addCallback(_check_json)
        return d


    def render_json(self, page):
        d = self.render1(page, args={"t": ["json"]})
        return d

    @mock.patch('allmydata.util.fileutil.get_disk_stats')
    def test_status_no_disk_stats(self, mock_get_disk_stats):
        mock_get_disk_stats.side_effect = AttributeError()

        # Some platforms may have no disk stats API. Make sure the code can handle that
        # (test runs on all platforms).
        basedir = "storage/WebStatus/status_no_disk_stats"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20)
        server.setServiceParent(self.s)
        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: Yes", s)
        self.failUnlessIn("Total disk space: ?", s)
        self.failUnlessIn("Space Available to Tahoe: ?", s)
        self.failUnless(server.get_available_space() is None)

    @mock.patch('allmydata.util.fileutil.get_disk_stats')
    def test_status_bad_disk_stats(self, mock_get_disk_stats):
        mock_get_disk_stats.side_effect = OSError()

        # If the API to get disk stats exists but a call to it fails, then the status should
        # show that no shares will be accepted, and get_available_space() should be 0.
        basedir = "storage/WebStatus/status_bad_disk_stats"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20)
        server.setServiceParent(self.s)
        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: No", s)
        self.failUnlessIn("Total disk space: ?", s)
        self.failUnlessIn("Space Available to Tahoe: ?", s)
        self.failUnlessEqual(server.get_available_space(), 0)

    @mock.patch('allmydata.util.fileutil.get_disk_stats')
    def test_status_right_disk_stats(self, mock_get_disk_stats):
        GB = 1000000000
        total            = 5*GB
        free_for_root    = 4*GB
        free_for_nonroot = 3*GB
        reserved_space   = 1*GB
        used = total - free_for_root
        avail = max(free_for_nonroot - reserved_space, 0)
        mock_get_disk_stats.return_value = {
            'total': total,
            'free_for_root': free_for_root,
            'free_for_nonroot': free_for_nonroot,
            'used': used,
            'avail': avail,
        }

        basedir = "storage/WebStatus/status_right_disk_stats"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20, reserved_space=reserved_space)
        expecteddir = server.sharedir
        server.setServiceParent(self.s)
        w = StorageStatus(server)
        html = w.renderSynchronously()

        self.failIf([True for args in mock_get_disk_stats.call_args_list if args != ((expecteddir, reserved_space), {})],
                    mock_get_disk_stats.call_args_list)

        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Total disk space: 5.00 GB", s)
        self.failUnlessIn("Disk space used: - 1.00 GB", s)
        self.failUnlessIn("Disk space free (root): 4.00 GB", s)
        self.failUnlessIn("Disk space free (non-root): 3.00 GB", s)
        self.failUnlessIn("Reserved space: - 1.00 GB", s)
        self.failUnlessIn("Space Available to Tahoe: 2.00 GB", s)
        self.failUnlessEqual(server.get_available_space(), 2*GB)

    def test_readonly(self):
        basedir = "storage/WebStatus/readonly"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20, readonly_storage=True)
        server.setServiceParent(self.s)
        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: No", s)

    def test_reserved(self):
        basedir = "storage/WebStatus/reserved"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20, reserved_space=10e6)
        server.setServiceParent(self.s)
        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Reserved space: - 10.00 MB (10000000)", s)

    def test_huge_reserved(self):
        basedir = "storage/WebStatus/reserved"
        fileutil.make_dirs(basedir)
        server = StorageServer(basedir, "\x00" * 20, reserved_space=10e6)
        server.setServiceParent(self.s)
        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Reserved space: - 10.00 MB (10000000)", s)

    def test_util(self):
        w = StorageStatus(None)
        self.failUnlessEqual(w.render_space(None, None), "?")
        self.failUnlessEqual(w.render_space(None, 10e6), "10000000")
        self.failUnlessEqual(w.render_abbrev_space(None, None), "?")
        self.failUnlessEqual(w.render_abbrev_space(None, 10e6), "10.00 MB")
        self.failUnlessEqual(remove_prefix("foo.bar", "foo."), "bar")
        self.failUnlessEqual(remove_prefix("foo.bar", "baz."), None)
