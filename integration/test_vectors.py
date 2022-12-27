"""
Verify certain results against test vectors with well-known results.
"""

from __future__ import annotations

from time import sleep
from typing import AsyncGenerator, Iterator
from hashlib import sha256
from itertools import product
from yaml import safe_dump

from attrs import frozen

from pytest import mark
from pytest_twisted import ensureDeferred

from . import vectors
from .util import reconfigure, upload, asyncfoldr, insert, TahoeProcess

def digest(bs: bytes) -> bytes:
    return sha256(bs).digest()


def hexdigest(bs: bytes) -> str:
    return sha256(bs).hexdigest()


# Sometimes upload fail spuriously...
RETRIES = 3


# Just a couple convergence secrets.  The only thing we do with this value is
# feed it into a tagged hash.  It certainly makes a difference to the output
# but the hash should destroy any structure in the input so it doesn't seem
# like there's a reason to test a lot of different values.
CONVERGENCE_SECRETS = [
    b"aaaaaaaaaaaaaaaa",
    digest(b"Hello world")[:16],
]


# Exercise at least a handful of different sizes, trying to cover:
#
#  1. Some cases smaller than one "segment" (128k).
#     This covers shrinking of some parameters to match data size.
#
#  2. Some cases right on the edges of integer segment multiples.
#     Because boundaries are tricky.
#
#  4. Some cases that involve quite a few segments.
#     This exercises merkle tree construction more thoroughly.
#
# See ``stretch`` for construction of the actual test data.

SEGMENT_SIZE = 128 * 1024
OBJECT_DESCRIPTIONS = [
    (b"a", 1024),
    (b"c", 4096),
    (digest(b"foo"), SEGMENT_SIZE - 1),
    (digest(b"bar"), SEGMENT_SIZE + 1),
    (digest(b"baz"), SEGMENT_SIZE * 16 - 1),
    (digest(b"quux"), SEGMENT_SIZE * 16 + 1),
    (digest(b"foobar"), SEGMENT_SIZE * 64 - 1),
    (digest(b"barbaz"), SEGMENT_SIZE * 64 + 1),
]

# CHK have a max of 256 shares.  SDMF / MDMF have a max of 255 shares!
# Represent max symbolically and resolve it when we know what format we're
# dealing with.
MAX_SHARES = "max"

# SDMF and MDMF encode share counts (N and k) into the share itself as an
# unsigned byte.  They could have encoded (share count - 1) to fit the full
# range supported by ZFEC into the unsigned byte - but they don't.  So 256 is
# inaccessible to those formats and we set the upper bound at 255.
MAX_SHARES_MAP = {
    "chk": 256,
    "sdmf": 255,
    "mdmf": 255,
}

ZFEC_PARAMS = [
    (1, 1),
    (1, 3),
    (2, 3),
    (3, 10),
    (71, 255),
    (101, MAX_SHARES),
]

FORMATS = [
    "chk",
    "sdmf",
    "mdmf",
]

@mark.parametrize('convergence_idx', range(len(CONVERGENCE_SECRETS)))
def test_convergence(convergence_idx):
    """
    Convergence secrets are 16 bytes.
    """
    convergence = CONVERGENCE_SECRETS[convergence_idx]
    assert isinstance(convergence, bytes), "Convergence secret must be bytes"
    assert len(convergence) == 16, "Convergence secret must by 16 bytes"


@mark.parametrize('params_idx', range(len(ZFEC_PARAMS)))
@mark.parametrize('convergence_idx', range(len(CONVERGENCE_SECRETS)))
@mark.parametrize('data_idx', range(len(OBJECT_DESCRIPTIONS)))
@mark.parametrize('fmt_idx', range(len(FORMATS)))
@ensureDeferred
async def test_capability(reactor, request, alice, params_idx, convergence_idx, data_idx, fmt_idx):
    """
    The capability that results from uploading certain well-known data
    with certain well-known parameters results in exactly the previously
    computed value.
    """
    case = load_case(
        params_idx,
        convergence_idx,
        data_idx,
        fmt_idx,
    )

    # rewrite alice's config to match params and convergence
    await reconfigure(reactor, request, alice, (1,) + case.params, case.convergence)

    # upload data in the correct format
    actual = upload(alice, case.fmt, case.data)

    # compare the resulting cap to the expected result
    expected = vectors.capabilities[case.key]
    assert actual == expected


