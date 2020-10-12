"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from ..util.future_builtins import *  # noqa: F401, F403

from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.immutable import ShareFile

def get_share_file(filename):
    with open(filename, "rb") as f:
        prefix = f.read(32)
    if prefix == MutableShareFile.MAGIC:
        return MutableShareFile(filename)
    # otherwise assume it's immutable
    return ShareFile(filename)

