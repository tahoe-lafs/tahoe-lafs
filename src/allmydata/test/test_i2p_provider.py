import os
from twisted.trial import unittest
from twisted.internet import defer, error
from twisted.python.usage import UsageError
from StringIO import StringIO
import mock
from ..util import i2p_provider
from ..scripts import create_node, runner

def mock_txi2p(txi2p):
    return mock.patch("allmydata.util.i2p_provider._import_txi2p",
                      return_value=txi2p)

def mock_i2p(i2p):
    return mock.patch("allmydata.util.i2p_provider._import_i2p",
                      return_value=i2p)

def make_cli_config(basedir, *argv):
    parent = runner.Options()
    cli_config = create_node.CreateNodeOptions()
    cli_config.parent = parent
    cli_config.parseOptions(argv)
    cli_config["basedir"] = basedir
    cli_config.stdout = StringIO()
    return cli_config

class TryToConnect(unittest.TestCase):
    def test_try(self):
        reactor = object()
        txi2p = mock.Mock()
        d = defer.succeed(True)
        txi2p.testAPI = mock.Mock(return_value=d)
        ep = object()
        stdout = StringIO()
        with mock.patch("allmydata.util.i2p_provider.clientFromString",
                        return_value=ep) as cfs:
            d = i2p_provider._try_to_connect(reactor, "desc", stdout, txi2p)
        r = self.successResultOf(d)
        self.assertTrue(r)
        cfs.assert_called_with(reactor, "desc")
        txi2p.testAPI.assert_called_with(reactor, 'SAM', ep)

    def test_try_handled_error(self):
        reactor = object()
        txi2p = mock.Mock()
        d = defer.fail(error.ConnectError("oops"))
        txi2p.testAPI = mock.Mock(return_value=d)
        ep = object()
        stdout = StringIO()
        with mock.patch("allmydata.util.i2p_provider.clientFromString",
                        return_value=ep) as cfs:
            d = i2p_provider._try_to_connect(reactor, "desc", stdout, txi2p)
        r = self.successResultOf(d)
        self.assertIs(r, None)
        cfs.assert_called_with(reactor, "desc")
        txi2p.testAPI.assert_called_with(reactor, 'SAM', ep)
        self.assertEqual(stdout.getvalue(),
                         "Unable to reach I2P SAM API at 'desc': "
                         "An error occurred while connecting: oops.\n")

    def test_try_unhandled_error(self):
        reactor = object()
        txi2p = mock.Mock()
        d = defer.fail(ValueError("oops"))
        txi2p.testAPI = mock.Mock(return_value=d)
        ep = object()
        stdout = StringIO()
        with mock.patch("allmydata.util.i2p_provider.clientFromString",
                        return_value=ep) as cfs:
            d = i2p_provider._try_to_connect(reactor, "desc", stdout, txi2p)
        f = self.failureResultOf(d)
        self.assertIsInstance(f.value, ValueError)
        self.assertEqual(str(f.value), "oops")
        cfs.assert_called_with(reactor, "desc")
        txi2p.testAPI.assert_called_with(reactor, 'SAM', ep)
        self.assertEqual(stdout.getvalue(), "")

class ConnectToI2P(unittest.TestCase):
    def _do_test_connect(self, endpoint, reachable):
        reactor = object()
        txi2p = object()
        args = []
        if endpoint:
            args = ["--i2p-sam-port=%s" % endpoint]
        cli_config = make_cli_config("basedir", "--listen=i2p", *args)
        stdout = cli_config.stdout
        expected_port = "tcp:127.0.0.1:7656"
        if endpoint:
            expected_port = endpoint
        tried = []
        def _try_to_connect(reactor, port, stdout, txi2p):
            tried.append( (reactor, port, stdout, txi2p) )
            if not reachable:
                return defer.succeed(None)
            if port == expected_port:
                return defer.succeed(True)
            return defer.succeed(None)

        with mock.patch("allmydata.util.i2p_provider._try_to_connect",
                        _try_to_connect):
            d = i2p_provider._connect_to_i2p(reactor, cli_config, txi2p)
        if not reachable:
            f = self.failureResultOf(d)
            self.assertIsInstance(f.value, ValueError)
            self.assertEqual(str(f.value),
                             "unable to reach any default I2P SAM port")
            return
        successful_port = self.successResultOf(d)
        self.assertEqual(successful_port, expected_port)
        expected = [(reactor, "tcp:127.0.0.1:7656", stdout, txi2p)]
        if endpoint:
            expected = [(reactor, endpoint, stdout, txi2p)]
        self.assertEqual(tried, expected)

    def test_connect(self):
        return self._do_test_connect(None, True)
    def test_connect_endpoint(self):
        return self._do_test_connect("tcp:other:port", True)
    def test_connect_unreachable(self):
        return self._do_test_connect(None, False)


