"""
Utilities used by allmydata.crypto modules
"""


class BadSignature(Exception):
    """
    An alleged signature did not match
    """


class BadPrefixError(Exception):
    """
    A key did not start with the required prefix
    """


def remove_prefix(s_bytes, prefix):
    """
    :param bytes s_bytes: a string of bytes whose prefix is removed

    :param bytes prefix: the bytes to remove from the beginning of `s_bytes`

    Removes `prefix` from `s_bytes` and returns the new bytes or
    raises `BadPrefixError` if `s_bytes` did not start with the
    `prefix` specified.

    :returns: `s_bytes` with `prefix` removed from the front.
    """
    if not s_bytes.startswith(prefix):
        raise BadPrefixError(
            "did not see expected '{}' prefix".format(prefix)
        )
    return s_bytes[len(prefix):]
