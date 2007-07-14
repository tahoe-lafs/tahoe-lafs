
from twisted.trial import unittest
from twisted.python.failure import Failure
from twisted.internet import defer
from cStringIO import StringIO

from allmydata import upload, encode
from allmydata.uri import unpack_uri, unpack_lit
from allmydata.util.assertutil import precondition
from foolscap import eventual

class FakePeer:
    def __init__(self, mode="good"):
        self.ss = FakeStorageServer(mode)

    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        return defer.maybeDeferred(_call)

    def get_service(self, sname):
        assert sname == "storageserver"
        return self.ss

class FakeStorageServer:
    def __init__(self, mode):
        self.mode = mode
    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        d = eventual.fireEventually()
        d.addCallback(lambda res: _call())
        return d

    def allocate_buckets(self, crypttext_hash, sharenums,
                         share_size, canary):
        #print "FakeStorageServer.allocate_buckets(num=%d, size=%d)" % (len(sharenums), share_size)
        if self.mode == "full":
            return (set(), {},)
        elif self.mode == "already got them":
            return (set(sharenums), {},)
        else:
            return (set(),
                    dict([( shnum, FakeBucketWriter(share_size) )
                          for shnum in sharenums]),
                    )

class FakeBucketWriter:
    # a diagnostic version of storageserver.BucketWriter
    def __init__(self, size):
        self.data = StringIO()
        self.closed = False
        self._size = size

    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, "remote_" + methname)
            return meth(*args, **kwargs)
        d = eventual.fireEventually()
        d.addCallback(lambda res: _call())
        return d

    def remote_write(self, offset, data):
        precondition(not self.closed)
        precondition(offset >= 0)
        precondition(offset+len(data) <= self._size,
                     "offset=%d + data=%d > size=%d" %
                     (offset, len(data), self._size))
        self.data.seek(offset)
        self.data.write(data)

    def remote_close(self):
        precondition(not self.closed)
        self.closed = True

class FakeClient:
    def __init__(self, mode="good"):
        self.mode = mode
    def get_permuted_peers(self, storage_index):
        return [ ("%20d"%fakeid, "%20d"%fakeid, FakePeer(self.mode),)
                 for fakeid in range(50) ]
    def get_encoding_parameters(self):
        return None

DATA = """
Once upon a time, there was a beautiful princess named Buttercup. She lived
in a magical land where every file was stored securely among millions of
machines, and nobody ever worried about their data being lost ever again.
The End.
"""
assert len(DATA) > upload.Uploader.URI_LIT_SIZE_THRESHOLD

SIZE_ZERO = 0
SIZE_SMALL = 16
SIZE_LARGE = len(DATA)

class GoodServer(unittest.TestCase):
    def setUp(self):
        self.node = FakeClient(mode="good")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _check_small(self, uri, size):
        self.failUnless(isinstance(uri, str))
        self.failUnless(uri.startswith("URI:LIT:"))
        d = unpack_lit(uri)
        self.failUnlessEqual(len(d), size)

    def _check_large(self, uri, size):
        self.failUnless(isinstance(uri, str))
        self.failUnless(uri.startswith("URI:"))
        d = unpack_uri(uri)
        self.failUnless(isinstance(d['storage_index'], str))
        self.failUnlessEqual(len(d['storage_index']), 32)
        self.failUnless(isinstance(d['key'], str))
        self.failUnlessEqual(len(d['key']), 16)
        self.failUnlessEqual(d['size'], size)

    def get_data(self, size):
        return DATA[:size]

    def test_data_zero(self):
        data = self.get_data(SIZE_ZERO)
        d = self.u.upload_data(data)
        d.addCallback(self._check_small, SIZE_ZERO)
        return d

    def test_data_small(self):
        data = self.get_data(SIZE_SMALL)
        d = self.u.upload_data(data)
        d.addCallback(self._check_small, SIZE_SMALL)
        return d

    def test_data_large(self):
        data = self.get_data(SIZE_LARGE)
        d = self.u.upload_data(data)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_data_large_odd_segments(self):
        data = self.get_data(SIZE_LARGE)
        segsize = int(SIZE_LARGE / 2.5)
        # we want 3 segments, since that's not a power of two
        d = self.u.upload_data(data, {"max_segment_size": segsize})
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_filehandle_zero(self):
        data = self.get_data(SIZE_ZERO)
        d = self.u.upload_filehandle(StringIO(data))
        d.addCallback(self._check_small, SIZE_ZERO)
        return d

    def test_filehandle_small(self):
        data = self.get_data(SIZE_SMALL)
        d = self.u.upload_filehandle(StringIO(data))
        d.addCallback(self._check_small, SIZE_SMALL)
        return d

    def test_filehandle_large(self):
        data = self.get_data(SIZE_LARGE)
        d = self.u.upload_filehandle(StringIO(data))
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_filename_zero(self):
        fn = "Uploader-test_filename_zero.data"
        f = open(fn, "wb")
        data = self.get_data(SIZE_ZERO)
        f.write(data)
        f.close()
        d = self.u.upload_filename(fn)
        d.addCallback(self._check_small, SIZE_ZERO)
        return d

    def test_filename_small(self):
        fn = "Uploader-test_filename_small.data"
        f = open(fn, "wb")
        data = self.get_data(SIZE_SMALL)
        f.write(data)
        f.close()
        d = self.u.upload_filename(fn)
        d.addCallback(self._check_small, SIZE_SMALL)
        return d

    def test_filename_large(self):
        fn = "Uploader-test_filename_large.data"
        f = open(fn, "wb")
        data = self.get_data(SIZE_LARGE)
        f.write(data)
        f.close()
        d = self.u.upload_filename(fn)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

class FullServer(unittest.TestCase):
    def setUp(self):
        self.node = FakeClient(mode="full")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _should_fail(self, f):
        self.failUnless(isinstance(f, Failure) and f.check(encode.NotEnoughPeersError))

    def test_data_large(self):
        data = DATA
        d = self.u.upload_data(data)
        d.addBoth(self._should_fail)
        return d


# TODO:
#  upload with exactly 75 peers (shares_of_happiness)
#  have a download fail
#  cancel a download (need to implement more cancel stuff)
