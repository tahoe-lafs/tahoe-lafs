from __future__ import annotations

import base64
import os
import stat
import sys
import time
from textwrap import dedent
import configparser

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    integers,
    sets,
)

from unittest import skipIf

from twisted.python.filepath import (
    FilePath,
)
from twisted.python.runtime import platform
from twisted.trial import unittest
from twisted.internet import defer

import foolscap.logging.log

from twisted.application import service
from allmydata.node import (
    PortAssignmentRequired,
    PrivacyError,
    tub_listen_on,
    create_tub_options,
    create_main_tub,
    create_node_dir,
    create_default_connection_handlers,
    create_connection_handlers,
    config_from_string,
    read_config,
    MissingConfigEntry,
    _tub_portlocation,
    formatTimeTahoeStyle,
    UnescapedHashError,
)
from allmydata.introducer.server import create_introducer
from allmydata import client

from allmydata.util import fileutil, iputil
from allmydata.util.namespace import Namespace
from allmydata.util.configutil import (
    ValidConfiguration,
    UnknownConfigError,
)

from allmydata.util.i2p_provider import create as create_i2p_provider
from allmydata.util.tor_provider import create as create_tor_provider
import allmydata.test.common_util as testutil

from .common import (
    ConstantAddresses,
    SameProcessStreamEndpointAssigner,
    UseNode,
    superuser,
)

def port_numbers():
    return integers(min_value=1, max_value=2 ** 16 - 1)

class LoggingMultiService(service.MultiService):
    def log(self, msg, **kw):
        pass


# see https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2946
def testing_tub(reactor, config_data=''):
    """
    Creates a 'main' Tub for testing purposes, from config data
    """
    basedir = 'dummy_basedir'
    config = config_from_string(basedir, 'DEFAULT_PORTNUMFILE_BLANK', config_data)
    fileutil.make_dirs(os.path.join(basedir, 'private'))

    i2p_provider = create_i2p_provider(reactor, config)
    tor_provider = create_tor_provider(reactor, config)
    handlers = create_connection_handlers(config, i2p_provider, tor_provider)
    default_connection_handlers, foolscap_connection_handlers = handlers
    tub_options = create_tub_options(config)

    main_tub = create_main_tub(
        config, tub_options, default_connection_handlers,
        foolscap_connection_handlers, i2p_provider, tor_provider,
        cert_filename='DEFAULT_CERTFILE_BLANK'
    )
    return main_tub


