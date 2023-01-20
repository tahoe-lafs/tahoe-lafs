"""
Define input parameters for test vector generation.

:ivar CONVERGENCE_SECRETS: Convergence secrets.

:ivar SEGMENT_SIZE: The single segment size that the Python implementation
    currently supports without a lot of refactoring.

:ivar OBJECT_DESCRIPTIONS: Small objects with instructions which can be
    expanded into a possibly large byte string.  These are intended to be used
    as plaintext inputs.

:ivar ZFEC_PARAMS: Input parameters to ZFEC.

:ivar FORMATS: Encoding/encryption formats.
"""

from __future__ import annotations

from hashlib import sha256

from .model import MAX_SHARES
from .vectors import Sample, SeedParam
from ..util import CHK, SSK

def digest(bs: bytes) -> bytes:
    """
    Digest bytes to bytes.
    """
    return sha256(bs).digest()


def hexdigest(bs: bytes) -> str:
    """
    Digest bytes to text.
    """
    return sha256(bs).hexdigest()

# Just a couple convergence secrets.  The only thing we do with this value is
# feed it into a tagged hash.  It certainly makes a difference to the output
# but the hash should destroy any structure in the input so it doesn't seem
# like there's a reason to test a lot of different values.
CONVERGENCE_SECRETS: list[bytes] = [
    b"aaaaaaaaaaaaaaaa",
    digest(b"Hello world")[:16],
]

SEGMENT_SIZE: int = 128 * 1024

# Exercise at least a handful of different sizes, trying to cover:
#
#  1. Some cases smaller than one "segment" (128k).
#     This covers shrinking of some parameters to match data size.
#     This includes one case of the smallest possible CHK.
#
#  2. Some cases right on the edges of integer segment multiples.
#     Because boundaries are tricky.
#
#  4. Some cases that involve quite a few segments.
#     This exercises merkle tree construction more thoroughly.
#
# See ``stretch`` for construction of the actual test data.
OBJECT_DESCRIPTIONS: list[Sample] = [
    # The smallest possible.  55 bytes and smaller are LIT.
    Sample(b"a", 56),
    Sample(b"a", 1024),
    Sample(b"c", 4096),
    Sample(digest(b"foo"), SEGMENT_SIZE - 1),
    Sample(digest(b"bar"), SEGMENT_SIZE + 1),
    Sample(digest(b"baz"), SEGMENT_SIZE * 16 - 1),
    Sample(digest(b"quux"), SEGMENT_SIZE * 16 + 1),
    Sample(digest(b"bazquux"), SEGMENT_SIZE * 32),
    Sample(digest(b"foobar"), SEGMENT_SIZE * 64 - 1),
    Sample(digest(b"barbaz"), SEGMENT_SIZE * 64 + 1),
]

ZFEC_PARAMS: list[SeedParam] = [
    SeedParam(1, 1),
    SeedParam(1, 3),
    SeedParam(2, 3),
    SeedParam(3, 10),
    SeedParam(71, 255),
    SeedParam(101, MAX_SHARES),
]

FORMATS: list[CHK | SSK] = [
    CHK(),

    # These start out unaware of a key but various keys will be supplied
    # during generation.
    SSK(name="sdmf", key=None),
    SSK(name="mdmf", key=None),
]
