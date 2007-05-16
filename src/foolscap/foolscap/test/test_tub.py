# -*- test-case-name: foolscap.test.test_tub -*-

import os.path
from twisted.trial import unittest
from twisted.internet import defer

crypto_available = False
try:
    from foolscap import crypto
    crypto_available = crypto.available
except ImportError:
    pass

from foolscap import Tub, UnauthenticatedTub
from foolscap.referenceable import RemoteReference
from foolscap.eventual import eventually, flushEventualQueue
from foolscap.test.common import HelperTarget, TargetMixin

# we use authenticated tubs if possible. If crypto is not available, fall
# back to unauthenticated ones
GoodEnoughTub = UnauthenticatedTub
if crypto_available:
    GoodEnoughTub = Tub

class TestCertFile(unittest.TestCase):
    def test_generate(self):
        t = Tub()
        certdata = t.getCertData()
        self.failUnless("BEGIN CERTIFICATE" in certdata)
        self.failUnless("BEGIN RSA PRIVATE KEY" in certdata)

    def test_certdata(self):
        t1 = Tub()
        data1 = t1.getCertData()
        t2 = Tub(certData=data1)
        data2 = t2.getCertData()
        self.failUnless(data1 == data2)

    def test_certfile(self):
        fn = "test_tub.TestCertFile.certfile"
        t1 = Tub(certFile=fn)
        self.failUnless(os.path.exists(fn))
        data1 = t1.getCertData()

        t2 = Tub(certFile=fn)
        data2 = t2.getCertData()
        self.failUnless(data1 == data2)

if not crypto_available:
    del TestCertFile

class QueuedStartup(TargetMixin, unittest.TestCase):
    # calling getReference and connectTo before the Tub has started should
    # put off network activity until the Tub is started.

    def setUp(self):
        TargetMixin.setUp(self)
        self.tubB = GoodEnoughTub()
        self.services = [self.tubB]
        for s in self.services:
            s.startService()
            l = s.listenOn("tcp:0:interface=127.0.0.1")
            s.setLocation("127.0.0.1:%d" % l.getPortnum())

        self.barry = HelperTarget("barry")
        self.barry_url = self.tubB.registerReference(self.barry)

        self.bill = HelperTarget("bill")
        self.bill_url = self.tubB.registerReference(self.bill)

        self.bob = HelperTarget("bob")
        self.bob_url = self.tubB.registerReference(self.bob)

    def tearDown(self):
        d = TargetMixin.tearDown(self)
        def _more(res):
            return defer.DeferredList([s.stopService() for s in self.services])
        d.addCallback(_more)
        d.addCallback(flushEventualQueue)
        return d

    def test_queued_getref(self):
        t1 = GoodEnoughTub()
        d1 = t1.getReference(self.barry_url)
        d2 = t1.getReference(self.bill_url)
        def _check(res):
            ((barry_success, barry_rref),
             (bill_success, bill_rref)) = res
            self.failUnless(barry_success)
            self.failUnless(bill_success)
            self.failUnless(isinstance(barry_rref, RemoteReference))
            self.failUnless(isinstance(bill_rref, RemoteReference))
            self.failIf(barry_rref == bill_success)
        dl = defer.DeferredList([d1, d2])
        dl.addCallback(_check)
        self.services.append(t1)
        eventually(t1.startService)
        return dl

    def test_queued_reconnector(self):
        t1 = GoodEnoughTub()
        bill_connections = []
        barry_connections = []
        t1.connectTo(self.bill_url, bill_connections.append)
        t1.connectTo(self.barry_url, barry_connections.append)
        def _check():
            if len(bill_connections) >= 1 and len(barry_connections) >= 1:
                return True
            return False
        d = self.poll(_check)
        def _validate(res):
            self.failUnless(isinstance(bill_connections[0], RemoteReference))
            self.failUnless(isinstance(barry_connections[0], RemoteReference))
            self.failIf(bill_connections[0] == barry_connections[0])
        d.addCallback(_validate)
        self.services.append(t1)
        eventually(t1.startService)
        return d