class CreateDest(unittest.TestCase):
    def test_no_txi2p(self):
        with mock.patch("allmydata.util.i2p_provider._import_txi2p",
                        return_value=None):
            d = i2p_provider.create_dest("reactor", "cli_config")
            f = self.failureResultOf(d)
            self.assertIsInstance(f.value, ValueError)
            self.assertEqual(str(f.value),
                             "Cannot create I2P Destination without txi2p. "
                             "Please 'pip install tahoe-lafs[i2p]' to fix this.")

    def _do_test_launch(self, executable):
        basedir = self.mktemp()
        os.mkdir(basedir)
        args = ["--listen=i2p", "--i2p-launch"]
        if executable:
            args.append("--i2p-executable=%s" % executable)
        self.assertRaises(UsageError, make_cli_config, basedir, *args)

    def test_launch(self):
        return self._do_test_launch(None)
    def test_launch_executable(self):
        return self._do_test_launch("myi2p")

    def test_sam_endpoint(self):
        basedir = self.mktemp()
        os.mkdir(basedir)
        private_dir = os.path.join(basedir, "private")
        os.mkdir(private_dir)
        privkeyfile = os.path.abspath(os.path.join(private_dir, "i2p_dest.privkey"))
        reactor = object()
        cli_config = make_cli_config(basedir, "--listen=i2p")
        connect_to_i2p = mock.Mock(return_value=defer.succeed("goodport"))
        txi2p = mock.Mock()
        ep = object()
        dest = mock.Mock()
        dest.host = "FOOBAR.b32.i2p"
        txi2p.generateDestination = mock.Mock(return_value=defer.succeed(dest))

        with mock_txi2p(txi2p):
            with mock.patch("allmydata.util.i2p_provider._connect_to_i2p",
                            connect_to_i2p):
                with mock.patch("allmydata.util.i2p_provider.clientFromString",
                                return_value=ep) as cfs:
                    d = i2p_provider.create_dest(reactor, cli_config)
        tahoe_config_i2p, i2p_port, i2p_location = self.successResultOf(d)

        connect_to_i2p.assert_called_with(reactor, cli_config, txi2p)
        cfs.assert_called_with(reactor, "goodport")
        txi2p.generateDestination.assert_called_with(reactor, privkeyfile, 'SAM', ep)

        expected = {"sam.port": "goodport",
                    "dest": "true",
                    "dest.port": "3457",
                    "dest.private_key_file": os.path.join("private",
                                                          "i2p_dest.privkey"),
                    }
        self.assertEqual(tahoe_config_i2p, expected)
        self.assertEqual(i2p_port, "i2p:%s:3457:api=SAM:apiEndpoint=goodport" % privkeyfile)
        self.assertEqual(i2p_location, "i2p:FOOBAR.b32.i2p:3457")

_None = object()
class FakeConfig(dict):
    def get_config(self, section, option, default=_None, boolean=False):
        if section != "i2p":
            raise ValueError(section)
        value = self.get(option, default)
        if value is _None:
            raise KeyError
        return value

