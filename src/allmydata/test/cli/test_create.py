import os
from twisted.trial import unittest
from twisted.internet import defer
from allmydata.util import configutil
from ..common_util import run_cli

class Config(unittest.TestCase):
    def read_config(self, basedir):
        tahoe_cfg = os.path.join(basedir, "tahoe.cfg")
        config = configutil.get_config(tahoe_cfg)
        return config

    @defer.inlineCallbacks
    def test_client(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-client", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)

    @defer.inlineCallbacks
    def test_client_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-client", "--hide-ip", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)

    @defer.inlineCallbacks
    def test_node(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-node", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)

    @defer.inlineCallbacks
    def test_node_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-node", "--hide-ip", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)

    @defer.inlineCallbacks
    def test_introducer(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-introducer", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)

    @defer.inlineCallbacks
    def test_introducer_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-introducer", "--hide-ip", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)
