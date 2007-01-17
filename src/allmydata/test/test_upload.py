
from twisted.trial import unittest
from twisted.internet import defer
from cStringIO import StringIO

from allmydata import upload
from allmydata.uri import unpack_uri

class StringBucketProxy:
    # This is for unit tests: make a StringIO look like a RIBucketWriter.

    def __init__(self):
        self.data = StringIO()
        self.size = None
        self.done = False

    def callRemote(self, methname, **kwargs):
        if methname == "write":
            return defer.maybeDeferred(self.write, **kwargs)
        elif methname == "close":
            return defer.maybeDeferred(self.close, **kwargs)
        else:
            return defer.fail(NameError("no such method named %s" % methname))

    def write(self, data):
        self.data.write(data)
    def close(self):
        self.done = True


class FakePeer:
    def __init__(self, peerid, response):
        self.peerid = peerid
        self.response = response

    def callRemote(self, methname, *args, **kwargs):
        assert not args
        return defer.maybeDeferred(self._callRemote, methname, **kwargs)

    def _callRemote(self, methname, **kwargs):
        assert methname == "allocate_bucket"
        assert kwargs["size"] == 100
        assert kwargs["leaser"] == "fakeclient"
        if self.response == "good":
            return self
        raise upload.TooFullError()

class FakeClient:
    nodeid = "fakeclient"
    def __init__(self, responses):
        self.peers = []
        for peerid,r in enumerate(responses):
            if r == "disconnected":
                self.peers.append(None)
            else:
                self.peers.append(FakePeer(str(peerid), r))

    def permute_peerids(self, key, max_peers):
        assert max_peers == None
        return [str(i) for i in range(len(self.peers))]

    def get_remote_service(self, peerid, name):
        peer = self.peers[int(peerid)]
        if not peer:
            return defer.fail(IndexError("no connection to that peer"))
        return defer.succeed(peer)


class NextPeerUploader(upload.FileUploader):
    _size = 100
    def _got_enough_peers(self, res):
        return res

class NextPeer(unittest.TestCase):
    responses = ["good", # 0
                 "full", # 1
                 "full", # 2
                 "disconnected", # 3
                 "good", # 4
                 ]

    def compare_landlords(self, u, c, expected):
        exp = [(str(peerid), bucketnum, c.peers[peerid])
               for peerid, bucketnum in expected]
        self.failUnlessEqual(u.landlords, exp)

    VERIFIERID = "\x00" * 20
    def test_0(self):
        c = FakeClient([])
        u = NextPeerUploader(c)
        u.set_verifierid(self.VERIFIERID)
        u.set_params(2, 2, 2)
        d = u.start()
        def _check(f):
            f.trap(upload.NotEnoughPeersError)
        d.addCallbacks(lambda res: self.fail("this was supposed to fail"),
                       _check)
        return d

    def test_1(self):
        c = FakeClient(self.responses)
        u = NextPeerUploader(c)
        u.set_verifierid(self.VERIFIERID)
        u.set_params(2, 2, 2)
        d = u.start()
        def _check(res):
            self.failUnlessEqual(u.goodness_points, 2)
            self.compare_landlords(u, c, [(0, 0),
                                          (4, 1),
                                          ])
        d.addCallback(_check)
        return d

    def test_2(self):
        c = FakeClient(self.responses)
        u = NextPeerUploader(c)
        u.set_verifierid(self.VERIFIERID)
        u.set_params(3, 3, 3)
        d = u.start()
        def _check(res):
            self.failUnlessEqual(u.goodness_points, 3)
            self.compare_landlords(u, c, [(0, 0),
                                          (4, 1),
                                          (0, 2),
                                          ])
        d.addCallback(_check)
        return d

    responses2 = ["good", # 0
                 "full", # 1
                 "full", # 2
                 "good", # 3
                 "full", # 4
                 ]

    def test_3(self):
        c = FakeClient(self.responses2)
        u = NextPeerUploader(c)
        u.set_verifierid(self.VERIFIERID)
        u.set_params(3, 3, 3)
        d = u.start()
        def _check(res):
            self.failUnlessEqual(u.goodness_points, 3)
            self.compare_landlords(u, c, [(0, 0),
                                          (3, 1),
                                          (0, 2),
                                          ])
        d.addCallback(_check)
        return d

    responses3 = ["good", # 0
                 "good", # 1
                 "good", # 2
                 "good", # 3
                 "good", # 4
                 ]

    def test_4(self):
        c = FakeClient(self.responses3)
        u = NextPeerUploader(c)
        u.set_verifierid(self.VERIFIERID)
        u.set_params(4, 4, 4)
        d = u.start()
        def _check(res):
            self.failUnlessEqual(u.goodness_points, 4)
            self.compare_landlords(u, c, [(0, 0),
                                          (1, 1),
                                          (2, 2),
                                          (3, 3),
                                          ])
        d.addCallback(_check)
        return d


class FakePeer2:
    def __init__(self, peerid):
        self.peerid = peerid
        self.data = ""

    def callRemote(self, methname, *args, **kwargs):
        if methname == "allocate_bucket":
            return defer.maybeDeferred(self._allocate_bucket, *args, **kwargs)
        if methname == "write":
            return defer.maybeDeferred(self._write, *args, **kwargs)
        if methname == "set_metadata":
            return defer.maybeDeferred(self._set_metadata, *args, **kwargs)
        if methname == "close":
            return defer.maybeDeferred(self._close, *args, **kwargs)
        return defer.maybeDeferred(self._bad_name, methname)

    def _allocate_bucket(self, verifierid, bucket_num, size, leaser, canary):
        self.allocated_size = size
        return self
    def _write(self, data):
        self.data = self.data + data
    def _set_metadata(self, metadata):
        self.metadata = metadata
    def _close(self):
        pass
    def _bad_name(self, methname):
        raise NameError("FakePeer2 has no such method named '%s'" % methname)

class FakeClient2:
    nodeid = "fakeclient"
    def __init__(self, max_peers):
        self.peers = []
        for peerid in range(max_peers):
            self.peers.append(FakePeer2(str(peerid)))

    def permute_peerids(self, key, max_peers):
        assert max_peers == None
        return [str(i) for i in range(len(self.peers))]

    def get_remote_service(self, peerid, name):
        peer = self.peers[int(peerid)]
        if not peer:
            return defer.fail(IndexError("no connection to that peer"))
        return defer.succeed(peer)

class Uploader(unittest.TestCase):
    def setUp(self):
        node = self.node = FakeClient2(10)
        u = self.u = upload.Uploader()
        u.running = 1
        u.parent = node

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
