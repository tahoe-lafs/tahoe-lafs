"""
Verify certain results against test vectors with well-known results.
"""

from __future__ import annotations

from typing import AsyncGenerator
from hashlib import sha256
from itertools import product
from yaml import safe_dump

from pytest import mark
from pytest_twisted import ensureDeferred

from . import vectors
from .util import reconfigure, upload, asyncfoldr, insert, TahoeProcess

def digest(bs: bytes) -> bytes:
    return sha256(bs).digest()


def hexdigest(bs: bytes) -> str:
    return sha256(bs).hexdigest()


CONVERGENCE_SECRETS = [
    b"aaaaaaaaaaaaaaaa",
    b"bbbbbbbbbbbbbbbb",
    b"abcdefghijklmnop",
    b"hello world stuf",
    b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f",
    digest(b"Hello world")[:16],
]

ONE_KB = digest(b"Hello world") * 32
assert len(ONE_KB) == 1024

OBJECT_DATA = [
    b"a" * 1024,
    b"b" * 2048,
    b"c" * 4096,
    (ONE_KB * 8)[:-1],
    (ONE_KB * 8) + b"z",
    (ONE_KB * 128)[:-1],
    (ONE_KB * 128) + b"z",
]

ZFEC_PARAMS = [
    (1, 1),
    (1, 3),
    (2, 3),
    (3, 10),
    (71, 255),
    (101, 256),
]

@mark.parametrize('convergence_idx', range(len(CONVERGENCE_SECRETS)))
def test_convergence(convergence_idx):
    """
    Convergence secrets are 16 bytes.
    """
    convergence = CONVERGENCE_SECRETS[convergence_idx]
    assert isinstance(convergence, bytes), "Convergence secret must be bytes"
    assert len(convergence) == 16, "Convergence secret must by 16 bytes"


@mark.parametrize('data_idx', range(len(OBJECT_DATA)))
def test_data(data_idx):
    """
    Plaintext data is bytes.
    """
    data = OBJECT_DATA[data_idx]
    assert isinstance(data, bytes), "Object data must be bytes."

@mark.parametrize('params_idx', range(len(ZFEC_PARAMS)))
@mark.parametrize('convergence_idx', range(len(CONVERGENCE_SECRETS)))
@mark.parametrize('data_idx', range(len(OBJECT_DATA)))
@ensureDeferred
async def test_chk_capability(reactor, request, alice, params_idx, convergence_idx, data_idx):
    """
    The CHK capability that results from uploading certain well-known data
    with certain well-known parameters results in exactly the previously
    computed value.
    """
    params = ZFEC_PARAMS[params_idx]
    convergence = CONVERGENCE_SECRETS[convergence_idx]
    data = OBJECT_DATA[data_idx]

    # rewrite alice's config to match params and convergence
    await reconfigure(reactor, request, alice, (1,) + params, convergence)

    # upload data as a CHK
    actual = upload(alice, "chk", data)

    # compare the resulting cap to the expected result
    expected = vectors.chk[key(params, convergence, data)]
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
    results = await asyncfoldr(
        generate(reactor, request, alice),
        insert,
        {},
    )
    with vectors.CHK_PATH.open("w") as f:
        f.write(safe_dump(results))


async def generate(reactor, request, alice: TahoeProcess) -> AsyncGenerator[tuple[str, str], None]:
    """
    Generate all of the test vectors using the given node.

    :param reactor: The reactor to use to restart the Tahoe-LAFS node when it
        needs to be reconfigured.

    :param request: The pytest request object to use to arrange process
        cleanup.

    :param alice: The Tahoe-LAFS node to use to generate the test vectors.

    :return: The yield values are two-tuples describing a test vector.  The
        first element is a string describing a case and the second element is
        the CHK capability for that case.
    """
    node_key = (None, None)
    for params, secret, data in product(ZFEC_PARAMS, CONVERGENCE_SECRETS, OBJECT_DATA):
        if node_key != (params, secret):
            await reconfigure(reactor, request, alice, params, secret)
            node_key = (params, secret)

        yield key(params, secret, data), upload(alice, "chk", data)


def key(params: tuple[int, int], secret: bytes, data: bytes) -> str:
    """
    Construct the key describing the case defined by the given parameters.

    :param params: The ``needed`` and ``total`` ZFEC encoding parameters.
    :param secret: The convergence secret.
    :param data: The plaintext data.

    :return: A distinct string for the given inputs, but shorter.  This is
        suitable for use as, eg, a key in a dictionary.
    """
    return f"{params[0]}/{params[1]},{hexdigest(secret)},{hexdigest(data)}"
