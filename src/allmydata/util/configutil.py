
from ConfigParser import SafeConfigParser


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
