
from twisted.trial import unittest
from twisted.internet import defer
import foolscap
from foolscap.test.common import HelperTarget
from foolscap.eventual import flushEventualQueue

crypto_available = False
try:
    from foolscap import crypto
    crypto_available = crypto.available
except ImportError:
    pass


class ConnectToSelf(unittest.TestCase):

    def setUp(self):
        self.services = []

    def requireCrypto(self):
        if not crypto_available:
            raise unittest.SkipTest("crypto not available")

    def startTub(self, tub):
        self.services = [tub]
        for s in self.services:
            s.startService()
            l = s.listenOn("tcp:0:interface=127.0.0.1")
            s.setLocation("127.0.0.1:%d" % l.getPortnum())

    def tearDown(self):
        d = defer.DeferredList([s.stopService() for s in self.services])
        d.addCallback(flushEventualQueue)
        return d

    def testConnectUnauthenticated(self):
        tub = foolscap.UnauthenticatedTub()
        self.startTub(tub)
        target = HelperTarget("bob")
        target.obj = "unset"
        url = tub.registerReference(target)
        # can we connect to a reference on our own Tub?
        d = tub.getReference(url)
        def _connected(ref):
            return ref.callRemote("set", 12)
        d.addCallback(_connected)
        def _check(res):
            self.failUnlessEqual(target.obj, 12)
        d.addCallback(_check)

        def _connect_again(res):
            target.obj = None
            return tub.getReference(url)
        d.addCallback(_connect_again)
        d.addCallback(_connected)
        d.addCallback(_check)

        return d

    def testConnectAuthenticated(self):
        self.requireCrypto()
        tub = foolscap.Tub()
        self.startTub(tub)
        target = HelperTarget("bob")
        target.obj = "unset"
        url = tub.registerReference(target)
        # can we connect to a reference on our own Tub?
        d = tub.getReference(url)
        def _connected(ref):
            return ref.callRemote("set", 12)
        d.addCallback(_connected)
        def _check(res):
            self.failUnlessEqual(target.obj, 12)
        d.addCallback(_check)
        def _connect_again(res):
            target.obj = None
            return tub.getReference(url)
        d.addCallback(_connect_again)
        d.addCallback(_connected)
        d.addCallback(_check)
        return d
