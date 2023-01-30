"""
A JSON encoder than can serialize bytes.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2, PY3
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import json
import codecs
from typing import Any, Iterator, Type, AnyStr, Union, Optional
if PY2:
    def backslashreplace_py2(ex):
        """
        On Python 2 'backslashreplace' error handler doesn't work, so write our
        own.
        """
        return ''.join('\\x{:02x}'.format(ord(c))
                       for c in ex.object[ex.start:ex.end]), ex.end

    codecs.register_error("backslashreplace_tahoe_py2", backslashreplace_py2)


def bytes_to_unicode(any_bytes: bool, obj: bytes) -> Optional[Union[str, bytes]]:
    """Convert bytes to unicode.

    :param any_bytes: If True, also support non-UTF-8-encoded bytes.
    :param obj: Object to de-byte-ify.
    """
    errors = "backslashreplace" if any_bytes else "strict"
    if PY2 and errors == "backslashreplace":
        errors = "backslashreplace_tahoe_py2"

    def doit(obj: bytes) -> Union[str, bytes]:
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
    def encode(self: json.JSONEncoder, o: bytes, **kwargs: dict[str, Any]) -> str:
        return json.JSONEncoder.encode(
            self, bytes_to_unicode(False, o), **kwargs)

    def iterencode(self: json.JSONEncoder, o: bytes, **kwargs: bool)-> Iterator[str]:   # This seems to violate Liskov's substitution principle according to some research.
        return json.JSONEncoder.iterencode(
            self, bytes_to_unicode(False, o), **kwargs)


class AnyBytesJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder than can also encode bytes of any sort.

    Bytes are decoded to strings using UTF-8, if that fails to decode then the
    bytes are quoted.
    """
    def encode(self, o: bytes, **kwargs: dict[str, Any]) -> str:
        return json.JSONEncoder.encode(
            self, bytes_to_unicode(True, o), **kwargs)

    def iterencode(self: json.JSONEncoder, o: bytes, **kwargs: bool) -> Iterator[str]:   # This seems to violate Liskov's substitution principle according to some research.
        return json.JSONEncoder.iterencode(
            self, bytes_to_unicode(True, o), **kwargs)


def dumps(obj: object, *args: tuple[int], **kwargs: Any) -> str:
    """Encode to JSON, supporting bytes as keys or values.

    :param bool any_bytes: If False (the default) the bytes are assumed to be
        UTF-8 encoded Unicode strings.  If True, non-UTF-8 bytes are quoted for
        human consumption.
    """
    any_bytes = kwargs.pop("any_bytes", False)
    if any_bytes:
        cls: Type[UTF8BytesJSONEncoder | AnyBytesJSONEncoder] = AnyBytesJSONEncoder
    else:
        cls = UTF8BytesJSONEncoder
    return json.dumps(obj, cls=cls, *args, **kwargs)


def dumps_bytes(obj: object, *args: tuple[int], **kwargs: dict[str, Any]) -> Any:
    """Encode to JSON, then encode as bytes.

    :param bool any_bytes: If False (the default) the bytes are assumed to be
        UTF-8 encoded Unicode strings.  If True, non-UTF-8 bytes are quoted for
        human consumption.
    """
    result: Any = dumps(obj, *args, **kwargs)
    if PY3:
        result = result.encode("utf-8")
    return result


# To make this module drop-in compatible with json module:
loads = json.loads


__all__ = ["dumps", "loads"]
