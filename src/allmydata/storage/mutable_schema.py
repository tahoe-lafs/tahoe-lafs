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

from .lease import (
    LeaseInfo,
)

class _V1(object):
    """
    Implement encoding and decoding for v1 of the mutable container.
    """
    version = 1

    _MAGIC = (
        # Make it easy for people to recognize
        b"Tahoe mutable container v1\n"
        # But also keep the chance of accidental collision low
        b"\x75\x09\x44\x03\x8e"
    )
    assert len(_MAGIC) == 32

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
        # type: (bytes, bytes) -> bytes
        """
        Construct a container header.

        :param nodeid: A unique identifier for the node holding this
            container.

        :param write_enabler: A secret shared with the client used to
            authorize changes to the contents of this container.
        """
        fixed_header = struct.pack(
            ">32s20s32sQQ",
            cls._MAGIC,
            nodeid,
            write_enabler,
            # data length, initially the container is empty
            0,
            cls._EXTRA_LEASE_OFFSET,
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

    @classmethod
    def serialize_lease(cls, lease_info):
        # type: (LeaseInfo) -> bytes
        """
        Serialize a lease to be written to a v1 container.

        :param lease: the lease to serialize

        :return: the serialized bytes
        """
        return lease_info.to_mutable_data()

    @classmethod
    def unserialize_lease(cls, data):
        # type: (bytes) -> LeaseInfo
        """
        Unserialize some bytes from a v1 container.

        :param data: the bytes from the container

        :return: the ``LeaseInfo`` the bytes represent
        """
        return LeaseInfo.from_mutable_data(data)


ALL_SCHEMAS = {_V1}
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
