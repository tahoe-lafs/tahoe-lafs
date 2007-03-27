
import os
from twisted.trial import unittest

from allmydata import client

class MyClient(client.Client):
    def __init__(self, basedir):
        self.connections = {}
        client.Client.__init__(self, basedir)

    def get_all_peerids(self):
        return self.connections

class Basic(unittest.TestCase):
    def test_loadable(self):
        basedir = "test_client.Basic.test_loadable"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        open(os.path.join(basedir, "vdrive.furl"), "w").write("")
        c = client.Client(basedir)

    def test_permute(self):
        basedir = "test_client.Basic.test_permute"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        open(os.path.join(basedir, "vdrive.furl"), "w").write("")
        c = MyClient(basedir)
        for k in ["%d" % i for i in range(5)]:
            c.connections[k] = None
        self.failUnlessEqual(c.permute_peerids("one"), ['3','1','0','4','2'])
        self.failUnlessEqual(c.permute_peerids("one", 3), ['3','1','0'])
        self.failUnlessEqual(c.permute_peerids("two"), ['0','4','2','1','3'])
        c.connections.clear()
        self.failUnlessEqual(c.permute_peerids("one"), [])

        c2 = MyClient(basedir)
        for k in ["%d" % i for i in range(5)]:
            c2.connections[k] = None
        self.failUnlessEqual(c2.permute_peerids("one"), ['3','1','0','4','2'])

