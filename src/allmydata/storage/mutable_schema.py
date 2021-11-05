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

from ..util.hashutil import (
    tagged_hash,
)
from .lease import (
    LeaseInfo,
    HashedLeaseInfo,
)

def _magic(version):
    # type: (int) -> bytes
    """
    Compute a "magic" header string for a container of the given version.

    :param version: The version number of the container.
    """
    # Make it easy for people to recognize
    human_readable = u"Tahoe mutable container v{:d}\n".format(version).encode("ascii")
    # But also keep the chance of accidental collision low
    if version == 1:
        # It's unclear where this byte sequence came from.  It may have just
        # been random.  In any case, preserve it since it is the magic marker
        # in all v1 share files.
        random_bytes = b"\x75\x09\x44\x03\x8e"
    else:
        # For future versions, use a reproducable scheme.
        random_bytes = tagged_hash(
            b"allmydata_mutable_container_header",
            human_readable,
            truncate_to=5,
        )
    magic = human_readable + random_bytes
    assert len(magic) == 32
    if version > 1:
        # The chance of collision is pretty low but let's just be sure about
        # it.
        assert magic != _magic(version - 1)

    return magic

def _header(magic, extra_lease_offset, nodeid, write_enabler):
    # type: (bytes, int, bytes, bytes) -> bytes
    """
    Construct a container header.

    :param nodeid: A unique identifier for the node holding this
        container.

    :param write_enabler: A secret shared with the client used to
        authorize changes to the contents of this container.
    """
    fixed_header = struct.pack(
        ">32s20s32sQQ",
        magic,
        nodeid,
        write_enabler,
        # data length, initially the container is empty
        0,
        extra_lease_offset,
    )
    blank_leases = b"\x00" * LeaseInfo().mutable_size() * 4
    extra_lease_count = struct.pack(">L", 0)

    return b"".join([
        fixed_header,
        # share data will go in between the next two items eventually but
        # for now there is none.
        blank_leases,
        extra_lease_count,
    ])


class _V2(object):
    """
    Implement encoding and decoding for v2 of the mutable container.
    """
    version = 2
    _MAGIC = _magic(version)

    _HEADER_FORMAT = ">32s20s32sQQ"

    # This size excludes leases
    _HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)

    _EXTRA_LEASE_OFFSET = _HEADER_SIZE + 4 * LeaseInfo().mutable_size()

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
    def magic_matches(cls, candidate_magic):
        # type: (bytes) -> bool
        """
        Return ``True`` if a candidate string matches the expected magic string
        from a mutable container header, ``False`` otherwise.
        """
        return candidate_magic[:len(cls._MAGIC)] == cls._MAGIC

    @classmethod
    def header(cls, nodeid, write_enabler):
        return _header(cls._MAGIC, cls._EXTRA_LEASE_OFFSET, nodeid, write_enabler)

    @classmethod
    def serialize_lease(cls, lease):
        # type: (Union[LeaseInfo, HashedLeaseInfo]) -> bytes
        """
        Serialize a lease to be written to a v2 container.

        :param lease: the lease to serialize

        :return: the serialized bytes
        """
        if isinstance(lease, LeaseInfo):
            # v2 of the mutable schema stores lease secrets hashed.  If we're
            # given a LeaseInfo then it holds plaintext secrets.  Hash them
            # before trying to serialize.
            lease = cls._hash_lease_info(lease)
        if isinstance(lease, HashedLeaseInfo):
            return lease.to_mutable_data()
        raise ValueError(
            "MutableShareFile v2 schema cannot represent lease {!r}".format(
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
        lease = LeaseInfo.from_mutable_data(data)
        return HashedLeaseInfo(lease, cls._hash_secret)


class _V1(object):
    """
    Implement encoding and decoding for v1 of the mutable container.
    """
    version = 1
    _MAGIC = _magic(version)

    _HEADER_FORMAT = ">32s20s32sQQ"

    # This size excludes leases
    _HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)

    _EXTRA_LEASE_OFFSET = _HEADER_SIZE + 4 * LeaseInfo().mutable_size()

    @classmethod
    def magic_matches(cls, candidate_magic):
        # type: (bytes) -> bool
        """
        Return ``True`` if a candidate string matches the expected magic string
        from a mutable container header, ``False`` otherwise.
        """
        return candidate_magic[:len(cls._MAGIC)] == cls._MAGIC

    @classmethod
    def header(cls, nodeid, write_enabler):
        return _header(cls._MAGIC, cls._EXTRA_LEASE_OFFSET, nodeid, write_enabler)


    @classmethod
    def serialize_lease(cls, lease_info):
        # type: (LeaseInfo) -> bytes
        """
        Serialize a lease to be written to a v1 container.

        :param lease: the lease to serialize

        :return: the serialized bytes
        """
        if isinstance(lease, LeaseInfo):
            return lease_info.to_mutable_data()
        raise ValueError(
            "MutableShareFile v1 schema only supports LeaseInfo, not {!r}".format(
                lease,
            ),
        )

    @classmethod
    def unserialize_lease(cls, data):
        # type: (bytes) -> LeaseInfo
        """
        Unserialize some bytes from a v1 container.

        :param data: the bytes from the container

        :return: the ``LeaseInfo`` the bytes represent
        """
        return LeaseInfo.from_mutable_data(data)


ALL_SCHEMAS = {_V2, _V1}
ALL_SCHEMA_VERSIONS = {schema.version for schema in ALL_SCHEMAS} # type: ignore
NEWEST_SCHEMA_VERSION = max(ALL_SCHEMAS, key=lambda schema: schema.version) # type: ignore

def schema_from_header(header):
    # (int) -> Optional[type]
    """
    Find the schema object that corresponds to a certain version number.
    """
    for schema in ALL_SCHEMAS:
        if schema.magic_matches(header):
            return schema
    return None
