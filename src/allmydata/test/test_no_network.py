"""
Test the NoNetworkGrid test harness.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

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
        DATA = b"Data to upload" * 100
        data = Data(DATA, b"")
        d = c0.upload(data)
        def _uploaded(res):
            n = c0.create_node_from_uri(res.get_uri())
            return download_to_data(n)
        d.addCallback(_uploaded)
        def _check(res):
            self.failUnlessEqual(res, DATA)
        d.addCallback(_check)

        return d
