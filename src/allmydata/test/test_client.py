
from twisted.trial import unittest

from allmydata import client

class Basic(unittest.TestCase):
    def test_loadable(self):
        c = client.Client("")
        d = c.startService()
        d.addCallback(lambda res: c.stopService())
        return d

    def test_permute(self):
        c = client.Client("")
        for k in ["%d" % i for i in range(5)]:
            c.connections[k] = None
        self.failUnlessEqual(c.permute_peerids("one"), ['3','1','0','4','2'])
        self.failUnlessEqual(c.permute_peerids("one", 3), ['3','1','0'])
        self.failUnlessEqual(c.permute_peerids("two"), ['0','4','2','1','3'])
        c.connections.clear()
        self.failUnlessEqual(c.permute_peerids("one"), [])

        c2 = client.Client("")
        for k in ["%d" % i for i in range(5)]:
            c2.connections[k] = None
        self.failUnlessEqual(c2.permute_peerids("one"), ['3','1','0','4','2'])

