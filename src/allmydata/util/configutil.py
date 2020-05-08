
from ConfigParser import SafeConfigParser

import attr

class UnknownConfigError(Exception):
    """
    An unknown config item was found.

    This is possibly raised by validate_config()
    """


def get_config(tahoe_cfg):
    config = SafeConfigParser()
    f = open(tahoe_cfg, "rb")
    try:
        # Skip any initial Byte Order Mark. Since this is an ordinary file, we
        # don't need to handle incomplete reads, and can assume seekability.
        if f.read(3) != '\xEF\xBB\xBF':
            f.seek(0)
        config.readfp(f)
    finally:
        f.close()
    return config

def set_config(config, section, option, value):
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, option, value)
    assert config.get(section, option) == value

def write_config(tahoe_cfg, config):
    f = open(tahoe_cfg, "wb")
    try:
        config.write(f)
    finally:
        f.close()

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
