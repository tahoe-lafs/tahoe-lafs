"""
Exceptions raise by allmydata.crypto.* modules

Ported to Python 3.
"""

class BadSignature(Exception):
    """
    An alleged signature did not match
    """


class BadPrefixError(Exception):
    """
    A key did not start with the required prefix
    """
