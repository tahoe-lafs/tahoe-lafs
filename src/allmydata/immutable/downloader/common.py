
(AVAILABLE, PENDING, OVERDUE, COMPLETE, CORRUPT, DEAD, BADSEGNUM) = \
 ("AVAILABLE", "PENDING", "OVERDUE", "COMPLETE", "CORRUPT", "DEAD", "BADSEGNUM")

class BadSegmentNumberError(Exception):
    pass
class WrongSegmentError(Exception):
    pass
class BadCiphertextHashError(Exception):
    pass

class DownloadStopped(Exception):
    pass
