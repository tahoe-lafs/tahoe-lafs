"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import yaml


if PY2:
    # On Python 2 the way pyyaml deals with Unicode strings is inconsistent.
    #
    # >>> yaml.safe_load(yaml.safe_dump(u"hello"))
    # 'hello'
    # >>> yaml.safe_load(yaml.safe_dump(u"hello\u1234"))
    # u'hello\u1234'
    #
    # In other words, Unicode strings get roundtripped to byte strings, but
    # only sometimes.
    #
    # In order to ensure unicode stays unicode, we add a configuration saying
    # that the YAML String Language-Independent Type ("a sequence of zero or
    # more Unicode characters") should be the underlying Unicode string object,
    # rather than converting to bytes when possible.
    #
    # Reference: https://yaml.org/type/str.html
    def construct_unicode(loader, node):
        return node.value
    yaml.SafeLoader.add_constructor("tag:yaml.org,2002:str",
                                    construct_unicode)

def safe_load(f):
    return yaml.safe_load(f)

def safe_dump(obj):
    return yaml.safe_dump(obj)
