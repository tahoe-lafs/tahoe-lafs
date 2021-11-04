"""
Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import struct

try:
    from typing import Union
except ImportError:
    pass

import attr

from nacl.hash import blake2b
from nacl.encoding import RawEncoder

from .lease import (
    LeaseInfo,
    HashedLeaseInfo,
)

def _header(version, max_size):
    # type: (int, int) -> bytes
    """
    Construct the header for an immutable container.

    :param version: the container version to include the in header
    :param max_size: the maximum data size the container will hold

    :return: some bytes to write at the beginning of the container
    """
    # The second field -- the four-byte share data length -- is no longer
    # used as of Tahoe v1.3.0, but we continue to write it in there in
    # case someone downgrades a storage server from >= Tahoe-1.3.0 to <
    # Tahoe-1.3.0, or moves a share file from one server to another,
    # etc. We do saturation -- a share data length larger than 2**32-1
    # (what can fit into the field) is marked as the largest length that
    # can fit into the field. That way, even if this does happen, the old
    # < v1.3.0 server will still allow clients to read the first part of
    # the share.
    return struct.pack(">LLL", version, min(2**32 - 1, max_size), 0)


class _V2(object):
    """
    Implement encoding and decoding for v2 of the immutable container.
    """
    version = 2

    @classmethod
    def _hash_secret(cls, secret):
        # type: (bytes) -> bytes
        """
        Hash a lease secret for storage.
        """
        return blake2b(secret, digest_size=32, encoder=RawEncoder())

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

    @classmethod
    def header(cls, max_size):
        # type: (int) -> bytes
        """
        Construct a container header.

        :param max_size: the maximum size the container can hold

        :return: the header bytes
        """
        return _header(cls.version, max_size)

    @classmethod
    def serialize_lease(cls, lease):
        # type: (Union[LeaseInfo, HashedLeaseInfo]) -> bytes
        """
        Serialize a lease to be written to a v2 container.

        :param lease: the lease to serialize

        :return: the serialized bytes
        """
        if isinstance(lease, LeaseInfo):
            # v2 of the immutable schema stores lease secrets hashed.  If
            # we're given a LeaseInfo then it holds plaintext secrets.  Hash
            # them before trying to serialize.
            lease = cls._hash_lease_info(lease)
        if isinstance(lease, HashedLeaseInfo):
            return lease.to_immutable_data()
        raise ValueError(
            "ShareFile v2 schema cannot represent lease {!r}".format(
                lease,
            ),
        )

    @classmethod
    def unserialize_lease(cls, data):
        # type: (bytes) -> HashedLeaseInfo
        """
        Unserialize some bytes from a v2 container.

        :param data: the bytes from the container

        :return: the ``HashedLeaseInfo`` the bytes represent
        """
        # In v2 of the immutable schema lease secrets are stored hashed.  Wrap
        # a LeaseInfo in a HashedLeaseInfo so it can supply the correct
        # interpretation for those values.
        return HashedLeaseInfo(LeaseInfo.from_immutable_data(data), cls._hash_secret)


class _V1(object):
    """
    Implement encoding and decoding for v1 of the immutable container.
    """
    version = 1

    @classmethod
    def header(cls, max_size):
        return _header(cls.version, max_size)

    @classmethod
    def serialize_lease(cls, lease):
        if isinstance(lease, LeaseInfo):
            return lease.to_immutable_data()
        raise ValueError(
            "ShareFile v1 schema only supports LeaseInfo, not {!r}".format(
                lease,
            ),
        )

    @classmethod
    def unserialize_lease(cls, data):
        # In v1 of the immutable schema lease secrets are stored plaintext.
        # So load the data into a plain LeaseInfo which works on plaintext
        # secrets.
        return LeaseInfo.from_immutable_data(data)


ALL_SCHEMAS = {_V2, _V1}
ALL_SCHEMA_VERSIONS = {schema.version for schema in ALL_SCHEMAS}
NEWEST_SCHEMA_VERSION = max(ALL_SCHEMAS, key=lambda schema: schema.version)

def schema_from_version(version):
    # (int) -> Optional[type]
    """
    Find the schema object that corresponds to a certain version number.
    """
    for schema in ALL_SCHEMAS:
        if schema.version == version:
            return schema
    return None
