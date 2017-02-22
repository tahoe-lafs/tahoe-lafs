
from ConfigParser import SafeConfigParser


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

def validate_config(fname, cfg, valid_sections):
    """
    raises UnknownConfigError if there are any unknown sections or config
    values.
    """
    for section in cfg.sections():
        try:
            valid_in_section = valid_sections[section]
        except KeyError:
            raise UnknownConfigError(
                "'{fname}' contains unknown section [{section}]".format(
                    fname=fname,
                    section=section,
                )
            )
        for option in cfg.options(section):
            if option not in valid_in_section:
                raise UnknownConfigError(
                    "'{fname}' section [{section}] contains unknown option '{option}'".format(
                        fname=fname,
                        section=section,
                        option=option,
                    )
                )
