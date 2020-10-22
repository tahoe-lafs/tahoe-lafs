"""
A few commonly needed functions.

Backwards compatibility for direct imports.

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
from pyutil.mathutil import div_ceil, next_multiple, pad_size, is_power_of_k, next_power_of_k, ave, log_ceil, log_floor


# This function is not present in pyutil.mathutil:
def round_sigfigs(f, n):
    fmt = "%." + str(n-1) + "e"
    return float(fmt % f)

__all__ = ["div_ceil", "next_multiple", "pad_size", "is_power_of_k", "next_power_of_k", "ave", "log_ceil", "log_floor", "round_sigfigs"]
