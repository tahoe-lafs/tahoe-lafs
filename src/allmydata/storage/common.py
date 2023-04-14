"""
Ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2, PY3
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os.path
from allmydata.util import base32

# Backwards compatibility.
from allmydata.interfaces import DataTooLargeError  # noqa: F401

class UnknownContainerVersionError(Exception):
    def __init__(self, filename, version):
        self.filename = filename
        self.version = version

    def __str__(self):
        return "sharefile {!r} had unexpected version {!r}".format(
            self.filename,
            self.version,
        )

class UnknownMutableContainerVersionError(UnknownContainerVersionError):
    pass

class UnknownImmutableContainerVersionError(UnknownContainerVersionError):
    pass

def si_b2a(storageindex):
    return base32.b2a(storageindex)

def si_a2b(ascii_storageindex):
    return base32.a2b(ascii_storageindex)

def si_to_human_readable(storageindex: bytes) -> str:
    """Create human-readable string of storage index."""
    return str(base32.b2a(storageindex), "ascii")

def storage_index_to_dir(storageindex):
    """Convert storage index to directory path.

    Returns native string.
    """
    sia = si_b2a(storageindex)
    if PY3:
        # On Python 3 we expect paths to be unicode.
        sia = sia.decode("ascii")
    return os.path.join(sia[:2], sia)
