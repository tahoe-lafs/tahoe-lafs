"""
Exceptions raise by allmydata.crypto.* modules
"""


class BadSignature(Exception):
    """
    An alleged signature did not match
    """


class BadPrefixError(Exception):
    """
    A key did not start with the required prefix
    """
