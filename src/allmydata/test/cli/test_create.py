"""
Ported to Python 3.
"""
from __future__ import annotations

import os

from typing import Any

from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import usage
from allmydata.util import configutil
from allmydata.util import tor_provider, i2p_provider
from ..common_util import run_cli, parse_cli
from ..common import (
    disable_modules,
)
from ...scripts import create_node
from ...listeners import ListenerConfig, StaticProvider
from ... import client

def read_config(basedir):
    tahoe_cfg = os.path.join(basedir, "tahoe.cfg")
    config = configutil.get_config(tahoe_cfg)
    return config

class MergeConfigTests(unittest.TestCase):
    """
    Tests for ``create_node.merge_config``.
    """
    def test_disable_left(self) -> None:
        """
        If the left argument to ``create_node.merge_config`` is ``None``
        then the return value is ``None``.
        """
        conf = ListenerConfig([], [], {})
        self.assertEqual(None, create_node.merge_config(None, conf))

    def test_disable_right(self) -> None:
        """
        If the right argument to ``create_node.merge_config`` is ``None``
        then the return value is ``None``.
        """
        conf = ListenerConfig([], [], {})
        self.assertEqual(None, create_node.merge_config(conf, None))

    def test_disable_both(self) -> None:
        """
        If both arguments to ``create_node.merge_config`` are ``None``
        then the return value is ``None``.
        """
        self.assertEqual(None, create_node.merge_config(None, None))

    def test_overlapping_keys(self) -> None:
        """
        If there are any keys in the ``node_config`` of the left and right
        parameters that are shared then ``ValueError`` is raised.
        """
        left = ListenerConfig([], [], {"foo": [("b", "ar")]})
        right = ListenerConfig([], [], {"foo": [("ba", "z")]})
        self.assertRaises(ValueError, lambda: create_node.merge_config(left, right))

    def test_merge(self) -> None:
        """
        ``create_node.merge_config`` returns a ``ListenerConfig`` that has
        all of the ports, locations, and node config from each of the two
        ``ListenerConfig`` values given.
        """
        left = ListenerConfig(
            ["left-port"],
            ["left-location"],
            {"left": [("f", "oo")]},
        )
        right = ListenerConfig(
            ["right-port"],
            ["right-location"],
            {"right": [("ba", "r")]},
        )
        result = create_node.merge_config(left, right)
        self.assertEqual(
            ListenerConfig(
                ["left-port", "right-port"],
                ["left-location", "right-location"],
                {"left": [("f", "oo")], "right": [("ba", "r")]},
            ),
            result,
        )

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

    async def test_create_client_config(self):
        """
        ``create_node.write_client_config`` writes a configuration file
        that can be parsed.

        TODO Maybe we should test that we can recover the given configuration
        from the parse, too.
        """
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            opts = {"nickname": "nick",
                    "webport": "tcp:3456",
                    "hide-ip": False,
                    "listen": "none",
                    "shares-needed": "1",
                    "shares-happy": "1",
                    "shares-total": "1",
                    }
            await create_node.write_node_config(f, opts)
            create_node.write_client_config(f, opts)

        # should succeed, no exceptions
        client.read_config(d, "")

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
    def test_non_default_storage_args(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli(
            "create-client",
            '--shares-total', '19',
            '--shares-needed', '2',
            '--shares-happy', '11',
            basedir,
        )
        cfg = read_config(basedir)
        self.assertEqual(2, cfg.getint("client", "shares.needed"))
        self.assertEqual(11, cfg.getint("client", "shares.happy"))
        self.assertEqual(19, cfg.getint("client", "shares.total"))

    @defer.inlineCallbacks
    def test_illegal_shares_total(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli(
            "create-client",
            '--shares-total', 'funballs',
            basedir,
        )
        self.assertNotEqual(0, rc)
        self.assertTrue('--shares-total must be an integer' in err + out)

    @defer.inlineCallbacks
    def test_client_hide_ip_no_i2p_txtorcon(self):
        """
        The ``create-client`` sub-command tells the user to install the necessary
        dependencies if they have neither tor nor i2p support installed and
        they request network location privacy with the ``--hide-ip`` flag.
        """
        with disable_modules("txi2p", "txtorcon"):
            basedir = self.mktemp()
            rc, out, err = yield run_cli("create-client", "--hide-ip", basedir)
            self.assertTrue(rc != 0, out)
            self.assertTrue('pip install tahoe-lafs[i2p]' in out)
            self.assertTrue('pip install tahoe-lafs[tor]' in out)

    @defer.inlineCallbacks
    def test_client_i2p_option_no_txi2p(self):
        with disable_modules("txi2p"):
            basedir = self.mktemp()
            rc, out, err = yield run_cli("create-node", "--listen=i2p", "--i2p-launch", basedir)
            self.assertTrue(rc != 0)
            self.assertTrue("Specifying any I2P options requires the 'txi2p' module" in out)

    @defer.inlineCallbacks
    def test_client_tor_option_no_txtorcon(self):
        with disable_modules("txtorcon"):
            basedir = self.mktemp()
            rc, out, err = yield run_cli("create-node", "--listen=tor", "--tor-launch", basedir)
            self.assertTrue(rc != 0)
            self.assertTrue("Specifying any Tor options requires the 'txtorcon' module" in out)

    @defer.inlineCallbacks
    def test_client_hide_ip(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-client", "--hide-ip", basedir)
        self.assertEqual(0, rc)
        cfg = read_config(basedir)
        self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)
        self.assertEqual(cfg.get("connections", "tcp"), "tor")

    @defer.inlineCallbacks
    def test_client_hide_ip_no_txtorcon(self):
        with disable_modules("txtorcon"):
            basedir = self.mktemp()
            rc, out, err = yield run_cli("create-client", "--hide-ip", basedir)
            self.assertEqual(0, rc)
            cfg = read_config(basedir)
            self.assertEqual(cfg.getboolean("node", "reveal-IP-address"), False)
            self.assertEqual(cfg.get("connections", "tcp"), "disabled")

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
    def test_storage_dir(self):
        basedir = self.mktemp()
        rc, out, err = yield run_cli("create-node", "--storage-dir", "/tmp/storage", "--hostname=foo", basedir)
        cfg = read_config(basedir)
        self.assertEqual(cfg.get("storage", "storage_dir"), "/tmp/storage")

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
        self.assertEqual(str(e), "--listen=tcp requires --hostname=")

    def test_node_listen_bad(self):
        basedir = self.mktemp()
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=XYZZY,tcp",
                              basedir)
        self.assertEqual(str(e), "--listen= must be one/some of: i2p, none, tcp, tor")

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

    @defer.inlineCallbacks
    def test_node_slow(self):
        """
        A node can be created using a listener type that returns an
        unfired Deferred from its ``create_config`` method.
        """
        d = defer.Deferred()
        slow = StaticProvider(True, False, d, None)
        create_node._LISTENERS["xxyzy"] = slow
        self.addCleanup(lambda: create_node._LISTENERS.pop("xxyzy"))

        basedir = self.mktemp()
        d2 = run_cli("create-node", "--listen=xxyzy", basedir)
        d.callback(None)
        rc, out, err = yield d2
        self.assertEqual(rc, 0)
        self.assertIn("Node created", out)
        self.assertEqual(err, "")

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

def fake_config(testcase: unittest.TestCase, module: Any, result: Any) -> list[tuple]:
    """
    Monkey-patch a fake configuration function into the given module.

    :param testcase: The test case to use to do the monkey-patching.

    :param module: The module into which to patch the fake function.

    :param result: The return value for the fake function.

    :return: A list of tuples of the arguments the fake function was called
        with.
    """
    calls = []
    def fake_config(reactor, cli_config):
        calls.append((reactor, cli_config))
        return result
    testcase.patch(module, "create_config", fake_config)
    return calls

class Tor(unittest.TestCase):
    def test_default(self):
        basedir = self.mktemp()
        tor_config = {"tor": [("abc", "def")]}
        tor_port = "ghi"
        tor_location = "jkl"
        config_d = defer.succeed(
            ListenerConfig([tor_port], [tor_location], tor_config)
        )

        calls = fake_config(self, tor_provider, config_d)
        rc, out, err = self.successResultOf(
            run_cli("create-node", "--listen=tor", basedir),
        )

        self.assertEqual(len(calls), 1)
        args = calls[0]
        self.assertIdentical(args[0], reactor)
        self.assertIsInstance(args[1], create_node.CreateNodeOptions)
        self.assertEqual(args[1]["listen"], "tor")
        cfg = read_config(basedir)
        self.assertEqual(cfg.get("tor", "abc"), "def")
        self.assertEqual(cfg.get("node", "tub.port"), "ghi")
        self.assertEqual(cfg.get("node", "tub.location"), "jkl")

    def test_launch(self):
        """
        The ``--tor-launch`` command line option sets ``tor-launch`` to
        ``True``.
        """
        basedir = self.mktemp()
        config_d = defer.succeed(None)

        calls = fake_config(self, tor_provider, config_d)
        rc, out, err = self.successResultOf(
            run_cli(
                "create-node", "--listen=tor", "--tor-launch",
                basedir,
            ),
        )
        args = calls[0]
        self.assertEqual(args[1]["listen"], "tor")
        self.assertEqual(args[1]["tor-launch"], True)
        self.assertEqual(args[1]["tor-control-port"], None)

    def test_control_port(self):
        """
        The ``--tor-control-port`` command line parameter's value is
        passed along as the ``tor-control-port`` value.
        """
        basedir = self.mktemp()
        config_d = defer.succeed(None)

        calls = fake_config(self, tor_provider, config_d)
        rc, out, err = self.successResultOf(
            run_cli(
                "create-node", "--listen=tor", "--tor-control-port=mno",
                basedir,
            ),
        )
        args = calls[0]
        self.assertEqual(args[1]["listen"], "tor")
        self.assertEqual(args[1]["tor-launch"], False)
        self.assertEqual(args[1]["tor-control-port"], "mno")

    def test_not_both(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=tor",
                              "--tor-launch", "--tor-control-port=foo")
        self.assertEqual(str(e), "use either --tor-launch or"
                         " --tor-control-port=, not both")

    def test_launch_without_listen(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=none", "--tor-launch")
        self.assertEqual(str(e), "--tor-launch requires --listen=tor")

    def test_control_port_without_listen(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=none",
                              "--tor-control-port=foo")
        self.assertEqual(str(e), "--tor-control-port= requires --listen=tor")

class I2P(unittest.TestCase):
    def test_default(self):
        basedir = self.mktemp()
        i2p_config = {"i2p": [("abc", "def")]}
        i2p_port = "ghi"
        i2p_location = "jkl"
        dest_d = defer.succeed(ListenerConfig([i2p_port], [i2p_location], i2p_config))

        calls = fake_config(self, i2p_provider, dest_d)
        rc, out, err = self.successResultOf(
            run_cli("create-node", "--listen=i2p", basedir),
        )
        self.assertEqual(len(calls), 1)
        args = calls[0]
        self.assertIdentical(args[0], reactor)
        self.assertIsInstance(args[1], create_node.CreateNodeOptions)
        self.assertEqual(args[1]["listen"], "i2p")
        cfg = read_config(basedir)
        self.assertEqual(cfg.get("i2p", "abc"), "def")
        self.assertEqual(cfg.get("node", "tub.port"), "ghi")
        self.assertEqual(cfg.get("node", "tub.location"), "jkl")

    def test_launch(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=i2p", "--i2p-launch")
        self.assertEqual(str(e), "--i2p-launch is under development")


    def test_sam_port(self):
        basedir = self.mktemp()
        dest_d = defer.succeed(None)

        calls = fake_config(self, i2p_provider, dest_d)
        rc, out, err = self.successResultOf(
            run_cli(
                "create-node", "--listen=i2p", "--i2p-sam-port=mno",
                basedir,
            ),
        )
        args = calls[0]
        self.assertEqual(args[1]["listen"], "i2p")
        self.assertEqual(args[1]["i2p-launch"], False)
        self.assertEqual(args[1]["i2p-sam-port"], "mno")

    def test_not_both(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=i2p",
                              "--i2p-launch", "--i2p-sam-port=foo")
        self.assertEqual(str(e), "use either --i2p-launch or"
                         " --i2p-sam-port=, not both")

    def test_launch_without_listen(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=none", "--i2p-launch")
        self.assertEqual(str(e), "--i2p-launch requires --listen=i2p")

    def test_sam_port_without_listen(self):
        e = self.assertRaises(usage.UsageError,
                              parse_cli,
                              "create-node", "--listen=none",
                              "--i2p-sam-port=foo")
        self.assertEqual(str(e), "--i2p-sam-port= requires --listen=i2p")
