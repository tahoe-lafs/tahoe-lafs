import os.path

from twisted.trial import unittest

from allmydata.util import configutil
from allmydata.test.no_network import GridTestMixin
from .cli.test_cli import CLITestMixin


class ConfigUtilTests(CLITestMixin, GridTestMixin, unittest.TestCase):

    def test_config_utils(self):
        self.basedir = "cli/ConfigUtilTests/test-config-utils"
        self.set_up_grid(oneshare=True)
        tahoe_cfg = os.path.join(self.get_clientdir(i=0), "tahoe.cfg")

        # test that at least one option was read correctly
        config = configutil.get_config(tahoe_cfg)
        self.failUnlessEqual(config.get("node", "nickname"), "client-0")

        # test that set_config can mutate an existing option
        configutil.set_config(config, "node", "nickname", "Alice!")
        configutil.write_config(tahoe_cfg, config)

        config = configutil.get_config(tahoe_cfg)
        self.failUnlessEqual(config.get("node", "nickname"), "Alice!")

        # test that set_config can set a new option
        descriptor = "Twas brillig, and the slithy toves Did gyre and gimble in the wabe"
        configutil.set_config(config, "node", "descriptor", descriptor)
        configutil.write_config(tahoe_cfg, config)

        config = configutil.get_config(tahoe_cfg)
        self.failUnlessEqual(config.get("node", "descriptor"), descriptor)

    def test_config_validation_success(self):
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\n')

        config = configutil.get_config(fname)
        # should succeed, no exceptions
        configutil.validate_config(fname, config, dict(node=['valid']))

    def test_config_validation_invalid_item(self):
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\ninvalid = foo\n')

        config = configutil.get_config(fname)
        self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config, dict(node=['valid']),
        )

    def test_config_validation_invalid_section(self):
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\n[invalid]\n')

        config = configutil.get_config(fname)
        self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config, dict(node=['valid']),
        )
