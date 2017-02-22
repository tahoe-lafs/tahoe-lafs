import os
import mock
from io import BytesIO
from twisted.trial import unittest
from twisted.internet import reactor, endpoints, defer
from twisted.internet.interfaces import IStreamClientEndpoint
from ConfigParser import SafeConfigParser
from foolscap.connections import tcp
from ..node import Node, PrivacyError
from ..util import connection_status

class FakeNode(Node):
    def __init__(self, config_str):
        self.config = SafeConfigParser()
        self.config.readfp(BytesIO(config_str))
        self._reveal_ip = True
        self.basedir = "BASEDIR"
        self.services = []
        self.create_i2p_provider()
        self.create_tor_provider()

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
        n = FakeNode(BASECONFIG+"[tor]\nenabled = false\n")
        h = n._make_tor_handler()
        self.assertEqual(h, None)

    def test_unimportable(self):
        with mock.patch("allmydata.util.tor_provider._import_tor",
                        return_value=None):
            n = FakeNode(BASECONFIG)
            h = n._make_tor_handler()
        self.assertEqual(h, None)

    def test_default(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.default_socks",
                        return_value=h1) as f:
            n = FakeNode(BASECONFIG)
            h = n._make_tor_handler()
            self.assertEqual(f.mock_calls, [mock.call()])
            self.assertIdentical(h, h1)

    def _do_test_launch(self, executable):
        # the handler is created right away
        config = BASECONFIG+"[tor]\nlaunch = true\n"
        if executable:
            config += "tor.executable = %s\n" % executable
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.control_endpoint_maker",
                        return_value=h1) as f:
            n = FakeNode(config)
            h = n._make_tor_handler()
            private_dir = os.path.join(n.basedir, "private")
            exp = mock.call(n._tor_provider._make_control_endpoint,
                            takes_status=True)
            self.assertEqual(f.mock_calls, [exp])
            self.assertIdentical(h, h1)

        # later, when Foolscap first connects, Tor should be launched
        tp = n._tor_provider
        reactor = "reactor"
        tcp = object()
        tcep = object()
        launch_tor = mock.Mock(return_value=defer.succeed(("ep_desc", tcp)))
        cfs = mock.Mock(return_value=tcep)
        with mock.patch("allmydata.util.tor_provider._launch_tor", launch_tor):
            with mock.patch("allmydata.util.tor_provider.clientFromString", cfs):
                d = tp._make_control_endpoint(reactor,
                                              update_status=lambda status: None)
                cep = self.successResultOf(d)
        launch_tor.assert_called_with(reactor, executable, private_dir,
                                      tp._txtorcon)
        cfs.assert_called_with(reactor, "ep_desc")
        self.assertIs(cep, tcep)

    def test_launch(self):
        self._do_test_launch(None)

    def test_launch_executable(self):
        self._do_test_launch("/special/tor")

    def test_socksport_unix_endpoint(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.socks_endpoint",
                        return_value=h1) as f:
            n = FakeNode(BASECONFIG+"[tor]\nsocks.port = unix:/var/lib/fw-daemon/tor_socks.socket\n")
            h = n._make_tor_handler()
            self.assertTrue(IStreamClientEndpoint.providedBy(f.mock_calls[0]))
            self.assertIdentical(h, h1)

    def test_socksport_endpoint(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.socks_endpoint",
                        return_value=h1) as f:
            n = FakeNode(BASECONFIG+"[tor]\nsocks.port = tcp:127.0.0.1:1234\n")
            h = n._make_tor_handler()
            self.assertTrue(IStreamClientEndpoint.providedBy(f.mock_calls[0]))
            self.assertIdentical(h, h1)

    def test_socksport_endpoint_otherhost(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.socks_endpoint",
                        return_value=h1) as f:
            n = FakeNode(BASECONFIG+"[tor]\nsocks.port = tcp:otherhost:1234\n")
            h = n._make_tor_handler()
            self.assertTrue(IStreamClientEndpoint.providedBy(f.mock_calls[0]))
            self.assertIdentical(h, h1)

    def test_socksport_bad_endpoint(self):
        n = FakeNode(BASECONFIG+"[tor]\nsocks.port = meow:unsupported\n")
        e = self.assertRaises(ValueError, n._make_tor_handler)
        self.assertIn("Unknown endpoint type: 'meow'", str(e))

    def test_socksport_not_integer(self):
        n = FakeNode(BASECONFIG+"[tor]\nsocks.port = tcp:localhost:kumquat\n")
        e = self.assertRaises(ValueError, n._make_tor_handler)
        self.assertIn("invalid literal for int() with base 10: 'kumquat'", str(e))

    def test_controlport(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.control_endpoint",
                        return_value=h1) as f:
            n = FakeNode(BASECONFIG+"[tor]\ncontrol.port = tcp:localhost:1234\n")
            h = n._make_tor_handler()
            self.assertEqual(len(f.mock_calls), 1)
            ep = f.mock_calls[0][1][0]
            self.assertIsInstance(ep, endpoints.TCP4ClientEndpoint)
            self.assertIdentical(h, h1)

