
# Test the NoNetworkGrid test harness

from twisted.trial import unittest
from twisted.application import service
from allmydata.test.no_network import NoNetworkGrid
from allmydata.immutable.upload import Data
from allmydata.util.consumer import download_to_data

from .common import (
    SameProcessStreamEndpointAssigner,
)

class Harness(unittest.TestCase):
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()
        self.addCleanup(self.s.stopService)
        self.port_assigner = SameProcessStreamEndpointAssigner()
        self.port_assigner.setUp()
        self.addCleanup(self.port_assigner.tearDown)

    def grid(self, basedir):
        return NoNetworkGrid(
            basedir,
            num_clients=1,
            num_servers=10,
            client_config_hooks={},
            port_assigner=self.port_assigner,
        )

    def test_create(self):
        basedir = "no_network/Harness/create"
        g = self.grid(basedir)
        g.startService()
        return g.stopService()

    def test_upload(self):
        basedir = "no_network/Harness/upload"
        g = self.grid(basedir)
        g.setServiceParent(self.s)

        c0 = g.clients[0]
        DATA = "Data to upload" * 100
        data = Data(DATA, "")
        d = c0.upload(data)
        def _uploaded(res):
            n = c0.create_node_from_uri(res.get_uri())
            return download_to_data(n)
        d.addCallback(_uploaded)
        def _check(res):
            self.failUnlessEqual(res, DATA)
        d.addCallback(_check)

        return d
