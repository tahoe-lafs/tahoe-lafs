# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

(AVAILABLE, PENDING, OVERDUE, COMPLETE, CORRUPT, DEAD, BADSEGNUM) = \
 ("AVAILABLE", "PENDING", "OVERDUE", "COMPLETE", "CORRUPT", "DEAD", "BADSEGNUM")

class BadSegmentNumberError(Exception):
    pass
class WrongSegmentError(Exception):
    pass
class BadCiphertextHashError(Exception):
    pass
