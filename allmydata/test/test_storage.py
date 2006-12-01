
import os
import random

from twisted.trial import unittest
from twisted.application import service
from twisted.internet import defer
from twisted.python import log
from foolscap import Tub

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
            def write_some(junk, bucket, bytes):
                return bucket.callRemote('write', data=bytes)
            def finalise(junk, bucket):
                return bucket.callRemote('finalise')
            off1 = len(data) / 2
            off2 = 3 * len(data) / 4
            d = defer.succeed(None)
            d.addCallback(write_some, bucket, data[:off1])
            d.addCallback(write_some, bucket, data[off1:off2])
            d.addCallback(write_some, bucket, data[off2:])
            d.addCallback(finalise, bucket)
            return d
        rssd.addCallback(write_to_bucket)

        return rssd

    def test_overwrite(self):
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
            def write_some(junk, bucket, bytes):
                return bucket.callRemote('write', data=bytes)
            def finalise(junk, bucket):
                return bucket.callRemote('finalise')
            off1 = len(data) / 2
            off2 = 3 * len(data) / 4
            d = defer.succeed(None)
            d.addCallback(write_some, bucket, data[:off1])
            d.addCallback(write_some, bucket, data[off1:off2])
            d.addCallback(write_some, bucket, data[off2:])
            # and then overwrite
            d.addCallback(write_some, bucket, data[off1:off2])
            d.addCallback(finalise, bucket)
            return d
        rssd.addCallback(write_to_bucket)

        def should_fail(f):
            f.trap(AssertionError)

        rssd.addCallbacks(self.fail, should_fail)
        return rssd

    def tearDown(self):
        return self.svc.stopService()
