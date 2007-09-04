
from twisted.trial import unittest

from twisted.application import service
from twisted.internet import defer
from foolscap import Referenceable
import time, os.path, stat
import itertools
from allmydata import interfaces
from allmydata.util import fileutil, hashutil
from allmydata.storage import BucketWriter, BucketReader, \
     WriteBucketProxy, ReadBucketProxy, StorageServer

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

    def make_lease(self):
        owner_num = 0
        renew_secret = os.urandom(32)
        cancel_secret = os.urandom(32)
        expiration_time = time.time() + 5000
        return (owner_num, renew_secret, cancel_secret, expiration_time)

    def test_create(self):
        incoming, final = self.make_workdir("test_create")
        bw = BucketWriter(self, incoming, final, 200, self.make_lease())
        bw.remote_write(0, "a"*25)
        bw.remote_write(25, "b"*25)
        bw.remote_write(50, "c"*25)
        bw.remote_write(75, "d"*7)
        bw.remote_close()

    def test_readwrite(self):
        incoming, final = self.make_workdir("test_readwrite")
        bw = BucketWriter(self, incoming, final, 200, self.make_lease())
        bw.remote_write(0, "a"*25)
        bw.remote_write(25, "b"*25)
        bw.remote_write(50, "c"*7) # last block may be short
        bw.remote_close()

        # now read from it
        br = BucketReader(final)
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
        bw = BucketWriter(self, incoming, final, size, self.make_lease())
        rb = RemoteBucket()
        rb.target = bw
        return bw, rb, final

    def make_lease(self):
        owner_num = 0
        renew_secret = os.urandom(32)
        cancel_secret = os.urandom(32)
        expiration_time = time.time() + 5000
        return (owner_num, renew_secret, cancel_secret, expiration_time)

    def bucket_writer_closed(self, bw, consumed):
        pass

    def test_create(self):
        bw, rb, final = self.make_bucket("test_create", 500)
        bp = WriteBucketProxy(rb,
                              data_size=300,
                              segment_size=10,
                              num_segments=5,
                              num_share_hashes=3,
                              uri_extension_size=500)
        self.failUnless(interfaces.IStorageBucketWriter.providedBy(bp))

    def test_readwrite(self):
        # Let's pretend each share has 100 bytes of data, and that there are
        # 4 segments (25 bytes each), and 8 shares total. So the three
        # per-segment merkle trees (plaintext_hash_tree, crypttext_hash_tree,
        # block_hashes) will have 4 leaves and 7 nodes each. The per-share
        # merkle tree (share_hashes) has 8 leaves and 15 nodes, and we need 3
        # nodes. Furthermore, let's assume the uri_extension is 500 bytes
        # long. That should make the whole share:
        #
        # 0x24 + 100 + 7*32 + 7*32 + 7*32 + 3*(2+32) + 4+500 = 1414 bytes long

        plaintext_hashes = [hashutil.tagged_hash("plain", "bar%d" % i)
                            for i in range(7)]
        crypttext_hashes = [hashutil.tagged_hash("crypt", "bar%d" % i)
                            for i in range(7)]
        block_hashes = [hashutil.tagged_hash("block", "bar%d" % i)
                        for i in range(7)]
        share_hashes = [(i, hashutil.tagged_hash("share", "bar%d" % i))
                        for i in (1,9,13)]
        uri_extension = "s" + "E"*498 + "e"

        bw, rb, final = self.make_bucket("test_readwrite", 1414)
        bp = WriteBucketProxy(rb,
                              data_size=95,
                              segment_size=25,
                              num_segments=4,
                              num_share_hashes=3,
                              uri_extension_size=len(uri_extension))

        d = bp.start()
        d.addCallback(lambda res: bp.put_block(0, "a"*25))
        d.addCallback(lambda res: bp.put_block(1, "b"*25))
        d.addCallback(lambda res: bp.put_block(2, "c"*25))
        d.addCallback(lambda res: bp.put_block(3, "d"*20))
        d.addCallback(lambda res: bp.put_plaintext_hashes(plaintext_hashes))
        d.addCallback(lambda res: bp.put_crypttext_hashes(crypttext_hashes))
        d.addCallback(lambda res: bp.put_block_hashes(block_hashes))
        d.addCallback(lambda res: bp.put_share_hashes(share_hashes))
        d.addCallback(lambda res: bp.put_uri_extension(uri_extension))
        d.addCallback(lambda res: bp.close())

        # now read everything back
        def _start_reading(res):
            br = BucketReader(final)
            rb = RemoteBucket()
            rb.target = br
            rbp = ReadBucketProxy(rb)
            self.failUnless(interfaces.IStorageBucketReader.providedBy(rbp))

            d1 = rbp.startIfNecessary()
            d1.addCallback(lambda res: rbp.get_block(0))
            d1.addCallback(lambda res: self.failUnlessEqual(res, "a"*25))
            d1.addCallback(lambda res: rbp.get_block(1))
            d1.addCallback(lambda res: self.failUnlessEqual(res, "b"*25))
            d1.addCallback(lambda res: rbp.get_block(2))
            d1.addCallback(lambda res: self.failUnlessEqual(res, "c"*25))
            d1.addCallback(lambda res: rbp.get_block(3))
            d1.addCallback(lambda res: self.failUnlessEqual(res, "d"*20))

            d1.addCallback(lambda res: rbp.get_plaintext_hashes())
            d1.addCallback(lambda res:
                           self.failUnlessEqual(res, plaintext_hashes))
            d1.addCallback(lambda res: rbp.get_crypttext_hashes())
            d1.addCallback(lambda res:
                           self.failUnlessEqual(res, crypttext_hashes))
            d1.addCallback(lambda res: rbp.get_block_hashes())
            d1.addCallback(lambda res: self.failUnlessEqual(res, block_hashes))
            d1.addCallback(lambda res: rbp.get_share_hashes())
            d1.addCallback(lambda res: self.failUnlessEqual(res, share_hashes))
            d1.addCallback(lambda res: rbp.get_uri_extension())
            d1.addCallback(lambda res:
                           self.failUnlessEqual(res, uri_extension))

            return d1

        d.addCallback(_start_reading)

        return d



