"""
Verify certain results against test vectors with well-known results.
"""

from typing import TypeVar, Iterator, Awaitable, Callable

from tempfile import NamedTemporaryFile
from hashlib import sha256
from itertools import product
from yaml import safe_dump

from pytest import mark
from pytest_twisted import ensureDeferred

from . import vectors
from .util import cli, await_client_ready
from allmydata.client import read_config
from allmydata.util import base32

CONVERGENCE_SECRETS = [
    b"aaaaaaaaaaaaaaaa",
    # b"bbbbbbbbbbbbbbbb",
    # b"abcdefghijklmnop",
    # b"hello world stuf",
    # b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f",
    # sha256(b"Hello world").digest()[:16],
]

ONE_KB = sha256(b"Hello world").digest() * 32
assert len(ONE_KB) == 1024

OBJECT_DATA = [
    b"a" * 1024,
    # b"b" * 2048,
    # b"c" * 4096,
    # (ONE_KB * 8)[:-1],
    # (ONE_KB * 8) + b"z",
    # (ONE_KB * 128)[:-1],
    # (ONE_KB * 128) + b"z",
]

ZFEC_PARAMS = [
    (1, 1),
    (1, 3),
    # (2, 3),
    # (3, 10),
    # (71, 255),
    # (101, 256),
]

@mark.parametrize('convergence', CONVERGENCE_SECRETS)
def test_convergence(convergence):
    assert isinstance(convergence, bytes), "Convergence secret must be bytes"
    assert len(convergence) == 16, "Convergence secret must by 16 bytes"


@mark.parametrize('data', OBJECT_DATA)
def test_data(data):
    assert isinstance(data, bytes), "Object data must be bytes."

@mark.parametrize('params', ZFEC_PARAMS)
@mark.parametrize('convergence', CONVERGENCE_SECRETS)
@mark.parametrize('data', OBJECT_DATA)
@ensureDeferred
async def test_chk_capability(alice, params, convergence, data):
    # rewrite alice's config to match params and convergence
    await reconfigure(alice, params, convergence)

    # upload data as a CHK
    actual = upload_immutable(alice, data)

    # compare the resulting cap to the expected result
    expected = vectors.chk[key(params, convergence, data)]
    assert actual == expected


α = TypeVar("α")
β = TypeVar("β")

async def asyncfoldr(
        i: Iterator[Awaitable[α]],
        f: Callable[[α, β], β],
        initial: β,
) -> β:
    result = initial
    async for a in i:
        result = f(a, result)
    return result

def insert(item: tuple[α, β], d: dict[α, β]) -> dict[α, β]:
    d[item[0]] = item[1]
    return d

@ensureDeferred
async def test_generate(reactor, request, alice):
    results = await asyncfoldr(
        generate(reactor, request, alice),
        insert,
        {},
    )
    with vectors.CHK_PATH.open("w") as f:
        f.write(safe_dump(results))


async def reconfigure(reactor, request, alice, params, convergence):
    needed, total = params
    config = read_config(alice.node_dir, "tub.port")
    config.set_config("client", "shares.happy", str(1))
    config.set_config("client", "shares.needed", str(needed))
    config.set_config("client", "shares.total", str(total))
    config.write_private_config("convergence", base32.b2a(convergence))

    # restart alice
    print(f"Restarting {alice.node_dir} for ZFEC reconfiguration")
    await alice.restart_async(reactor, request)
    print("Restarted.  Waiting for ready state.")
    await_client_ready(alice)
    print("Ready.")


async def generate(reactor, request, alice):
    node_key = (None, None)
    for params, secret, data in product(ZFEC_PARAMS, CONVERGENCE_SECRETS, OBJECT_DATA):
        if node_key != (params, secret):
            await reconfigure(reactor, request, alice, params, secret)
            node_key = (params, secret)

        yield key(params, secret, data), upload_immutable(alice, data)


def key(params, secret, data):
    return f"{params[0]}/{params[1]},{digest(secret)},{digest(data)}"


def upload_immutable(alice, data):
    with NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        return cli(alice, "put", "--format=chk", f.name).decode("utf-8").strip()


def digest(bs):
    return sha256(bs).hexdigest()