class TestCase(testutil.SignalMixin, unittest.TestCase):

    def setUp(self):
        testutil.SignalMixin.setUp(self)
        self.parent = LoggingMultiService()
        # We can use a made-up port number because these tests never actually
        # try to bind the port.  We'll use a low-numbered one that's likely to
        # conflict with another service to prove it.
        self._available_port = 22
        self.port_assigner = SameProcessStreamEndpointAssigner()
        self.port_assigner.setUp()
        self.addCleanup(self.port_assigner.tearDown)

    def _test_location(
            self,
            expected_addresses,
            tub_port=None,
            tub_location=None,
            local_addresses=None,
    ):
        """
        Verify that a Tub configured with the given *tub.port* and *tub.location*
        values generates fURLs with the given addresses in its location hints.

        :param [str] expected_addresses: The addresses which must appear in
            the generated fURL for the test to pass.  All addresses must
            appear.

        :param tub_port: If not ``None`` then a value for the *tub.port*
            configuration item.

        :param tub_location: If not ``None`` then a value for the *tub.port*
            configuration item.

        :param local_addresses: If not ``None`` then a list of addresses to
            supply to the system under test as local addresses.
        """
        from twisted.internet import reactor

        basedir = self.mktemp()
        create_node_dir(basedir, "testing")
        if tub_port is None:
            # Always configure a usable tub.port address instead of relying on
            # the automatic port assignment.  The automatic port assignment is
            # prone to collisions and spurious test failures.
            _, tub_port = self.port_assigner.assign(reactor)

        config_data = "[node]\n"
        config_data += "tub.port = {}\n".format(tub_port)

        # If they wanted a certain location, go for it.  This probably won't
        # agree with the tub.port value we set but that only matters if
        # anything tries to use this to establish a connection ... which
        # nothing in this test suite will.
        if tub_location is not None:
            config_data += "tub.location = {}\n".format(tub_location)

        if local_addresses is not None:
            self.patch(iputil, 'get_local_addresses_sync',
                       lambda: local_addresses)

        tub = testing_tub(reactor, config_data)

        class Foo(object):
            pass

        furl = tub.registerReference(Foo())
        for address in expected_addresses:
            self.assertIn(address, furl)

    def test_location1(self):
        return self._test_location(expected_addresses=["192.0.2.0:1234"],
                                   tub_location="192.0.2.0:1234")

    def test_location2(self):
        return self._test_location(expected_addresses=["192.0.2.0:1234", "example.org:8091"],
                                   tub_location="192.0.2.0:1234,example.org:8091")

    def test_location_not_set(self):
        """Checks the autogenerated furl when tub.location is not set."""
        return self._test_location(
            expected_addresses=[
                "127.0.0.1:{}".format(self._available_port),
                "192.0.2.0:{}".format(self._available_port),
            ],
            tub_port=self._available_port,
            local_addresses=["127.0.0.1", "192.0.2.0"],
        )

    def test_location_auto_and_explicit(self):
        """Checks the autogenerated furl when tub.location contains 'AUTO'."""
        return self._test_location(
            expected_addresses=[
                "127.0.0.1:{}".format(self._available_port),
                "192.0.2.0:{}".format(self._available_port),
                "example.com:4321",
            ],
            tub_port=self._available_port,
            tub_location="AUTO,example.com:{}".format(self._available_port),
            local_addresses=["127.0.0.1", "192.0.2.0", "example.com:4321"],
        )

    def test_tahoe_cfg_utf8(self):
        basedir = "test_node/test_tahoe_cfg_utf8"
        fileutil.make_dirs(basedir)
        f = open(os.path.join(basedir, 'tahoe.cfg'), 'wb')
        f.write(u"\uFEFF[node]\n".encode('utf-8'))
        f.write(u"nickname = \u2621\n".encode('utf-8'))
        f.close()

        config = read_config(basedir, "")
        self.failUnlessEqual(config.get_config("node", "nickname"),
                             u"\u2621")

    def test_tahoe_cfg_hash_in_name(self):
        basedir = "test_node/test_cfg_hash_in_name"
        nickname = "Hash#Bang!" # a clever nickname containing a hash
        fileutil.make_dirs(basedir)
        f = open(os.path.join(basedir, 'tahoe.cfg'), 'wt')
        f.write("[node]\n")
        f.write("nickname = %s\n" % (nickname,))
        f.close()

        config = read_config(basedir, "")
        self.failUnless(config.nickname == nickname)

    def test_hash_in_furl(self):
        """
        Hashes in furl options are not allowed, resulting in exception.
        """
        basedir = self.mktemp()
        fileutil.make_dirs(basedir)
        with open(os.path.join(basedir, 'tahoe.cfg'), 'wt') as f:
            f.write("[node]\n")
            f.write("log_gatherer.furl = lalal#onohash\n")

        config = read_config(basedir, "")
        with self.assertRaises(UnescapedHashError):
            config.get_config("node", "log_gatherer.furl")

    def test_missing_config_item(self):
        """
        If a config item is missing:

        1. Given a default, return default.
        2. Otherwise, raise MissingConfigEntry.
        """
        basedir = self.mktemp()
        fileutil.make_dirs(basedir)
        with open(os.path.join(basedir, 'tahoe.cfg'), 'wt') as f:
            f.write("[node]\n")
        config = read_config(basedir, "")

        self.assertEquals(config.get_config("node", "log_gatherer.furl", "def"), "def")
        with self.assertRaises(MissingConfigEntry):
            config.get_config("node", "log_gatherer.furl")

    def test_missing_config_section(self):
        """
        Enumerating a missing section returns empty dict
        """
        basedir = self.mktemp()
        fileutil.make_dirs(basedir)
        with open(os.path.join(basedir, 'tahoe.cfg'), 'w'):
            pass
        config = read_config(basedir, "")
        self.assertEquals(
            config.enumerate_section("not-a-section"),
            {}
        )

    def test_config_required(self):
        """
        Asking for missing (but required) configuration is an error
        """
        basedir = u"test_node/test_config_required"
        config = read_config(basedir, "portnum")

        with self.assertRaises(Exception):
            config.get_config_from_file("it_does_not_exist", required=True)

    def test_config_items(self):
        """
        All items in a config section can be retrieved.
        """
        basedir = u"test_node/test_config_items"
        create_node_dir(basedir, "testing")

        with open(os.path.join(basedir, 'tahoe.cfg'), 'wt') as f:
            f.write(dedent(
                """
                [node]
                nickname = foo
                timeout.disconnect = 12
                """
            ))
        config = read_config(basedir, "portnum")
        self.assertEqual(
            config.items("node"),
            [("nickname", "foo"),
             ("timeout.disconnect", "12"),
            ],
        )
        self.assertEqual(
            config.items("node", [("unnecessary", "default")]),
            [("nickname", "foo"),
             ("timeout.disconnect", "12"),
            ],
        )


    def test_config_items_missing_section(self):
        """
        If a default is given for a missing section, the default is used.

        Lacking both default and section, an error is raised.
        """
        basedir = self.mktemp()
        create_node_dir(basedir, "testing")

        with open(os.path.join(basedir, 'tahoe.cfg'), 'wt') as f:
            f.write("")

        config = read_config(basedir, "portnum")
        with self.assertRaises(configparser.NoSectionError):
            config.items("nosuch")
        default = [("hello", "world")]
        self.assertEqual(config.items("nosuch", default), default)

    @skipIf(platform.isWindows(), "We don't know how to set permissions on Windows.")
    @skipIf(superuser, "cannot test as superuser with all permissions")
    def test_private_config_unreadable(self):
        """
        Asking for inaccessible private config is an error
        """
        basedir = u"test_node/test_private_config_unreadable"
        create_node_dir(basedir, "testing")
        config = read_config(basedir, "portnum")
        config.get_or_create_private_config("foo", "contents")
        fname = os.path.join(basedir, "private", "foo")
        os.chmod(fname, 0)

        with self.assertRaises(Exception):
            config.get_or_create_private_config("foo")

    @skipIf(platform.isWindows(), "We don't know how to set permissions on Windows.")
    @skipIf(superuser, "cannot test as superuser with all permissions")
    def test_private_config_unreadable_preexisting(self):
        """
        error if reading private config data fails
        """
        basedir = u"test_node/test_private_config_unreadable_preexisting"
        create_node_dir(basedir, "testing")
        config = read_config(basedir, "portnum")
        fname = os.path.join(basedir, "private", "foo")
        with open(fname, "w") as f:
            f.write("stuff")
        os.chmod(fname, 0)

        with self.assertRaises(Exception):
            config.get_private_config("foo")

    def test_private_config_missing(self):
        """
        a missing config with no default is an error
        """
        basedir = u"test_node/test_private_config_missing"
        create_node_dir(basedir, "testing")
        config = read_config(basedir, "portnum")

        with self.assertRaises(MissingConfigEntry):
            config.get_or_create_private_config("foo")

    def test_private_config(self):
        basedir = u"test_node/test_private_config"
        privdir = os.path.join(basedir, "private")
        fileutil.make_dirs(privdir)
        f = open(os.path.join(privdir, 'already'), 'wt')
        f.write("secret")
        f.close()

        basedir = fileutil.abspath_expanduser_unicode(basedir)
        config = config_from_string(basedir, "", "")

        self.assertEqual(config.get_private_config("already"), "secret")
        self.assertEqual(config.get_private_config("not", "default"), "default")
        self.assertRaises(MissingConfigEntry, config.get_private_config, "not")
        value = config.get_or_create_private_config("new", "start")
        self.assertEqual(value, "start")
        self.assertEqual(config.get_private_config("new"), "start")
        counter = []
        def make_newer():
            counter.append("called")
            return "newer"
        value = config.get_or_create_private_config("newer", make_newer)
        self.assertEqual(len(counter), 1)
        self.assertEqual(value, "newer")
        self.assertEqual(config.get_private_config("newer"), "newer")

        value = config.get_or_create_private_config("newer", make_newer)
        self.assertEqual(len(counter), 1) # don't call unless necessary
        self.assertEqual(value, "newer")

    @skipIf(superuser, "cannot test as superuser with all permissions")
    def test_write_config_unwritable_file(self):
        """
        Existing behavior merely logs any errors upon writing
        configuration files; this bad behavior should probably be
        fixed to do something better (like fail entirely). See #2905
        """
        basedir = "test_node/configdir"
        fileutil.make_dirs(basedir)
        config = config_from_string(basedir, "", "")
        with open(os.path.join(basedir, "bad"), "w") as f:
            f.write("bad")
        os.chmod(os.path.join(basedir, "bad"), 0o000)

        config.write_config_file("bad", "some value")

        errs = self.flushLoggedErrors(IOError)
        self.assertEqual(1, len(errs))

    def test_timestamp(self):
        # this modified logger doesn't seem to get used during the tests,
        # probably because we don't modify the LogObserver that trial
        # installs (only the one that twistd installs). So manually exercise
        # it a little bit.
        t = formatTimeTahoeStyle("ignored", time.time())
        self.failUnless("Z" in t)
        t2 = formatTimeTahoeStyle("ignored", int(time.time()))
        self.failUnless("Z" in t2)

    def test_secrets_dir(self):
        basedir = "test_node/test_secrets_dir"
        create_node_dir(basedir, "testing")
        self.failUnless(os.path.exists(os.path.join(basedir, "private")))

    def test_secrets_dir_protected(self):
        if "win32" in sys.platform.lower() or "cygwin" in sys.platform.lower():
            # We don't know how to test that unprivileged users can't read this
            # thing.  (Also we don't know exactly how to set the permissions so
            # that unprivileged users can't read this thing.)
            raise unittest.SkipTest("We don't know how to set permissions on Windows.")
        basedir = "test_node/test_secrets_dir_protected"
        create_node_dir(basedir, "nothing to see here")

        # make sure private dir was created with correct modes
        privdir = os.path.join(basedir, "private")
        st = os.stat(privdir)
        bits = stat.S_IMODE(st[stat.ST_MODE])
        self.failUnless(bits & 0o001 == 0, bits)

    @defer.inlineCallbacks
    def test_logdir_is_str(self):
        from twisted.internet import reactor

        basedir = FilePath(self.mktemp())
        fixture = UseNode(None, None, basedir, "pb://introducer/furl", {}, reactor=reactor)
        fixture.setUp()
        self.addCleanup(fixture.cleanUp)

        ns = Namespace()
        ns.called = False
        def call_setLogDir(logdir):
            ns.called = True
            self.failUnless(isinstance(logdir, str), logdir)
        self.patch(foolscap.logging.log, 'setLogDir', call_setLogDir)

        yield fixture.create_node()
        self.failUnless(ns.called)

    def test_set_config_unescaped_furl_hash(self):
        """
        ``_Config.set_config`` raises ``UnescapedHashError`` if the item being set
        is a furl and the value includes ``"#"`` and does not set the value.
        """
        basedir = self.mktemp()
        new_config = config_from_string(basedir, "", "")
        with self.assertRaises(UnescapedHashError):
            new_config.set_config("foo", "bar.furl", "value#1")
        with self.assertRaises(MissingConfigEntry):
            new_config.get_config("foo", "bar.furl")

    def test_set_config_new_section(self):
        """
        ``_Config.set_config`` can be called with the name of a section that does
        not already exist to create that section and set an item in it.
        """
        basedir = self.mktemp()
        new_config = config_from_string(basedir, "", "", ValidConfiguration.everything())
        new_config.set_config("foo", "bar", "value1")
        self.assertEqual(
            new_config.get_config("foo", "bar"),
            "value1"
        )

    def test_set_config_replace(self):
        """
        ``_Config.set_config`` can be called with a section and item that already
        exists to change an existing value to a new one.
        """
        basedir = self.mktemp()
        new_config = config_from_string(basedir, "", "", ValidConfiguration.everything())
        new_config.set_config("foo", "bar", "value1")
        new_config.set_config("foo", "bar", "value2")
        self.assertEqual(
            new_config.get_config("foo", "bar"),
            "value2"
        )

    def test_set_config_write(self):
        """
        ``_Config.set_config`` persists the configuration change so it can be
        re-loaded later.
        """
        # Let our nonsense config through
        valid_config = ValidConfiguration.everything()
        basedir = FilePath(self.mktemp())
        basedir.makedirs()
        cfg = basedir.child(b"tahoe.cfg")
        cfg.setContent(b"")
        new_config = read_config(basedir.path, "", [], valid_config)
        new_config.set_config("foo", "bar", "value1")
        loaded_config = read_config(basedir.path, "", [], valid_config)
        self.assertEqual(
            loaded_config.get_config("foo", "bar"),
            "value1",
        )

    def test_set_config_rejects_invalid_config(self):
        """
        ``_Config.set_config`` raises ``UnknownConfigError`` if the section or
        item is not recognized by the validation object and does not set the
        value.
        """
        # Make everything invalid.
        valid_config = ValidConfiguration.nothing()
        new_config = config_from_string(self.mktemp(), "", "", valid_config)
        with self.assertRaises(UnknownConfigError):
            new_config.set_config("foo", "bar", "baz")
        with self.assertRaises(MissingConfigEntry):
            new_config.get_config("foo", "bar")


