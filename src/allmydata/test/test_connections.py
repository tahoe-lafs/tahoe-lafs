import os
import mock
from twisted.trial import unittest
from twisted.internet import reactor, endpoints, defer
from twisted.internet.interfaces import IStreamClientEndpoint
from foolscap.connections import tcp
from ..node import PrivacyError, config_from_string
from ..node import create_connection_handlers
from ..node import create_main_tub, _tub_portlocation
from ..util.i2p_provider import create as create_i2p_provider
from ..util.tor_provider import create as create_tor_provider


BASECONFIG = b""


class TCP(unittest.TestCase):

    def test_default(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG,
        )
        _, foolscap_handlers = create_connection_handlers(None, config, mock.Mock(), mock.Mock())
        self.assertIsInstance(
            foolscap_handlers['tcp'],
            tcp.DefaultTCP,
        )


class Tor(unittest.TestCase):

    def test_disabled(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[tor]\nenabled = false\n",
        )
        tor_provider = create_tor_provider(reactor, config)
        h = tor_provider.get_tor_handler()
        self.assertEqual(h, None)

    def test_unimportable(self):
        with mock.patch("allmydata.util.tor_provider._import_tor",
                        return_value=None):
            config = config_from_string("fake.port", "no-basedir", BASECONFIG)
            tor_provider = create_tor_provider(reactor, config)
            h = tor_provider.get_tor_handler()
        self.assertEqual(h, None)

    def test_default(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.default_socks",
                        return_value=h1) as f:

            config = config_from_string("fake.port", "no-basedir", BASECONFIG)
            tor_provider = create_tor_provider(reactor, config)
            h = tor_provider.get_tor_handler()
        self.assertEqual(f.mock_calls, [mock.call()])
        self.assertIdentical(h, h1)

    def _do_test_launch(self, executable):
        # the handler is created right away
        config = BASECONFIG + b"[tor]\nlaunch = true\n"
        if executable:
            config += "tor.executable = %s\n" % executable
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.control_endpoint_maker",
                        return_value=h1) as f:

            config = config_from_string("fake.port", ".", config)
            tp = create_tor_provider("reactor", config)
            h = tp.get_tor_handler()

            private_dir = config.get_config_path("private")
            exp = mock.call(tp._make_control_endpoint,
                            takes_status=True)
            self.assertEqual(f.mock_calls, [exp])
            self.assertIdentical(h, h1)

        # later, when Foolscap first connects, Tor should be launched
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
        launch_tor.assert_called_with(reactor, executable,
                                      os.path.abspath(private_dir),
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
            config = config_from_string(
                "fake.port",
                "no-basedir",
                (
                    BASECONFIG +
                    b"[tor]\nsocks.port = unix:/var/lib/fw-daemon/tor_socks.socket\n"
                ),
            )
            tor_provider = create_tor_provider(reactor, config)
            h = tor_provider.get_tor_handler()
        self.assertTrue(IStreamClientEndpoint.providedBy(f.mock_calls[0][1][0]))
        self.assertIdentical(h, h1)

    def test_socksport_endpoint(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.socks_endpoint",
                        return_value=h1) as f:
            config = config_from_string(
                "fake.port",
                "no-basedir",
                BASECONFIG + b"[tor]\nsocks.port = tcp:127.0.0.1:1234\n",
            )
            tor_provider = create_tor_provider(reactor, config)
            h = tor_provider.get_tor_handler()
        self.assertTrue(IStreamClientEndpoint.providedBy(f.mock_calls[0][1][0]))
        self.assertIdentical(h, h1)

    def test_socksport_endpoint_otherhost(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.socks_endpoint",
                        return_value=h1) as f:
            config = config_from_string(
                "no-basedir",
                "fake.port",
                BASECONFIG + b"[tor]\nsocks.port = tcp:otherhost:1234\n",
            )
            tor_provider = create_tor_provider(reactor, config)
            h = tor_provider.get_tor_handler()
        self.assertTrue(IStreamClientEndpoint.providedBy(f.mock_calls[0][1][0]))
        self.assertIdentical(h, h1)

    def test_socksport_bad_endpoint(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[tor]\nsocks.port = meow:unsupported\n",
        )
        with self.assertRaises(ValueError) as ctx:
            tor_provider = create_tor_provider(reactor, config)
            tor_provider.get_tor_handler()
        self.assertIn(
            "Unknown endpoint type: 'meow'",
            str(ctx.exception)
        )

    def test_socksport_not_integer(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[tor]\nsocks.port = tcp:localhost:kumquat\n",
        )
        with self.assertRaises(ValueError) as ctx:
            tor_provider = create_tor_provider(reactor, config)
            tor_provider.get_tor_handler()
        self.assertIn(
            "invalid literal for int() with base 10: 'kumquat'",
            str(ctx.exception)
        )

    def test_controlport(self):
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.tor.control_endpoint",
                        return_value=h1) as f:
            config = config_from_string(
                "fake.port",
                "no-basedir",
                BASECONFIG + b"[tor]\ncontrol.port = tcp:localhost:1234\n",
            )
            tor_provider = create_tor_provider(reactor, config)
            h = tor_provider.get_tor_handler()
            self.assertEqual(len(f.mock_calls), 1)
            ep = f.mock_calls[0][1][0]
            self.assertIsInstance(ep, endpoints.TCP4ClientEndpoint)
            self.assertIdentical(h, h1)

