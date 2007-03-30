
from twisted.trial import unittest
from twisted.python import log
from twisted.python.failure import Failure
from twisted.internet import defer
from cStringIO import StringIO

from foolscap import eventual

from allmydata import upload
from allmydata.uri import unpack_uri

from test_encode import FakePeer

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
    def allocate_buckets(self, verifierid, sharenums, shareize, blocksize, canary):
        if self.mode == "full":
            return (set(), {},)
        elif self.mode == "already got them":
            return (set(sharenums), {},)
        else:
            return (set(), dict([(shnum, FakePeer(),) for shnum in sharenums]),)

class FakeClient:
    def __init__(self, mode="good"):
        self.mode = mode
    def get_permuted_peers(self, verifierid):
        return [ ("%20d"%fakeid, "%20d"%fakeid, FakeStorageServer(self.mode),) for fakeid in range(50) ]

class GoodServer(unittest.TestCase):
    def setUp(self):
        self.node = FakeClient(mode="good")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _check(self, uri):
        self.failUnless(isinstance(uri, str))
        self.failUnless(uri.startswith("URI:"))
        codec_name, codec_params, verifierid = unpack_uri(uri)
        self.failUnless(isinstance(verifierid, str))
        self.failUnlessEqual(len(verifierid), 20)
        self.failUnless(isinstance(codec_params, str))
        peers = self.node.peers
        self.failUnlessEqual(peers[0].allocated_size,
                             len(peers[0].data))
    def testData(self):
        data = "This is some data to upload"
        d = self.u.upload_data(data)
        d.addCallback(self._check)
        return d

    def testFileHandle(self):
        data = "This is some data to upload"
        d = self.u.upload_filehandle(StringIO(data))
        d.addCallback(self._check)
        return d

    def testFilename(self):
        fn = "Uploader-testFilename.data"
        f = open(fn, "w")
        data = "This is some data to upload"
        f.write(data)
        f.close()
        d = self.u.upload_filename(fn)
        d.addCallback(self._check)
        return d

class FullServer(unittest.TestCase):
    def setUp(self):
        self.node = FakeClient(mode="full")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _should_fail(self, f):
        self.failUnless(isinstance(f, Failure) and f.check(upload.NotEnoughPeersError))

    def testData(self):
        data = "This is some data to upload"
        d = self.u.upload_data(data)
        d.addBoth(self._should_fail)
        return d

