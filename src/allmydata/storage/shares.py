#! /usr/bin/python

from allmydata.storage.backends.disk.mutable import MutableShareFile
from allmydata.storage.backends.disk.immutable import ShareFile

def get_share_file(filename):
    f = open(filename, "rb")
    prefix = f.read(32)
    f.close()
    if prefix == MutableShareFile.MAGIC:
        return MutableShareFile(filename)
    # otherwise assume it's immutable
    return ShareFile(filename)

