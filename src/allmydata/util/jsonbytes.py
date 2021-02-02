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


class BytesJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder than can also encode bytes.

    The bytes are assumed to be UTF-8 encoded Unicode strings.
    """
    def default(self, o):
        if isinstance(o, bytes):
            return o.decode("utf-8")
        return json.JSONEncoder.default(self, o)


def dumps(obj, *args, **kwargs):
    """Encode to JSON, supporting bytes as keys or values.

    The bytes are assumed to be UTF-8 encoded Unicode strings.
    """
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if isinstance(k, bytes):
                k = k.decode("utf-8")
            new_obj[k] = v
        obj = new_obj
    return json.dumps(obj, cls=BytesJSONEncoder, *args, **kwargs)


# To make this module drop-in compatible with json module:
loads = json.loads


__all__ = ["dumps", "loads"]
