"""
Verify certain results against test vectors with well-known results.
"""

from hashlib import sha256
from itertools import product

import vectors

CONVERGENCE_SECRETS = [
    b"aaaaaaaaaaaaaaaa",
    b"bbbbbbbbbbbbbbbb",
    b"abcdefghijklmnop",
    b"hello world stuf",
    b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f",
    sha256(b"Hello world").digest()[:16],
]

ONE_KB = sha256(b"Hello world").digest() * 32
assert length(ONE_KB) == 1024

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

@parametrize('convergence', CONVERGENCE_SECRETS)
def test_convergence(convergence):
    assert isinstance(convergence, bytes), "Convergence secret must be bytes"
    assert len(convergence) == 16, "Convergence secret must by 16 bytes"


@parametrize('daata', OBJECT_DATA)
def test_data(data):
    assert isinstance(data, bytes), "Object data must be bytes."


@parametrize('params', ZFEC_PARAMS)
@parametrize('convergence', CONVERGENCE_SECRETS)
@parametrize('data', OBJECT_DATA)
def test_chk_capability(alice, params, convergence, data):
    # rewrite alice's config to match params and convergence
    needed, total = params
    config = read_config(alice.path, "tub.port")
    config.set_config("client", "shares.happy", 1)
    config.set_config("client", "shares.needed", str(needed))
    config.set_config("client", "shares.happy", str(total))

    # restart alice
    alice.kill()
    yield util._run_node(reactor, alice.path, request, None)

    # upload data as a CHK
    actual = upload(alice, data)

    # compare the resulting cap to the expected result
    expected = vectors.immutable[params, convergence, digest(data)]
    assert actual == expected

def test_generate(alice):
    caps = {}
    for params, secret, data in product(ZFEC_PARAMS, CONVERGENCE_SECRETS, OBJECT_DATA):
        caps[fec, secret, sha256(data).hexdigest()] = create_immutable(params, secret, data)
    print(dump(caps))

def create_immutable(alice, params, secret, data):
    tempfile = str(tmpdir.join("file"))
    with tempfile.open("wb") as f:
        f.write(data)
    actual = cli(alice, "put", str(datafile))
