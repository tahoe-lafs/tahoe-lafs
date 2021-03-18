"""
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
from twisted.internet import reactor

from foolscap.connections import tcp

from testtools.matchers import (
    MatchesDict,
    IsInstance,
    Equals,
)

from ..node import PrivacyError, config_from_string
from ..node import create_connection_handlers
from ..node import create_main_tub
from ..util.i2p_provider import create as create_i2p_provider
from ..util.tor_provider import create as create_tor_provider

from .common import (
    SyncTestCase,
    ConstantAddresses,
)


BASECONFIG = ""


class CreateConnectionHandlersTests(SyncTestCase):
    """
    Tests for the Foolscap connection handlers return by
    ``create_connection_handlers``.
    """
    def test_foolscap_handlers(self):
        """
        ``create_connection_handlers`` returns a Foolscap connection handlers
        dictionary mapping ``"tcp"`` to
        ``foolscap.connections.tcp.DefaultTCP``, ``"tor"`` to the supplied Tor
        provider's handler, and ``"i2p"`` to the supplied I2P provider's
        handler.
        """
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG,
        )
        tor_endpoint = object()
        tor = ConstantAddresses(handler=tor_endpoint)
        i2p_endpoint = object()
        i2p = ConstantAddresses(handler=i2p_endpoint)
        _, foolscap_handlers = create_connection_handlers(
            config,
            i2p,
            tor,
        )
        self.assertThat(
            foolscap_handlers,
            MatchesDict({
                "tcp": IsInstance(tcp.DefaultTCP),
                "i2p": Equals(i2p_endpoint),
                "tor": Equals(tor_endpoint),
            }),
        )


class Tor(unittest.TestCase):

    def test_socksport_bad_endpoint(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + "[tor]\nsocks.port = meow:unsupported\n",
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
            BASECONFIG + "[tor]\nsocks.port = tcp:localhost:kumquat\n",
        )
        with self.assertRaises(ValueError) as ctx:
            tor_provider = create_tor_provider(reactor, config)
            tor_provider.get_tor_handler()
        self.assertIn(
            "invalid literal for int()",
            str(ctx.exception)
        )
        self.assertIn(
            "kumquat",
            str(ctx.exception)
        )

class I2P(unittest.TestCase):

    def test_samport_and_launch(self):
        config = config_from_string(
            "no-basedir",
            "fake.port",
            BASECONFIG + "[i2p]\n" +
            "sam.port = tcp:localhost:1234\n" + "launch = true\n",
        )
        with self.assertRaises(ValueError) as ctx:
            i2p_provider = create_i2p_provider(reactor, config)
            i2p_provider.get_i2p_handler()
        self.assertIn(
            "must not set both sam.port and launch",
            str(ctx.exception)
        )

class Connections(unittest.TestCase):

    def setUp(self):
        self.basedir = 'BASEDIR'
        self.config = config_from_string("fake.port", self.basedir, BASECONFIG)

    def test_default(self):
        default_connection_handlers, _ = create_connection_handlers(
            self.config,
            ConstantAddresses(handler=object()),
            ConstantAddresses(handler=object()),
        )
        self.assertEqual(default_connection_handlers["tcp"], "tcp")
        self.assertEqual(default_connection_handlers["tor"], "tor")
        self.assertEqual(default_connection_handlers["i2p"], "i2p")

    def test_tor(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + "[connections]\ntcp = tor\n",
        )
        default_connection_handlers, _ = create_connection_handlers(
            config,
            ConstantAddresses(handler=object()),
            ConstantAddresses(handler=object()),
        )

        self.assertEqual(default_connection_handlers["tcp"], "tor")
        self.assertEqual(default_connection_handlers["tor"], "tor")
        self.assertEqual(default_connection_handlers["i2p"], "i2p")

    def test_tor_unimportable(self):
        """
        If the configuration calls for substituting Tor for TCP and
        ``foolscap.connections.tor`` is not importable then
        ``create_connection_handlers`` raises ``ValueError`` with a message
        explaining this makes Tor unusable.
        """
        self.config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + "[connections]\ntcp = tor\n",
        )
        tor_provider = create_tor_provider(
            reactor,
            self.config,
            import_tor=lambda: None,
        )
        with self.assertRaises(ValueError) as ctx:
            default_connection_handlers, _ = create_connection_handlers(
                self.config,
                i2p_provider=ConstantAddresses(handler=object()),
                tor_provider=tor_provider,
            )
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
            BASECONFIG + "[connections]\ntcp = unknown\n",
        )
        with self.assertRaises(ValueError) as ctx:
            create_connection_handlers(
                config,
                ConstantAddresses(handler=object()),
                ConstantAddresses(handler=object()),
            )
        self.assertIn("'tahoe.cfg [connections] tcp='", str(ctx.exception))
        self.assertIn("uses unknown handler type 'unknown'", str(ctx.exception))

    def test_tcp_disabled(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + "[connections]\ntcp = disabled\n",
        )
        default_connection_handlers, _ = create_connection_handlers(
            config,
            ConstantAddresses(handler=object()),
            ConstantAddresses(handler=object()),
        )
        self.assertEqual(default_connection_handlers["tcp"], None)
        self.assertEqual(default_connection_handlers["tor"], "tor")
        self.assertEqual(default_connection_handlers["i2p"], "i2p")

class Privacy(unittest.TestCase):

    def test_connections(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + "[node]\nreveal-IP-address = false\n",
        )

        with self.assertRaises(PrivacyError) as ctx:
            create_connection_handlers(
                config,
                ConstantAddresses(handler=object()),
                ConstantAddresses(handler=object()),
            )

        self.assertEqual(
            str(ctx.exception),
            "Privacy requested with `reveal-IP-address = false` "
            "but `tcp = tcp` conflicts with this.",
        )

    def test_connections_tcp_disabled(self):
        config = config_from_string(
            "no-basedir",
            "fake.port",
            BASECONFIG + "[connections]\ntcp = disabled\n" +
            "[node]\nreveal-IP-address = false\n",
        )
        default_connection_handlers, _ = create_connection_handlers(
            config,
            ConstantAddresses(handler=object()),
            ConstantAddresses(handler=object()),
        )
        self.assertEqual(default_connection_handlers["tcp"], None)

    def test_tub_location_auto(self):
        config = config_from_string(
            "fake.port",
            "no-basedir",
            BASECONFIG + "[node]\nreveal-IP-address = false\n",
        )

        with self.assertRaises(PrivacyError) as ctx:
            create_main_tub(
                config,
                tub_options={},
                default_connection_handlers={},
                foolscap_connection_handlers={},
                i2p_provider=ConstantAddresses(),
                tor_provider=ConstantAddresses(),
            )
        self.assertEqual(
            str(ctx.exception),
            "tub.location uses AUTO",
        )
