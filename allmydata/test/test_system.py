
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.application import service
from allmydata import upload, client, queen
import os
from foolscap.eventual import flushEventualQueue

class SystemTest(unittest.TestCase):
    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()
    def tearDown(self):
        d = self.sparent.stopService()
        d.addCallback(lambda res: flushEventualQueue())
        return d

    def addService(self, s):
        s.setServiceParent(self.sparent)
        return s

    def setUpNodes(self):
        os.mkdir("queen")
        q = self.addService(queen.Queen(basedir="queen"))
        clients = []
        NUMCLIENTS = 5
        for i in range(NUMCLIENTS):
            basedir = "client%d" % i
            os.mkdir(basedir)
            c = self.addService(client.Client(basedir=basedir))
            clients.append(c)


    def waitForConnections(self):
        # the cheap way: time
        d = defer.Deferred()
        reactor.callLater(1, d.callback, None)
        return d

    def test_it(self):
        self.setUpNodes()
        d = self.waitForConnections()
        return d
