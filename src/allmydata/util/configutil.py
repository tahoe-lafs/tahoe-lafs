"""
Read/write config files.

Configuration is returned as Unicode strings.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

# On Python 2 we use the backport package; that means we always get unicode
# out.
from configparser import ConfigParser

import attr


class UnknownConfigError(Exception):
    """
    An unknown config item was found.

    This is possibly raised by validate_config()
    """


def get_config(tahoe_cfg):
    """Load the config, returning a ConfigParser.

    Configuration is returned as Unicode strings.
    """
    # Byte Order Mark is an optional garbage code point you sometimes get at
    # the start of UTF-8 encoded files. Especially on Windows. Skip it by using
    # utf-8-sig. https://en.wikipedia.org/wiki/Byte_order_mark
    with open(tahoe_cfg, "r", encoding="utf-8-sig") as f:
        cfg_string = f.read()
    return get_config_from_string(cfg_string)


def get_config_from_string(tahoe_cfg_string):
    """Load the config from a string, return the ConfigParser.

    Configuration is returned as Unicode strings.
    """
    parser = ConfigParser(strict=False)
    parser.read_string(tahoe_cfg_string)
    return parser


def set_config(config, section, option, value):
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, option, value)
    assert config.get(section, option) == value

def write_config(tahoe_cfg, config):
    with open(tahoe_cfg, "w") as f:
        config.write(f)

def validate_config(fname, cfg, valid_config):
    """
    :param ValidConfiguration valid_config: The definition of a valid
        configuration.

    :raises UnknownConfigError: if there are any unknown sections or config
        values.
    """
    for section in cfg.sections():
        if not valid_config.is_valid_section(section):
            raise UnknownConfigError(
                "'{fname}' contains unknown section [{section}]".format(
                    fname=fname,
                    section=section,
                )
            )
        for option in cfg.options(section):
            if not valid_config.is_valid_item(section, option):
                raise UnknownConfigError(
                    "'{fname}' section [{section}] contains unknown option '{option}'".format(
                        fname=fname,
                        section=section,
                        option=option,
                    )
                )


@attr.s
class ValidConfiguration(object):
    """
    :ivar dict[bytes, tuple[bytes]] _static_valid_sections: A mapping from
        valid section names to valid items in those sections.

    :ivar _is_valid_section: A callable which accepts a section name as bytes
        and returns True if that section name is valid, False otherwise.

    :ivar _is_valid_item: A callable which accepts a section name as bytes and
        an item name as bytes and returns True if that section, item pair is
        valid, False otherwise.
    """
    _static_valid_sections = attr.ib()
    _is_valid_section = attr.ib(default=lambda section_name: False)
    _is_valid_item = attr.ib(default=lambda section_name, item_name: False)

    def is_valid_section(self, section_name):
        """
        :return: True if the given section name is valid, False otherwise.
        """
        return (
            section_name in self._static_valid_sections or
            self._is_valid_section(section_name)
        )

    def is_valid_item(self, section_name, item_name):
        """
        :return: True if the given section name, ite name pair is valid, False
            otherwise.
        """
        valid_items = self._static_valid_sections.get(section_name, ())
        if valid_items is None:
            return True
        return (
            item_name in valid_items or
            self._is_valid_item(section_name, item_name)
        )


    def update(self, valid_config):
        static_valid_sections = self._static_valid_sections.copy()
        static_valid_sections.update(valid_config._static_valid_sections)
        return ValidConfiguration(
            static_valid_sections,
            _either(self._is_valid_section, valid_config._is_valid_section),
            _either(self._is_valid_item, valid_config._is_valid_item),
        )


def _either(f, g):
    """
    :return: A function which returns True if either f or g returns True.
    """
    return lambda *a, **kw: f(*a, **kw) or g(*a, **kw)
