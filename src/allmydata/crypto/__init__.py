"""
Helper functions for cryptography-related operations inside Tahoe

For the most part, these functions use and return objects that are
documented in the `cryptography` library -- however, code inside Tahoe
should only use these functions and not rely on features of any
objects that `cryptography` documents.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
