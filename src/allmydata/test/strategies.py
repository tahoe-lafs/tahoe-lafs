"""
Hypothesis strategies use for testing Tahoe-LAFS.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from hypothesis.strategies import (
    one_of,
    builds,
    binary,
    integers,
)

from ..uri import (
    WriteableSSKFileURI,
    WriteableMDMFFileURI,
    DirectoryURI,
    MDMFDirectoryURI,
)

from allmydata.util.base32 import (
    b2a,
)


def write_capabilities():
    """
    Build ``IURI`` providers representing all kinds of write capabilities.
    """
    return one_of([
        ssk_capabilities(),
        mdmf_capabilities(),
        dir2_capabilities(),
        dir2_mdmf_capabilities(),
    ])


def ssk_capabilities():
    """
    Build ``WriteableSSKFileURI`` instances.
    """
    return builds(
        WriteableSSKFileURI,
        ssk_writekeys(),
        ssk_fingerprints(),
    )


def _writekeys(size=16):
    """
    Build ``bytes`` representing write keys.
    """
    return binary(min_size=size, max_size=size)


def ssk_writekeys():
    """
    Build ``bytes`` representing SSK write keys.
    """
    return _writekeys()


def _fingerprints(size=32):
    """
    Build ``bytes`` representing fingerprints.
    """
    return binary(min_size=size, max_size=size)


def ssk_fingerprints():
    """
    Build ``bytes`` representing SSK fingerprints.
    """
    return _fingerprints()


def mdmf_capabilities():
    """
    Build ``WriteableMDMFFileURI`` instances.
    """
    return builds(
        WriteableMDMFFileURI,
        mdmf_writekeys(),
        mdmf_fingerprints(),
    )


def mdmf_writekeys():
    """
    Build ``bytes`` representing MDMF write keys.
    """
    return _writekeys()


def mdmf_fingerprints():
    """
    Build ``bytes`` representing MDMF fingerprints.
    """
    return _fingerprints()


def dir2_capabilities():
    """
    Build ``DirectoryURI`` instances.
    """
    return builds(
        DirectoryURI,
        ssk_capabilities(),
    )


def dir2_mdmf_capabilities():
    """
    Build ``MDMFDirectoryURI`` instances.
    """
    return builds(
        MDMFDirectoryURI,
        mdmf_capabilities(),
    )


def offsets(min_value=0, max_value=2 ** 16):
    """
    Build ``int`` values that could be used as valid offsets into a sequence
    (such as share data in a share file).
    """
    return integers(min_value, max_value)

def lengths(min_value=1, max_value=2 ** 16):
    """
    Build ``int`` values that could be used as valid lengths of data (such as
    share data in a share file).
    """
    return integers(min_value, max_value)


def base32text():
    """
    Build text()s that are valid base32
    """
    return builds(
        lambda b: str(b2a(b), "ascii"),
        binary(),
    )
