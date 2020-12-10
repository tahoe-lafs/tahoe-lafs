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

from six import ensure_text
from foolscap import base32


def nodeid_b2a(nodeid):
    """
    We display nodeids using the same base32 alphabet that Foolscap uses.

    Returns a Unicode string.
    """
    return ensure_text(base32.encode(nodeid))

def shortnodeid_b2a(nodeid):
    """
    Short version of nodeid_b2a() output, Unicode string.
    """
    return nodeid_b2a(nodeid)[:8]
