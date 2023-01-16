"""
Verify certain results against test vectors with well-known results.
"""

from __future__ import annotations

from typing import AsyncGenerator, Iterator
from hashlib import sha256
from itertools import starmap, product
from yaml import safe_dump

from attrs import evolve

from pytest import mark
from pytest_twisted import ensureDeferred

from . import vectors
from .util import CHK, SSK, reconfigure, upload, TahoeProcess

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
CONVERGENCE_SECRETS = [
    b"aaaaaaaaaaaaaaaa",
    digest(b"Hello world")[:16],
]

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

SEGMENT_SIZE = 128 * 1024
OBJECT_DESCRIPTIONS = [
    # The smallest possible.  55 bytes and smaller are LIT.
    vectors.Sample(b"a", 56),
    vectors.Sample(b"a", 1024),
    vectors.Sample(b"c", 4096),
    vectors.Sample(digest(b"foo"), SEGMENT_SIZE - 1),
    vectors.Sample(digest(b"bar"), SEGMENT_SIZE + 1),
    vectors.Sample(digest(b"baz"), SEGMENT_SIZE * 16 - 1),
    vectors.Sample(digest(b"quux"), SEGMENT_SIZE * 16 + 1),
    vectors.Sample(digest(b"foobar"), SEGMENT_SIZE * 64 - 1),
    vectors.Sample(digest(b"barbaz"), SEGMENT_SIZE * 64 + 1),
]

ZFEC_PARAMS = [
    vectors.SeedParam(1, 1),
    vectors.SeedParam(1, 3),
    vectors.SeedParam(2, 3),
    vectors.SeedParam(3, 10),
    vectors.SeedParam(71, 255),
    vectors.SeedParam(101, vectors.MAX_SHARES),
]

FORMATS = [
    CHK(),
    # These start out unaware of a key but various keys will be supplied
    # during generation.
    SSK(name="sdmf", key=None),
    SSK(name="mdmf", key=None),
]

@mark.parametrize('convergence', CONVERGENCE_SECRETS)
def test_convergence(convergence):
    """
    Convergence secrets are 16 bytes.
    """
    assert isinstance(convergence, bytes), "Convergence secret must be bytes"
    assert len(convergence) == 16, "Convergence secret must by 16 bytes"


@mark.parametrize('case_and_expected', vectors.capabilities.items())
@ensureDeferred
async def test_capability(reactor, request, alice, case_and_expected):
    """
    The capability that results from uploading certain well-known data
    with certain well-known parameters results in exactly the previously
    computed value.
    """
    case, expected = case_and_expected

    # rewrite alice's config to match params and convergence
    await reconfigure(reactor, request, alice, (1, case.params.required, case.params.total), case.convergence)

    # upload data in the correct format
    actual = upload(alice, case.fmt, case.data)

    # compare the resulting cap to the expected result
    assert actual == expected


@ensureDeferred
async def test_generate(reactor, request, alice):
    """
    This is a helper for generating the test vectors.

    You can re-generate the test vectors by fixing the name of the test and
    running it.  Normally this test doesn't run because it ran once and we
    captured its output.  Other tests run against that output and we want them
    to run against the results produced originally, not a possibly
    ever-changing set of outputs.
    """
    space = starmap(vectors.Case, product(
        ZFEC_PARAMS,
        CONVERGENCE_SECRETS,
        OBJECT_DESCRIPTIONS,
        FORMATS,
    ))
    iterresults = generate(reactor, request, alice, space)

    # Update the output file with results as they become available.
    results = []
    async for result in iterresults:
        results.append(result)
        write_results(vectors.DATA_PATH, results)

def write_results(path: FilePath, results: list[tuple[Case, str]]) -> None:
    """
    Save the given results.
    """
    path.setContent(safe_dump({
        "version": vectors.CURRENT_VERSION,
        "vector": [
            {
                "convergence": vectors.encode_bytes(case.convergence),
                "format": {
                    "kind": case.fmt.kind,
                    "params": case.fmt.to_json(),
                },
                "sample": {
                    "seed": vectors.encode_bytes(case.seed_data.seed),
                    "length": case.seed_data.length,
                },
                "zfec": {
                    "segmentSize": SEGMENT_SIZE,
                    "required": case.params.required,
                    "total": case.params.total,
                },
                "expected": cap,
            }
            for (case, cap)
            in results
        ],
    }).encode("ascii"))

async def generate(
        reactor,
        request,
        alice: TahoeProcess,
        cases: Iterator[vectors.Case],
) -> AsyncGenerator[[vectors.Case, str], None]:
    """
    Generate all of the test vectors using the given node.

    :param reactor: The reactor to use to restart the Tahoe-LAFS node when it
        needs to be reconfigured.

    :param request: The pytest request object to use to arrange process
        cleanup.

    :param format: The name of the encryption/data format to use.

    :param alice: The Tahoe-LAFS node to use to generate the test vectors.

    :param case: The inputs for which to generate a value.

    :return: The capability for the case.
    """
    # Share placement doesn't affect the resulting capability.  For maximum
    # reliability of this generator, be happy if we can put shares anywhere
    happy = 1
    for case in cases:
        await reconfigure(
            reactor,
            request,
            alice,
            (happy, case.params.required, case.params.total),
            case.convergence
        )

        # Give the format a chance to make an RSA key if it needs it.
        case = evolve(case, fmt=case.fmt.customize())
        cap = upload(alice, case.fmt, case.data)
        yield case, cap
