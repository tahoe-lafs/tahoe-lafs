"""
Tests for allmydata.util.configutil.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    # Omitted dict, cause worried about interactions.
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, list, object, range, str, max, min  # noqa: F401

import os.path

from twisted.trial import unittest

from allmydata.util import configutil


class ConfigUtilTests(unittest.TestCase):
    def setUp(self):
        super(ConfigUtilTests, self).setUp()
        self.static_valid_config = configutil.ValidConfiguration(
            dict(node=['valid']),
        )
        self.dynamic_valid_config = configutil.ValidConfiguration(
            dict(),
            lambda section_name: section_name == "node",
            lambda section_name, item_name: (section_name, item_name) == ("node", "valid"),
        )

    def create_tahoe_cfg(self, cfg):
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')
        with open(fname, "w") as f:
            f.write(cfg)
        return fname

    def test_config_utils(self):
        tahoe_cfg = self.create_tahoe_cfg("""\
[node]
nickname = client-0
web.port = adopt-socket:fd=5
[storage]
enabled = false
""")

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
        fname = self.create_tahoe_cfg('[node]\nvalid = foo\n')

        config = configutil.get_config(fname)
        # should succeed, no exceptions
        configutil.validate_config(
            fname,
            config,
            self.static_valid_config,
        )

    def test_config_dynamic_validation_success(self):
        """
        A configuration with sections and items that are not matched by the static
        validation but are matched by the dynamic validation is considered
        valid.
        """
        fname = self.create_tahoe_cfg('[node]\nvalid = foo\n')

        config = configutil.get_config(fname)
        # should succeed, no exceptions
        configutil.validate_config(
            fname,
            config,
            self.dynamic_valid_config,
        )

    def test_config_validation_invalid_item(self):
        fname = self.create_tahoe_cfg('[node]\nvalid = foo\ninvalid = foo\n')

        config = configutil.get_config(fname)
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config,
            self.static_valid_config,
        )
        self.assertIn("section [node] contains unknown option 'invalid'", str(e))

    def test_config_validation_invalid_section(self):
        """
        A configuration with a section that is matched by neither the static nor
        dynamic validators is rejected.
        """
        fname = self.create_tahoe_cfg('[node]\nvalid = foo\n[invalid]\n')

        config = configutil.get_config(fname)
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config,
            self.static_valid_config,
        )
        self.assertIn("contains unknown section [invalid]", str(e))

    def test_config_dynamic_validation_invalid_section(self):
        """
        A configuration with a section that is matched by neither the static nor
        dynamic validators is rejected.
        """
        fname = self.create_tahoe_cfg('[node]\nvalid = foo\n[invalid]\n')

        config = configutil.get_config(fname)
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config,
            self.dynamic_valid_config,
        )
        self.assertIn("contains unknown section [invalid]", str(e))

    def test_config_dynamic_validation_invalid_item(self):
        """
        A configuration with a section, item pair that is matched by neither the
        static nor dynamic validators is rejected.
        """
        fname = self.create_tahoe_cfg('[node]\nvalid = foo\ninvalid = foo\n')

        config = configutil.get_config(fname)
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config,
            self.dynamic_valid_config,
        )
        self.assertIn("section [node] contains unknown option 'invalid'", str(e))
