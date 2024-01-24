"""
Unified entry point for CBOR encoding and decoding.
"""

import sys

# We don't want to use the C extension for loading, at least for now, but using
# it for dumping should be fine.
from cbor2 import dumps, dump

# Now, override the C extension so we can import the Python versions of loading
# functions.
del sys.modules["cbor2"]
sys.modules["_cbor2"] = None
from cbor2 import load, loads

# Quick validation that we got the Python version, not the C version.
assert type(load) == type(lambda: None), repr(load)
assert type(loads) == type(lambda: None), repr(loads)

__all__ = ["dumps", "loads", "dump", "load"]