class I2P(unittest.TestCase):

    def test_disabled(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[i2p]\nenabled = false\n",
        )
        i2p_provider = create_i2p_provider(None, config)
        h = i2p_provider.get_i2p_handler()
        self.assertEqual(h, None)

    def test_unimportable(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG,
        )
        with mock.patch("allmydata.util.i2p_provider._import_i2p",
                        return_value=None):
            i2p_provider = create_i2p_provider(reactor, config)
            h = i2p_provider.get_i2p_handler()
        self.assertEqual(h, None)

    def test_default(self):
        config = config_from_string("fake.port", "no-basedir", BASECONFIG)
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.default",
                        return_value=h1) as f:
            i2p_provider = create_i2p_provider(reactor, config)
            h = i2p_provider.get_i2p_handler()
        self.assertEqual(f.mock_calls, [mock.call(reactor, keyfile=None)])
        self.assertIdentical(h, h1)

    def test_samport(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[i2p]\nsam.port = tcp:localhost:1234\n",
        )
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.sam_endpoint",
                        return_value=h1) as f:
            i2p_provider = create_i2p_provider(reactor, config)
            h = i2p_provider.get_i2p_handler()

        self.assertEqual(len(f.mock_calls), 1)
        ep = f.mock_calls[0][1][0]
        self.assertIsInstance(ep, endpoints.TCP4ClientEndpoint)
        self.assertIdentical(h, h1)

    def test_samport_and_launch(self):
        config = config_from_string(
            "no-basedir",
            "fake.port",
            (
                BASECONFIG +
                b"[i2p]\n" +
                b"sam.port = tcp:localhost:1234\n" +
                b"launch = true\n"
            ),
        )
        with self.assertRaises(ValueError) as ctx:
            i2p_provider = create_i2p_provider(reactor, config)
            i2p_provider.get_i2p_handler()
        self.assertIn(
            "must not set both sam.port and launch",
            str(ctx.exception)
        )

    def test_launch(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[i2p]\nlaunch = true\n",
        )
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.launch",
                        return_value=h1) as f:
            i2p_provider = create_i2p_provider(reactor, config)
            h = i2p_provider.get_i2p_handler()
            exp = mock.call(i2p_configdir=None, i2p_binary=None)
        self.assertEqual(f.mock_calls, [exp])
        self.assertIdentical(h, h1)

    def test_launch_executable(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            (
                BASECONFIG +
                b"[i2p]\nlaunch = true\n" +
                b"i2p.executable = i2p\n"
            ),
        )
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.launch",
                        return_value=h1) as f:
            i2p_provider = create_i2p_provider(reactor, config)
            h = i2p_provider.get_i2p_handler()
            exp = mock.call(i2p_configdir=None, i2p_binary="i2p")
        self.assertEqual(f.mock_calls, [exp])
        self.assertIdentical(h, h1)

    def test_launch_configdir(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            (
                BASECONFIG +
                b"[i2p]\nlaunch = true\n" +
                b"i2p.configdir = cfg\n"
            ),
        )
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.launch",
                        return_value=h1) as f:
            i2p_provider = create_i2p_provider(reactor, config)
            h = i2p_provider.get_i2p_handler()
            exp = mock.call(i2p_configdir="cfg", i2p_binary=None)
        self.assertEqual(f.mock_calls, [exp])
        self.assertIdentical(h, h1)

    def test_launch_configdir_and_executable(self):
        config = config_from_string(
            "no-basedir",
            "fake.port",
            (
                BASECONFIG +
                b"[i2p]\nlaunch = true\n" +
                b"i2p.executable = i2p\n" +
                b"i2p.configdir = cfg\n"
            ),
        )
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.launch",
                        return_value=h1) as f:
            i2p_provider = create_i2p_provider(reactor, config)
            h = i2p_provider.get_i2p_handler()
            exp = mock.call(i2p_configdir="cfg", i2p_binary="i2p")
        self.assertEqual(f.mock_calls, [exp])
        self.assertIdentical(h, h1)

    def test_configdir(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[i2p]\ni2p.configdir = cfg\n",
        )
        h1 = mock.Mock()
        with mock.patch("foolscap.connections.i2p.local_i2p",
                        return_value=h1) as f:
            i2p_provider = create_i2p_provider(None, config)
            h = i2p_provider.get_i2p_handler()

        self.assertEqual(f.mock_calls, [mock.call("cfg")])
        self.assertIdentical(h, h1)

