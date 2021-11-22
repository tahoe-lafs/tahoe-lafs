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

from .lease_schema import (
    v1_immutable,
    v2_immutable,
)

@attr.s(frozen=True)
class _Schema(object):
    """
    Implement encoding and decoding for multiple versions of the immutable
    container schema.

    :ivar int version: the version number of the schema this object supports

    :ivar lease_serializer: an object that is responsible for lease
        serialization and unserialization
    """
    version = attr.ib()
    lease_serializer = attr.ib()

    def header(self, max_size):
        # type: (int) -> bytes
        """
        Construct a container header.

        :param max_size: the maximum size the container can hold

        :return: the header bytes
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
        return struct.pack(">LLL", self.version, min(2**32 - 1, max_size), 0)

ALL_SCHEMAS = {
    _Schema(version=2, lease_serializer=v2_immutable),
    _Schema(version=1, lease_serializer=v1_immutable),
}
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
