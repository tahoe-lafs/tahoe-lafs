"""
A module that loads pre-generated test vectors.

:ivar DATA_PATH: The path of the file containing test vectors.

:ivar capabilities: The capability test vectors.
"""

from __future__ import annotations

from typing import TextIO
from attrs import frozen
from yaml import safe_load, safe_dump
from base64 import b64encode, b64decode

from twisted.python.filepath import FilePath

from .model import Param, Sample, SeedParam
from ..util import CHK, SSK

DATA_PATH: FilePath = FilePath(__file__).sibling("test_vectors.yaml")

# The version of the persisted test vector data this code can interpret.
CURRENT_VERSION: str = "2023-01-16.2"

@frozen
class Case:
    """
    Represent one case for which we want/have a test vector.
    """
    seed_params: Param
    convergence: bytes
    seed_data: Sample
    fmt: CHK | SSK
    segment_size: int

    @property
    def data(self):
        return stretch(self.seed_data.seed, self.seed_data.length)

    @property
    def params(self):
        return self.seed_params.realize(self.fmt.max_shares)


def encode_bytes(b: bytes) -> str:
    """
    Base64 encode some bytes to text so they are representable in JSON.
    """
    return b64encode(b).decode("ascii")


def decode_bytes(b: str) -> bytes:
    """
    Base64 decode some text to bytes.
    """
    return b64decode(b.encode("ascii"))


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


def save_capabilities(results: list[tuple[Case, str]], path: FilePath = DATA_PATH) -> None:
    """
    Save some test vector cases and their expected values.

    This is logically the inverse of ``load_capabilities``.
    """
    path.setContent(safe_dump({
        "version": CURRENT_VERSION,
        "vector": [
            {
                "convergence": encode_bytes(case.convergence),
                "format": {
                    "kind": case.fmt.kind,
                    "params": case.fmt.to_json(),
                },
                "sample": {
                    "seed": encode_bytes(case.seed_data.seed),
                    "length": case.seed_data.length,
                },
                "zfec": {
                    "segmentSize": case.segment_size,
                    "required": case.params.required,
                    "total": case.params.total,
                },
                "expected": cap,
            }
            for (case, cap)
            in results
        ],
    }).encode("ascii"))


def load_format(serialized: dict) -> CHK | SSK:
    """
    Load an encrypted object format from a simple description of it.

    :param serialized: A ``dict`` describing either CHK or SSK, possibly with
        some parameters.
    """
    if serialized["kind"] == "chk":
        return CHK.load(serialized["params"])
    elif serialized["kind"] == "ssk":
        return SSK.load(serialized["params"])
    else:
        raise ValueError(f"Unrecognized format: {serialized}")


def load_capabilities(f: TextIO) -> dict[Case, str]:
    """
    Load some test vector cases and their expected results from the given
    file.

    This is logically the inverse of ``save_capabilities``.
    """
    data = safe_load(f)
    if data is None:
        return {}
    if data["version"] != CURRENT_VERSION:
        print(
            f"Current version is {CURRENT_VERSION}; "
            f"cannot load version {data['version']} data."
        )
        return {}

    return {
        Case(
            seed_params=SeedParam(case["zfec"]["required"], case["zfec"]["total"]),
            segment_size=case["zfec"]["segmentSize"],
            convergence=decode_bytes(case["convergence"]),
            seed_data=Sample(decode_bytes(case["sample"]["seed"]), case["sample"]["length"]),
            fmt=load_format(case["format"]),
        ): case["expected"]
        for case
        in data["vector"]
    }


try:
    with DATA_PATH.open() as f:
        capabilities: dict[Case, str] = load_capabilities(f)
except FileNotFoundError:
    capabilities = {}
