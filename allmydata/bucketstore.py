import os

from foolscap import Referenceable
from twisted.application import service
from twisted.python.failure import Failure
from allmydata.util import idlib

from amdlib.util.assertutil import precondition

class NoSuchBucketError(Failure):
    pass

class BucketStore(service.MultiService, Referenceable):
    def __init__(self, store_dir):
        precondition(os.path.isdir(store_dir))
        service.MultiService.__init__(self)
        self._store_dir = store_dir

        self._buckets = {} # v_id -> Bucket()
        self._leases = set() # should do weakref dances.

    def _get_bucket_dir(self, verifierid):
        avid = idlib.b2a(verifierid)
        return os.path.join(self._store_dir, avid)

    def has_bucket(self, verifierid):
        return os.path.exists(self._get_bucket_dir(verifierid))

    def allocate_bucket(self, verifierid, bucket_num, size, leaser_credentials):
        bucket_dir = self._get_bucket_dir(verifierid)
        precondition(not os.path.exists(bucket_dir))
        precondition(isinstance(bucket_num, int))
        bucket = Bucket(bucket_dir, verifierid, bucket_num, size)
        self._buckets[verifierid] = bucket
        bucket.set_leaser(leaser_credentials)
        lease = Lease(verifierid, leaser_credentials, bucket)
        self._leases.add(lease)
        return lease

    def get_bucket(self, verifierid):
        # for now, only returns those created by this process, in this run
        bucket = self._buckets.get(verifierid)
        if bucket:
            precondition(bucket.is_complete())
            return BucketReader(bucket)
        elif os.path.exists(self._get_bucket_dir(verifierid)):
            bucket_dir = self._get_bucket_dir(verifierid)
            bucket = Bucket(bucket_dir, verifierid, None, None)
            return BucketReader(bucket)
        else:
            return NoSuchBucketError()

class Lease(Referenceable):
    def __init__(self, verifierid, leaser, bucket):
        self._leaser = leaser
        self._verifierid = verifierid
        self._bucket = bucket

    def get_bucket(self):
        return self._bucket

    def remote_write(self, data):
        self._bucket.write(data)

    def remote_finalise(self):
        self._bucket.finalise()

class BucketReader(Referenceable):
    def __init__(self, bucket):
        self._bucket = bucket

    def remote_get_bucket_num(self):
        return self._bucket.get_bucket_num()

    def remote_read(self):
        return self._bucket.read()

class Bucket:
    def __init__(self, bucket_dir, verifierid, bucket_num, size):
        if not os.path.isdir(bucket_dir):
            os.mkdir(bucket_dir)
        self._bucket_dir = bucket_dir
        self._verifierid = verifierid

        if size is not None:
            self._size = size
            self._data = file(os.path.join(self._bucket_dir, 'data'), 'wb')
            self._bytes_written = 0
        else:
            precondition(os.path.exists(os.path.join(self._bucket_dir, 'closed')))
            self._size = os.path.getsize(os.path.join(self._bucket_dir, 'data'))
            self._bytes_written = self._size

        if bucket_num is not None:
            self._write_attr('bucket_num', str(bucket_num))
        #else:
            #bucket_num = int(self._read_attr('bucket_num'))

    def _write_attr(self, name, val):
        f = file(os.path.join(self._bucket_dir, name), 'wb')
        f.write(val)
        f.close()

    def _read_attr(self, name):
        f = file(os.path.join(self._bucket_dir, name), 'rb')
        data = f.read()
        f.close()
        return data

    def set_leaser(self, leaser):
        f = file(os.path.join(self._bucket_dir, 'leases'), 'wb')
        f.write(leaser)
        f.close()

    def write(self, data):
        precondition(len(data) + self._bytes_written <= self._size)
        self._data.write(data)
        self._data.flush()
        self._bytes_written += len(data)

    def finalise(self):
        precondition(self._bytes_written == self._size)
        self._data.close()
        self._write_attr('closed', '')

    def is_complete(self):
        return os.path.getsize(os.path.join(self._bucket_dir, 'data')) == self._size

    def get_bucket_num(self):
        return int(self._read_attr('bucket_num'))

    def read(self):
        precondition(self.is_complete())
        f = file(os.path.join(self._bucket_dir, 'data'), 'rb')
        data = f.read()
        f.close()
        return data

