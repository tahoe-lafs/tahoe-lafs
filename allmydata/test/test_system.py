
from twisted.trial import unittest
from twisted.application import service
from allmydata import upload, client, queen
import os

class SystemTest(unittest.TestCase):
    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()
    def tearDown(self):
        return self.sparent.stopService()

    def addService(self, s):
        s.setServiceParent(self.sparent)
        return s

    def test_it(self):
        os.mkdir("queen")
        q = self.addService(queen.Queen(basedir="queen"))
        clients = []
        NUMCLIENTS = 5
        for i in range(NUMCLIENTS):
            basedir = "client%d" % i
            os.mkdir(basedir)
            clients.append(self.addService(client.Client(basedir=basedir)))

