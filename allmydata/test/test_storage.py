
import os
import random

from twisted.trial import unittest
from twisted.application import service
from twisted.internet import defer
from twisted.python import log
from foolscap import Tub
from foolscap.eventual import flushEventualQueue

from allmydata import client

class StorageTest(unittest.TestCase):

    def setUp(self):
        self.svc = service.MultiService()
        self.node = client.Client('')
        self.node.setServiceParent(self.svc)
        self.tub = Tub()
        self.tub.setServiceParent(self.svc)
        return self.svc.startService()

    def test_create_bucket(self):
        """
        checks that the storage server can return bucket data accurately.
        """
        vid = os.urandom(20)
        bnum = random.randint(0,100)
        data = os.urandom(random.randint(1024, 16384))

        rssd = self.tub.getReference(self.node.my_pburl)
        def get_storageserver(node):
            return node.callRemote('get_service', name='storageserver')
        rssd.addCallback(get_storageserver)

        def create_bucket(storageserver):
            return storageserver.callRemote('allocate_bucket',
                                            verifierid=vid,
                                            bucket_num=bnum,
                                            size=len(data),
                                            leaser=self.node.nodeid,
                                            )
        rssd.addCallback(create_bucket)

        def write_to_bucket(bucket):
            def write_some(junk, bytes):
                return bucket.callRemote('write', data=bytes)
            def finalise(junk):
                return bucket.callRemote('finalise')
            off1 = len(data) / 2
            off2 = 3 * len(data) / 4
            d = defer.succeed(None)
            d.addCallback(write_some, data[:off1])
            d.addCallback(write_some, data[off1:off2])
            d.addCallback(write_some, data[off2:])
            d.addCallback(finalise)
            return d
        rssd.addCallback(write_to_bucket)

        def get_node_again(junk):
            return self.tub.getReference(self.node.my_pburl)
        rssd.addCallback(get_node_again)
        rssd.addCallback(get_storageserver)

        def get_bucket(storageserver):
            return storageserver.callRemote('get_bucket', verifierid=vid)
        rssd.addCallback(get_bucket)

        def read_bucket(bucket):
            def check_data(bytes_read):
                self.failUnlessEqual(bytes_read, data)
            d = bucket.callRemote('read')
            d.addCallback(check_data)

            def get_bucket_num(junk):
                return bucket.callRemote('get_bucket_num')
            d.addCallback(get_bucket_num)
            def check_bucket_num(bucket_num):
                self.failUnlessEqual(bucket_num, bnum)
            d.addCallback(check_bucket_num)
            return d
        rssd.addCallback(read_bucket)

        return rssd

    def test_overwrite(self):
        """
        checks that the storage server rejects an attempt to write to much data
        """
        vid = os.urandom(20)
        bnum = random.randint(0,100)
        data = os.urandom(random.randint(1024, 16384))

        rssd = self.tub.getReference(self.node.my_pburl)
        def get_storageserver(node):
            return node.callRemote('get_service', name='storageserver')
        rssd.addCallback(get_storageserver)

        def create_bucket(storageserver):
            return storageserver.callRemote('allocate_bucket',
                                            verifierid=vid,
                                            bucket_num=bnum,
                                            size=len(data),
                                            leaser=self.node.nodeid,
                                            )
        rssd.addCallback(create_bucket)

        def write_to_bucket(bucket):
            def write_some(junk, bytes):
                return bucket.callRemote('write', data=bytes)
            def finalise(junk):
                return bucket.callRemote('finalise')
            off1 = len(data) / 2
            off2 = 3 * len(data) / 4
            d = defer.succeed(None)
            d.addCallback(write_some, data[:off1])
            d.addCallback(write_some, data[off1:off2])
            d.addCallback(write_some, data[off2:])
            # and then overwrite
            d.addCallback(write_some, data[off1:off2])
            d.addCallback(finalise)
            return d
        rssd.addCallback(write_to_bucket)

        def should_fail(f):
            f.trap(AssertionError)

        rssd.addCallbacks(self.fail, should_fail)
        return rssd

    def tearDown(self):
        d = self.svc.stopService()
        d.addCallback(lambda res: flushEventualQueue())
        return d
