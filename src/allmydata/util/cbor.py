"""
Unified entry point for CBOR encoding and decoding.

Makes it less likely to use ``cbor2.loads()`` by mistake, which we want to avoid.
"""

# We don't want to use the C extension for loading, at least for now, but using
# it for dumping should be fine.
from cbor2 import dumps, dump

def load(*args, **kwargs):
    """
    Don't use this!  Here just in case someone uses it by mistake.
    """
    raise RuntimeError("Use pycddl for decoding CBOR")

loads = load

__all__ = ["dumps", "loads", "dump", "load"]