class Provider(unittest.TestCase):
    def test_build(self):
        i2p_provider.Provider("basedir", FakeConfig(), "reactor")

    def test_handler_disabled(self):
        p = i2p_provider.Provider("basedir", FakeConfig(enabled=False),
                                  "reactor")
        self.assertEqual(p.get_i2p_handler(), None)

    def test_handler_no_i2p(self):
        with mock_i2p(None):
            p = i2p_provider.Provider("basedir", FakeConfig(), "reactor")
        self.assertEqual(p.get_i2p_handler(), None)

    def test_handler_sam_endpoint(self):
        i2p = mock.Mock()
        handler = object()
        i2p.sam_endpoint = mock.Mock(return_value=handler)
        ep = object()
        reactor = object()

        with mock_i2p(i2p):
            p = i2p_provider.Provider("basedir",
                                      FakeConfig(**{"sam.port": "ep_desc"}),
                                      reactor)
            with mock.patch("allmydata.util.i2p_provider.clientFromString",
                            return_value=ep) as cfs:
                h = p.get_i2p_handler()
        cfs.assert_called_with(reactor, "ep_desc")
        self.assertIs(h, handler)
        i2p.sam_endpoint.assert_called_with(ep, keyfile=None)

    def test_handler_launch(self):
        i2p = mock.Mock()
        handler = object()
        i2p.launch = mock.Mock(return_value=handler)
        reactor = object()

        with mock_i2p(i2p):
            p = i2p_provider.Provider("basedir", FakeConfig(launch=True),
                                      reactor)
        h = p.get_i2p_handler()
        self.assertIs(h, handler)
        i2p.launch.assert_called_with(i2p_configdir=None, i2p_binary=None)

    def test_handler_launch_configdir(self):
        i2p = mock.Mock()
        handler = object()
        i2p.launch = mock.Mock(return_value=handler)
        reactor = object()

        with mock_i2p(i2p):
            p = i2p_provider.Provider("basedir",
                                      FakeConfig(launch=True,
                                                 **{"i2p.configdir": "configdir"}),
                                      reactor)
        h = p.get_i2p_handler()
        self.assertIs(h, handler)
        i2p.launch.assert_called_with(i2p_configdir="configdir", i2p_binary=None)

    def test_handler_launch_configdir_executable(self):
        i2p = mock.Mock()
        handler = object()
        i2p.launch = mock.Mock(return_value=handler)
        reactor = object()

        with mock_i2p(i2p):
            p = i2p_provider.Provider("basedir",
                                      FakeConfig(launch=True,
                                                 **{"i2p.configdir": "configdir",
                                                    "i2p.executable": "myi2p",
                                                   }),
                                      reactor)
        h = p.get_i2p_handler()
        self.assertIs(h, handler)
        i2p.launch.assert_called_with(i2p_configdir="configdir", i2p_binary="myi2p")

    def test_handler_configdir(self):
        i2p = mock.Mock()
        handler = object()
        i2p.local_i2p = mock.Mock(return_value=handler)
        reactor = object()

        with mock_i2p(i2p):
            p = i2p_provider.Provider("basedir",
                                      FakeConfig(**{"i2p.configdir": "configdir"}),
                                      reactor)
        h = p.get_i2p_handler()
        i2p.local_i2p.assert_called_with("configdir")
        self.assertIs(h, handler)

    def test_handler_default(self):
        i2p = mock.Mock()
        handler = object()
        i2p.default = mock.Mock(return_value=handler)
        reactor = object()

        with mock_i2p(i2p):
            p = i2p_provider.Provider("basedir", FakeConfig(), reactor)
        h = p.get_i2p_handler()
        self.assertIs(h, handler)
        i2p.default.assert_called_with(reactor, keyfile=None)

class Provider_CheckI2PConfig(unittest.TestCase):
    def test_default(self):
        # default config doesn't start an I2P service, so it should be
        # happy both with and without txi2p

        p = i2p_provider.Provider("basedir", FakeConfig(), "reactor")
        p.check_dest_config()

        with mock_txi2p(None):
            p = i2p_provider.Provider("basedir", FakeConfig(), "reactor")
            p.check_dest_config()

    def test_no_txi2p(self):
        with mock_txi2p(None):
            p = i2p_provider.Provider("basedir", FakeConfig(dest=True),
                                      "reactor")
            e = self.assertRaises(ValueError, p.check_dest_config)
            self.assertEqual(str(e), "Cannot create I2P Destination without txi2p. "
                             "Please 'pip install tahoe-lafs[i2p]' to fix.")

    def test_no_launch_no_control(self):
        p = i2p_provider.Provider("basedir", FakeConfig(dest=True), "reactor")
        e = self.assertRaises(ValueError, p.check_dest_config)
        self.assertEqual(str(e), "[i2p] dest = true, but we have neither "
                         "sam.port= nor launch=true nor configdir=")

    def test_missing_keys(self):
        p = i2p_provider.Provider("basedir", FakeConfig(dest=True,
                                             **{"sam.port": "x",
                                                }), "reactor")
        e = self.assertRaises(ValueError, p.check_dest_config)
        self.assertEqual(str(e), "[i2p] dest = true, "
                         "but dest.port= is missing")

        p = i2p_provider.Provider("basedir",
                                  FakeConfig(dest=True,
                                             **{"sam.port": "x",
                                                "dest.port": "y",
                                                }), "reactor")
        e = self.assertRaises(ValueError, p.check_dest_config)
        self.assertEqual(str(e), "[i2p] dest = true, "
                         "but dest.private_key_file= is missing")

    def test_launch_not_implemented(self):
        p = i2p_provider.Provider("basedir",
                                  FakeConfig(dest=True, launch=True,
                                             **{"dest.port": "x",
                                                "dest.private_key_file": "y",
                                                }), "reactor")
        e = self.assertRaises(NotImplementedError, p.check_dest_config)
        self.assertEqual(str(e), "[i2p] launch is under development.")

    def test_ok(self):
        p = i2p_provider.Provider("basedir",
                                  FakeConfig(dest=True,
                                             **{"sam.port": "x",
                                                "dest.port": "y",
                                                "dest.private_key_file": "z",
                                                }), "reactor")
        p.check_dest_config()
