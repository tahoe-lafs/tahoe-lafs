import os
import mock
from io import BytesIO
from twisted.trial import unittest
from twisted.internet import reactor, endpoints
from ConfigParser import SafeConfigParser
from foolscap.connections import tcp
from ..node import Node

class FakeNode(Node):
    def __init__(self, config_str):
        self.config = SafeConfigParser()
        self.config.readfp(BytesIO(config_str))

BASECONFIG = ("[client]\n"
              "introducer.furl = \n"
              )


class TCP(unittest.TestCase):
    def test_default(self):
        n = FakeNode(BASECONFIG)
        h = n._make_tcp_handler()
        self.assertIsInstance(h, tcp.DefaultTCP)

class Tor(unittest.TestCase):
    def test_disabled(self):
        n = FakeNode(BASECONFIG+"[tor]\nenable = false\n")
        h = n._make_tor_handler()
        self.assertEqual(h, None)

    def test_default(self):
        n = FakeNode(BASECONFIG)
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.default_socks",
                        return_value=h1) as f:
            h = n._make_tor_handler()
            self.assertEqual(f.mock_calls, [mock.call()])
            self.assertIdentical(h, h1)

    def test_launch(self):
        n = FakeNode(BASECONFIG+"[tor]\nlaunch = true\n")
        n.basedir = "BASEDIR"
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.launch",
                        return_value=h1) as f:
            h = n._make_tor_handler()
            data_directory = os.path.join(n.basedir, "private", "tor-statedir")
            exp = mock.call(data_directory=data_directory,
                            tor_binary=None)
            self.assertEqual(f.mock_calls, [exp])
            self.assertIdentical(h, h1)

    def test_launch_executable(self):
        n = FakeNode(BASECONFIG+"[tor]\nlaunch = true\ntor.executable = tor")
        n.basedir = "BASEDIR"
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.launch",
                        return_value=h1) as f:
            h = n._make_tor_handler()
            data_directory = os.path.join(n.basedir, "private", "tor-statedir")
            exp = mock.call(data_directory=data_directory,
                            tor_binary="tor")
            self.assertEqual(f.mock_calls, [exp])
            self.assertIdentical(h, h1)

    def test_socksport(self):
        n = FakeNode(BASECONFIG+"[tor]\nsocks.port = 1234\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.socks_port",
                        return_value=h1) as f:
            h = n._make_tor_handler()
            self.assertEqual(f.mock_calls, [mock.call(1234)])
            self.assertIdentical(h, h1)

    def test_socksport_localhost(self):
        n = FakeNode(BASECONFIG+"[tor]\nsocks.port = 127.0.0.1:1234\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.socks_port",
                        return_value=h1) as f:
            h = n._make_tor_handler()
            self.assertEqual(f.mock_calls, [mock.call(1234)])
            self.assertIdentical(h, h1)

    def test_socksport_bad_host(self):
        n = FakeNode(BASECONFIG+"[tor]\nsocks.port = example.com:1234\n")
        e = self.assertRaises(ValueError, n._make_tor_handler)
        self.assertIn("must be '127.0.0.1:PORT'", str(e))

    def test_socksport_not_integer(self):
        n = FakeNode(BASECONFIG+"[tor]\nsocks.port = kumquat\n")
        e = self.assertRaises(ValueError, n._make_tor_handler)
        self.assertIn("used non-numeric PORT value", str(e))

    def test_controlport(self):
        n = FakeNode(BASECONFIG+"[tor]\ncontrol.port = tcp:localhost:1234\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.control_endpoint",
                        return_value=h1) as f:
            h = n._make_tor_handler()
            self.assertEqual(len(f.mock_calls), 1)
            ep = f.mock_calls[0][1][0]
            self.assertIsInstance(ep, endpoints.TCP4ClientEndpoint)
            self.assertIdentical(h, h1)

