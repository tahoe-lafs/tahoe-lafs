"""
Netstring encoding and decoding.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from past.builtins import long

try:
    from typing import Optional, Tuple, List  # noqa: F401
except ImportError:
    pass


def netstring(s):  # type: (bytes) -> bytes
    assert isinstance(s, bytes), s # no unicode here
    return b"%d:%s," % (len(s), s,)

def split_netstring(data, numstrings,
                    position=0,
                    required_trailer=None):  # type (bytes, init, int, Optional[bytes]) -> Tuple[List[bytes], int]
    """like string.split(), but extracts netstrings. Ignore all bytes of data
    before the 'position' byte. Return a tuple of (list of elements (numstrings
    in length), new position index). The new position index points to the first
    byte which was not consumed (the 'required_trailer', if any, counts as
    consumed).  If 'required_trailer' is not None, throw ValueError if leftover
    data does not exactly equal 'required_trailer'."""
    assert isinstance(data, bytes)
    assert required_trailer is None or isinstance(required_trailer, bytes)
    assert isinstance(position, (int, long)), (repr(position), type(position))
    elements = []
    assert numstrings >= 0
    while position < len(data):
        colon = data.index(b":", position)
        length = int(data[position:colon])
        string = data[colon+1:colon+1+length]
        assert len(string) == length, (len(string), length)
        elements.append(string)
        position = colon+1+length
        assert data[position] == b","[0], position
        position += 1
        if len(elements) == numstrings:
            break
    if len(elements) < numstrings:
        raise ValueError("ran out of netstrings")
    if required_trailer is not None:
        if ((len(data) - position) != len(required_trailer)) or (data[position:] != required_trailer):
            raise ValueError("leftover data in netstrings")
        return (elements, position + len(required_trailer))
    else:
        return (elements, position)
