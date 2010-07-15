
import time, os.path, stat, re, simplejson, struct

from twisted.trial import unittest

from twisted.internet import defer
from twisted.application import service
from foolscap.api import fireEventually
import itertools
from allmydata import interfaces
from allmydata.util import fileutil, hashutil, base32, pollmixin, time_format
from allmydata.storage.server import StorageServer
from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.immutable import BucketWriter, BucketReader
from allmydata.storage.common import DataTooLargeError, storage_index_to_dir, \
     UnknownMutableContainerVersionError, UnknownImmutableContainerVersionError
from allmydata.storage.lease import LeaseInfo
from allmydata.storage.crawler import BucketCountingCrawler
from allmydata.storage.expirer import LeaseCheckingCrawler
from allmydata.immutable.layout import WriteBucketProxy, WriteBucketProxy_v2, \
     ReadBucketProxy
from allmydata.interfaces import BadWriteEnablerError
from allmydata.test.common import LoggingServiceParent
from allmydata.test.common_web import WebRenderingMixin
from allmydata.web.storage import StorageStatus, remove_prefix

class Marker:
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
                         expiration_time, "\x00" * 20)

    def test_create(self):
        incoming, final = self.make_workdir("test_create")
        bw = BucketWriter(self, incoming, final, 200, self.make_lease(),
                          FakeCanary())
        bw.remote_write(0, "a"*25)
        bw.remote_write(25, "b"*25)
        bw.remote_write(50, "c"*25)
        bw.remote_write(75, "d"*7)
        bw.remote_close()

    def test_readwrite(self):
        incoming, final = self.make_workdir("test_readwrite")
        bw = BucketWriter(self, incoming, final, 200, self.make_lease(),
                          FakeCanary())
        bw.remote_write(0, "a"*25)
        bw.remote_write(25, "b"*25)
        bw.remote_write(50, "c"*7) # last block may be short
        bw.remote_close()

        # now read from it
        br = BucketReader(self, bw.finalhome)
        self.failUnlessEqual(br.remote_read(0, 25), "a"*25)
        self.failUnlessEqual(br.remote_read(25, 25), "b"*25)
        self.failUnlessEqual(br.remote_read(50, 7), "c"*7)

class RemoteBucket:

    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self.target, "remote_" + methname)
            return meth(*args, **kwargs)
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
        rb = RemoteBucket()
        rb.target = bw
        return bw, rb, final

    def make_lease(self):
        owner_num = 0
        renew_secret = os.urandom(32)
        cancel_secret = os.urandom(32)
        expiration_time = time.time() + 5000
        return LeaseInfo(owner_num, renew_secret, cancel_secret,
                         expiration_time, "\x00" * 20)

    def bucket_writer_closed(self, bw, consumed):
        pass
    def add_latency(self, category, latency):
        pass
    def count(self, name, delta=1):
        pass

    def test_create(self):
        bw, rb, sharefname = self.make_bucket("test_create", 500)
        bp = WriteBucketProxy(rb,
                              data_size=300,
                              block_size=10,
                              num_segments=5,
                              num_share_hashes=3,
                              uri_extension_size_max=500, nodeid=None)
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
        bp = wbp_class(rb,
                       data_size=95,
                       block_size=25,
                       num_segments=4,
                       num_share_hashes=3,
                       uri_extension_size_max=len(uri_extension),
                       nodeid=None)

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
            rbp = rbp_class(rb, peerid="abc", storage_index="")
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

