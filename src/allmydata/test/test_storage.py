
import os
import random

from twisted.trial import unittest
from twisted.application import service
from twisted.internet import defer
from foolscap import Tub, Referenceable
from foolscap.eventual import flushEventualQueue

from allmydata import client

class Canary(Referenceable):
    pass

class StorageTest(unittest.TestCase):

    def setUp(self):
        self.svc = service.MultiService()
        self.node = client.Client('')
        self.node.setServiceParent(self.svc)
        self.tub = Tub()
        self.tub.setServiceParent(self.svc)
        self.svc.startService()
        return self.node.when_tub_ready()

    def test_create_bucket(self):
        """
        Check that the storage server can return bucket data accurately.
        """
        vid = os.urandom(20)
        bnum = random.randrange(0, 256)
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
                                            canary=Canary(),
                                            )
        rssd.addCallback(create_bucket)

        def write_to_bucket(bucket):
            def write_some(junk, bytes):
                return bucket.callRemote('write', data=bytes)
            def set_metadata(junk, metadata):
                return bucket.callRemote('set_metadata', metadata)
            def finalise(junk):
                return bucket.callRemote('close')
            off1 = len(data) / 2
            off2 = 3 * len(data) / 4
            d = defer.succeed(None)
            d.addCallback(write_some, data[:off1])
            d.addCallback(write_some, data[off1:off2])
            d.addCallback(set_metadata, "metadata")
            d.addCallback(write_some, data[off2:])
            d.addCallback(finalise)
            return d
        rssd.addCallback(write_to_bucket)

        def get_node_again(junk):
            return self.tub.getReference(self.node.my_pburl)
        rssd.addCallback(get_node_again)
        rssd.addCallback(get_storageserver)

        def get_buckets(storageserver):
            return storageserver.callRemote('get_buckets', verifierid=vid)
        rssd.addCallback(get_buckets)

        def read_buckets(buckets):
            self.failUnlessEqual(len(buckets), 1)
            bucket_num, bucket = buckets[0]
            self.failUnlessEqual(bucket_num, bnum)

            def check_data(bytes_read):
                self.failUnlessEqual(bytes_read, data)
            d = bucket.callRemote('read')
            d.addCallback(check_data)

            def check_metadata(metadata):
                self.failUnlessEqual(metadata, 'metadata')
            d.addCallback(lambda res: bucket.callRemote('get_metadata'))
            d.addCallback(check_metadata)
            return d
        rssd.addCallback(read_buckets)

        return rssd

    def test_overwrite(self):
        """
        Check that the storage server rejects an attempt to write too much data.
        """
        vid = os.urandom(20)
        bnum = random.randrange(0, 256)
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
                                            canary=Canary(),
                                            )
        rssd.addCallback(create_bucket)

        def write_to_bucket(bucket):
            def write_some(junk, bytes):
                return bucket.callRemote('write', data=bytes)
            def finalise(junk):
                return bucket.callRemote('close')
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

        self.deferredShouldFail(rssd, ftype=AssertionError)
        return rssd

    def deferredShouldFail(self, d, ftype=None, checker=None):

        def _worked(res):
            self.fail("hey, this was supposed to fail, not return %s" % res)
        if not ftype and not checker:
            d.addCallbacks(_worked,
                           lambda f: None)
        elif ftype and not checker:
            d.addCallbacks(_worked,
                           lambda f: f.trap(ftype) or None)
        else:
            d.addCallbacks(_worked,
                           checker)

    def tearDown(self):
        d = self.svc.stopService()
        d.addCallback(lambda res: flushEventualQueue())
        return d
