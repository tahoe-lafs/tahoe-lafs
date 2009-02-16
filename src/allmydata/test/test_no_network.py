
# Test the NoNetworkGrid test harness

from twisted.trial import unittest
from twisted.application import service
from allmydata.test.no_network import NoNetworkGrid
from allmydata.immutable.upload import Data


class Harness(unittest.TestCase):
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()

    def tearDown(self):
        return self.s.stopService()

    def test_create(self):
        basedir = "no_network/Harness/create"
        g = NoNetworkGrid(basedir)
        g.startService()
        return g.stopService()

    def test_upload(self):
        basedir = "no_network/Harness/upload"
        g = NoNetworkGrid(basedir)
        g.setServiceParent(self.s)

        c0 = g.clients[0]
        DATA = "Data to upload" * 100
        data = Data(DATA, "")
        d = c0.upload(data)
        def _uploaded(res):
            n = c0.create_node_from_uri(res.uri)
            return n.download_to_data()
        d.addCallback(_uploaded)
        def _check(res):
            self.failUnlessEqual(res, DATA)
        d.addCallback(_check)

        return d

