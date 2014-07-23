#! /usr/bin/python
# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.immutable import ShareFile

def get_share_file(filename):
    f = open(filename, "rb")
    prefix = f.read(32)
    f.close()
    if prefix == MutableShareFile.MAGIC:
        return MutableShareFile(filename)
    # otherwise assume it's immutable
    return ShareFile(filename)

