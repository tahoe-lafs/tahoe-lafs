"""
Ported to Python 3.
"""

from typing import Union

import attr

from nacl.hash import blake2b
from nacl.encoding import RawEncoder

from .lease import (
    LeaseInfo,
    HashedLeaseInfo,
)

@attr.s(frozen=True)
class CleartextLeaseSerializer(object):
    """
    Serialize and unserialize leases with cleartext secrets.
    """
    _to_data = attr.ib()
    _from_data = attr.ib()

    def serialize(self, lease):
        # type: (LeaseInfo) -> bytes
        """
        Represent the given lease as bytes with cleartext secrets.
        """
        if isinstance(lease, LeaseInfo):
            return self._to_data(lease)
        raise ValueError(
            "ShareFile v1 schema only supports LeaseInfo, not {!r}".format(
                lease,
            ),
        )

    def unserialize(self, data):
        # type: (bytes) -> LeaseInfo
        """
        Load a lease with cleartext secrets from the given bytes representation.
        """
        # In v1 of the immutable schema lease secrets are stored plaintext.
        # So load the data into a plain LeaseInfo which works on plaintext
        # secrets.
        return self._from_data(data)

@attr.s(frozen=True)
class HashedLeaseSerializer(object):
    _to_data = attr.ib()
    _from_data = attr.ib()

    @classmethod
    def _hash_secret(cls, secret):
        # type: (bytes) -> bytes
        """
        Hash a lease secret for storage.
        """
        return blake2b(secret, digest_size=32, encoder=RawEncoder)

    @classmethod
    def _hash_lease_info(cls, lease_info):
        # type: (LeaseInfo) -> HashedLeaseInfo
        """
        Hash the cleartext lease info secrets into a ``HashedLeaseInfo``.
        """
        if not isinstance(lease_info, LeaseInfo):
            # Provide a little safety against misuse, especially an attempt to
            # re-hash an already-hashed lease info which is represented as a
            # different type.
            raise TypeError(
                "Can only hash LeaseInfo, not {!r}".format(lease_info),
            )

        # Hash the cleartext secrets in the lease info and wrap the result in
        # a new type.
        return HashedLeaseInfo(
            attr.assoc(
                lease_info,
                renew_secret=cls._hash_secret(lease_info.renew_secret),
                cancel_secret=cls._hash_secret(lease_info.cancel_secret),
            ),
            cls._hash_secret,
        )

    def serialize(self, lease: Union[LeaseInfo, HashedLeaseInfo]) -> bytes:
        if isinstance(lease, LeaseInfo):
            # v2 of the immutable schema stores lease secrets hashed.  If
            # we're given a LeaseInfo then it holds plaintext secrets.  Hash
            # them before trying to serialize.
            lease = self._hash_lease_info(lease)
        if isinstance(lease, HashedLeaseInfo):
            return self._to_data(lease)
        raise ValueError(
            "ShareFile v2 schema cannot represent lease {!r}".format(
                lease,
            ),
        )

    def unserialize(self, data):
        # type: (bytes) -> HashedLeaseInfo
        # In v2 of the immutable schema lease secrets are stored hashed.  Wrap
        # a LeaseInfo in a HashedLeaseInfo so it can supply the correct
        # interpretation for those values.
        return HashedLeaseInfo(self._from_data(data), self._hash_secret)

v1_immutable = CleartextLeaseSerializer(
    LeaseInfo.to_immutable_data,
    LeaseInfo.from_immutable_data,
)

v2_immutable = HashedLeaseSerializer(
    HashedLeaseInfo.to_immutable_data,
    LeaseInfo.from_immutable_data,
)

v1_mutable = CleartextLeaseSerializer(
    LeaseInfo.to_mutable_data,
    LeaseInfo.from_mutable_data,
)

v2_mutable = HashedLeaseSerializer(
    HashedLeaseInfo.to_mutable_data,
    LeaseInfo.from_mutable_data,
)