@ensureDeferred
async def skiptest_generate(reactor, request, alice):
    """
    This is a helper for generating the test vectors.

    You can re-generate the test vectors by fixing the name of the test and
    running it.  Normally this test doesn't run because it ran once and we
    captured its output.  Other tests run against that output and we want them
    to run against the results produced originally, not a possibly
    ever-changing set of outputs.
    """
    space = product(
        range(len(ZFEC_PARAMS)),
        range(len(CONVERGENCE_SECRETS)),
        range(len(OBJECT_DESCRIPTIONS)),
        range(len(FORMATS)),
    )
    results = await asyncfoldr(
        generate(reactor, request, alice, space),
        insert,
        {},
    )
    with vectors.CHK_PATH.open("w") as f:
        f.write(safe_dump({
            "version": "2022-12-26",
            "params": {
                "zfec": ZFEC_PARAMS,
                "convergence": CONVERGENCE_SECRETS,
                "objects": OBJECT_DESCRIPTIONS,
                "formats": FORMATS,
            },
            "vector": results,
        }))


async def generate(
        reactor,
        request,
        alice: TahoeProcess,
        space: Iterator[int, int, int, int],
) -> AsyncGenerator[tuple[str, str], None]:
    """
    Generate all of the test vectors using the given node.

    :param reactor: The reactor to use to restart the Tahoe-LAFS node when it
        needs to be reconfigured.

    :param request: The pytest request object to use to arrange process
        cleanup.

    :param format: The name of the encryption/data format to use.

    :param alice: The Tahoe-LAFS node to use to generate the test vectors.

    :param space: An iterator of coordinates in the test vector space for
       which to generate values.  The elements of each tuple give indexes into
       ZFEC_PARAMS, CONVERGENCE_SECRETS, OBJECT_DESCRIPTIONS, and FORMATS.

    :return: The yield values are two-tuples describing a test vector.  The
        first element is a string describing a case and the second element is
        the capability for that case.
    """
    # Share placement doesn't affect the resulting capability.  For maximum
    # reliability of this generator, be happy if we can put shares anywhere
    happy = 1
    node_key = (None, None)
    for params_idx, secret_idx, data_idx, fmt_idx in space:
        case = load_case(params_idx, secret_idx, data_idx, fmt_idx)
        if node_key != (case.params, case.convergence):
            await reconfigure(reactor, request, alice, (happy,) + case.params, case.convergence)
            node_key = (case.params, case.convergence)

        cap = upload(alice, case.fmt, case.data)
        yield case.key, cap


def key(params: int, secret: int, data: int, fmt: int) -> str:
    """
    Construct the key describing the case defined by the given parameters.

    The parameters are indexes into the test data for a certain case.

    :return: A distinct string for the given inputs.
    """
    return f"{params}-{secret}-{data}-{fmt}"


def stretch(seed: bytes, size: int) -> bytes:
    """
    Given a simple description of a byte string, return the byte string
    itself.
    """
    assert isinstance(seed, bytes)
    assert isinstance(size, int)
    assert size > 0
    assert len(seed) > 0

    multiples = size // len(seed) + 1
    return (seed * multiples)[:size]


def load_case(
        params_idx: int,
        convergence_idx: int,
        data_idx: int,
        fmt_idx: int
) -> Case:
    """
    :return:
    """
    params = ZFEC_PARAMS[params_idx]
    fmt = FORMATS[fmt_idx]
    convergence = CONVERGENCE_SECRETS[convergence_idx]
    data = stretch(*OBJECT_DESCRIPTIONS[data_idx])
    if params[1] == MAX_SHARES:
        params = (params[0], MAX_SHARES_MAP[fmt])
    k = key(params_idx, convergence_idx, data_idx, fmt_idx)
    return Case(k, fmt, params, convergence, data)


@frozen
class Case:
    """
    Represent one case for which we want/have a test vector.
    """
    key: str
    fmt: str
    params: tuple[int, int]
    convergence: bytes
    data: bytes
