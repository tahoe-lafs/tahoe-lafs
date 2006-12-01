
from twisted.trial import unittest
from twisted.internet import defer

from allmydata import upload

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
                self.peers.append(FakePeer(peerid, r))

    def permute_peerids(self, key, max_peers):
        assert max_peers == None
        return range(len(self.peers))
    def get_remote_service(self, peerid, name):
        peer = self.peers[peerid]
        if not peer:
            return defer.fail(IndexError("no connection to that peer"))
        return defer.succeed(peer)

class NextPeerUploader(upload.Uploader):
    def _got_all_peers(self, res):
        return res

class NextPeer(unittest.TestCase):
    responses = ["good", # 0
                 "full", # 1
                 "full", # 2
                 "disconnected", # 3
                 "good", # 4
                 ]

    def test_0(self):
        c = FakeClient([])
        u = NextPeerUploader(c)
        u._verifierid = "verifierid"
        u._shares = 2
        u._share_size = 100
        d = u.start()
        def _check(f):
            f.trap(upload.NotEnoughPeersError)
        d.addCallbacks(lambda res: self.fail("this was supposed to fail"),
                       _check)
        return d

    def test_1(self):
        c = FakeClient(self.responses)
        u = NextPeerUploader(c)
        u._verifierid = "verifierid"
        u._shares = 2
        u._share_size = 100
        d = u.start()
        def _check(res):
            self.failUnlessEqual(u.goodness_points, 2)
            self.failUnlessEqual(u.landlords,
                                 [(0, 0, c.peers[0]),
                                  (4, 1, c.peers[4]),
                                  ])
        d.addCallback(_check)
        return d

    def test_2(self):
        c = FakeClient(self.responses)
        u = NextPeerUploader(c)
        u._verifierid = "verifierid"
        u._shares = 3
        u._share_size = 100
        d = u.start()
        def _check(res):
            self.failUnlessEqual(u.goodness_points, 3)
            self.failUnlessEqual(u.landlords,
                                 [(0, 0, c.peers[0]),
                                  (4, 1, c.peers[4]),
                                  (0, 2, c.peers[0]),
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
        u._verifierid = "verifierid"
        u._shares = 3
        u._share_size = 100
        d = u.start()
        def _check(res):
            self.failUnlessEqual(u.goodness_points, 3)
            self.failUnlessEqual(u.landlords,
                                 [(0, 0, c.peers[0]),
                                  (3, 1, c.peers[3]),
                                  (0, 2, c.peers[0]),
                                  ])
        d.addCallback(_check)
        return d

