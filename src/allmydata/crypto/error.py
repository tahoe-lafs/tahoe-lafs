"""
Exceptions raise by allmydata.crypto.* modules

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401


class BadSignature(Exception):
    """
    An alleged signature did not match
    """


class BadPrefixError(Exception):
    """
    A key did not start with the required prefix
    """