class FakeDiskStorageServer(StorageServer):
    DISKAVAIL = 0
    def get_disk_stats(self):
        return { 'free_for_nonroot': self.DISKAVAIL, 'avail': max(self.DISKAVAIL - self.reserved_space, 0), }

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
        ss = klass(workdir, "\x00" * 20, reserved_space=reserved_space,
                   stats_provider=FakeStatsProvider())
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        self.create("test_create")

    def allocate(self, ss, storage_index, sharenums, size, canary=None):
        renew_secret = hashutil.tagged_hash("blah", "%d" % self._lease_secret.next())
        cancel_secret = hashutil.tagged_hash("blah", "%d" % self._lease_secret.next())
        if not canary:
            canary = FakeCanary()
        return ss.remote_allocate_buckets(storage_index,
                                          renew_secret, cancel_secret,
                                          sharenums, size, canary)

    def test_large_share(self):
        ss = self.create("test_large_share")

        already,writers = self.allocate(ss, "allocate", [0], 2**32+2)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0]))

        shnum, bucket = writers.items()[0]
        # This test is going to hammer your filesystem if it doesn't make a sparse file for this.  :-(
        bucket.remote_write(2**32, "ab")
        bucket.remote_close()

        readers = ss.remote_get_buckets("allocate")
        reader = readers[shnum]
        self.failUnlessEqual(reader.remote_read(2**32, 2), "ab")
    test_large_share.skip = "This test can spuriously fail if you have less than 4 GiB free on your filesystem, and if your filesystem doesn't support efficient sparse files then it is very expensive (Mac OS X is the only system I know of in the desktop/server area that doesn't support efficient sparse files)."

    def test_dont_overfill_dirs(self):
        """
        This test asserts that if you add a second share whose storage index
        share lots of leading bits with an extant share (but isn't the exact
        same storage index), this won't add an entry to the share directory.
        """
        ss = self.create("test_dont_overfill_dirs")
        already, writers = self.allocate(ss, "storageindex", [0], 10)
        for i, wb in writers.items():
            wb.remote_write(0, "%10d" % i)
            wb.remote_close()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")
        children_of_storedir = set(os.listdir(storedir))

        # Now store another one under another storageindex that has leading
        # chars the same as the first storageindex.
        already, writers = self.allocate(ss, "storageindey", [0], 10)
        for i, wb in writers.items():
            wb.remote_write(0, "%10d" % i)
            wb.remote_close()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")
        new_children_of_storedir = set(os.listdir(storedir))
        self.failUnlessEqual(children_of_storedir, new_children_of_storedir)

    def test_remove_incoming(self):
        ss = self.create("test_remove_incoming")
        already, writers = self.allocate(ss, "vid", range(3), 10)
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
        ss = self.create("test_abort")
        already, writers = self.allocate(ss, "allocate", [0, 1, 2], 150)
        self.failIfEqual(ss.allocated_size(), 0)

        # Now abort the writers.
        for writer in writers.itervalues():
            writer.remote_abort()
        self.failUnlessEqual(ss.allocated_size(), 0)


    def test_allocate(self):
        ss = self.create("test_allocate")

        self.failUnlessEqual(ss.remote_get_buckets("allocate"), {})

        already,writers = self.allocate(ss, "allocate", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

        # while the buckets are open, they should not count as readable
        self.failUnlessEqual(ss.remote_get_buckets("allocate"), {})

        # close the buckets
        for i,wb in writers.items():
            wb.remote_write(0, "%25d" % i)
            wb.remote_close()
            # aborting a bucket that was already closed is a no-op
            wb.remote_abort()

        # now they should be readable
        b = ss.remote_get_buckets("allocate")
        self.failUnlessEqual(set(b.keys()), set([0,1,2]))
        self.failUnlessEqual(b[0].remote_read(0, 25), "%25d" % 0)
        b_str = str(b[0])
        self.failUnlessIn("BucketReader", b_str)
        self.failUnlessIn("mfwgy33dmf2g 0", b_str)

        # now if we ask about writing again, the server should offer those
        # three buckets as already present. It should offer them even if we
        # don't ask about those specific ones.
        already,writers = self.allocate(ss, "allocate", [2,3,4], 75)
        self.failUnlessEqual(already, set([0,1,2]))
        self.failUnlessEqual(set(writers.keys()), set([3,4]))

        # while those two buckets are open for writing, the server should
        # refuse to offer them to uploaders

        already2,writers2 = self.allocate(ss, "allocate", [2,3,4,5], 75)
        self.failUnlessEqual(already2, set([0,1,2]))
        self.failUnlessEqual(set(writers2.keys()), set([5]))

        # aborting the writes should remove the tempfiles
        for i,wb in writers2.items():
            wb.remote_abort()
        already2,writers2 = self.allocate(ss, "allocate", [2,3,4,5], 75)
        self.failUnlessEqual(already2, set([0,1,2]))
        self.failUnlessEqual(set(writers2.keys()), set([5]))

        for i,wb in writers2.items():
            wb.remote_abort()
        for i,wb in writers.items():
            wb.remote_abort()

    def test_bad_container_version(self):
        ss = self.create("test_bad_container_version")
        a,w = self.allocate(ss, "si1", [0], 10)
        w[0].remote_write(0, "\xff"*10)
        w[0].remote_close()

        fn = os.path.join(ss.sharedir, storage_index_to_dir("si1"), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write(struct.pack(">L", 0)) # this is invalid: minimum used is v1
        f.close()

        ss.remote_get_buckets("allocate")

        e = self.failUnlessRaises(UnknownImmutableContainerVersionError,
                                  ss.remote_get_buckets, "si1")
        self.failUnlessIn(" had version 0 but we wanted 1", str(e))

    def test_disconnect(self):
        # simulate a disconnection
        ss = self.create("test_disconnect")
        canary = FakeCanary()
        already,writers = self.allocate(ss, "disconnect", [0,1,2], 75, canary)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))
        for (f,args,kwargs) in canary.disconnectors.values():
            f(*args, **kwargs)
        del already
        del writers

        # that ought to delete the incoming shares
        already,writers = self.allocate(ss, "disconnect", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

    def test_reserved_space(self):
        ss = self.create("test_reserved_space", reserved_space=10000,
                         klass=FakeDiskStorageServer)
        # the FakeDiskStorageServer doesn't do real calls to get_disk_stats
        ss.DISKAVAIL = 15000
        # 15k available, 10k reserved, leaves 5k for shares

        # a newly created and filled share incurs this much overhead, beyond
        # the size we request.
        OVERHEAD = 3*4
        LEASE_SIZE = 4+32+32+4
        canary = FakeCanary(True)
        already,writers = self.allocate(ss, "vid1", [0,1,2], 1000, canary)
        self.failUnlessEqual(len(writers), 3)
        # now the StorageServer should have 3000 bytes provisionally
        # allocated, allowing only 2000 more to be claimed
        self.failUnlessEqual(len(ss._active_writers), 3)

        # allocating 1001-byte shares only leaves room for one
        already2,writers2 = self.allocate(ss, "vid2", [0,1,2], 1001, canary)
        self.failUnlessEqual(len(writers2), 1)
        self.failUnlessEqual(len(ss._active_writers), 4)

        # we abandon the first set, so their provisional allocation should be
        # returned
        del already
        del writers
        self.failUnlessEqual(len(ss._active_writers), 1)
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
        self.failUnlessEqual(len(ss._active_writers), 0)

        allocated = 1001 + OVERHEAD + LEASE_SIZE

        # we have to manually increase DISKAVAIL, since we're not doing real
        # disk measurements
        ss.DISKAVAIL -= allocated

        # now there should be ALLOCATED=1001+12+72=1085 bytes allocated, and
        # 5000-1085=3915 free, therefore we can fit 39 100byte shares
        already3,writers3 = self.allocate(ss,"vid3", range(100), 100, canary)
        self.failUnlessEqual(len(writers3), 39)
        self.failUnlessEqual(len(ss._active_writers), 39)

        del already3
        del writers3
        self.failUnlessEqual(len(ss._active_writers), 0)
        ss.disownServiceParent()
        del ss

    def test_disk_stats(self):
        # This will spuriously fail if there is zero disk space left (but so will other tests).
        ss = self.create("test_disk_stats", reserved_space=0)

        disk = ss.get_disk_stats()
        self.failUnless(disk['total'] > 0, disk['total'])
        self.failUnless(disk['used'] > 0, disk['used'])
        self.failUnless(disk['free_for_root'] > 0, disk['free_for_root'])
        self.failUnless(disk['free_for_nonroot'] > 0, disk['free_for_nonroot'])
        self.failUnless(disk['avail'] > 0, disk['avail'])

    def test_disk_stats_avail_nonnegative(self):
        ss = self.create("test_disk_stats_avail_nonnegative", reserved_space=2**64)

        disk = ss.get_disk_stats()
        self.failUnlessEqual(disk['avail'], 0)

    def test_seek(self):
        basedir = self.workdir("test_seek_behavior")
        fileutil.make_dirs(basedir)
        filename = os.path.join(basedir, "testfile")
        f = open(filename, "wb")
        f.write("start")
        f.close()
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


    def test_leases(self):
        ss = self.create("test_leases")
        canary = FakeCanary()
        sharenums = range(5)
        size = 100

        rs0,cs0 = (hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()))
        already,writers = ss.remote_allocate_buckets("si0", rs0, cs0,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 0)
        self.failUnlessEqual(len(writers), 5)
        for wb in writers.values():
            wb.remote_close()

        leases = list(ss.get_leases("si0"))
        self.failUnlessEqual(len(leases), 1)
        self.failUnlessEqual(set([l.renew_secret for l in leases]), set([rs0]))

        rs1,cs1 = (hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()))
        already,writers = ss.remote_allocate_buckets("si1", rs1, cs1,
                                                     sharenums, size, canary)
        for wb in writers.values():
            wb.remote_close()

        # take out a second lease on si1
        rs2,cs2 = (hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()))
        already,writers = ss.remote_allocate_buckets("si1", rs2, cs2,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 5)
        self.failUnlessEqual(len(writers), 0)

        leases = list(ss.get_leases("si1"))
        self.failUnlessEqual(len(leases), 2)
        self.failUnlessEqual(set([l.renew_secret for l in leases]), set([rs1, rs2]))

        # and a third lease, using add-lease
        rs2a,cs2a = (hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()),
                     hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()))
        ss.remote_add_lease("si1", rs2a, cs2a)
        leases = list(ss.get_leases("si1"))
        self.failUnlessEqual(len(leases), 3)
        self.failUnlessEqual(set([l.renew_secret for l in leases]), set([rs1, rs2, rs2a]))

        # add-lease on a missing storage index is silently ignored
        self.failUnlessEqual(ss.remote_add_lease("si18", "", ""), None)

        # check that si0 is readable
        readers = ss.remote_get_buckets("si0")
        self.failUnlessEqual(len(readers), 5)

        # renew the first lease. Only the proper renew_secret should work
        ss.remote_renew_lease("si0", rs0)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si0", cs0)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si0", rs1)

        # check that si0 is still readable
        readers = ss.remote_get_buckets("si0")
        self.failUnlessEqual(len(readers), 5)

        # now cancel it
        self.failUnlessRaises(IndexError, ss.remote_cancel_lease, "si0", rs0)
        self.failUnlessRaises(IndexError, ss.remote_cancel_lease, "si0", cs1)
        ss.remote_cancel_lease("si0", cs0)

        # si0 should now be gone
        readers = ss.remote_get_buckets("si0")
        self.failUnlessEqual(len(readers), 0)
        # and the renew should no longer work
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si0", rs0)


        # cancel the first lease on si1, leaving the second and third in place
        ss.remote_cancel_lease("si1", cs1)
        readers = ss.remote_get_buckets("si1")
        self.failUnlessEqual(len(readers), 5)
        # the corresponding renew should no longer work
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si1", rs1)

        leases = list(ss.get_leases("si1"))
        self.failUnlessEqual(len(leases), 2)
        self.failUnlessEqual(set([l.renew_secret for l in leases]), set([rs2, rs2a]))

        ss.remote_renew_lease("si1", rs2)
        # cancelling the second and third should make it go away
        ss.remote_cancel_lease("si1", cs2)
        ss.remote_cancel_lease("si1", cs2a)
        readers = ss.remote_get_buckets("si1")
        self.failUnlessEqual(len(readers), 0)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si1", rs1)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si1", rs2)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si1", rs2a)

        leases = list(ss.get_leases("si1"))
        self.failUnlessEqual(len(leases), 0)


        # test overlapping uploads
        rs3,cs3 = (hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()))
        rs4,cs4 = (hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()))
        already,writers = ss.remote_allocate_buckets("si3", rs3, cs3,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 0)
        self.failUnlessEqual(len(writers), 5)
        already2,writers2 = ss.remote_allocate_buckets("si3", rs4, cs4,
                                                       sharenums, size, canary)
        self.failUnlessEqual(len(already2), 0)
        self.failUnlessEqual(len(writers2), 0)
        for wb in writers.values():
            wb.remote_close()

        leases = list(ss.get_leases("si3"))
        self.failUnlessEqual(len(leases), 1)

        already3,writers3 = ss.remote_allocate_buckets("si3", rs4, cs4,
                                                       sharenums, size, canary)
        self.failUnlessEqual(len(already3), 5)
        self.failUnlessEqual(len(writers3), 0)

        leases = list(ss.get_leases("si3"))
        self.failUnlessEqual(len(leases), 2)

    def test_readonly(self):
        workdir = self.workdir("test_readonly")
        ss = StorageServer(workdir, "\x00" * 20, readonly_storage=True)
        ss.setServiceParent(self.sparent)

        already,writers = self.allocate(ss, "vid", [0,1,2], 75)
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
        ss = StorageServer(workdir, "\x00" * 20, discard_storage=True)
        ss.setServiceParent(self.sparent)

        already,writers = self.allocate(ss, "vid", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))
        for i,wb in writers.items():
            wb.remote_write(0, "%25d" % i)
            wb.remote_close()
        # since we discard the data, the shares should be present but sparse.
        # Since we write with some seeks, the data we read back will be all
        # zeros.
        b = ss.remote_get_buckets("vid")
        self.failUnlessEqual(set(b.keys()), set([0,1,2]))
        self.failUnlessEqual(b[0].remote_read(0, 25), "\x00" * 25)

    def test_advise_corruption(self):
        workdir = self.workdir("test_advise_corruption")
        ss = StorageServer(workdir, "\x00" * 20, discard_storage=True)
        ss.setServiceParent(self.sparent)

        si0_s = base32.b2a("si0")
        ss.remote_advise_corrupt_share("immutable", "si0", 0,
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
        already,writers = self.allocate(ss, "si1", [1], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([1]))
        writers[1].remote_write(0, "data")
        writers[1].remote_close()

        b = ss.remote_get_buckets("si1")
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
        ss = StorageServer(workdir, "\x00" * 20)
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        self.create("test_create")

    def write_enabler(self, we_tag):
        return hashutil.tagged_hash("we_blah", we_tag)

    def renew_secret(self, tag):
        return hashutil.tagged_hash("renew_blah", str(tag))

    def cancel_secret(self, tag):
        return hashutil.tagged_hash("cancel_blah", str(tag))

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
        self.allocate(ss, "si1", "we1", self._lease_secret.next(), set([0]), 10)
        fn = os.path.join(ss.sharedir, storage_index_to_dir("si1"), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write("BAD MAGIC")
        f.close()
        read = ss.remote_slot_readv
        e = self.failUnlessRaises(UnknownMutableContainerVersionError,
                                  read, "si1", [0], [(0,10)])
        self.failUnlessIn(" had magic ", str(e))
        self.failUnlessIn(" but we wanted ", str(e))

    def test_container_size(self):
        ss = self.create("test_container_size")
        self.allocate(ss, "si1", "we1", self._lease_secret.next(),
                      set([0,1,2]), 100)
        read = ss.remote_slot_readv
        rstaraw = ss.remote_slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        answer = rstaraw("si1", secrets,
                         {0: ([], [(0,data)], len(data)+12)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        # trying to make the container too large will raise an exception
        TOOBIG = MutableShareFile.MAX_SIZE + 10
        self.failUnlessRaises(DataTooLargeError,
                              rstaraw, "si1", secrets,
                              {0: ([], [(0,data)], TOOBIG)},
                              [])

        # it should be possible to make the container smaller, although at
        # the moment this doesn't actually affect the share, unless the
        # container size is dropped to zero, in which case the share is
        # deleted.
        answer = rstaraw("si1", secrets,
                         {0: ([], [(0,data)], len(data)+8)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        answer = rstaraw("si1", secrets,
                         {0: ([], [(0,data)], 0)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

        read_answer = read("si1", [0], [(0,10)])
        self.failUnlessEqual(read_answer, {})

    def test_allocate(self):
        ss = self.create("test_allocate")
        self.allocate(ss, "si1", "we1", self._lease_secret.next(),
                      set([0,1,2]), 100)

        read = ss.remote_slot_readv
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
        write = ss.remote_slot_testv_and_readv_and_writev
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
        ss = self.create("test_operators")

        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = ss.remote_slot_testv_and_readv_and_writev
        read = ss.remote_slot_readv

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
        ss = self.create("test_readv")
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = ss.remote_slot_testv_and_readv_and_writev
        read = ss.remote_slot_readv
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
            return ( self.write_enabler("we1"),
                     self.renew_secret("we1-%d" % n),
                     self.cancel_secret("we1-%d" % n) )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = ss.remote_slot_testv_and_readv_and_writev
        read = ss.remote_slot_readv
        rc = write("si1", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(rc, (True, {}))

        # create a random non-numeric file in the bucket directory, to
        # exercise the code that's supposed to ignore those.
        bucket_dir = os.path.join(self.workdir("test_leases"),
                                  "shares", storage_index_to_dir("si1"))
        f = open(os.path.join(bucket_dir, "ignore_me.txt"), "w")
        f.write("you ought to be ignoring me\n")
        f.close()

        s0 = MutableShareFile(os.path.join(bucket_dir, "0"))
        self.failUnlessEqual(len(list(s0.get_leases())), 1)

        # add-lease on a missing storage index is silently ignored
        self.failUnlessEqual(ss.remote_add_lease("si18", "", ""), None)

        # re-allocate the slots and use the same secrets, that should update
        # the lease
        write("si1", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(len(list(s0.get_leases())), 1)

        # renew it directly
        ss.remote_renew_lease("si1", secrets(0)[1])
        self.failUnlessEqual(len(list(s0.get_leases())), 1)

        # now allocate them with a bunch of different secrets, to trigger the
        # extended lease code. Use add_lease for one of them.
        write("si1", secrets(1), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(len(list(s0.get_leases())), 2)
        secrets2 = secrets(2)
        ss.remote_add_lease("si1", secrets2[1], secrets2[2])
        self.failUnlessEqual(len(list(s0.get_leases())), 3)
        write("si1", secrets(3), {0: ([], [(0,data)], None)}, [])
        write("si1", secrets(4), {0: ([], [(0,data)], None)}, [])
        write("si1", secrets(5), {0: ([], [(0,data)], None)}, [])

        self.failUnlessEqual(len(list(s0.get_leases())), 6)

        # cancel one of them
        ss.remote_cancel_lease("si1", secrets(5)[2])
        self.failUnlessEqual(len(list(s0.get_leases())), 5)

        all_leases = list(s0.get_leases())
        # and write enough data to expand the container, forcing the server
        # to move the leases
        write("si1", secrets(0),
              {0: ([], [(0,data)], 200), },
              [])

        # read back the leases, make sure they're still intact.
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

        ss.remote_renew_lease("si1", secrets(0)[1])
        ss.remote_renew_lease("si1", secrets(1)[1])
        ss.remote_renew_lease("si1", secrets(2)[1])
        ss.remote_renew_lease("si1", secrets(3)[1])
        ss.remote_renew_lease("si1", secrets(4)[1])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))
        # get a new copy of the leases, with the current timestamps. Reading
        # data and failing to renew/cancel leases should leave the timestamps
        # alone.
        all_leases = list(s0.get_leases())
        # renewing with a bogus token should prompt an error message

        # examine the exception thus raised, make sure the old nodeid is
        # present, to provide for share migration
        e = self.failUnlessRaises(IndexError,
                                  ss.remote_renew_lease, "si1",
                                  secrets(20)[1])
        e_s = str(e)
        self.failUnlessIn("Unable to renew non-existent lease", e_s)
        self.failUnlessIn("I have leases accepted by nodeids:", e_s)
        self.failUnlessIn("nodeids: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' .", e_s)

        # same for cancelling
        self.failUnlessRaises(IndexError,
                              ss.remote_cancel_lease, "si1",
                              secrets(20)[2])
        self.compare_leases(all_leases, list(s0.get_leases()))

        # reading shares should not modify the timestamp
        read("si1", [], [(0,200)])
        self.compare_leases(all_leases, list(s0.get_leases()))

        write("si1", secrets(0),
              {0: ([], [(200, "make me bigger")], None)}, [])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

        write("si1", secrets(0),
              {0: ([], [(500, "make me really bigger")], None)}, [])
        self.compare_leases_without_timestamps(all_leases, list(s0.get_leases()))

        # now cancel them all
        ss.remote_cancel_lease("si1", secrets(0)[2])
        ss.remote_cancel_lease("si1", secrets(1)[2])
        ss.remote_cancel_lease("si1", secrets(2)[2])
        ss.remote_cancel_lease("si1", secrets(3)[2])

        # the slot should still be there
        remaining_shares = read("si1", [], [(0,10)])
        self.failUnlessEqual(len(remaining_shares), 1)
        self.failUnlessEqual(len(list(s0.get_leases())), 1)

        # cancelling a non-existent lease should raise an IndexError
        self.failUnlessRaises(IndexError,
                              ss.remote_cancel_lease, "si1", "nonsecret")

        # and the slot should still be there
        remaining_shares = read("si1", [], [(0,10)])
        self.failUnlessEqual(len(remaining_shares), 1)
        self.failUnlessEqual(len(list(s0.get_leases())), 1)

        ss.remote_cancel_lease("si1", secrets(4)[2])
        # now the slot should be gone
        no_shares = read("si1", [], [(0,10)])
        self.failUnlessEqual(no_shares, {})

        # cancelling a lease on a non-existent share should raise an IndexError
        self.failUnlessRaises(IndexError,
                              ss.remote_cancel_lease, "si2", "nonsecret")

    def test_remove(self):
        ss = self.create("test_remove")
        self.allocate(ss, "si1", "we1", self._lease_secret.next(),
                      set([0,1,2]), 100)
        readv = ss.remote_slot_readv
        writev = ss.remote_slot_testv_and_readv_and_writev
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
        ss = StorageServer(workdir, "\x00" * 20)
        ss.setServiceParent(self.sparent)
        return ss

    def test_latencies(self):
        ss = self.create("test_latencies")
        for i in range(10000):
            ss.add_latency("allocate", 1.0 * i)
        for i in range(1000):
            ss.add_latency("renew", 1.0 * i)
        for i in range(10):
            ss.add_latency("cancel", 2.0 * i)
        ss.add_latency("get", 5.0)

        output = ss.get_latencies()

        self.failUnlessEqual(sorted(output.keys()),
                             sorted(["allocate", "renew", "cancel", "get"]))
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

        self.failUnlessEqual(len(ss.latencies["cancel"]), 10)
        self.failUnless(abs(output["cancel"]["mean"] - 9) < 1, output)
        self.failUnless(abs(output["cancel"]["01_0_percentile"] -  0) < 1, output)
        self.failUnless(abs(output["cancel"]["10_0_percentile"] -  2) < 1, output)
        self.failUnless(abs(output["cancel"]["50_0_percentile"] - 10) < 1, output)
        self.failUnless(abs(output["cancel"]["90_0_percentile"] - 18) < 1, output)
        self.failUnless(abs(output["cancel"]["95_0_percentile"] - 18) < 1, output)
        self.failUnless(abs(output["cancel"]["99_0_percentile"] - 18) < 1, output)
        self.failUnless(abs(output["cancel"]["99_9_percentile"] - 18) < 1, output)

        self.failUnlessEqual(len(ss.latencies["get"]), 1)
        self.failUnless(abs(output["get"]["mean"] - 5) < 1, output)
        self.failUnless(abs(output["get"]["01_0_percentile"] - 5) < 1, output)
        self.failUnless(abs(output["get"]["10_0_percentile"] - 5) < 1, output)
        self.failUnless(abs(output["get"]["50_0_percentile"] - 5) < 1, output)
        self.failUnless(abs(output["get"]["90_0_percentile"] - 5) < 1, output)
        self.failUnless(abs(output["get"]["95_0_percentile"] - 5) < 1, output)
        self.failUnless(abs(output["get"]["99_0_percentile"] - 5) < 1, output)
        self.failUnless(abs(output["get"]["99_9_percentile"] - 5) < 1, output)

def remove_tags(s):
    s = re.sub(r'<[^>]*>', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s

class MyBucketCountingCrawler(BucketCountingCrawler):
    def finished_prefix(self, cycle, prefix):
        BucketCountingCrawler.finished_prefix(self, cycle, prefix)
        if self.hook_ds:
            d = self.hook_ds.pop(0)
            d.callback(None)

class MyStorageServer(StorageServer):
    def add_bucket_counter(self):
        statefile = os.path.join(self.storedir, "bucket_counter.state")
        self.bucket_counter = MyBucketCountingCrawler(self, statefile)
        self.bucket_counter.setServiceParent(self)

class BucketCounter(unittest.TestCase, pollmixin.PollMixin):

    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()
    def tearDown(self):
        return self.s.stopService()

    def test_bucket_counter(self):
        basedir = "storage/BucketCounter/bucket_counter"
        fileutil.make_dirs(basedir)
        ss = StorageServer(basedir, "\x00" * 20)
        # to make sure we capture the bucket-counting-crawler in the middle
        # of a cycle, we reach in and reduce its maximum slice time to 0. We
        # also make it start sooner than usual.
        ss.bucket_counter.slow_start = 0
        orig_cpu_slice = ss.bucket_counter.cpu_slice
        ss.bucket_counter.cpu_slice = 0
        ss.setServiceParent(self.s)

        w = StorageStatus(ss)

        # this sample is before the crawler has started doing anything
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: Yes", s)
        self.failUnlessIn("Reserved space: - 0 B (0)", s)
        self.failUnlessIn("Total buckets: Not computed yet", s)
        self.failUnlessIn("Next crawl in", s)

        # give the bucket-counting-crawler one tick to get started. The
        # cpu_slice=0 will force it to yield right after it processes the
        # first prefix

        d = fireEventually()
        def _check(ignored):
            # are we really right after the first prefix?
            state = ss.bucket_counter.get_state()
            if state["last-complete-prefix"] is None:
                d2 = fireEventually()
                d2.addCallback(_check)
                return d2
            self.failUnlessEqual(state["last-complete-prefix"],
                                 ss.bucket_counter.prefixes[0])
            ss.bucket_counter.cpu_slice = 100.0 # finish as fast as possible
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn(" Current crawl ", s)
            self.failUnlessIn(" (next work in ", s)
        d.addCallback(_check)

        # now give it enough time to complete a full cycle
        def _watch():
            return not ss.bucket_counter.get_progress()["cycle-in-progress"]
        d.addCallback(lambda ignored: self.poll(_watch))
        def _check2(ignored):
            ss.bucket_counter.cpu_slice = orig_cpu_slice
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("Total buckets: 0 (the number of", s)
            self.failUnlessIn("Next crawl in 59 minutes", s)
        d.addCallback(_check2)
        return d

    def test_bucket_counter_cleanup(self):
        basedir = "storage/BucketCounter/bucket_counter_cleanup"
        fileutil.make_dirs(basedir)
        ss = StorageServer(basedir, "\x00" * 20)
        # to make sure we capture the bucket-counting-crawler in the middle
        # of a cycle, we reach in and reduce its maximum slice time to 0.
        ss.bucket_counter.slow_start = 0
        orig_cpu_slice = ss.bucket_counter.cpu_slice
        ss.bucket_counter.cpu_slice = 0
        ss.setServiceParent(self.s)

        d = fireEventually()

        def _after_first_prefix(ignored):
            state = ss.bucket_counter.state
            if state["last-complete-prefix"] is None:
                d2 = fireEventually()
                d2.addCallback(_after_first_prefix)
                return d2
            ss.bucket_counter.cpu_slice = 100.0 # finish as fast as possible
            # now sneak in and mess with its state, to make sure it cleans up
            # properly at the end of the cycle
            self.failUnlessEqual(state["last-complete-prefix"],
                                 ss.bucket_counter.prefixes[0])
            state["bucket-counts"][-12] = {}
            state["storage-index-samples"]["bogusprefix!"] = (-12, [])
            ss.bucket_counter.save_state()
        d.addCallback(_after_first_prefix)

        # now give it enough time to complete a cycle
        def _watch():
            return not ss.bucket_counter.get_progress()["cycle-in-progress"]
        d.addCallback(lambda ignored: self.poll(_watch))
        def _check2(ignored):
            ss.bucket_counter.cpu_slice = orig_cpu_slice
            s = ss.bucket_counter.get_state()
            self.failIf(-12 in s["bucket-counts"], s["bucket-counts"].keys())
            self.failIf("bogusprefix!" in s["storage-index-samples"],
                        s["storage-index-samples"].keys())
        d.addCallback(_check2)
        return d

    def test_bucket_counter_eta(self):
        basedir = "storage/BucketCounter/bucket_counter_eta"
        fileutil.make_dirs(basedir)
        ss = MyStorageServer(basedir, "\x00" * 20)
        ss.bucket_counter.slow_start = 0
        # these will be fired inside finished_prefix()
        hooks = ss.bucket_counter.hook_ds = [defer.Deferred() for i in range(3)]
        w = StorageStatus(ss)

        d = defer.Deferred()

        def _check_1(ignored):
            # no ETA is available yet
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("complete (next work", s)

        def _check_2(ignored):
            # one prefix has finished, so an ETA based upon that elapsed time
            # should be available.
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("complete (ETA ", s)

        def _check_3(ignored):
            # two prefixes have finished
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("complete (ETA ", s)
            d.callback("done")

        hooks[0].addCallback(_check_1).addErrback(d.errback)
        hooks[1].addCallback(_check_2).addErrback(d.errback)
        hooks[2].addCallback(_check_3).addErrback(d.errback)

        ss.setServiceParent(self.s)
        return d

class InstrumentedLeaseCheckingCrawler(LeaseCheckingCrawler):
    stop_after_first_bucket = False
    def process_bucket(self, *args, **kwargs):
        LeaseCheckingCrawler.process_bucket(self, *args, **kwargs)
        if self.stop_after_first_bucket:
            self.stop_after_first_bucket = False
            self.cpu_slice = -1.0
    def yielding(self, sleep_time):
        if not self.stop_after_first_bucket:
            self.cpu_slice = 500

class BrokenStatResults:
    pass
class No_ST_BLOCKS_LeaseCheckingCrawler(LeaseCheckingCrawler):
    def stat(self, fn):
        s = os.stat(fn)
        bsr = BrokenStatResults()
        for attrname in dir(s):
            if attrname.startswith("_"):
                continue
            if attrname == "st_blocks":
                continue
            setattr(bsr, attrname, getattr(s, attrname))
        return bsr

class InstrumentedStorageServer(StorageServer):
    LeaseCheckerClass = InstrumentedLeaseCheckingCrawler
class No_ST_BLOCKS_StorageServer(StorageServer):
    LeaseCheckerClass = No_ST_BLOCKS_LeaseCheckingCrawler

class LeaseCrawler(unittest.TestCase, pollmixin.PollMixin, WebRenderingMixin):

    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()
    def tearDown(self):
        return self.s.stopService()

    def make_shares(self, ss):
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

        a,w = ss.remote_allocate_buckets(immutable_si_0, rs0, cs0, sharenums,
                                         1000, canary)
        w[0].remote_write(0, data)
        w[0].remote_close()

        a,w = ss.remote_allocate_buckets(immutable_si_1, rs1, cs1, sharenums,
                                         1000, canary)
        w[0].remote_write(0, data)
        w[0].remote_close()
        ss.remote_add_lease(immutable_si_1, rs1a, cs1a)

        writev = ss.remote_slot_testv_and_readv_and_writev
        writev(mutable_si_2, (we2, rs2, cs2),
               {0: ([], [(0,data)], len(data))}, [])
        writev(mutable_si_3, (we3, rs3, cs3),
               {0: ([], [(0,data)], len(data))}, [])
        ss.remote_add_lease(mutable_si_3, rs3a, cs3a)

        self.sis = [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3]
        self.renew_secrets = [rs0, rs1, rs1a, rs2, rs3, rs3a]
        self.cancel_secrets = [cs0, cs1, cs1a, cs2, cs3, cs3a]

    def test_basic(self):
        basedir = "storage/LeaseCrawler/basic"
        fileutil.make_dirs(basedir)
        ss = InstrumentedStorageServer(basedir, "\x00" * 20)
        # make it start sooner than usual.
        lc = ss.lease_checker
        lc.slow_start = 0
        lc.cpu_slice = 500
        lc.stop_after_first_bucket = True
        webstatus = StorageStatus(ss)

        # create a few shares, with some leases on them
        self.make_shares(ss)
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

        # add a non-sharefile to exercise another code path
        fn = os.path.join(ss.sharedir,
                          storage_index_to_dir(immutable_si_0),
                          "not-a-share")
        f = open(fn, "wb")
        f.write("I am not a share.\n")
        f.close()

        # this is before the crawl has started, so we're not in a cycle yet
        initial_state = lc.get_state()
        self.failIf(lc.get_progress()["cycle-in-progress"])
        self.failIfIn("cycle-to-date", initial_state)
        self.failIfIn("estimated-remaining-cycle", initial_state)
        self.failIfIn("estimated-current-cycle", initial_state)
        self.failUnlessIn("history", initial_state)
        self.failUnlessEqual(initial_state["history"], {})

        ss.setServiceParent(self.s)

        DAY = 24*60*60

        d = fireEventually()

        # now examine the state right after the first bucket has been
        # processed.
        def _after_first_bucket(ignored):
            initial_state = lc.get_state()
            if "cycle-to-date" not in initial_state:
                d2 = fireEventually()
                d2.addCallback(_after_first_bucket)
                return d2
            self.failUnlessIn("cycle-to-date", initial_state)
            self.failUnlessIn("estimated-remaining-cycle", initial_state)
            self.failUnlessIn("estimated-current-cycle", initial_state)
            self.failUnlessIn("history", initial_state)
            self.failUnlessEqual(initial_state["history"], {})

            so_far = initial_state["cycle-to-date"]
            self.failUnlessEqual(so_far["expiration-enabled"], False)
            self.failUnlessIn("configured-expiration-mode", so_far)
            self.failUnlessIn("lease-age-histogram", so_far)
            lah = so_far["lease-age-histogram"]
            self.failUnlessEqual(type(lah), list)
            self.failUnlessEqual(len(lah), 1)
            self.failUnlessEqual(lah, [ (0.0, DAY, 1) ] )
            self.failUnlessEqual(so_far["leases-per-share-histogram"], {1: 1})
            self.failUnlessEqual(so_far["corrupt-shares"], [])
            sr1 = so_far["space-recovered"]
            self.failUnlessEqual(sr1["examined-buckets"], 1)
            self.failUnlessEqual(sr1["examined-shares"], 1)
            self.failUnlessEqual(sr1["actual-shares"], 0)
            self.failUnlessEqual(sr1["configured-diskbytes"], 0)
            self.failUnlessEqual(sr1["original-sharebytes"], 0)
            left = initial_state["estimated-remaining-cycle"]
            sr2 = left["space-recovered"]
            self.failUnless(sr2["examined-buckets"] > 0, sr2["examined-buckets"])
            self.failUnless(sr2["examined-shares"] > 0, sr2["examined-shares"])
            self.failIfEqual(sr2["actual-shares"], None)
            self.failIfEqual(sr2["configured-diskbytes"], None)
            self.failIfEqual(sr2["original-sharebytes"], None)
        d.addCallback(_after_first_bucket)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_in_cycle(html):
            s = remove_tags(html)
            self.failUnlessIn("So far, this cycle has examined "
                              "1 shares in 1 buckets (0 mutable / 1 immutable) ", s)
            self.failUnlessIn("and has recovered: "
                              "0 shares, 0 buckets (0 mutable / 0 immutable), "
                              "0 B (0 B / 0 B)", s)
            self.failUnlessIn("If expiration were enabled, "
                              "we would have recovered: "
                              "0 shares, 0 buckets (0 mutable / 0 immutable),"
                              " 0 B (0 B / 0 B) by now", s)
            self.failUnlessIn("and the remainder of this cycle "
                              "would probably recover: "
                              "0 shares, 0 buckets (0 mutable / 0 immutable),"
                              " 0 B (0 B / 0 B)", s)
            self.failUnlessIn("and the whole cycle would probably recover: "
                              "0 shares, 0 buckets (0 mutable / 0 immutable),"
                              " 0 B (0 B / 0 B)", s)
            self.failUnlessIn("if we were strictly using each lease's default "
                              "31-day lease lifetime", s)
            self.failUnlessIn("this cycle would be expected to recover: ", s)
        d.addCallback(_check_html_in_cycle)

        # wait for the crawler to finish the first cycle. Nothing should have
        # been removed.
        def _wait():
            return bool(lc.get_state()["last-cycle-finished"] is not None)
        d.addCallback(lambda ign: self.poll(_wait))

        def _after_first_cycle(ignored):
            s = lc.get_state()
            self.failIf("cycle-to-date" in s)
            self.failIf("estimated-remaining-cycle" in s)
            self.failIf("estimated-current-cycle" in s)
            last = s["history"][0]
            self.failUnlessIn("cycle-start-finish-times", last)
            self.failUnlessEqual(type(last["cycle-start-finish-times"]), tuple)
            self.failUnlessEqual(last["expiration-enabled"], False)
            self.failUnlessIn("configured-expiration-mode", last)

            self.failUnlessIn("lease-age-histogram", last)
            lah = last["lease-age-histogram"]
            self.failUnlessEqual(type(lah), list)
            self.failUnlessEqual(len(lah), 1)
            self.failUnlessEqual(lah, [ (0.0, DAY, 6) ] )

            self.failUnlessEqual(last["leases-per-share-histogram"], {1: 2, 2: 2})
            self.failUnlessEqual(last["corrupt-shares"], [])

            rec = last["space-recovered"]
            self.failUnlessEqual(rec["examined-buckets"], 4)
            self.failUnlessEqual(rec["examined-shares"], 4)
            self.failUnlessEqual(rec["actual-buckets"], 0)
            self.failUnlessEqual(rec["original-buckets"], 0)
            self.failUnlessEqual(rec["configured-buckets"], 0)
            self.failUnlessEqual(rec["actual-shares"], 0)
            self.failUnlessEqual(rec["original-shares"], 0)
            self.failUnlessEqual(rec["configured-shares"], 0)
            self.failUnlessEqual(rec["actual-diskbytes"], 0)
            self.failUnlessEqual(rec["original-diskbytes"], 0)
            self.failUnlessEqual(rec["configured-diskbytes"], 0)
            self.failUnlessEqual(rec["actual-sharebytes"], 0)
            self.failUnlessEqual(rec["original-sharebytes"], 0)
            self.failUnlessEqual(rec["configured-sharebytes"], 0)

            def _get_sharefile(si):
                return list(ss._iter_share_files(si))[0]
            def count_leases(si):
                return len(list(_get_sharefile(si).get_leases()))
            self.failUnlessEqual(count_leases(immutable_si_0), 1)
            self.failUnlessEqual(count_leases(immutable_si_1), 2)
            self.failUnlessEqual(count_leases(mutable_si_2), 1)
            self.failUnlessEqual(count_leases(mutable_si_3), 2)
        d.addCallback(_after_first_cycle)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html(html):
            s = remove_tags(html)
            self.failUnlessIn("recovered: 0 shares, 0 buckets "
                              "(0 mutable / 0 immutable), 0 B (0 B / 0 B) ", s)
            self.failUnlessIn("and saw a total of 4 shares, 4 buckets "
                              "(2 mutable / 2 immutable),", s)
            self.failUnlessIn("but expiration was not enabled", s)
        d.addCallback(_check_html)
        d.addCallback(lambda ign: self.render_json(webstatus))
        def _check_json(json):
            data = simplejson.loads(json)
            self.failUnlessIn("lease-checker", data)
            self.failUnlessIn("lease-checker-progress", data)
        d.addCallback(_check_json)
        return d

    def backdate_lease(self, sf, renew_secret, new_expire_time):
        # ShareFile.renew_lease ignores attempts to back-date a lease (i.e.
        # "renew" a lease with a new_expire_time that is older than what the
        # current lease has), so we have to reach inside it.
        for i,lease in enumerate(sf.get_leases()):
            if lease.renew_secret == renew_secret:
                lease.expiration_time = new_expire_time
                f = open(sf.home, 'rb+')
                sf._write_lease_record(f, i, lease)
                f.close()
                return
        raise IndexError("unable to renew non-existent lease")

    def test_expire_age(self):
        basedir = "storage/LeaseCrawler/expire_age"
        fileutil.make_dirs(basedir)
        # setting expiration_time to 2000 means that any lease which is more
        # than 2000s old will be expired.
        ss = InstrumentedStorageServer(basedir, "\x00" * 20,
                                       expiration_enabled=True,
                                       expiration_mode="age",
                                       expiration_override_lease_duration=2000)
        # make it start sooner than usual.
        lc = ss.lease_checker
        lc.slow_start = 0
        lc.stop_after_first_bucket = True
        webstatus = StorageStatus(ss)

        # create a few shares, with some leases on them
        self.make_shares(ss)
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

        def count_shares(si):
            return len(list(ss._iter_share_files(si)))
        def _get_sharefile(si):
            return list(ss._iter_share_files(si))[0]
        def count_leases(si):
            return len(list(_get_sharefile(si).get_leases()))

        self.failUnlessEqual(count_shares(immutable_si_0), 1)
        self.failUnlessEqual(count_leases(immutable_si_0), 1)
        self.failUnlessEqual(count_shares(immutable_si_1), 1)
        self.failUnlessEqual(count_leases(immutable_si_1), 2)
        self.failUnlessEqual(count_shares(mutable_si_2), 1)
        self.failUnlessEqual(count_leases(mutable_si_2), 1)
        self.failUnlessEqual(count_shares(mutable_si_3), 1)
        self.failUnlessEqual(count_leases(mutable_si_3), 2)

        # artificially crank back the expiration time on the first lease of
        # each share, to make it look like it expired already (age=1000s).
        # Some shares have an extra lease which is set to expire at the
        # default time in 31 days from now (age=31days). We then run the
        # crawler, which will expire the first lease, making some shares get
        # deleted and others stay alive (with one remaining lease)
        now = time.time()

        sf0 = _get_sharefile(immutable_si_0)
        self.backdate_lease(sf0, self.renew_secrets[0], now - 1000)
        sf0_size = os.stat(sf0.home).st_size

        # immutable_si_1 gets an extra lease
        sf1 = _get_sharefile(immutable_si_1)
        self.backdate_lease(sf1, self.renew_secrets[1], now - 1000)

        sf2 = _get_sharefile(mutable_si_2)
        self.backdate_lease(sf2, self.renew_secrets[3], now - 1000)
        sf2_size = os.stat(sf2.home).st_size

        # mutable_si_3 gets an extra lease
        sf3 = _get_sharefile(mutable_si_3)
        self.backdate_lease(sf3, self.renew_secrets[4], now - 1000)

        ss.setServiceParent(self.s)

        d = fireEventually()
        # examine the state right after the first bucket has been processed
        def _after_first_bucket(ignored):
            p = lc.get_progress()
            if not p["cycle-in-progress"]:
                d2 = fireEventually()
                d2.addCallback(_after_first_bucket)
                return d2
        d.addCallback(_after_first_bucket)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_in_cycle(html):
            s = remove_tags(html)
            # the first bucket encountered gets deleted, and its prefix
            # happens to be about 1/5th of the way through the ring, so the
            # predictor thinks we'll have 5 shares and that we'll delete them
            # all. This part of the test depends upon the SIs landing right
            # where they do now.
            self.failUnlessIn("The remainder of this cycle is expected to "
                              "recover: 4 shares, 4 buckets", s)
            self.failUnlessIn("The whole cycle is expected to examine "
                              "5 shares in 5 buckets and to recover: "
                              "5 shares, 5 buckets", s)
        d.addCallback(_check_html_in_cycle)

        # wait for the crawler to finish the first cycle. Two shares should
        # have been removed
        def _wait():
            return bool(lc.get_state()["last-cycle-finished"] is not None)
        d.addCallback(lambda ign: self.poll(_wait))

        def _after_first_cycle(ignored):
            self.failUnlessEqual(count_shares(immutable_si_0), 0)
            self.failUnlessEqual(count_shares(immutable_si_1), 1)
            self.failUnlessEqual(count_leases(immutable_si_1), 1)
            self.failUnlessEqual(count_shares(mutable_si_2), 0)
            self.failUnlessEqual(count_shares(mutable_si_3), 1)
            self.failUnlessEqual(count_leases(mutable_si_3), 1)

            s = lc.get_state()
            last = s["history"][0]

            self.failUnlessEqual(last["expiration-enabled"], True)
            self.failUnlessEqual(last["configured-expiration-mode"],
                                 ("age", 2000, None, ("mutable", "immutable")))
            self.failUnlessEqual(last["leases-per-share-histogram"], {1: 2, 2: 2})

            rec = last["space-recovered"]
            self.failUnlessEqual(rec["examined-buckets"], 4)
            self.failUnlessEqual(rec["examined-shares"], 4)
            self.failUnlessEqual(rec["actual-buckets"], 2)
            self.failUnlessEqual(rec["original-buckets"], 2)
            self.failUnlessEqual(rec["configured-buckets"], 2)
            self.failUnlessEqual(rec["actual-shares"], 2)
            self.failUnlessEqual(rec["original-shares"], 2)
            self.failUnlessEqual(rec["configured-shares"], 2)
            size = sf0_size + sf2_size
            self.failUnlessEqual(rec["actual-sharebytes"], size)
            self.failUnlessEqual(rec["original-sharebytes"], size)
            self.failUnlessEqual(rec["configured-sharebytes"], size)
            # different platforms have different notions of "blocks used by
            # this file", so merely assert that it's a number
            self.failUnless(rec["actual-diskbytes"] >= 0,
                            rec["actual-diskbytes"])
            self.failUnless(rec["original-diskbytes"] >= 0,
                            rec["original-diskbytes"])
            self.failUnless(rec["configured-diskbytes"] >= 0,
                            rec["configured-diskbytes"])
        d.addCallback(_after_first_cycle)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html(html):
            s = remove_tags(html)
            self.failUnlessIn("Expiration Enabled: expired leases will be removed", s)
            self.failUnlessIn("Leases created or last renewed more than 33 minutes ago will be considered expired.", s)
            self.failUnlessIn(" recovered: 2 shares, 2 buckets (1 mutable / 1 immutable), ", s)
        d.addCallback(_check_html)
        return d

    def test_expire_cutoff_date(self):
        basedir = "storage/LeaseCrawler/expire_cutoff_date"
        fileutil.make_dirs(basedir)
        # setting cutoff-date to 2000 seconds ago means that any lease which
        # is more than 2000s old will be expired.
        now = time.time()
        then = int(now - 2000)
        ss = InstrumentedStorageServer(basedir, "\x00" * 20,
                                       expiration_enabled=True,
                                       expiration_mode="cutoff-date",
                                       expiration_cutoff_date=then)
        # make it start sooner than usual.
        lc = ss.lease_checker
        lc.slow_start = 0
        lc.stop_after_first_bucket = True
        webstatus = StorageStatus(ss)

        # create a few shares, with some leases on them
        self.make_shares(ss)
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

        def count_shares(si):
            return len(list(ss._iter_share_files(si)))
        def _get_sharefile(si):
            return list(ss._iter_share_files(si))[0]
        def count_leases(si):
            return len(list(_get_sharefile(si).get_leases()))

        self.failUnlessEqual(count_shares(immutable_si_0), 1)
        self.failUnlessEqual(count_leases(immutable_si_0), 1)
        self.failUnlessEqual(count_shares(immutable_si_1), 1)
        self.failUnlessEqual(count_leases(immutable_si_1), 2)
        self.failUnlessEqual(count_shares(mutable_si_2), 1)
        self.failUnlessEqual(count_leases(mutable_si_2), 1)
        self.failUnlessEqual(count_shares(mutable_si_3), 1)
        self.failUnlessEqual(count_leases(mutable_si_3), 2)

        # artificially crank back the expiration time on the first lease of
        # each share, to make it look like was renewed 3000s ago. To achieve
        # this, we need to set the expiration time to now-3000+31days. This
        # will change when the lease format is improved to contain both
        # create/renew time and duration.
        new_expiration_time = now - 3000 + 31*24*60*60

        # Some shares have an extra lease which is set to expire at the
        # default time in 31 days from now (age=31days). We then run the
        # crawler, which will expire the first lease, making some shares get
        # deleted and others stay alive (with one remaining lease)

        sf0 = _get_sharefile(immutable_si_0)
        self.backdate_lease(sf0, self.renew_secrets[0], new_expiration_time)
        sf0_size = os.stat(sf0.home).st_size

        # immutable_si_1 gets an extra lease
        sf1 = _get_sharefile(immutable_si_1)
        self.backdate_lease(sf1, self.renew_secrets[1], new_expiration_time)

        sf2 = _get_sharefile(mutable_si_2)
        self.backdate_lease(sf2, self.renew_secrets[3], new_expiration_time)
        sf2_size = os.stat(sf2.home).st_size

        # mutable_si_3 gets an extra lease
        sf3 = _get_sharefile(mutable_si_3)
        self.backdate_lease(sf3, self.renew_secrets[4], new_expiration_time)

        ss.setServiceParent(self.s)

        d = fireEventually()
        # examine the state right after the first bucket has been processed
        def _after_first_bucket(ignored):
            p = lc.get_progress()
            if not p["cycle-in-progress"]:
                d2 = fireEventually()
                d2.addCallback(_after_first_bucket)
                return d2
        d.addCallback(_after_first_bucket)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html_in_cycle(html):
            s = remove_tags(html)
            # the first bucket encountered gets deleted, and its prefix
            # happens to be about 1/5th of the way through the ring, so the
            # predictor thinks we'll have 5 shares and that we'll delete them
            # all. This part of the test depends upon the SIs landing right
            # where they do now.
            self.failUnlessIn("The remainder of this cycle is expected to "
                              "recover: 4 shares, 4 buckets", s)
            self.failUnlessIn("The whole cycle is expected to examine "
                              "5 shares in 5 buckets and to recover: "
                              "5 shares, 5 buckets", s)
        d.addCallback(_check_html_in_cycle)

        # wait for the crawler to finish the first cycle. Two shares should
        # have been removed
        def _wait():
            return bool(lc.get_state()["last-cycle-finished"] is not None)
        d.addCallback(lambda ign: self.poll(_wait))

        def _after_first_cycle(ignored):
            self.failUnlessEqual(count_shares(immutable_si_0), 0)
            self.failUnlessEqual(count_shares(immutable_si_1), 1)
            self.failUnlessEqual(count_leases(immutable_si_1), 1)
            self.failUnlessEqual(count_shares(mutable_si_2), 0)
            self.failUnlessEqual(count_shares(mutable_si_3), 1)
            self.failUnlessEqual(count_leases(mutable_si_3), 1)

            s = lc.get_state()
            last = s["history"][0]

            self.failUnlessEqual(last["expiration-enabled"], True)
            self.failUnlessEqual(last["configured-expiration-mode"],
                                 ("cutoff-date", None, then,
                                  ("mutable", "immutable")))
            self.failUnlessEqual(last["leases-per-share-histogram"],
                                 {1: 2, 2: 2})

            rec = last["space-recovered"]
            self.failUnlessEqual(rec["examined-buckets"], 4)
            self.failUnlessEqual(rec["examined-shares"], 4)
            self.failUnlessEqual(rec["actual-buckets"], 2)
            self.failUnlessEqual(rec["original-buckets"], 0)
            self.failUnlessEqual(rec["configured-buckets"], 2)
            self.failUnlessEqual(rec["actual-shares"], 2)
            self.failUnlessEqual(rec["original-shares"], 0)
            self.failUnlessEqual(rec["configured-shares"], 2)
            size = sf0_size + sf2_size
            self.failUnlessEqual(rec["actual-sharebytes"], size)
            self.failUnlessEqual(rec["original-sharebytes"], 0)
            self.failUnlessEqual(rec["configured-sharebytes"], size)
            # different platforms have different notions of "blocks used by
            # this file", so merely assert that it's a number
            self.failUnless(rec["actual-diskbytes"] >= 0,
                            rec["actual-diskbytes"])
            self.failUnless(rec["original-diskbytes"] >= 0,
                            rec["original-diskbytes"])
            self.failUnless(rec["configured-diskbytes"] >= 0,
                            rec["configured-diskbytes"])
        d.addCallback(_after_first_cycle)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html(html):
            s = remove_tags(html)
            self.failUnlessIn("Expiration Enabled:"
                              " expired leases will be removed", s)
            date = time.strftime("%Y-%m-%d (%d-%b-%Y) UTC", time.gmtime(then))
            substr = "Leases created or last renewed before %s will be considered expired." % date
            self.failUnlessIn(substr, s)
            self.failUnlessIn(" recovered: 2 shares, 2 buckets (1 mutable / 1 immutable), ", s)
        d.addCallback(_check_html)
        return d

    def test_only_immutable(self):
        basedir = "storage/LeaseCrawler/only_immutable"
        fileutil.make_dirs(basedir)
        now = time.time()
        then = int(now - 2000)
        ss = StorageServer(basedir, "\x00" * 20,
                           expiration_enabled=True,
                           expiration_mode="cutoff-date",
                           expiration_cutoff_date=then,
                           expiration_sharetypes=("immutable",))
        lc = ss.lease_checker
        lc.slow_start = 0
        webstatus = StorageStatus(ss)

        self.make_shares(ss)
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis
        # set all leases to be expirable
        new_expiration_time = now - 3000 + 31*24*60*60

        def count_shares(si):
            return len(list(ss._iter_share_files(si)))
        def _get_sharefile(si):
            return list(ss._iter_share_files(si))[0]
        def count_leases(si):
            return len(list(_get_sharefile(si).get_leases()))

        sf0 = _get_sharefile(immutable_si_0)
        self.backdate_lease(sf0, self.renew_secrets[0], new_expiration_time)
        sf1 = _get_sharefile(immutable_si_1)
        self.backdate_lease(sf1, self.renew_secrets[1], new_expiration_time)
        self.backdate_lease(sf1, self.renew_secrets[2], new_expiration_time)
        sf2 = _get_sharefile(mutable_si_2)
        self.backdate_lease(sf2, self.renew_secrets[3], new_expiration_time)
        sf3 = _get_sharefile(mutable_si_3)
        self.backdate_lease(sf3, self.renew_secrets[4], new_expiration_time)
        self.backdate_lease(sf3, self.renew_secrets[5], new_expiration_time)

        ss.setServiceParent(self.s)
        def _wait():
            return bool(lc.get_state()["last-cycle-finished"] is not None)
        d = self.poll(_wait)

        def _after_first_cycle(ignored):
            self.failUnlessEqual(count_shares(immutable_si_0), 0)
            self.failUnlessEqual(count_shares(immutable_si_1), 0)
            self.failUnlessEqual(count_shares(mutable_si_2), 1)
            self.failUnlessEqual(count_leases(mutable_si_2), 1)
            self.failUnlessEqual(count_shares(mutable_si_3), 1)
            self.failUnlessEqual(count_leases(mutable_si_3), 2)
        d.addCallback(_after_first_cycle)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html(html):
            s = remove_tags(html)
            self.failUnlessIn("The following sharetypes will be expired: immutable.", s)
        d.addCallback(_check_html)
        return d

    def test_only_mutable(self):
        basedir = "storage/LeaseCrawler/only_mutable"
        fileutil.make_dirs(basedir)
        now = time.time()
        then = int(now - 2000)
        ss = StorageServer(basedir, "\x00" * 20,
                           expiration_enabled=True,
                           expiration_mode="cutoff-date",
                           expiration_cutoff_date=then,
                           expiration_sharetypes=("mutable",))
        lc = ss.lease_checker
        lc.slow_start = 0
        webstatus = StorageStatus(ss)

        self.make_shares(ss)
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis
        # set all leases to be expirable
        new_expiration_time = now - 3000 + 31*24*60*60

        def count_shares(si):
            return len(list(ss._iter_share_files(si)))
        def _get_sharefile(si):
            return list(ss._iter_share_files(si))[0]
        def count_leases(si):
            return len(list(_get_sharefile(si).get_leases()))

        sf0 = _get_sharefile(immutable_si_0)
        self.backdate_lease(sf0, self.renew_secrets[0], new_expiration_time)
        sf1 = _get_sharefile(immutable_si_1)
        self.backdate_lease(sf1, self.renew_secrets[1], new_expiration_time)
        self.backdate_lease(sf1, self.renew_secrets[2], new_expiration_time)
        sf2 = _get_sharefile(mutable_si_2)
        self.backdate_lease(sf2, self.renew_secrets[3], new_expiration_time)
        sf3 = _get_sharefile(mutable_si_3)
        self.backdate_lease(sf3, self.renew_secrets[4], new_expiration_time)
        self.backdate_lease(sf3, self.renew_secrets[5], new_expiration_time)

        ss.setServiceParent(self.s)
        def _wait():
            return bool(lc.get_state()["last-cycle-finished"] is not None)
        d = self.poll(_wait)

        def _after_first_cycle(ignored):
            self.failUnlessEqual(count_shares(immutable_si_0), 1)
            self.failUnlessEqual(count_leases(immutable_si_0), 1)
            self.failUnlessEqual(count_shares(immutable_si_1), 1)
            self.failUnlessEqual(count_leases(immutable_si_1), 2)
            self.failUnlessEqual(count_shares(mutable_si_2), 0)
            self.failUnlessEqual(count_shares(mutable_si_3), 0)
        d.addCallback(_after_first_cycle)
        d.addCallback(lambda ign: self.render1(webstatus))
        def _check_html(html):
            s = remove_tags(html)
            self.failUnlessIn("The following sharetypes will be expired: mutable.", s)
        d.addCallback(_check_html)
        return d

    def test_bad_mode(self):
        basedir = "storage/LeaseCrawler/bad_mode"
        fileutil.make_dirs(basedir)
        e = self.failUnlessRaises(ValueError,
                                  StorageServer, basedir, "\x00" * 20,
                                  expiration_mode="bogus")
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
        basedir = "storage/LeaseCrawler/limited_history"
        fileutil.make_dirs(basedir)
        ss = StorageServer(basedir, "\x00" * 20)
        # make it start sooner than usual.
        lc = ss.lease_checker
        lc.slow_start = 0
        lc.cpu_slice = 500

        # create a few shares, with some leases on them
        self.make_shares(ss)

        ss.setServiceParent(self.s)

        def _wait_until_15_cycles_done():
            last = lc.state["last-cycle-finished"]
            if last is not None and last >= 15:
                return True
            if lc.timer:
                lc.timer.reset(0)
            return False
        d = self.poll(_wait_until_15_cycles_done)

        def _check(ignored):
            s = lc.get_state()
            h = s["history"]
            self.failUnlessEqual(len(h), 10)
            self.failUnlessEqual(max(h.keys()), 15)
            self.failUnlessEqual(min(h.keys()), 6)
        d.addCallback(_check)
        return d

    def test_unpredictable_future(self):
        basedir = "storage/LeaseCrawler/unpredictable_future"
        fileutil.make_dirs(basedir)
        ss = StorageServer(basedir, "\x00" * 20)
        # make it start sooner than usual.
        lc = ss.lease_checker
        lc.slow_start = 0
        lc.cpu_slice = -1.0 # stop quickly

        self.make_shares(ss)

        ss.setServiceParent(self.s)

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
            s = lc.get_state()
            if "cycle-to-date" not in s:
                d2 = fireEventually()
                d2.addCallback(_check)
                return d2
            self.failUnlessIn("cycle-to-date", s)
            self.failUnlessIn("estimated-remaining-cycle", s)
            self.failUnlessIn("estimated-current-cycle", s)

            left = s["estimated-remaining-cycle"]["space-recovered"]
            self.failUnlessEqual(left["actual-buckets"], None)
            self.failUnlessEqual(left["original-buckets"], None)
            self.failUnlessEqual(left["configured-buckets"], None)
            self.failUnlessEqual(left["actual-shares"], None)
            self.failUnlessEqual(left["original-shares"], None)
            self.failUnlessEqual(left["configured-shares"], None)
            self.failUnlessEqual(left["actual-diskbytes"], None)
            self.failUnlessEqual(left["original-diskbytes"], None)
            self.failUnlessEqual(left["configured-diskbytes"], None)
            self.failUnlessEqual(left["actual-sharebytes"], None)
            self.failUnlessEqual(left["original-sharebytes"], None)
            self.failUnlessEqual(left["configured-sharebytes"], None)

            full = s["estimated-remaining-cycle"]["space-recovered"]
            self.failUnlessEqual(full["actual-buckets"], None)
            self.failUnlessEqual(full["original-buckets"], None)
            self.failUnlessEqual(full["configured-buckets"], None)
            self.failUnlessEqual(full["actual-shares"], None)
            self.failUnlessEqual(full["original-shares"], None)
            self.failUnlessEqual(full["configured-shares"], None)
            self.failUnlessEqual(full["actual-diskbytes"], None)
            self.failUnlessEqual(full["original-diskbytes"], None)
            self.failUnlessEqual(full["configured-diskbytes"], None)
            self.failUnlessEqual(full["actual-sharebytes"], None)
            self.failUnlessEqual(full["original-sharebytes"], None)
            self.failUnlessEqual(full["configured-sharebytes"], None)

        d.addCallback(_check)
        return d

    def test_no_st_blocks(self):
        basedir = "storage/LeaseCrawler/no_st_blocks"
        fileutil.make_dirs(basedir)
        ss = No_ST_BLOCKS_StorageServer(basedir, "\x00" * 20,
                                        expiration_mode="age",
                                        expiration_override_lease_duration=-1000)
        # a negative expiration_time= means the "configured-"
        # space-recovered counts will be non-zero, since all shares will have
        # expired by then

        # make it start sooner than usual.
        lc = ss.lease_checker
        lc.slow_start = 0

        self.make_shares(ss)
        ss.setServiceParent(self.s)
        def _wait():
            return bool(lc.get_state()["last-cycle-finished"] is not None)
        d = self.poll(_wait)

        def _check(ignored):
            s = lc.get_state()
            last = s["history"][0]
            rec = last["space-recovered"]
            self.failUnlessEqual(rec["configured-buckets"], 4)
            self.failUnlessEqual(rec["configured-shares"], 4)
            self.failUnless(rec["configured-sharebytes"] > 0,
                            rec["configured-sharebytes"])
            # without the .st_blocks field in os.stat() results, we should be
            # reporting diskbytes==sharebytes
            self.failUnlessEqual(rec["configured-sharebytes"],
                                 rec["configured-diskbytes"])
        d.addCallback(_check)
        return d

    def test_share_corruption(self):
        self._poll_should_ignore_these_errors = [
            UnknownMutableContainerVersionError,
            UnknownImmutableContainerVersionError,
            ]
        basedir = "storage/LeaseCrawler/share_corruption"
        fileutil.make_dirs(basedir)
        ss = InstrumentedStorageServer(basedir, "\x00" * 20)
        w = StorageStatus(ss)
        # make it start sooner than usual.
        lc = ss.lease_checker
        lc.stop_after_first_bucket = True
        lc.slow_start = 0
        lc.cpu_slice = 500

        # create a few shares, with some leases on them
        self.make_shares(ss)

        # now corrupt one, and make sure the lease-checker keeps going
        [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis
        first = min(self.sis)
        first_b32 = base32.b2a(first)
        fn = os.path.join(ss.sharedir, storage_index_to_dir(first), "0")
        f = open(fn, "rb+")
        f.seek(0)
        f.write("BAD MAGIC")
        f.close()
        # if get_share_file() doesn't see the correct mutable magic, it
        # assumes the file is an immutable share, and then
        # immutable.ShareFile sees a bad version. So regardless of which kind
        # of share we corrupted, this will trigger an
        # UnknownImmutableContainerVersionError.

        # also create an empty bucket
        empty_si = base32.b2a("\x04"*16)
        empty_bucket_dir = os.path.join(ss.sharedir,
                                        storage_index_to_dir(empty_si))
        fileutil.make_dirs(empty_bucket_dir)

        ss.setServiceParent(self.s)

        d = fireEventually()

        # now examine the state right after the first bucket has been
        # processed.
        def _after_first_bucket(ignored):
            s = lc.get_state()
            if "cycle-to-date" not in s:
                d2 = fireEventually()
                d2.addCallback(_after_first_bucket)
                return d2
            so_far = s["cycle-to-date"]
            rec = so_far["space-recovered"]
            self.failUnlessEqual(rec["examined-buckets"], 1)
            self.failUnlessEqual(rec["examined-shares"], 0)
            self.failUnlessEqual(so_far["corrupt-shares"], [(first_b32, 0)])
        d.addCallback(_after_first_bucket)

        d.addCallback(lambda ign: self.render_json(w))
        def _check_json(json):
            data = simplejson.loads(json)
            # grr. json turns all dict keys into strings.
            so_far = data["lease-checker"]["cycle-to-date"]
            corrupt_shares = so_far["corrupt-shares"]
            # it also turns all tuples into lists
            self.failUnlessEqual(corrupt_shares, [[first_b32, 0]])
        d.addCallback(_check_json)
        d.addCallback(lambda ign: self.render1(w))
        def _check_html(html):
            s = remove_tags(html)
            self.failUnlessIn("Corrupt shares: SI %s shnum 0" % first_b32, s)
        d.addCallback(_check_html)

        def _wait():
            return bool(lc.get_state()["last-cycle-finished"] is not None)
        d.addCallback(lambda ign: self.poll(_wait))

        def _after_first_cycle(ignored):
            s = lc.get_state()
            last = s["history"][0]
            rec = last["space-recovered"]
            self.failUnlessEqual(rec["examined-buckets"], 5)
            self.failUnlessEqual(rec["examined-shares"], 3)
            self.failUnlessEqual(last["corrupt-shares"], [(first_b32, 0)])
        d.addCallback(_after_first_cycle)
        d.addCallback(lambda ign: self.render_json(w))
        def _check_json_history(json):
            data = simplejson.loads(json)
            last = data["lease-checker"]["history"]["0"]
            corrupt_shares = last["corrupt-shares"]
            self.failUnlessEqual(corrupt_shares, [[first_b32, 0]])
        d.addCallback(_check_json_history)
        d.addCallback(lambda ign: self.render1(w))
        def _check_html_history(html):
            s = remove_tags(html)
            self.failUnlessIn("Corrupt shares: SI %s shnum 0" % first_b32, s)
        d.addCallback(_check_html_history)

        def _cleanup(res):
            self.flushLoggedErrors(UnknownMutableContainerVersionError,
                                   UnknownImmutableContainerVersionError)
            return res
        d.addBoth(_cleanup)
        return d

    def render_json(self, page):
        d = self.render1(page, args={"t": ["json"]})
        return d

class NoDiskStatsServer(StorageServer):
    def get_disk_stats(self):
        raise AttributeError

class BadDiskStatsServer(StorageServer):
    def get_disk_stats(self):
        raise OSError

class WebStatus(unittest.TestCase, pollmixin.PollMixin, WebRenderingMixin):

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
        ss = StorageServer(basedir, "\x00" * 20)
        ss.setServiceParent(self.s)
        w = StorageStatus(ss)
        d = self.render1(w)
        def _check_html(html):
            self.failUnlessIn("<h1>Storage Server Status</h1>", html)
            s = remove_tags(html)
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

    def test_status_no_disk_stats(self):
        # Some platforms may have no disk stats API. Make sure the code can handle that
        # (test runs on all platforms).
        basedir = "storage/WebStatus/status_no_disk_stats"
        fileutil.make_dirs(basedir)
        ss = NoDiskStatsServer(basedir, "\x00" * 20)
        ss.setServiceParent(self.s)
        w = StorageStatus(ss)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: Yes", s)
        self.failUnlessIn("Total disk space: ?", s)
        self.failUnlessIn("Space Available to Tahoe: ?", s)
        self.failUnless(ss.get_available_space() is None)

    def test_status_bad_disk_stats(self):
        # If the API to get disk stats exists but a call to it fails, then the status should
        # show that no shares will be accepted, and get_available_space() should be 0.
        basedir = "storage/WebStatus/status_bad_disk_stats"
        fileutil.make_dirs(basedir)
        ss = BadDiskStatsServer(basedir, "\x00" * 20)
        ss.setServiceParent(self.s)
        w = StorageStatus(ss)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: No", s)
        self.failUnlessIn("Total disk space: ?", s)
        self.failUnlessIn("Space Available to Tahoe: ?", s)
        self.failUnlessEqual(ss.get_available_space(), 0)

    def test_readonly(self):
        basedir = "storage/WebStatus/readonly"
        fileutil.make_dirs(basedir)
        ss = StorageServer(basedir, "\x00" * 20, readonly_storage=True)
        ss.setServiceParent(self.s)
        w = StorageStatus(ss)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: No", s)

    def test_reserved(self):
        basedir = "storage/WebStatus/reserved"
        fileutil.make_dirs(basedir)
        ss = StorageServer(basedir, "\x00" * 20, reserved_space=10e6)
        ss.setServiceParent(self.s)
        w = StorageStatus(ss)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Reserved space: - 10.00 MB (10000000)", s)

    def test_huge_reserved(self):
        basedir = "storage/WebStatus/reserved"
        fileutil.make_dirs(basedir)
        ss = StorageServer(basedir, "\x00" * 20, reserved_space=10e6)
        ss.setServiceParent(self.s)
        w = StorageStatus(ss)
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

