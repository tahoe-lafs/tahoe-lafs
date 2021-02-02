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
from configparser import (
    ConfigParser,
)
from functools import (
    partial,
)

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    dictionaries,
    text,
    characters,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.trial import unittest

from allmydata.util import configutil


def arbitrary_config_dicts(
        min_sections=0,
        max_sections=3,
        max_section_name_size=8,
        max_items_per_section=3,
        max_item_length=8,
        max_value_length=8,
):
    """
    Build ``dict[str, dict[str, str]]`` instances populated with arbitrary
    configurations.
    """
    identifier_text = partial(
        text,
        # Don't allow most control characters or spaces
        alphabet=characters(
            blacklist_categories=('Cc', 'Cs', 'Zs'),
        ),
    )
    return dictionaries(
        identifier_text(
            min_size=1,
            max_size=max_section_name_size,
        ),
        dictionaries(
            identifier_text(
                min_size=1,
                max_size=max_item_length,
            ),
            text(max_size=max_value_length),
            max_size=max_items_per_section,
        ),
        min_size=min_sections,
        max_size=max_sections,
    )


def to_configparser(dictconfig):
    """
    Take a ``dict[str, dict[str, str]]`` and turn it into the corresponding
    populated ``ConfigParser`` instance.
    """
    cp = ConfigParser()
    for section, items in dictconfig.items():
        cp.add_section(section)
        for k, v in items.items():
            cp.set(
                section,
                k,
                # ConfigParser has a feature that everyone knows and loves
                # where it will use %-style interpolation to substitute
                # values from one part of the config into another part of
                # the config.  Escape all our `%`s to avoid hitting this
                # and complicating things.
                v.replace("%", "%%"),
            )
    return cp


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
        configutil.write_config(FilePath(tahoe_cfg), config)

        config = configutil.get_config(tahoe_cfg)
        self.failUnlessEqual(config.get("node", "nickname"), "Alice!")

        # test that set_config can set a new option
        descriptor = "Twas brillig, and the slithy toves Did gyre and gimble in the wabe"
        configutil.set_config(config, "node", "descriptor", descriptor)
        configutil.write_config(FilePath(tahoe_cfg), config)

        config = configutil.get_config(tahoe_cfg)
        self.failUnlessEqual(config.get("node", "descriptor"), descriptor)

    def test_config_validation_success(self):
        """
        ``configutil.validate_config`` returns ``None`` when the configuration it
        is given has nothing more than the static sections and items defined
        by the validator.
        """
        # should succeed, no exceptions
        configutil.validate_config(
            "<test_config_validation_success>",
            to_configparser({"node": {"valid": "foo"}}),
            self.static_valid_config,
        )

    def test_config_dynamic_validation_success(self):
        """
        A configuration with sections and items that are not matched by the static
        validation but are matched by the dynamic validation is considered
        valid.
        """
        # should succeed, no exceptions
        configutil.validate_config(
            "<test_config_dynamic_validation_success>",
            to_configparser({"node": {"valid": "foo"}}),
            self.dynamic_valid_config,
        )

    def test_config_validation_invalid_item(self):
        config = to_configparser({"node": {"valid": "foo", "invalid": "foo"}})
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            "<test_config_validation_invalid_item>",
            config,
            self.static_valid_config,
        )
        self.assertIn("section [node] contains unknown option 'invalid'", str(e))

    def test_config_validation_invalid_section(self):
        """
        A configuration with a section that is matched by neither the static nor
        dynamic validators is rejected.
        """
        config = to_configparser({"node": {"valid": "foo"}, "invalid": {}})
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            "<test_config_validation_invalid_section>",
            config,
            self.static_valid_config,
        )
        self.assertIn("contains unknown section [invalid]", str(e))

    def test_config_dynamic_validation_invalid_section(self):
        """
        A configuration with a section that is matched by neither the static nor
        dynamic validators is rejected.
        """
        config = to_configparser({"node": {"valid": "foo"}, "invalid": {}})
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            "<test_config_dynamic_validation_invalid_section>",
            config,
            self.dynamic_valid_config,
        )
        self.assertIn("contains unknown section [invalid]", str(e))

    def test_config_dynamic_validation_invalid_item(self):
        """
        A configuration with a section, item pair that is matched by neither the
        static nor dynamic validators is rejected.
        """
        config = to_configparser({"node": {"valid": "foo", "invalid": "foo"}})
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            "<test_config_dynamic_validation_invalid_item>",
            config,
            self.dynamic_valid_config,
        )
        self.assertIn("section [node] contains unknown option 'invalid'", str(e))

    def test_duplicate_sections(self):
        """
        Duplicate section names are merged.
        """
        fname = self.create_tahoe_cfg('[node]\na = foo\n[node]\n b = bar\n')
        config = configutil.get_config(fname)
        self.assertEqual(config.get("node", "a"), "foo")
        self.assertEqual(config.get("node", "b"), "bar")

    @given(arbitrary_config_dicts())
    def test_everything_valid(self, cfgdict):
        """
        ``validate_config`` returns ``None`` when the validator is
        ``ValidConfiguration.everything()``.
        """
        cfg = to_configparser(cfgdict)
        self.assertIs(
            configutil.validate_config(
                "<test_everything_valid>",
                cfg,
                configutil.ValidConfiguration.everything(),
            ),
            None,
        )

    @given(arbitrary_config_dicts(min_sections=1))
    def test_nothing_valid(self, cfgdict):
        """
        ``validate_config`` raises ``UnknownConfigError`` when the validator is
        ``ValidConfiguration.nothing()`` for all non-empty configurations.
        """
        cfg = to_configparser(cfgdict)
        with self.assertRaises(configutil.UnknownConfigError):
            configutil.validate_config(
                "<test_everything_valid>",
                cfg,
                configutil.ValidConfiguration.nothing(),
            )

    def test_nothing_empty_valid(self):
        """
        ``validate_config`` returns ``None`` when the validator is
        ``ValidConfiguration.nothing()`` if the configuration is empty.
        """
        cfg = ConfigParser()
        self.assertIs(
            configutil.validate_config(
                "<test_everything_valid>",
                cfg,
                configutil.ValidConfiguration.nothing(),
            ),
            None,
        )

    @given(arbitrary_config_dicts())
    def test_copy_config(self, cfgdict):
        """
        ``copy_config`` creates a new ``ConfigParser`` object containing the same
        values as its input.
        """
        cfg = to_configparser(cfgdict)
        copied = configutil.copy_config(cfg)
        # Should be equal
        self.assertEqual(cfg, copied)
        # But not because they're the same object.
        self.assertIsNot(cfg, copied)