class I2P(unittest.TestCase):
    def test_disabled(self):
        n = FakeNode(BASECONFIG+"[i2p]\nenable = false\n")
        h = n._make_i2p_handler()
        self.assertEqual(h, None)

    def test_default(self):
        n = FakeNode(BASECONFIG)
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.default",
                        return_value=h1) as f:
            h = n._make_i2p_handler()
            self.assertEqual(f.mock_calls, [mock.call(reactor)])
            self.assertIdentical(h, h1)

    def test_samport(self):
        n = FakeNode(BASECONFIG+"[i2p]\nsam.port = tcp:localhost:1234\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.sam_endpoint",
                        return_value=h1) as f:
            h = n._make_i2p_handler()
            self.assertEqual(len(f.mock_calls), 1)
            ep = f.mock_calls[0][1][0]
            self.assertIsInstance(ep, endpoints.TCP4ClientEndpoint)
            self.assertIdentical(h, h1)

    def test_samport_and_launch(self):
        n = FakeNode(BASECONFIG+"[i2p]\n" +
                     "sam.port = tcp:localhost:1234\n"
                     +"launch = true\n")
        e = self.assertRaises(ValueError, n._make_i2p_handler)
        self.assertIn("must not set both sam.port and launch", str(e))

    def test_launch(self):
        n = FakeNode(BASECONFIG+"[i2p]\nlaunch = true\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.launch",
                        return_value=h1) as f:
            h = n._make_i2p_handler()
            exp = mock.call(i2p_configdir=None, i2p_binary=None)
            self.assertEqual(f.mock_calls, [exp])
            self.assertIdentical(h, h1)

    def test_launch_executable(self):
        n = FakeNode(BASECONFIG+"[i2p]\nlaunch = true\n" +
                     "i2p.executable = i2p\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.launch",
                        return_value=h1) as f:
            h = n._make_i2p_handler()
            exp = mock.call(i2p_configdir=None, i2p_binary="i2p")
            self.assertEqual(f.mock_calls, [exp])
            self.assertIdentical(h, h1)

    def test_launch_configdir(self):
        n = FakeNode(BASECONFIG+"[i2p]\nlaunch = true\n" +
                     "i2p.configdir = cfg\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.launch",
                        return_value=h1) as f:
            h = n._make_i2p_handler()
            exp = mock.call(i2p_configdir="cfg", i2p_binary=None)
            self.assertEqual(f.mock_calls, [exp])
            self.assertIdentical(h, h1)

    def test_launch_configdir_and_executable(self):
        n = FakeNode(BASECONFIG+"[i2p]\nlaunch = true\n" +
                     "i2p.executable = i2p\n" +
                     "i2p.configdir = cfg\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.launch",
                        return_value=h1) as f:
            h = n._make_i2p_handler()
            exp = mock.call(i2p_configdir="cfg", i2p_binary="i2p")
            self.assertEqual(f.mock_calls, [exp])
            self.assertIdentical(h, h1)

    def test_configdir(self):
        n = FakeNode(BASECONFIG+"[i2p]\ni2p.configdir = cfg\n")
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.local_i2p",
                        return_value=h1) as f:
            h = n._make_i2p_handler()
            self.assertEqual(f.mock_calls, [mock.call("cfg")])
            self.assertIdentical(h, h1)

class Connections(unittest.TestCase):
    def test_default(self):
        n = FakeNode(BASECONFIG)
        n.init_connections()
        self.assertEqual(n._default_connection_handlers["tcp"], "tcp")
        self.assertEqual(n._default_connection_handlers["tor"], "tor")
        self.assertEqual(n._default_connection_handlers["i2p"], "i2p")

    def test_tor(self):
        n = FakeNode(BASECONFIG+"[connections]\ntcp = tor\n")
        n.init_connections()
        self.assertEqual(n._default_connection_handlers["tcp"], "tor")
        self.assertEqual(n._default_connection_handlers["tor"], "tor")
        self.assertEqual(n._default_connection_handlers["i2p"], "i2p")

    def test_unknown(self):
        n = FakeNode(BASECONFIG+"[connections]\ntcp = unknown\n")
        e = self.assertRaises(ValueError, n.init_connections)
        self.assertIn("'tahoe.cfg [connections] tcp='", str(e))
        self.assertIn("uses unknown handler type 'unknown'", str(e))
