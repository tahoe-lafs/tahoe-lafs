"""
Ported to Python 3.
"""

from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.immutable import ShareFile

def get_share_file(filename):
    with open(filename, "rb") as f:
        prefix = f.read(32)
    if MutableShareFile.is_valid_header(prefix):
        return MutableShareFile(filename)
    # otherwise assume it's immutable
    return ShareFile(filename)
