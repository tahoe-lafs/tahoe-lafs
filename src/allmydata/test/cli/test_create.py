import os
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import usage
from allmydata.util import configutil
from ..common_util import run_cli, parse_cli

def read_config(basedir):
    tahoe_cfg = os.path.join(basedir, "tahoe.cfg")
    config = configutil.get_config(tahoe_cfg)
    return config

class Config(unittest.TestCase):
    def test_client_unrecognized_options(self):
        tests = [
            ("--listen", "create-client", "--listen=tcp"),
            ("--hostname", "create-client", "--hostname=computer"),
            ("--port",
             "create-client", "--port=unix:/var/tahoe/socket",
             "--location=tor:myservice.onion:12345"),
            ("--port", "create-client", "--port=unix:/var/tahoe/socket"),
            ("--location",
             "create-client", "--location=tor:myservice.onion:12345"),
            ("--listen", "create-client", "--listen=tor"),
            ("--listen", "create-client", "--listen=i2p"),
                ]
        for test in tests:
            option = test[0]
            verb = test[1]
            args = test[2:]
            e = self.assertRaises(usage.UsageError, parse_cli, verb, *args)
            self.assertIn("option %s not recognized" % (option,), str(e))

    @defer.inlineCallbacks
    def test_client(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-client", basedir)
        cfg = read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)
        self.assertEqual(cfg.get("node", "tub.port"), "disabled")
        self.assertEqual(cfg.get("node", "tub.location"), "disabled")
        self.assertFalse(cfg.has_section("connections"))

    @defer.inlineCallbacks
    def test_client_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-client", "--hide-ip", basedir)
        cfg = read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)
        self.assertEqual(cfg.get("connections", "tcp"), "tor")

    @defer.inlineCallbacks
    def test_client_basedir_exists(self):
        basedir = self.mktemp()
        os.mkdir(basedir)
        with open(os.path.join(basedir, "foo"), "w") as f:
            f.write("blocker")
        rc, out, err = yield run_cli("create-client", basedir)
        self.assertEqual(rc, -1)
        self.assertIn(basedir, err)
        self.assertIn("is not empty", err)
        self.assertIn("To avoid clobbering anything, I am going to quit now", err)

    @defer.inlineCallbacks
    def test_node(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-node", "--hostname=foo", basedir)
        cfg = read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)
        self.assertFalse(cfg.has_section("connections"))

    @defer.inlineCallbacks
    def test_node_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-node", "--hide-ip",
                                     "--hostname=foo", basedir)
        cfg = read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)
        self.assertEqual(cfg.get("connections", "tcp"), "tor")

    @defer.inlineCallbacks
    def test_node_hostname(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-node", "--hostname=computer", basedir)
        cfg = read_config(basedir)
        port = cfg.get("node", "tub.port")
        location = cfg.get("node", "tub.location")
        self.assertRegex(port, r'^tcp:\d+$')
        self.assertRegex(location, r'^tcp:computer:\d+$')

    @defer.inlineCallbacks
    def test_node_port_location(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-node",
                                     "--port=unix:/var/tahoe/socket",
                                     "--location=tor:myservice.onion:12345",
                                     basedir)
        cfg = read_config(basedir)
        self.assertEqual(cfg.get("node", "tub.location"), "tor:myservice.onion:12345")
        self.assertEqual(cfg.get("node", "tub.port"), "unix:/var/tahoe/socket")

    def test_node_hostname_port_location(self):
        basedir = self.mktemp()
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=tcp",
                              "--hostname=foo", "--port=bar", "--location=baz",
                              basedir)
        self.assertEqual(str(e),
                         "--hostname cannot be used with --location/--port")

    def test_node_listen_tcp_no_hostname(self):
        basedir = self.mktemp()
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=tcp", basedir)
        self.assertIn("--listen=tcp requires --hostname=", str(e))

    @defer.inlineCallbacks
    def test_node_listen_none(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-node", "--listen=none", basedir)
        cfg = read_config(basedir)
        self.assertEqual(cfg.get("node", "tub.port"), "disabled")
        self.assertEqual(cfg.get("node", "tub.location"), "disabled")

    def test_node_listen_none_errors(self):
        basedir = self.mktemp()
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=none",
                              "--hostname=foo",
                              basedir)
        self.assertEqual(str(e), "--hostname cannot be used when --listen=none")

        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=none",
                              "--port=foo", "--location=foo",
                              basedir)
        self.assertEqual(str(e), "--port/--location cannot be used when --listen=none")

        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=tcp,none",
                              basedir)
        self.assertEqual(str(e), "--listen= must be none, or one/some of: tcp, tor, i2p")

    def test_node_listen_bad(self):
        basedir = self.mktemp()
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=XYZZY,tcp",
                              basedir)
        self.assertEqual(str(e), "--listen= must be none, or one/some of: tcp, tor, i2p")

    @defer.inlineCallbacks
    def test_node_listen_tor(self):
        basedir = self.mktemp()
        d = run_cli("create-node", "--listen=tor", basedir)
        e = yield self.assertFailure(d, NotImplementedError)
        self.assertEqual(str(e), "--listen=tor is under development, "
                         "see ticket #2490 for details")

    @defer.inlineCallbacks
    def test_node_listen_i2p(self):
        basedir = self.mktemp()
        d = run_cli("create-node", "--listen=i2p", basedir)
        e = yield self.assertFailure(d, NotImplementedError)
        self.assertEqual(str(e), "--listen=i2p is under development, "
                         "see ticket #2490 for details")

    def test_node_listen_tor_hostname(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=tor",
                              "--hostname=foo")
        self.assertEqual(str(e), "--listen= must be tcp to use --hostname")

    def test_node_port_only(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--port=unix:/var/tahoe/socket")
        self.assertEqual(str(e), "--port must be used with --location")

    def test_node_location_only(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--location=tor:myservice.onion:12345")
        self.assertEqual(str(e), "--location must be used with --port")

    @defer.inlineCallbacks
    def test_node_basedir_exists(self):
        basedir = self.mktemp()
        os.mkdir(basedir)
        with open(os.path.join(basedir, "foo"), "w") as f:
            f.write("blocker")
        rc, out, err = yield run_cli("create-node", "--hostname=foo", basedir)
        self.assertEqual(rc, -1)
        self.assertIn(basedir, err)
        self.assertIn("is not empty", err)
        self.assertIn("To avoid clobbering anything, I am going to quit now", err)

    def test_introducer_no_hostname(self):
        basedir = self.mktemp()
        e = self.assertRaises(usage.UsageError, parse_cli,
                              "create-introducer", basedir)
        self.assertEqual(str(e), "--listen=tcp requires --hostname=")

    @defer.inlineCallbacks
    def test_introducer_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-introducer", "--hide-ip",
                                     "--hostname=foo", basedir)
        cfg = read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)

    @defer.inlineCallbacks
    def test_introducer_hostname(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-introducer",
                                     "--hostname=foo", basedir)
        cfg = read_config(basedir)
        self.assertTrue("foo" in cfg.get("node", "tub.location"))
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), True)

    @defer.inlineCallbacks
    def test_introducer_basedir_exists(self):
        basedir = self.mktemp()
        os.mkdir(basedir)
        with open(os.path.join(basedir, "foo"), "w") as f:
            f.write("blocker")
        rc, out, err = yield run_cli("create-introducer", "--hostname=foo",
                                     basedir)
        self.assertEqual(rc, -1)
        self.assertIn(basedir, err)
        self.assertIn("is not empty", err)
        self.assertIn("To avoid clobbering anything, I am going to quit now", err)
