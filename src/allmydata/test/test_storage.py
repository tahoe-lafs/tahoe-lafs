
from twisted.trial import unittest

from twisted.application import service
from foolscap import Referenceable
import os.path
from allmydata import storageserver
from allmydata.util import fileutil


class Bucket(unittest.TestCase):
    def make_workdir(self, name):
        basedir = os.path.join("test_storage", "Bucket", name)
        incoming = os.path.join(basedir, "tmp", "bucket")
        final = os.path.join(basedir, "bucket")
        fileutil.make_dirs(basedir)
        return incoming, final

    def bucket_writer_closed(self, bw, consumed):
        pass

    def test_create(self):
        incoming, final = self.make_workdir("test_create")
        bw = storageserver.BucketWriter(self, incoming, final, 25, 57)
        bw.remote_put_block(0, "a"*25)
        bw.remote_put_block(1, "b"*25)
        bw.remote_put_block(2, "c"*7) # last block may be short
        bw.remote_close()

    def test_readwrite(self):
        incoming, final = self.make_workdir("test_readwrite")
        bw = storageserver.BucketWriter(self, incoming, final, 25, 57)
        bw.remote_put_block(0, "a"*25)
        bw.remote_put_block(1, "b"*25)
        bw.remote_put_block(2, "c"*7) # last block may be short
        bw.remote_put_block_hashes(["1"*32, "2"*32, "3"*32, "4"*32])
        bw.remote_put_share_hashes([(5, "5"*32), (6, "6"*32)])
        bw.remote_close()

        # now read from it
        br = storageserver.BucketReader(final)
        self.failUnlessEqual(br.remote_get_block(0), "a"*25)
        self.failUnlessEqual(br.remote_get_block(1), "b"*25)
        self.failUnlessEqual(br.remote_get_block(2), "c"*7)
        self.failUnlessEqual(br.remote_get_block_hashes(),
                             ["1"*32, "2"*32, "3"*32, "4"*32])
        self.failUnlessEqual(br.remote_get_share_hashes(),
                             [(5, "5"*32), (6, "6"*32)])

class Server(unittest.TestCase):

    def setUp(self):
        self.sparent = service.MultiService()
    def tearDown(self):
        return self.sparent.stopService()

    def workdir(self, name):
        basedir = os.path.join("storage", "Server", name)
        return basedir

    def create(self, name, sizelimit=None):
        workdir = self.workdir(name)
        ss = storageserver.StorageServer(workdir, sizelimit)
        ss.setServiceParent(self.sparent)
        return ss

    def test_create(self):
        ss = self.create("test_create")

    def test_allocate(self):
        ss = self.create("test_allocate")

        self.failUnlessEqual(ss.remote_get_buckets("vid"), {})

        canary = Referenceable()
        already,writers = ss.remote_allocate_buckets("vid", [0,1,2],
                                                     75, 25, canary)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

        # while the buckets are open, they should not count as readable
        self.failUnlessEqual(ss.remote_get_buckets("vid"), {})

        for i,wb in writers.items():
            wb.remote_put_block(0, "%25d" % i)
            wb.remote_close()

        # now they should be readable
        b = ss.remote_get_buckets("vid")
        self.failUnlessEqual(set(b.keys()), set([0,1,2]))
        self.failUnlessEqual(b[0].remote_get_block(0),
                             "%25d" % 0)

        # now if we about writing again, the server should offer those three
        # buckets as already present
        already,writers = ss.remote_allocate_buckets("vid", [0,1,2,3,4],
                                                     75, 25, canary)
        self.failUnlessEqual(already, set([0,1,2]))
        self.failUnlessEqual(set(writers.keys()), set([3,4]))

        # while those two buckets are open for writing, the server should
        # tell new uploaders that they already exist (so that we don't try to
        # upload into them a second time)

        already,writers = ss.remote_allocate_buckets("vid", [2,3,4,5],
                                                     75, 25, canary)
        self.failUnlessEqual(already, set([2,3,4]))
        self.failUnlessEqual(set(writers.keys()), set([5]))

    def test_sizelimits(self):
        ss = self.create("test_sizelimits", 100)
        canary = Referenceable()
        
        already,writers = ss.remote_allocate_buckets("vid1", [0,1,2],
                                                     25, 5, canary)
        self.failUnlessEqual(len(writers), 3)
        # now the StorageServer should have 75 bytes provisionally allocated,
        # allowing only 25 more to be claimed

        already2,writers2 = ss.remote_allocate_buckets("vid2", [0,1,2],
                                                       25, 5, canary)
        self.failUnlessEqual(len(writers2), 1)

        # we abandon the first set, so their provisional allocation should be
        # returned
        del already
        del writers

        # and we close the second set, so their provisional allocation should
        # become real, long-term allocation
        for bw in writers2.values():
            bw.remote_close()
        del already2
        del writers2
        del bw

        # now there should be 25 bytes allocated, and 75 free
        already3,writers3 = ss.remote_allocate_buckets("vid3", [0,1,2,3],
                                                       25, 5, canary)
        self.failUnlessEqual(len(writers3), 3)

        del already3
        del writers3
        ss.disownServiceParent()
        del ss

        # creating a new StorageServer in the same directory should see the
        # same usage. note that metadata will be counted at startup but not
        # during runtime, so if we were creating any metadata, the allocation
        # would be more than 25 bytes and this test would need to be changed.
        ss = self.create("test_sizelimits", 100)
        already4,writers4 = ss.remote_allocate_buckets("vid4", [0,1,2,3],
                                                       25, 5, canary)
        self.failUnlessEqual(len(writers4), 3)
