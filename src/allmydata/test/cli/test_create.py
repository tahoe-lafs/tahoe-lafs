import os
from StringIO import StringIO
from twisted.trial import unittest
from allmydata.scripts import runner
from allmydata.util import configutil

class Config(unittest.TestCase):
    def do_cli(self, *args):
        argv = list(args)
        stdout, stderr = StringIO(), StringIO()
        rc = runner.runner(argv, run_by_human=False,
                           stdout=stdout, stderr=stderr)
        return rc, stdout.getvalue(), stderr.getvalue()

    def read_config(self, basedir):
        tahoe_cfg = os.path.join(basedir, "tahoe.cfg")
        config = configutil.get_config(tahoe_cfg)
        return config

    def test_client(self):
        basedir = self.mktemp()
        rc, out, err = self.do_cli("create-client", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)

    def test_client_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = self.do_cli("create-client", "--hide-ip", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)

    def test_node(self):
        basedir = self.mktemp()
        rc, out, err = self.do_cli("create-node", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)

    def test_node_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = self.do_cli("create-node", "--hide-ip", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)

    def test_introducer(self):
        basedir = self.mktemp()
        rc, out, err = self.do_cli("create-introducer", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)

    def test_introducer_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = self.do_cli("create-introducer", "--hide-ip", basedir)
        cfg = self.read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)
