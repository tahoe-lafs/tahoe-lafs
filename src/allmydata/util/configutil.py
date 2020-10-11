"""
Read/write config files.

Configuration is returned as native strings.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY3
from future.utils import PY2
if PY2:
    # We don't do open(), because we want files to read/write native strs when
    # we do "r" or "w".
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

if PY2:
    # In theory on Python 2 configparser also works, but then code gets the
    # wrong exceptions and they don't get handled. So just use native parser
    # for now.
    from ConfigParser import SafeConfigParser
else:
    from configparser import SafeConfigParser

import attr

BOM_CHARACTER = u"\uFEFF"



class UnknownConfigError(Exception):
    """
    An unknown config item was found.

    This is possibly raised by validate_config()
    """


def get_config(tahoe_cfg):
    """Load the config, returning a SafeConfigParser.

    Configuration is returned as native strings.
    """
    config = SafeConfigParser()
    with open(tahoe_cfg, "r") as f:
        # On Python 2, where we read in bytes, skip any initial Byte Order
        # Mark. Since this is an ordinary file, we don't need to handle
        # incomplete reads, and can assume seekability.
        if (
                (PY3 and f.read(1) != BOM_CHARACTER)
                or
                (PY2 and f.read(3) != BOM_CHARACTER.encode("utf-8"))
        ):
            f.seek(0)
        config.readfp(f)
    return config

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
        return (
            item_name in self._static_valid_sections.get(section_name, ()) or
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