class I2P(unittest.TestCase):
    def test_disabled(self):
        n = FakeNode(BASECONFIG+"[i2p]\nenabled = false\n")
        h = n._make_i2p_handler()
        self.assertEqual(h, None)

    def test_unimportable(self):
        with mock.patch("allmydata.util.i2p_provider._import_i2p",
                        return_value=None):
            n = FakeNode(BASECONFIG)
            h = n._make_i2p_handler()
        self.assertEqual(h, None)

    def test_default(self):
        n = FakeNode(BASECONFIG)
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.default",
                        return_value=h1) as f:
            h = n._make_i2p_handler()
            self.assertEqual(f.mock_calls, [mock.call(reactor, keyfile=None)])
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
        n.set_tub_options()
        n._create_tub()

    def test_tor(self):
        n = FakeNode(BASECONFIG+"[connections]\ntcp = tor\n")
        n.init_connections()
        self.assertEqual(n._default_connection_handlers["tcp"], "tor")
        self.assertEqual(n._default_connection_handlers["tor"], "tor")
        self.assertEqual(n._default_connection_handlers["i2p"], "i2p")

    def test_tor_unimportable(self):
        with mock.patch("allmydata.util.tor_provider._import_tor",
                        return_value=None):
            n = FakeNode(BASECONFIG+"[connections]\ntcp = tor\n")
        e = self.assertRaises(ValueError, n.init_connections)
        self.assertEqual(str(e),
                         "'tahoe.cfg [connections] tcp='"
                         " uses unavailable/unimportable handler type 'tor'."
                         " Please pip install tahoe-lafs[tor] to fix.")

    def test_unknown(self):
        n = FakeNode(BASECONFIG+"[connections]\ntcp = unknown\n")
        e = self.assertRaises(ValueError, n.init_connections)
        self.assertIn("'tahoe.cfg [connections] tcp='", str(e))
        self.assertIn("uses unknown handler type 'unknown'", str(e))

    def test_tcp_disabled(self):
        n = FakeNode(BASECONFIG+"[connections]\ntcp = disabled\n")
        n.init_connections()
        self.assertEqual(n._default_connection_handlers["tcp"], None)
        self.assertEqual(n._default_connection_handlers["tor"], "tor")
        self.assertEqual(n._default_connection_handlers["i2p"], "i2p")
        n.set_tub_options()
        n._create_tub()

