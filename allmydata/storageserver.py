import os

from foolscap import Referenceable
from twisted.application import service
from twisted.python.failure import Failure

from amdlib.util.assertutil import precondition

from allmydata.bucketstore import BucketStore

class BucketAlreadyExistsError(Exception):
    pass

class StorageServer(service.MultiService, Referenceable):
    name = 'storageserver'

    def __init__(self, store_dir):
        if not os.path.isdir(store_dir):
            os.mkdir(store_dir)
        service.MultiService.__init__(self)
        self._bucketstore = BucketStore(store_dir)
        self._bucketstore.setServiceParent(self)

    def remote_allocate_bucket(self, verifierid, bucket_num, size, leaser):
        if self._bucketstore.has_bucket(verifierid):
            raise BucketAlreadyExistsError()
        lease = self._bucketstore.allocate_bucket(verifierid, bucket_num, size, leaser)
        return lease

    def remote_get_bucket(self, verifierid):
        return self._bucketstore.get_bucket(verifierid)
