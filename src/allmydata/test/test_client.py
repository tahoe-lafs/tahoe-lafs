
import os
from twisted.trial import unittest

from allmydata import client, introducer

class MyIntroducerClient(introducer.IntroducerClient):
    def __init__(self):
        self.connections = {}

def permute(c, key):
    return [ y for x, y, z in c.get_permuted_peers(key) ]

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
        c = client.Client(basedir)
        c.introducer_client = MyIntroducerClient()
        for k in ["%d" % i for i in range(5)]:
            c.introducer_client.connections[k] = None
        self.failUnlessEqual(permute(c, "one"), ['3','1','0','4','2'])
        self.failUnlessEqual(permute(c, "two"), ['0','4','2','1','3'])
        c.introducer_client.connections.clear()
        self.failUnlessEqual(permute(c, "one"), [])

        c2 = client.Client(basedir)
        c2.introducer_client = MyIntroducerClient()
        for k in ["%d" % i for i in range(5)]:
            c2.introducer_client.connections[k] = None
        self.failUnlessEqual(permute(c2, "one"), ['3','1','0','4','2'])

