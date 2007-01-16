
from twisted.trial import unittest

from allmydata import client

class Basic(unittest.TestCase):
    def test_loadable(self):
        c = client.Client("")
        c.startService()
        return c.stopService()

    def test_permute(self):
        c = client.Client("")
        c.all_peers = ["%d" % i for i in range(5)]
        self.failUnlessEqual(c.permute_peerids("one"), ['3','1','0','4','2'])
        self.failUnlessEqual(c.permute_peerids("one", 3), ['3','1','0'])
        self.failUnlessEqual(c.permute_peerids("two"), ['0','4','2','1','3'])
        c.all_peers = []
        self.failUnlessEqual(c.permute_peerids("one"), [])

        c2 = client.Client("")
        c2.all_peers = ["%d" % i for i in range(5)]
        self.failUnlessEqual(c2.permute_peerids("one"), ['3','1','0','4','2'])

