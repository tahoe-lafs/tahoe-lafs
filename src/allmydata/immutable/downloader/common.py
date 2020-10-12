"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from ...util.future_builtins import *  # noqa: F401, F403


(AVAILABLE, PENDING, OVERDUE, COMPLETE, CORRUPT, DEAD, BADSEGNUM) = \
 ("AVAILABLE", "PENDING", "OVERDUE", "COMPLETE", "CORRUPT", "DEAD", "BADSEGNUM")

class BadSegmentNumberError(Exception):
    pass
class WrongSegmentError(Exception):
    pass
class BadCiphertextHashError(Exception):
    pass
