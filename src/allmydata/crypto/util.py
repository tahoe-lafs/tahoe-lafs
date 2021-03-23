"""
Utilities used by allmydata.crypto modules

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from allmydata.crypto.error import BadPrefixError


def remove_prefix(s_bytes, prefix):
    """
    :param bytes s_bytes: a string of bytes whose prefix is removed

    :param bytes prefix: the bytes to remove from the beginning of `s_bytes`

    Removes `prefix` from `s_bytes` and returns the new bytes or
    raises `BadPrefixError` if `s_bytes` did not start with the
    `prefix` specified.

    :returns: `s_bytes` with `prefix` removed from the front.
    """
    if s_bytes.startswith(prefix):
        return s_bytes[len(prefix):]
    raise BadPrefixError(
        "did not see expected '{!r}' prefix".format(prefix)
    )
