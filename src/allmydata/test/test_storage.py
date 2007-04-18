
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

    def test_create(self):
        incoming, final = self.make_workdir("test_create")
        bw = storageserver.BucketWriter(incoming, final, 25)
        bw.remote_put_block(0, "a"*25)
        bw.remote_put_block(1, "b"*25)
        bw.remote_put_block(2, "c"*7) # last block may be short
        bw.remote_close()

    def test_readwrite(self):
        incoming, final = self.make_workdir("test_readwrite")
        bw = storageserver.BucketWriter(incoming, final, 25)
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
        basedir = os.path.join("test_storage", "Server", name)
        return basedir

    def create(self, name):
        workdir = self.workdir(name)
        ss = storageserver.StorageServer(workdir)
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

