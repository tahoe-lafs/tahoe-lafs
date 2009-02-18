from twisted.trial import unittest

from twisted.internet import defer
import time, os.path, stat
import itertools
from allmydata import interfaces
from allmydata.util import fileutil, hashutil, base32
from allmydata.storage.server import StorageServer, storage_index_to_dir
from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.immutable import BucketWriter, BucketReader
from allmydata.storage.common import DataTooLargeError
from allmydata.storage.lease import LeaseInfo
from allmydata.immutable.layout import WriteBucketProxy, WriteBucketProxy_v2, \
     ReadBucketProxy
from allmydata.interfaces import BadWriteEnablerError
from allmydata.test.common import LoggingServiceParent

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
        self.failUnless(interfaces.IStorageBucketWriter.providedBy(bp))

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
            self.failUnless("to peer" in repr(rbp))
            self.failUnless(interfaces.IStorageBucketReader.providedBy(rbp))

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
    def stat_disk(self, d):
        return self.DISKAVAIL

class Server(unittest.TestCase):

    def setUp(self):
        self.sparent = LoggingServiceParent()
        self._lease_secret = itertools.count()
    def tearDown(self):
        return self.sparent.stopService()

    def workdir(self, name):
        basedir = os.path.join("storage", "Server", name)
        return basedir

    def create(self, name, reserved_space=0, klass=StorageServer):
        workdir = self.workdir(name)
        ss = klass(workdir, reserved_space=reserved_space,
                   stats_provider=FakeStatsProvider())
        ss.setNodeID("\x00" * 20)
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        ss = self.create("test_create")

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
        self.failIf(os.path.exists(incoming_bucket_dir))
        self.failIf(os.path.exists(incoming_prefix_dir))
        self.failUnless(os.path.exists(incoming_dir))

    def test_allocate(self):
        ss = self.create("test_allocate")

        self.failUnlessEqual(ss.remote_get_buckets("allocate"), {})

        canary = FakeCanary()
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
        # the FakeDiskStorageServer doesn't do real statvfs() calls
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
        ss = StorageServer(workdir, readonly_storage=True)
        ss.setNodeID("\x00" * 20)
        ss.setServiceParent(self.sparent)

        already,writers = self.allocate(ss, "vid", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(writers, {})

        stats = ss.get_stats()
        self.failUnlessEqual(stats["storage_server.accepting_immutable_shares"],
                             False)
        if "storage_server.disk_avail" in stats:
            # windows does not have os.statvfs, so it doesn't give us disk
            # stats. But if there are stats, readonly_storage means
            # disk_avail=0
            self.failUnlessEqual(stats["storage_server.disk_avail"], 0)

    def test_discard(self):
        # discard is really only used for other tests, but we test it anyways
        workdir = self.workdir("test_discard")
        ss = StorageServer(workdir, discard_storage=True)
        ss.setNodeID("\x00" * 20)
        ss.setServiceParent(self.sparent)

        canary = FakeCanary()
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
        ss = StorageServer(workdir, discard_storage=True)
        ss.setNodeID("\x00" * 20)
        ss.setServiceParent(self.sparent)

        si0_s = base32.b2a("si0")
        ss.remote_advise_corrupt_share("immutable", "si0", 0,
                                       "This share smells funny.\n")
        reportdir = os.path.join(workdir, "corruption-advisories")
        reports = os.listdir(reportdir)
        self.failUnlessEqual(len(reports), 1)
        report_si0 = reports[0]
        self.failUnless(si0_s in report_si0, report_si0)
        f = open(os.path.join(reportdir, report_si0), "r")
        report = f.read()
        f.close()
        self.failUnless("type: immutable" in report)
        self.failUnless(("storage_index: %s" % si0_s) in report)
        self.failUnless("share_number: 0" in report)
        self.failUnless("This share smells funny." in report)

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
        self.failUnless("type: immutable" in report)
        self.failUnless(("storage_index: %s" % si1_s) in report)
        self.failUnless("share_number: 1" in report)
        self.failUnless("This share tastes like dust." in report)



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
        ss = StorageServer(workdir)
        ss.setServiceParent(self.sparent)
        ss.setNodeID("\x00" * 20)
        return ss

    def test_create(self):
        ss = self.create("test_create")

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

    def test_container_size(self):
        ss = self.create("test_container_size")
        self.allocate(ss, "si1", "we1", self._lease_secret.next(),
                      set([0,1,2]), 100)
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
        # the moment this doesn't actually affect the share
        answer = rstaraw("si1", secrets,
                         {0: ([], [(0,data)], len(data)+8)},
                         [])
        self.failUnlessEqual(answer, (True, {0:[],1:[],2:[]}) )

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
        self.failUnless("The write enabler was recorded by nodeid 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'." in f, f)

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
            num_a, a = leases_a[i]
            num_b, b = leases_b[i]
            self.failUnlessEqual(num_a, num_b)
            self.failUnlessEqual(a.owner_num,       b.owner_num)
            self.failUnlessEqual(a.renew_secret,    b.renew_secret)
            self.failUnlessEqual(a.cancel_secret,   b.cancel_secret)
            self.failUnlessEqual(a.nodeid,          b.nodeid)

    def compare_leases(self, leases_a, leases_b):
        self.failUnlessEqual(len(leases_a), len(leases_b))
        for i in range(len(leases_a)):
            num_a, a = leases_a[i]
            num_b, b = leases_b[i]
            self.failUnlessEqual(num_a, num_b)
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
        self.failUnlessEqual(len(s0.debug_get_leases()), 1)

        # add-lease on a missing storage index is silently ignored
        self.failUnlessEqual(ss.remote_add_lease("si18", "", ""), None)

        # re-allocate the slots and use the same secrets, that should update
        # the lease
        write("si1", secrets(0), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(len(s0.debug_get_leases()), 1)

        # renew it directly
        ss.remote_renew_lease("si1", secrets(0)[1])
        self.failUnlessEqual(len(s0.debug_get_leases()), 1)

        # now allocate them with a bunch of different secrets, to trigger the
        # extended lease code. Use add_lease for one of them.
        write("si1", secrets(1), {0: ([], [(0,data)], None)}, [])
        self.failUnlessEqual(len(s0.debug_get_leases()), 2)
        secrets2 = secrets(2)
        ss.remote_add_lease("si1", secrets2[1], secrets2[2])
        self.failUnlessEqual(len(s0.debug_get_leases()), 3)
        write("si1", secrets(3), {0: ([], [(0,data)], None)}, [])
        write("si1", secrets(4), {0: ([], [(0,data)], None)}, [])
        write("si1", secrets(5), {0: ([], [(0,data)], None)}, [])

        self.failUnlessEqual(len(s0.debug_get_leases()), 6)

        # cancel one of them
        ss.remote_cancel_lease("si1", secrets(5)[2])
        self.failUnlessEqual(len(s0.debug_get_leases()), 5)

        all_leases = s0.debug_get_leases()
        # and write enough data to expand the container, forcing the server
        # to move the leases
        write("si1", secrets(0),
              {0: ([], [(0,data)], 200), },
              [])

        # read back the leases, make sure they're still intact.
        self.compare_leases_without_timestamps(all_leases,
                                               s0.debug_get_leases())

        ss.remote_renew_lease("si1", secrets(0)[1])
        ss.remote_renew_lease("si1", secrets(1)[1])
        ss.remote_renew_lease("si1", secrets(2)[1])
        ss.remote_renew_lease("si1", secrets(3)[1])
        ss.remote_renew_lease("si1", secrets(4)[1])
        self.compare_leases_without_timestamps(all_leases,
                                               s0.debug_get_leases())
        # get a new copy of the leases, with the current timestamps. Reading
        # data and failing to renew/cancel leases should leave the timestamps
        # alone.
        all_leases = s0.debug_get_leases()
        # renewing with a bogus token should prompt an error message

        # examine the exception thus raised, make sure the old nodeid is
        # present, to provide for share migration
        e = self.failUnlessRaises(IndexError,
                                  ss.remote_renew_lease, "si1",
                                  secrets(20)[1])
        e_s = str(e)
        self.failUnless("Unable to renew non-existent lease" in e_s)
        self.failUnless("I have leases accepted by nodeids:" in e_s)
        self.failUnless("nodeids: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ." in e_s)

        # same for cancelling
        self.failUnlessRaises(IndexError,
                              ss.remote_cancel_lease, "si1",
                              secrets(20)[2])
        self.compare_leases(all_leases, s0.debug_get_leases())

        # reading shares should not modify the timestamp
        read("si1", [], [(0,200)])
        self.compare_leases(all_leases, s0.debug_get_leases())

        write("si1", secrets(0),
              {0: ([], [(200, "make me bigger")], None)}, [])
        self.compare_leases_without_timestamps(all_leases,
                                               s0.debug_get_leases())

        write("si1", secrets(0),
              {0: ([], [(500, "make me really bigger")], None)}, [])
        self.compare_leases_without_timestamps(all_leases,
                                               s0.debug_get_leases())

        # now cancel them all
        ss.remote_cancel_lease("si1", secrets(0)[2])
        ss.remote_cancel_lease("si1", secrets(1)[2])
        ss.remote_cancel_lease("si1", secrets(2)[2])
        ss.remote_cancel_lease("si1", secrets(3)[2])

        # the slot should still be there
        remaining_shares = read("si1", [], [(0,10)])
        self.failUnlessEqual(len(remaining_shares), 1)
        self.failUnlessEqual(len(s0.debug_get_leases()), 1)

        # cancelling a non-existent lease should raise an IndexError
        self.failUnlessRaises(IndexError,
                              ss.remote_cancel_lease, "si1", "nonsecret")

        # and the slot should still be there
        remaining_shares = read("si1", [], [(0,10)])
        self.failUnlessEqual(len(remaining_shares), 1)
        self.failUnlessEqual(len(s0.debug_get_leases()), 1)

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
        self.failUnless(os.path.exists(prefixdir))
        self.failIf(os.path.exists(bucketdir))

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
        ss = StorageServer(workdir)
        ss.setNodeID("\x00" * 20)
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
        self.failUnless(abs(output["allocate"]["mean"] - 9500) < 1)
        self.failUnless(abs(output["allocate"]["01_0_percentile"] - 9010) < 1)
        self.failUnless(abs(output["allocate"]["10_0_percentile"] - 9100) < 1)
        self.failUnless(abs(output["allocate"]["50_0_percentile"] - 9500) < 1)
        self.failUnless(abs(output["allocate"]["90_0_percentile"] - 9900) < 1)
        self.failUnless(abs(output["allocate"]["95_0_percentile"] - 9950) < 1)
        self.failUnless(abs(output["allocate"]["99_0_percentile"] - 9990) < 1)
        self.failUnless(abs(output["allocate"]["99_9_percentile"] - 9999) < 1)

        self.failUnlessEqual(len(ss.latencies["renew"]), 1000)
        self.failUnless(abs(output["renew"]["mean"] - 500) < 1)
        self.failUnless(abs(output["renew"]["01_0_percentile"] -  10) < 1)
        self.failUnless(abs(output["renew"]["10_0_percentile"] - 100) < 1)
        self.failUnless(abs(output["renew"]["50_0_percentile"] - 500) < 1)
        self.failUnless(abs(output["renew"]["90_0_percentile"] - 900) < 1)
        self.failUnless(abs(output["renew"]["95_0_percentile"] - 950) < 1)
        self.failUnless(abs(output["renew"]["99_0_percentile"] - 990) < 1)
        self.failUnless(abs(output["renew"]["99_9_percentile"] - 999) < 1)

        self.failUnlessEqual(len(ss.latencies["cancel"]), 10)
        self.failUnless(abs(output["cancel"]["mean"] - 9) < 1)
        self.failUnless(abs(output["cancel"]["01_0_percentile"] -  0) < 1)
        self.failUnless(abs(output["cancel"]["10_0_percentile"] -  2) < 1)
        self.failUnless(abs(output["cancel"]["50_0_percentile"] - 10) < 1)
        self.failUnless(abs(output["cancel"]["90_0_percentile"] - 18) < 1)
        self.failUnless(abs(output["cancel"]["95_0_percentile"] - 18) < 1)
        self.failUnless(abs(output["cancel"]["99_0_percentile"] - 18) < 1)
        self.failUnless(abs(output["cancel"]["99_9_percentile"] - 18) < 1)

        self.failUnlessEqual(len(ss.latencies["get"]), 1)
        self.failUnless(abs(output["get"]["mean"] - 5) < 1)
        self.failUnless(abs(output["get"]["01_0_percentile"] - 5) < 1)
        self.failUnless(abs(output["get"]["10_0_percentile"] - 5) < 1)
        self.failUnless(abs(output["get"]["50_0_percentile"] - 5) < 1)
        self.failUnless(abs(output["get"]["90_0_percentile"] - 5) < 1)
        self.failUnless(abs(output["get"]["95_0_percentile"] - 5) < 1)
        self.failUnless(abs(output["get"]["99_0_percentile"] - 5) < 1)
        self.failUnless(abs(output["get"]["99_9_percentile"] - 5) < 1)