class Server(unittest.TestCase):

    def setUp(self):
        self.sparent = service.MultiService()
        self._secret = itertools.count()
    def tearDown(self):
        return self.sparent.stopService()

    def workdir(self, name):
        basedir = os.path.join("storage", "Server", name)
        return basedir

    def create(self, name, sizelimit=None):
        workdir = self.workdir(name)
        ss = StorageServer(workdir, sizelimit)
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        ss = self.create("test_create")

    def allocate(self, ss, storage_index, sharenums, size):
        renew_secret = hashutil.tagged_hash("blah", "%d" % self._secret.next())
        cancel_secret = hashutil.tagged_hash("blah", "%d" % self._secret.next())
        return ss.remote_allocate_buckets(storage_index,
                                          renew_secret, cancel_secret,
                                          sharenums, size, Referenceable())

    def test_allocate(self):
        ss = self.create("test_allocate")

        self.failUnlessEqual(ss.remote_get_buckets("vid"), {})

        canary = Referenceable()
        already,writers = self.allocate(ss, "vid", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

        # while the buckets are open, they should not count as readable
        self.failUnlessEqual(ss.remote_get_buckets("vid"), {})

        for i,wb in writers.items():
            wb.remote_write(0, "%25d" % i)
            wb.remote_close()

        # now they should be readable
        b = ss.remote_get_buckets("vid")
        self.failUnlessEqual(set(b.keys()), set([0,1,2]))
        self.failUnlessEqual(b[0].remote_read(0, 25), "%25d" % 0)

        # now if we about writing again, the server should offer those three
        # buckets as already present
        already,writers = self.allocate(ss, "vid", [0,1,2,3,4], 75)
        self.failUnlessEqual(already, set([0,1,2]))
        self.failUnlessEqual(set(writers.keys()), set([3,4]))

        # while those two buckets are open for writing, the server should
        # tell new uploaders that they already exist (so that we don't try to
        # upload into them a second time)

        already,writers = self.allocate(ss, "vid", [2,3,4,5], 75)
        self.failUnlessEqual(already, set([2,3,4]))
        self.failUnlessEqual(set(writers.keys()), set([5]))

    def test_sizelimits(self):
        ss = self.create("test_sizelimits", 5000)
        canary = Referenceable()
        # a newly created and filled share incurs this much overhead, beyond
        # the size we request.
        OVERHEAD = 3*4
        LEASE_SIZE = 4+32+32+4

        already,writers = self.allocate(ss, "vid1", [0,1,2], 1000)
        self.failUnlessEqual(len(writers), 3)
        # now the StorageServer should have 3000 bytes provisionally
        # allocated, allowing only 2000 more to be claimed
        self.failUnlessEqual(len(ss._active_writers), 3)

        # allocating 1001-byte shares only leaves room for one
        already2,writers2 = self.allocate(ss, "vid2", [0,1,2], 1001)
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
        # now there should be ALLOCATED=1001+12+72=1085 bytes allocated, and
        # 5000-1085=3915 free, therefore we can fit 39 100byte shares
        already3,writers3 = self.allocate(ss,"vid3", range(100), 100)
        self.failUnlessEqual(len(writers3), 39)
        self.failUnlessEqual(len(ss._active_writers), 39)

        del already3
        del writers3
        self.failUnlessEqual(len(ss._active_writers), 0)
        ss.disownServiceParent()
        del ss

        # creating a new StorageServer in the same directory should see the
        # same usage.

        # metadata that goes into the share file is counted upon share close,
        # as well as at startup. metadata that goes into other files will not
        # be counted until the next startup, so if we were creating any
        # extra-file metadata, the allocation would be more than 'allocated'
        # and this test would need to be changed.
        ss = self.create("test_sizelimits", 5000)
        already4,writers4 = self.allocate(ss, "vid4", range(100), 100)
        self.failUnlessEqual(len(writers4), 39)
        self.failUnlessEqual(len(ss._active_writers), 39)

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
        canary = Referenceable()
        sharenums = range(5)
        size = 100

        rs0,cs0 = (hashutil.tagged_hash("blah", "%d" % self._secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._secret.next()))
        already,writers = ss.remote_allocate_buckets("si0", rs0, cs0,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 0)
        self.failUnlessEqual(len(writers), 5)
        for wb in writers.values():
            wb.remote_close()

        leases = list(ss.get_leases("si0"))
        self.failUnlessEqual(len(leases), 1)
        self.failUnlessEqual(set([l[1] for l in leases]), set([rs0]))

        rs1,cs1 = (hashutil.tagged_hash("blah", "%d" % self._secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._secret.next()))
        already,writers = ss.remote_allocate_buckets("si1", rs1, cs1,
                                                     sharenums, size, canary)
        for wb in writers.values():
            wb.remote_close()

        # take out a second lease on si1
        rs2,cs2 = (hashutil.tagged_hash("blah", "%d" % self._secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._secret.next()))
        already,writers = ss.remote_allocate_buckets("si1", rs2, cs2,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 5)
        self.failUnlessEqual(len(writers), 0)

        leases = list(ss.get_leases("si1"))
        self.failUnlessEqual(len(leases), 2)
        self.failUnlessEqual(set([l[1] for l in leases]), set([rs1, rs2]))

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


        # cancel the first lease on si1, leaving the second in place
        ss.remote_cancel_lease("si1", cs1)
        readers = ss.remote_get_buckets("si1")
        self.failUnlessEqual(len(readers), 5)
        # the corresponding renew should no longer work
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si1", rs1)

        leases = list(ss.get_leases("si1"))
        self.failUnlessEqual(len(leases), 1)
        self.failUnlessEqual(set([l[1] for l in leases]), set([rs2]))

        ss.remote_renew_lease("si1", rs2)
        # cancelling the second should make it go away
        ss.remote_cancel_lease("si1", cs2)
        readers = ss.remote_get_buckets("si1")
        self.failUnlessEqual(len(readers), 0)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si1", rs1)
        self.failUnlessRaises(IndexError, ss.remote_renew_lease, "si1", rs2)

        leases = list(ss.get_leases("si1"))
        self.failUnlessEqual(len(leases), 0)


        # test overlapping uploads
        rs3,cs3 = (hashutil.tagged_hash("blah", "%d" % self._secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._secret.next()))
        rs4,cs4 = (hashutil.tagged_hash("blah", "%d" % self._secret.next()),
                   hashutil.tagged_hash("blah", "%d" % self._secret.next()))
        already,writers = ss.remote_allocate_buckets("si3", rs3, cs3,
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 0)
        self.failUnlessEqual(len(writers), 5)
        already2,writers2 = ss.remote_allocate_buckets("si3", rs4, cs4,
                                                       sharenums, size, canary)
        self.failUnlessEqual(len(already2), 5)
        self.failUnlessEqual(len(writers2), 0)
        for wb in writers.values():
            wb.remote_close()

        leases = list(ss.get_leases("si3"))
        self.failUnlessEqual(len(leases), 2)

