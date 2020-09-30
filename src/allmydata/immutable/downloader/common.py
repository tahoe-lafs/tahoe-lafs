"""
Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

(AVAILABLE, PENDING, OVERDUE, COMPLETE, CORRUPT, DEAD, BADSEGNUM) = \
 ("AVAILABLE", "PENDING", "OVERDUE", "COMPLETE", "CORRUPT", "DEAD", "BADSEGNUM")

class BadSegmentNumberError(Exception):
    pass
class WrongSegmentError(Exception):
    pass
class BadCiphertextHashError(Exception):
    pass
