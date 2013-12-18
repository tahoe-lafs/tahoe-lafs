# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

import os.path
from allmydata.util import base32

class DataTooLargeError(Exception):
    pass
class UnknownMutableContainerVersionError(Exception):
    pass
class UnknownImmutableContainerVersionError(Exception):
    pass


def si_b2a(storageindex):
    return base32.b2a(storageindex)

def si_a2b(ascii_storageindex):
    return base32.a2b(ascii_storageindex)

def storage_index_to_dir(storageindex):
    sia = si_b2a(storageindex)
    return os.path.join(sia[:2], sia)
