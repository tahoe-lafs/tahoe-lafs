"""
Tests useful in assertion checking, prints out nicely formated messages too.

Backwards compatibility layer, the versions in pyutil are better maintained and
have tests.

Ported to Python 3.
"""

from __future__ import division
from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401


# The API importers expect:
from pyutil.assertutil import _assert, precondition, postcondition

__all__ = ["_assert", "precondition", "postcondition"]
