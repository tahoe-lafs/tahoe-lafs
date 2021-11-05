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

import attr

from ..util.hashutil import (
    tagged_hash,
)
from .lease import (
    LeaseInfo,
)
from .lease_schema import (
    v1_mutable,
    v2_mutable,
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


_HEADER_FORMAT = ">32s20s32sQQ"

# This size excludes leases
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)

_EXTRA_LEASE_OFFSET = _HEADER_SIZE + 4 * LeaseInfo().mutable_size()


@attr.s(frozen=True)
class _Schema(object):
    """
    Implement encoding and decoding for the mutable container.

    :ivar int version: the version number of the schema this object supports

    :ivar lease_serializer: an object that is responsible for lease
        serialization and unserialization
    """
    version = attr.ib()
    lease_serializer = attr.ib()
    _magic = attr.ib()

    @classmethod
    def for_version(cls, version, lease_serializer):
        return cls(version, lease_serializer, magic=_magic(version))

    def magic_matches(self, candidate_magic):
        # type: (bytes) -> bool
        """
        Return ``True`` if a candidate string matches the expected magic string
        from a mutable container header, ``False`` otherwise.
        """
        return candidate_magic[:len(self._magic)] == self._magic

    def header(self, nodeid, write_enabler):
        return _header(self._magic, _EXTRA_LEASE_OFFSET, nodeid, write_enabler)

ALL_SCHEMAS = {
    _Schema.for_version(version=2, lease_serializer=v2_mutable),
    _Schema.for_version(version=1, lease_serializer=v1_mutable),
}
ALL_SCHEMA_VERSIONS = {schema.version for schema in ALL_SCHEMAS}
NEWEST_SCHEMA_VERSION = max(ALL_SCHEMAS, key=lambda schema: schema.version)

def schema_from_header(header):
    # (int) -> Optional[type]
    """
    Find the schema object that corresponds to a certain version number.
    """
    for schema in ALL_SCHEMAS:
        if schema.magic_matches(header):
            return schema
    return None