def _stub_get_local_addresses_sync():
    """
    A function like ``allmydata.util.iputil.get_local_addresses_sync``.
    """
    return ["LOCAL"]


def _stub_allocate_tcp_port():
    """
    A function like ``allmydata.util.iputil.allocate_tcp_port``.
    """
    return 999

def _stub_none():
    """
    A function like ``_stub_allocate_tcp`` or ``_stub_get_local_addresses_sync``
    but that return an empty list since ``allmydata.node._tub_portlocation`` requires a
    callable for paramter 1 and 2 counting from 0.
    """
    return []


class TestMissingPorts(unittest.TestCase):
    """
    Test certain ``_tub_portlocation`` error cases for ports setup.
    """
    def setUp(self):
        self.basedir = self.mktemp()
        create_node_dir(self.basedir, "testing")

    def test_listen_on_zero(self):
        """
        ``_tub_portlocation`` raises ``PortAssignmentRequired`` called with a
        listen address including port 0 and no interface.
        """
        config_data = (
            "[node]\n"
            "tub.port = tcp:0\n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)
        with self.assertRaises(PortAssignmentRequired):
            _tub_portlocation(config, _stub_none, _stub_none)

    def test_listen_on_zero_with_host(self):
        """
        ``_tub_portlocation`` raises ``PortAssignmentRequired`` called with a
        listen address including port 0 and an interface.
        """
        config_data = (
            "[node]\n"
            "tub.port = tcp:0:interface=127.0.0.1\n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)
        with self.assertRaises(PortAssignmentRequired):
            _tub_portlocation(config, _stub_none, _stub_none)

    def test_parsing_tcp(self):
        """
        When ``tub.port`` is given and ``tub.location`` is **AUTO** the port
        number from ``tub.port`` is used as the port number for the value
        constructed for ``tub.location``.
        """
        config_data = (
            "[node]\n"
            "tub.port = tcp:777\n"
            "tub.location = AUTO\n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)

        tubport, tublocation = _tub_portlocation(
            config,
            _stub_get_local_addresses_sync,
            _stub_allocate_tcp_port,
        )
        self.assertEqual(tubport, "tcp:777")
        self.assertEqual(tublocation, b"tcp:LOCAL:777")

    def test_parsing_defaults(self):
        """
        parse empty config, check defaults
        """
        config_data = (
            "[node]\n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)

        tubport, tublocation = _tub_portlocation(
            config,
            _stub_get_local_addresses_sync,
            _stub_allocate_tcp_port,
        )
        self.assertEqual(tubport, "tcp:999")
        self.assertEqual(tublocation, b"tcp:LOCAL:999")

    def test_parsing_location_complex(self):
        """
        location with two options (including defaults)
        """
        config_data = (
            "[node]\n"
            "tub.location = tcp:HOST:888,AUTO\n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)

        tubport, tublocation = _tub_portlocation(
            config,
            _stub_get_local_addresses_sync,
            _stub_allocate_tcp_port,
        )
        self.assertEqual(tubport, "tcp:999")
        self.assertEqual(tublocation, b"tcp:HOST:888,tcp:LOCAL:999")

    def test_parsing_all_disabled(self):
        """
        parse config with both port + location disabled
        """
        config_data = (
            "[node]\n"
            "tub.port = disabled\n"
            "tub.location = disabled\n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)

        res = _tub_portlocation(
            config,
            _stub_get_local_addresses_sync,
            _stub_allocate_tcp_port,
        )
        self.assertTrue(res is None)

    def test_empty_tub_port(self):
        """
        port povided, but empty is an error
        """
        config_data = (
            "[node]\n"
            "tub.port = \n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)

        with self.assertRaises(ValueError) as ctx:
            _tub_portlocation(
                config,
                _stub_get_local_addresses_sync,
                _stub_allocate_tcp_port,
            )
        self.assertIn(
            "tub.port must not be empty",
            str(ctx.exception)
        )

    def test_empty_tub_location(self):
        """
        location povided, but empty is an error
        """
        config_data = (
            "[node]\n"
            "tub.location = \n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)

        with self.assertRaises(ValueError) as ctx:
            _tub_portlocation(
                config,
                _stub_get_local_addresses_sync,
                _stub_allocate_tcp_port,
            )
        self.assertIn(
            "tub.location must not be empty",
            str(ctx.exception)
        )

    def test_disabled_port_not_tub(self):
        """
        error to disable port but not location
        """
        config_data = (
            "[node]\n"
            "tub.port = disabled\n"
            "tub.location = not_disabled\n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)

        with self.assertRaises(ValueError) as ctx:
            _tub_portlocation(
                config,
                _stub_get_local_addresses_sync,
                _stub_allocate_tcp_port,
            )
        self.assertIn(
            "tub.port is disabled, but not tub.location",
            str(ctx.exception)
        )

    def test_disabled_tub_not_port(self):
        """
        error to disable location but not port
        """
        config_data = (
            "[node]\n"
            "tub.port = not_disabled\n"
            "tub.location = disabled\n"
        )
        config = config_from_string(self.basedir, "portnum", config_data)

        with self.assertRaises(ValueError) as ctx:
            _tub_portlocation(
                config,
                _stub_get_local_addresses_sync,
                _stub_allocate_tcp_port,
            )
        self.assertIn(
            "tub.location is disabled, but not tub.port",
            str(ctx.exception)
        )

    def test_tub_location_tcp(self):
        """
        If ``reveal-IP-address`` is set to false and ``tub.location`` includes a
        **tcp** hint then ``_tub_portlocation`` raises `PrivacyError`` because
        TCP leaks IP addresses.
        """
        config = config_from_string(
            "fake.port",
            "no-basedir",
            "[node]\nreveal-IP-address = false\ntub.location=tcp:hostname:1234\n",
        )
        with self.assertRaises(PrivacyError) as ctx:
            _tub_portlocation(
                config,
                _stub_get_local_addresses_sync,
                _stub_allocate_tcp_port,
            )
        self.assertEqual(
            str(ctx.exception),
            "tub.location includes tcp: hint",
        )

    def test_tub_location_legacy_tcp(self):
        """
        If ``reveal-IP-address`` is set to false and ``tub.location`` includes a
        "legacy" hint with no explicit type (which means it is a **tcp** hint)
        then the behavior is the same as for an explicit **tcp** hint.
        """
        config = config_from_string(
            "fake.port",
            "no-basedir",
            "[node]\nreveal-IP-address = false\ntub.location=hostname:1234\n",
        )

        with self.assertRaises(PrivacyError) as ctx:
            _tub_portlocation(
                config,
                _stub_get_local_addresses_sync,
                _stub_allocate_tcp_port,
            )

        self.assertEqual(
            str(ctx.exception),
            "tub.location includes tcp: hint",
        )


BASE_CONFIG = """
[tor]
enabled = false
[i2p]
enabled = false
"""

NOLISTEN = """
[node]
tub.port = disabled
tub.location = disabled
"""

DISABLE_STORAGE = """
[storage]
enabled = false
"""

ENABLE_STORAGE = """
[storage]
enabled = true
"""

ENABLE_HELPER = """
[helper]
enabled = true
"""

class FakeTub(object):
    def __init__(self):
        self.tubID = base64.b32encode(b"foo")
        self.listening_ports = []
    def setOption(self, name, value): pass
    def removeAllConnectionHintHandlers(self): pass
    def addConnectionHintHandler(self, hint_type, handler): pass
    def listenOn(self, what):
        self.listening_ports.append(what)
    def setLocation(self, location): pass
    def setServiceParent(self, parent): pass

class Listeners(unittest.TestCase):

    # Randomly allocate a couple distinct port numbers to try out.  The test
    # never actually binds these port numbers so we don't care if they're "in
    # use" on the system or not.  We just want a couple distinct values we can
    # check expected results against.
    @given(ports=sets(elements=port_numbers(), min_size=2, max_size=2))
    def test_multiple_ports(self, ports):
        """
        When there are multiple listen addresses suggested by the ``tub.port`` and
        ``tub.location`` configuration, the node's *main* port listens on all
        of them.
        """
        port1, port2 = iter(ports)
        port = ("tcp:%d:interface=127.0.0.1,tcp:%d:interface=127.0.0.1" %
                (port1, port2))
        location = "tcp:localhost:%d,tcp:localhost:%d" % (port1, port2)
        t = FakeTub()
        tub_listen_on(None, None, t, port, location)
        self.assertEqual(t.listening_ports,
                         ["tcp:%d:interface=127.0.0.1" % port1,
                          "tcp:%d:interface=127.0.0.1" % port2])

    def test_tor_i2p_listeners(self):
        """
        When configured to listen on an "i2p" or "tor" address, ``tub_listen_on``
        tells the Tub to listen on endpoints supplied by the given Tor and I2P
        providers.
        """
        t = FakeTub()

        i2p_listener = object()
        i2p_provider = ConstantAddresses(i2p_listener)
        tor_listener = object()
        tor_provider = ConstantAddresses(tor_listener)

        tub_listen_on(
            i2p_provider,
            tor_provider,
            t,
            "listen:i2p,listen:tor",
            "tcp:example.org:1234",
        )
        self.assertEqual(
            t.listening_ports,
            [i2p_listener, tor_listener],
        )


class ClientNotListening(unittest.TestCase):

    @defer.inlineCallbacks
    def test_disabled(self):
        basedir = "test_node/test_disabled"
        create_node_dir(basedir, "testing")
        f = open(os.path.join(basedir, 'tahoe.cfg'), 'wt')
        f.write(BASE_CONFIG)
        f.write(NOLISTEN)
        f.write(DISABLE_STORAGE)
        f.close()
        n = yield client.create_client(basedir)
        self.assertEqual(n.tub.getListeners(), [])

    @defer.inlineCallbacks
    def test_disabled_but_storage(self):
        basedir = "test_node/test_disabled_but_storage"
        create_node_dir(basedir, "testing")
        f = open(os.path.join(basedir, 'tahoe.cfg'), 'wt')
        f.write(BASE_CONFIG)
        f.write(NOLISTEN)
        f.write(ENABLE_STORAGE)
        f.close()
        with self.assertRaises(ValueError) as ctx:
            yield client.create_client(basedir)
        self.assertIn(
            "storage is enabled, but tub is not listening",
            str(ctx.exception),
        )

    @defer.inlineCallbacks
    def test_disabled_but_helper(self):
        basedir = "test_node/test_disabled_but_helper"
        create_node_dir(basedir, "testing")
        f = open(os.path.join(basedir, 'tahoe.cfg'), 'wt')
        f.write(BASE_CONFIG)
        f.write(NOLISTEN)
        f.write(DISABLE_STORAGE)
        f.write(ENABLE_HELPER)
        f.close()
        with self.assertRaises(ValueError) as ctx:
            yield client.create_client(basedir)
        self.assertIn(
            "helper is enabled, but tub is not listening",
            str(ctx.exception),
        )

class IntroducerNotListening(unittest.TestCase):

    @defer.inlineCallbacks
    def test_port_none_introducer(self):
        basedir = "test_node/test_port_none_introducer"
        create_node_dir(basedir, "testing")
        with open(os.path.join(basedir, 'tahoe.cfg'), 'wt') as f:
            f.write("[node]\n")
            f.write("tub.port = disabled\n")
            f.write("tub.location = disabled\n")
        with self.assertRaises(ValueError) as ctx:
            yield create_introducer(basedir)
        self.assertIn(
            "we are Introducer, but tub is not listening",
            str(ctx.exception),
        )

class Configuration(unittest.TestCase):

    def setUp(self):
        self.basedir = self.mktemp()
        fileutil.make_dirs(self.basedir)

    def test_read_invalid_config(self):
        with open(os.path.join(self.basedir, 'tahoe.cfg'), 'w') as f:
            f.write(
                '[invalid section]\n'
                'foo = bar\n'
            )
        with self.assertRaises(UnknownConfigError) as ctx:
            read_config(
                self.basedir,
                "client.port",
            )

        self.assertIn(
            "invalid section",
            str(ctx.exception),
        )

    @defer.inlineCallbacks
    def test_create_client_invalid_config(self):
        with open(os.path.join(self.basedir, 'tahoe.cfg'), 'w') as f:
            f.write(
                '[invalid section]\n'
                'foo = bar\n'
            )
        with self.assertRaises(UnknownConfigError) as ctx:
            yield client.create_client(self.basedir)

        self.assertIn(
            "invalid section",
            str(ctx.exception),
        )



class CreateDefaultConnectionHandlersTests(unittest.TestCase):
    """
    Tests for create_default_connection_handlers().
    """

    def test_tcp_disabled(self):
        """
        If tcp is set to disabled, no TCP handler is set.
        """
        config = config_from_string("", "", dedent("""
        [connections]
        tcp = disabled
        """))
        default_handlers = create_default_connection_handlers(
            config,
            {},
        )
        self.assertIs(default_handlers["tcp"], None)
