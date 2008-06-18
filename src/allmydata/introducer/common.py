
import re
from base64 import b32decode

def make_index(announcement):
    (furl, service_name, ri_name, nickname, ver, oldest) = announcement
    m = re.match(r'pb://(\w+)@', furl)
    assert m
    nodeid = b32decode(m.group(1).upper())
    return (nodeid, service_name)

