"""
Ported to Python 3.
"""

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
