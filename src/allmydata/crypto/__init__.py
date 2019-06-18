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
    Removes `prefix` from `s_bytes` safely
    """
    if not s_bytes.startswith(prefix):
        raise BadPrefixError(
            "did not see expected '{}' prefix".format(prefix)
        )
    return s_bytes[len(prefix):]
