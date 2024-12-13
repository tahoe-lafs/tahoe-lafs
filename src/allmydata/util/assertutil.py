"""
Tests useful in assertion checking, prints out nicely formated messages too.

Backwards compatibility layer, the versions in pyutil are better maintained and
have tests.

Ported to Python 3.
"""

# The API importers expect:
from pyutil.assertutil import _assert, precondition, postcondition

__all__ = ["_assert", "precondition", "postcondition"]
