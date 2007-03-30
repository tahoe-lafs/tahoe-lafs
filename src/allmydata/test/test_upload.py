
from twisted.trial import unittest
from twisted.internet import defer
from cStringIO import StringIO

from allmydata import upload
from allmydata.uri import unpack_uri

class FakeStorageServer:
    pass

class FakeClient:
    def get_permuted_peers(self, verifierid):
        return [ ("%20d"%fakeid, "%20d"%fakeid, FakeStorageServer(),) for fakeid in range(50) ]

class Uploader(unittest.TestCase):
    def setUp(self):
        self.node = FakeClient()
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
