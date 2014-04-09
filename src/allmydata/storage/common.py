
import os, re

from allmydata.util import base32


# Share numbers match this regex:
NUM_RE=re.compile("^[0-9]+$")

PREFIX = re.compile("^[%s]{2}$" % (base32.z_base_32_alphabet,))


class DataTooLargeError(Exception):
    pass


class CorruptStoredShareError(Exception):
    def __init__(self, shnum, *rest):
        Exception.__init__(self, shnum, *rest)
        self.shnum = shnum

class UnknownContainerVersionError(CorruptStoredShareError):
    pass

class UnknownMutableContainerVersionError(UnknownContainerVersionError):
    pass

class UnknownImmutableContainerVersionError(UnknownContainerVersionError):
    pass


def si_b2a(storageindex):
    return base32.b2a(storageindex)

def si_a2b(ascii_storageindex):
    return base32.a2b(ascii_storageindex)

def storage_index_to_prefix(storageindex):
    sia = si_b2a(storageindex)
    return sia[:2]

def storage_index_to_dir(storageindex):
    sia = si_b2a(storageindex)
    return os.path.join(sia[:2], sia)