class Privacy(unittest.TestCase):
    def test_flag(self):
        n = FakeNode(BASECONFIG)
        n.check_privacy()
        self.assertTrue(n._reveal_ip)

        n = FakeNode(BASECONFIG+"[node]\nreveal-IP-address = true\n")
        n.check_privacy()
        self.assertTrue(n._reveal_ip)

        n = FakeNode(BASECONFIG+"[node]\nreveal-IP-address = false\n")
        n.check_privacy()
        self.assertFalse(n._reveal_ip)

        n = FakeNode(BASECONFIG+"[node]\nreveal-ip-address = false\n")
        n.check_privacy()
        self.assertFalse(n._reveal_ip)

    def test_connections(self):
        n = FakeNode(BASECONFIG+"[node]\nreveal-IP-address = false\n")
        n.check_privacy()
        e = self.assertRaises(PrivacyError, n.init_connections)
        self.assertEqual(str(e),
                         "tcp = tcp, must be set to 'tor' or 'disabled'")

    def test_connections_tcp_disabled(self):
        n = FakeNode(BASECONFIG+
                     "[connections]\ntcp = disabled\n"+
                     "[node]\nreveal-IP-address = false\n")
        n.check_privacy()
        n.init_connections() # passes privacy check
        self.assertEqual(n._default_connection_handlers["tcp"], None)

    def test_tub_location_auto(self):
        n = FakeNode(BASECONFIG+"[node]\nreveal-IP-address = false\n")
        n._portnumfile = "missing"
        n.check_privacy()
        e = self.assertRaises(PrivacyError, n.get_tub_portlocation, None, None)
        self.assertEqual(str(e), "tub.location uses AUTO")

        n = FakeNode(BASECONFIG+"[node]\nreveal-IP-address = false\n")
        n._portnumfile = "missing"
        n.check_privacy()
        e = self.assertRaises(PrivacyError, n.get_tub_portlocation,
                              None, "AUTO")
        self.assertEqual(str(e), "tub.location uses AUTO")

        n = FakeNode(BASECONFIG+"[node]\nreveal-IP-address = false\n")
        n._portnumfile = "missing"
        n.check_privacy()
        e = self.assertRaises(PrivacyError, n.get_tub_portlocation,
                              None, "AUTO,tcp:hostname:1234")
        self.assertEqual(str(e), "tub.location uses AUTO")

    def test_tub_location_tcp(self):
        n = FakeNode(BASECONFIG+"[node]\nreveal-IP-address = false\n")
        n._portnumfile = "missing"
        n.check_privacy()
        e = self.assertRaises(PrivacyError, n.get_tub_portlocation,
                              None, "tcp:hostname:1234")
        self.assertEqual(str(e), "tub.location includes tcp: hint")

    def test_tub_location_legacy_tcp(self):
        n = FakeNode(BASECONFIG+"[node]\nreveal-IP-address = false\n")
        n._portnumfile = "missing"
        n.check_privacy()
        e = self.assertRaises(PrivacyError, n.get_tub_portlocation,
                              None, "hostname:1234")
        self.assertEqual(str(e), "tub.location includes tcp: hint")

class Status(unittest.TestCase):
    def test_hint_statuses(self):
        ncs = connection_status._hint_statuses(["h2","h1"],
                                               {"h1": "hand1", "h4": "hand4"},
                                               {"h1": "st1", "h2": "st2",
                                                "h3": "st3"})
        self.assertEqual(ncs, {"h1 via hand1": "st1",
                               "h2": "st2"})

    def test_reconnector_connected(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1"}
        ci.connectionHandlers = {"h1": "hand1"}
        ci.winningHint = "h1"
        ci.establishedAt = 120
        ri = mock.Mock()
        ri.state = "connected"
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected to h1 via hand1")
        self.assertEqual(cs.non_connected_statuses, {})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connected_others(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1", "h2": "st2"}
        ci.connectionHandlers = {"h1": "hand1"}
        ci.winningHint = "h1"
        ci.establishedAt = 120
        ri = mock.Mock()
        ri.state = "connected"
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected to h1 via hand1")
        self.assertEqual(cs.non_connected_statuses, {"h2": "st2"})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connected_listener(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1", "h2": "st2"}
        ci.connectionHandlers = {"h1": "hand1"}
        ci.listenerStatus = ("listener1", "successful")
        ci.winningHint = None
        ci.establishedAt = 120
        ri = mock.Mock()
        ri.state = "connected"
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected via listener (listener1)")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connecting(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1", "h2": "st2"}
        ci.connectionHandlers = {"h1": "hand1"}
        ri = mock.Mock()
        ri.state = "connecting"
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, False)
        self.assertEqual(cs.summary, "Trying to connect")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, None)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_waiting(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1", "h2": "st2"}
        ci.connectionHandlers = {"h1": "hand1"}
        ri = mock.Mock()
        ri.state = "waiting"
        ri.lastAttempt = 10
        ri.nextAttempt = 20
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        with mock.patch("time.time", return_value=12):
            cs = connection_status.from_foolscap_reconnector(rc, 5)
        self.assertEqual(cs.connected, False)
        self.assertEqual(cs.summary,
                         "Reconnecting in 8 seconds (last attempt 2s ago)")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, None)
        self.assertEqual(cs.last_received_time, 5)

