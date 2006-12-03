
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.application import service
from allmydata import client, queen
import os
from foolscap.eventual import flushEventualQueue
from twisted.python import log

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

    def setUpNodes(self, NUMCLIENTS=5):
        if not os.path.isdir("queen"):
            os.mkdir("queen")
        q = self.queen = self.addService(queen.Queen(basedir="queen"))
        queen_pburl = q.urls["roster"]
        clients = self.clients = []

        for i in range(NUMCLIENTS):
            basedir = "client%d" % i
            if not os.path.isdir(basedir):
                os.mkdir(basedir)
            c = self.addService(client.Client(basedir=basedir))
            c.set_queen_pburl(queen_pburl)
            clients.append(c)


    def waitForConnections(self):
        # the cheap way: time
        d = defer.Deferred()
        reactor.callLater(1, d.callback, None)
        return d

    def test_connections(self):
        self.setUpNodes()
        d = self.waitForConnections()
        def _check(res):
            log.msg("CHECKING")
            for c in self.clients:
                self.failUnlessEqual(len(c.connections), 4)
        d.addCallback(_check)
        return d

    def test_upload(self):
        self.setUpNodes()
        d = self.waitForConnections()
        def _upload(res):
            log.msg("DONE")
            u = self.clients[0].getServiceNamed("uploader")
            d1 = u.upload_data("Some data to upload")
            return d1
        d.addCallback(_upload)
        return d