class Connections(unittest.TestCase):

    def setUp(self):
        self.basedir = 'BASEDIR'
        self.config = config_from_string("fake.port", self.basedir, BASECONFIG)

    def test_default(self):
        default_connection_handlers, _ = create_connection_handlers(None, self.config, mock.Mock(), mock.Mock())
        self.assertEqual(default_connection_handlers["tcp"], "tcp")
        self.assertEqual(default_connection_handlers["tor"], "tor")
        self.assertEqual(default_connection_handlers["i2p"], "i2p")

    def test_tor(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[connections]\ntcp = tor\n",
        )
        default_connection_handlers, _ = create_connection_handlers(None, config, mock.Mock(), mock.Mock())

        self.assertEqual(default_connection_handlers["tcp"], "tor")
        self.assertEqual(default_connection_handlers["tor"], "tor")
        self.assertEqual(default_connection_handlers["i2p"], "i2p")

    def test_tor_unimportable(self):
        with mock.patch("allmydata.util.tor_provider._import_tor",
                        return_value=None):
            self.config = config_from_string(
                "fake.port",
                "no-basedir",
                BASECONFIG + b"[connections]\ntcp = tor\n",
            )
            with self.assertRaises(ValueError) as ctx:
                tor_provider = create_tor_provider(reactor, self.config)
                default_connection_handlers, _ = create_connection_handlers(None, self.config, mock.Mock(), tor_provider)
        self.assertEqual(
            str(ctx.exception),
            "'tahoe.cfg [connections] tcp='"
            " uses unavailable/unimportable handler type 'tor'."
            " Please pip install tahoe-lafs[tor] to fix.",
        )

    def test_unknown(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[connections]\ntcp = unknown\n",
        )
        with self.assertRaises(ValueError) as ctx:
            create_connection_handlers(None, config, mock.Mock(), mock.Mock())
        self.assertIn("'tahoe.cfg [connections] tcp='", str(ctx.exception))
        self.assertIn("uses unknown handler type 'unknown'", str(ctx.exception))

    def test_tcp_disabled(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[connections]\ntcp = disabled\n",
        )
        default_connection_handlers, _ = create_connection_handlers(None, config, mock.Mock(), mock.Mock())
        self.assertEqual(default_connection_handlers["tcp"], None)
        self.assertEqual(default_connection_handlers["tor"], "tor")
        self.assertEqual(default_connection_handlers["i2p"], "i2p")

class Privacy(unittest.TestCase):

    def test_connections(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[node]\nreveal-IP-address = false\n",
        )

        with self.assertRaises(PrivacyError) as ctx:
            create_connection_handlers(None, config, mock.Mock(), mock.Mock())

        self.assertEqual(
            str(ctx.exception),
            "tcp = tcp, must be set to 'tor' or 'disabled'",
        )

    def test_connections_tcp_disabled(self):
        config = config_from_string(
            "no-basedir",
            "fake.port",
            (
                BASECONFIG +
                b"[connections]\ntcp = disabled\n" +
                b"[node]\nreveal-IP-address = false\n"
            ),
        )
        default_connection_handlers, _ = create_connection_handlers(None, config, mock.Mock(), mock.Mock())
        self.assertEqual(default_connection_handlers["tcp"], None)

    def test_tub_location_auto(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + b"[node]\nreveal-IP-address = false\n",
        )

        with self.assertRaises(PrivacyError) as ctx:
            create_main_tub(config, {}, {}, {}, mock.Mock(), mock.Mock())
        self.assertEqual(
            str(ctx.exception),
            "tub.location uses AUTO",
        )

    def test_tub_location_tcp(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            (
                BASECONFIG +
                b"[node]\nreveal-IP-address = false\ntub.location=tcp:hostname:1234\n"
            ),
        )
        with self.assertRaises(PrivacyError) as ctx:
            _tub_portlocation(config)
        self.assertEqual(
            str(ctx.exception),
            "tub.location includes tcp: hint",
        )

    def test_tub_location_legacy_tcp(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            (
                BASECONFIG +
                b"[node]\nreveal-IP-address = false\ntub.location=hostname:1234\n"
            ),
        )

        with self.assertRaises(PrivacyError) as ctx:
            _tub_portlocation(config)

        self.assertEqual(
            str(ctx.exception),
            "tub.location includes tcp: hint",
        )
