"""
Simple data type definitions useful in the definition/verification of test
vectors.
"""

from __future__ import annotations

from attrs import frozen

# CHK have a max of 256 shares.  SDMF / MDMF have a max of 255 shares!
# Represent max symbolically and resolve it when we know what format we're
# dealing with.
MAX_SHARES = "max"

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
