
import os
from twisted.trial import unittest
from twisted.application import service

import allmydata
from allmydata import client, introducer
from allmydata.util import version
from foolscap.eventual import flushEventualQueue

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

    def test_loadable_without_vdrive(self):
        basedir = "test_client.Basic.test_loadable_without_vdrive"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
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

    def test_versions(self):
        basedir = "test_client.Basic.test_versions"
        os.mkdir(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write("")
        open(os.path.join(basedir, "vdrive.furl"), "w").write("")
        c = client.Client(basedir)
        mine, oldest = c.remote_get_versions()
        self.failUnlessEqual(version.Version(mine), allmydata.__version__)

def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

class Run(unittest.TestCase):

    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()
    def tearDown(self):
        d = self.sparent.stopService()
        d.addBoth(flush_but_dont_ignore)
        return d

    def test_loadable(self):
        basedir = "test_client.Run.test_loadable"
        os.mkdir(basedir)
        dummy = "pb://wl74cyahejagspqgy4x5ukrvfnevlknt@127.0.0.1:58889/bogus"
        open(os.path.join(basedir, "introducer.furl"), "w").write(dummy)
        open(os.path.join(basedir, "suicide_prevention_hotline"), "w")
        c = client.Client(basedir)

    def test_reloadable(self):
        basedir = "test_client.Run.test_reloadable"
        os.mkdir(basedir)
        dummy = "pb://wl74cyahejagspqgy4x5ukrvfnevlknt@127.0.0.1:58889/bogus"
        open(os.path.join(basedir, "introducer.furl"), "w").write(dummy)
        c1 = client.Client(basedir)
        c1.setServiceParent(self.sparent)

        d = c1.disownServiceParent()
        def _restart(res):
            c2 = client.Client(basedir)
            c2.setServiceParent(self.sparent)
            return c2.disownServiceParent()
        d.addCallback(_restart)
        return d

