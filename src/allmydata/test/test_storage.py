
from twisted.trial import unittest

import os.path
from allmydata import storageserver
from allmydata.util import fileutil

class Storage(unittest.TestCase):
    def make_workdir(self, name):
        basedir = os.path.join("test_storage", "Storage", name)
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

