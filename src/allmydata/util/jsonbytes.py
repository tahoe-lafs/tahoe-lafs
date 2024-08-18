"""
A JSON encoder than can serialize bytes.

Ported to Python 3.
"""

import json


def bytes_to_unicode(any_bytes, obj):
    """Convert bytes to unicode.

    :param any_bytes: If True, also support non-UTF-8-encoded bytes.
    :param obj: Object to de-byte-ify.
    """
    errors = "backslashreplace" if any_bytes else "strict"

    def doit(obj):
        """Convert any bytes objects to unicode, recursively."""
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors=errors)
        if isinstance(obj, dict):
            new_obj = {}
            for k, v in obj.items():
                if isinstance(k, bytes):
                    k = k.decode("utf-8", errors=errors)
                v = doit(v)
                new_obj[k] = v
            return new_obj
        if isinstance(obj, (list, set, tuple)):
            return [doit(i) for i in obj]
        return obj

    return doit(obj)


class UTF8BytesJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder than can also encode UTF-8 encoded strings.
    """
    def default(self, o):
        return bytes_to_unicode(False, o)

    def encode(self, o, **kwargs):
        return json.JSONEncoder.encode(
            self, bytes_to_unicode(False, o), **kwargs)

    def iterencode(self, o, **kwargs):
        return json.JSONEncoder.iterencode(
            self, bytes_to_unicode(False, o), **kwargs)


class AnyBytesJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder than can also encode bytes of any sort.

    Bytes are decoded to strings using UTF-8, if that fails to decode then the
    bytes are quoted.
    """
    def default(self, o):
        return bytes_to_unicode(True, o)

    def encode(self, o, **kwargs):
        return json.JSONEncoder.encode(
            self, bytes_to_unicode(True, o), **kwargs)

    def iterencode(self, o, **kwargs):
        return json.JSONEncoder.iterencode(
            self, bytes_to_unicode(True, o), **kwargs)


def dumps(obj, *args, **kwargs):
    """Encode to JSON, supporting bytes as keys or values.

    :param bool any_bytes: If False (the default) the bytes are assumed to be
        UTF-8 encoded Unicode strings.  If True, non-UTF-8 bytes are quoted for
        human consumption.
    """
    any_bytes = kwargs.pop("any_bytes", False)
    if any_bytes:
        cls = AnyBytesJSONEncoder
    else:
        cls = UTF8BytesJSONEncoder
    return json.dumps(obj, cls=cls, *args, **kwargs)


def dumps_bytes(obj, *args, **kwargs):
    """Encode to JSON, then encode as bytes.

    :param bool any_bytes: If False (the default) the bytes are assumed to be
        UTF-8 encoded Unicode strings.  If True, non-UTF-8 bytes are quoted for
        human consumption.
    """
    return dumps(obj, *args, **kwargs).encode("utf-8")


# To make this module drop-in compatible with json module:
loads = json.loads
load = json.load


__all__ = ["dumps", "loads", "load"]
