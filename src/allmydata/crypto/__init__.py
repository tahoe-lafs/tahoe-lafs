class BadSignature(Exception):
    pass


class BadPrefixError(Exception):
    pass


def remove_prefix(s_bytes, prefix):
    if not s_bytes.startswith(prefix):
        raise BadPrefixError("did not see expected '%s' prefix" % (prefix,))
    return s_bytes[len(prefix):]
