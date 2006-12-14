import os

from foolscap import Referenceable
from twisted.application import service

from allmydata.bucketstore import BucketStore
from zope.interface import implements
from allmydata.interfaces import RIStorageServer
from allmydata.util import idlib

class BucketAlreadyExistsError(Exception):
    pass

class StorageServer(service.MultiService, Referenceable):
    implements(RIStorageServer)
    name = 'storageserver'

    def __init__(self, store_dir):
        if not os.path.isdir(store_dir):
            os.mkdir(store_dir)
        service.MultiService.__init__(self)
        self._bucketstore = BucketStore(store_dir)
        self._bucketstore.setServiceParent(self)

    def remote_allocate_bucket(self, verifierid, bucket_num, size, leaser,
                               canary):
        if self._bucketstore.has_bucket(verifierid):
            raise BucketAlreadyExistsError()
        lease = self._bucketstore.allocate_bucket(verifierid, bucket_num, size,
                                                  idlib.b2a(leaser), canary)
        return lease

    def remote_get_buckets(self, verifierid):
        return self._bucketstore.get_buckets(verifierid)
