
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.application import service
from allmydata import client, queen
import os
from foolscap.eventual import flushEventualQueue
from twisted.python import log
from allmydata.util import idlib

class SystemTest(unittest.TestCase):
    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()
    def tearDown(self):
        d = self.sparent.stopService()
        d.addCallback(lambda res: flushEventualQueue())
        def _done(res):
            d1 = defer.Deferred()
            reactor.callLater(0.1, d1.callback, None)
            return d1
        d.addCallback(_done)
        return d

    def add_service(self, s):
        s.setServiceParent(self.sparent)
        return s

    def set_up_nodes(self, NUMCLIENTS=5):
        self.numclients = NUMCLIENTS
        if not os.path.isdir("queen"):
            os.mkdir("queen")
        q = self.queen = self.add_service(queen.Queen(basedir="queen"))
        self.queen_pburl = q.urls["roster"]
        self.clients = []
        for i in range(NUMCLIENTS):
            basedir = "client%d" % i
            if not os.path.isdir(basedir):
                os.mkdir(basedir)
            c = self.add_service(client.Client(basedir=basedir))
            c.set_queen_pburl(self.queen_pburl)
            self.clients.append(c)
        log.msg("STARTING")
        return self.wait_for_connections()

    def wait_for_connections(self, ignored=None):
        for c in self.clients:
            if len(c.connections) != self.numclients - 1:
                d = defer.Deferred()
                d.addCallback(self.wait_for_connections)
                reactor.callLater(0.05, d.callback, None)
                return d
        return defer.succeed(None)

    def test_connections(self):
        d = self.set_up_nodes()
        def _check(res):
            for c in self.clients:
                self.failUnlessEqual(len(c.connections), 4)
        d.addCallback(_check)
        return d
    test_connections.timeout = 20

    def test_upload_and_download(self):
        DATA = "Some data to upload\n"
        d = self.set_up_nodes()
        def _do_upload(res):
            log.msg("UPLOADING")
            u = self.clients[0].getServiceNamed("uploader")
            d1 = u.upload_data(DATA)
            return d1
        d.addCallback(_do_upload)
        def _upload_done(verifierid):
            log.msg("upload finished: verifierid=%s" % idlib.b2a(verifierid))
            dl = self.clients[1].getServiceNamed("downloader")
            d1 = dl.download_to_data(verifierid)
            return d1
        d.addCallback(_upload_done)
        def _download_done(data):
            log.msg("download finished")
            self.failUnlessEqual(data, DATA)
        d.addCallback(_download_done)
        return d
    test_upload_and_download.timeout = 20

