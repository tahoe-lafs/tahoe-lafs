"""
A JSON encoder than can serialize bytes.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import json


def _bytes_to_unicode(obj):
    """Convert keys of dicts from bytes to unicode, recursively."""
    if isinstance(obj, bytes):
        return obj.decode("utf-8")
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if isinstance(k, bytes):
                k = k.decode("utf-8")
            v = _bytes_to_unicode(v)
            new_obj[k] = v
        return new_obj
    if isinstance(obj, (list, set, tuple)):
        return [_bytes_to_unicode(i) for i in obj]
    return obj


class BytesJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder than can also encode bytes.

    The bytes are assumed to be UTF-8 encoded Unicode strings.
    """
    def iterencode(self, o, **kwargs):
        return json.JSONEncoder.iterencode(self, _bytes_to_unicode(o), **kwargs)


def dumps(obj, *args, **kwargs):
    """Encode to JSON, supporting bytes as keys or values.

    The bytes are assumed to be UTF-8 encoded Unicode strings.
    """
    return json.dumps(obj, cls=BytesJSONEncoder, *args, **kwargs)


# To make this module drop-in compatible with json module:
loads = json.loads


__all__ = ["dumps", "loads"]
