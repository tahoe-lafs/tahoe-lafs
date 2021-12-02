"""
Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.immutable import ShareFile

def get_share_file(filename):
    with open(filename, "rb") as f:
        prefix = f.read(32)
    if MutableShareFile.is_valid_header(prefix):
        return MutableShareFile(filename)
    # otherwise assume it's immutable
    return ShareFile(filename)
