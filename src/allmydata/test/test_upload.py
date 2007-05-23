
from twisted.trial import unittest
from twisted.python.failure import Failure
from cStringIO import StringIO

from allmydata import upload
from allmydata.uri import unpack_uri

from test_encode import FakePeer

class FakeClient:
    def __init__(self, mode="good"):
        self.mode = mode
    def get_permuted_peers(self, verifierid):
        return [ ("%20d"%fakeid, "%20d"%fakeid, FakePeer(self.mode),) for fakeid in range(50) ]

class GoodServer(unittest.TestCase):
    def setUp(self):
        self.node = FakeClient(mode="good")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _check(self, uri):
        self.failUnless(isinstance(uri, str))
        self.failUnless(uri.startswith("URI:"))
        d = unpack_uri(uri)
        self.failUnless(isinstance(d['verifierid'], str))
        self.failUnlessEqual(len(d['verifierid']), 20)
        self.failUnless(isinstance(d['fileid'], str))
        self.failUnlessEqual(len(d['fileid']), 20)
        self.failUnless(isinstance(d['key'], str))
        self.failUnlessEqual(len(d['key']), 16)
        self.failUnless(isinstance(d['codec_params'], str))

    def testData(self):
        data = "This is some data to upload"
        d = self.u.upload_data(data)
        d.addCallback(self._check)
        return d
    testData.timeout = 300

    def testFileHandle(self):
        data = "This is some data to upload"
        d = self.u.upload_filehandle(StringIO(data))
        d.addCallback(self._check)
        return d
    testFileHandle.timeout = 300

    def testFilename(self):
        fn = "Uploader-testFilename.data"
        f = open(fn, "wb")
        data = "This is some data to upload"
        f.write(data)
        f.close()
        d = self.u.upload_filename(fn)
        d.addCallback(self._check)
        return d
    testFilename.test = 300

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


# TODO:
#  upload with exactly 75 peers (shares_of_happiness)
#  have a download fail
#  cancel a download (need to implement more cancel stuff)
