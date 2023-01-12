"""
A module that loads pre-generated test vectors.

:ivar DATA_PATH: The path of the file containing test vectors.

:ivar capabilities: The CHK test vectors.
"""

from __future__ import annotations

from typing import TextIO
from attrs import frozen
from yaml import safe_load
from pathlib import Path
from base64 import b64encode, b64decode

from twisted.python.filepath import FilePath

DATA_PATH: FilePath = FilePath(__file__).sibling("test_vectors.yaml")

@frozen
class Sample:
    """
    Some instructions for building a long byte string.

    :ivar seed: Some bytes to repeat some times to produce the string.
    :ivar length: The length of the desired byte string.
    """
    seed: bytes
    length: int

@frozen
class Param:
    """
    Some ZFEC parameters.
    """
    required: int
    total: int

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

@frozen
class SeedParam:
    """
    Some ZFEC parameters, almost.

    :ivar required: The number of required shares.

    :ivar total: Either the number of total shares or the constant
        ``MAX_SHARES`` to indicate that the total number of shares should be
        the maximum number supported by the object format.
    """
    required: int
    total: int | str

    def realize(self, max_total: int) -> Param:
        """
        Create a ``Param`` from this object's values, possibly
        substituting the given real value for total if necessary.

        :param max_total: The value to use to replace ``MAX_SHARES`` if
            necessary.
        """
        if self.total == MAX_SHARES:
            return Param(self.required, max_total)
        return Param(self.required, self.total)

@frozen
class Case:
    """
    Represent one case for which we want/have a test vector.
    """
    seed_params: Param
    convergence: bytes
    seed_data: Sample
    fmt: str

    @property
    def data(self):
        return stretch(self.seed_data.seed, self.seed_data.length)

    @property
    def params(self):
        return self.seed_params.realize(MAX_SHARES_MAP[self.fmt])


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


def load_capabilities(f: TextIO) -> dict[Case, str]:
    data = safe_load(f)
    if data is None:
        return {}
    return {
        Case(
            seed_params=SeedParam(case["zfec"]["required"], case["zfec"]["total"]),
            convergence=decode_bytes(case["convergence"]),
            seed_data=Sample(decode_bytes(case["sample"]["seed"]), case["sample"]["length"]),
            fmt=case["format"],
        ): case["expected"]
        for case
        in data["vector"]
    }


try:
    with DATA_PATH.open() as f:
        capabilities: dict[Case, str] = load_capabilities(f)
except FileNotFoundError:
    capabilities = {}
