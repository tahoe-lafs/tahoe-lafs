"""
HTTP-based storage client.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import attr
from zope.interface import implementer
from twisted.internet.defer import inlineCallbacks, returnValue, Deferred, succeed

from .interfaces import IStorageClientV2, IStorageServer


@attr.s
class _ClientV2BucketWriter(object):
    """
    Emulate a ``RIBucketWriter``.
    """
    client = attr.ib(type=IStorageClientV2)
    storage_index = attr.ib(type=bytes)
    share_number = attr.ib(type=int)

    @inlineCallbacks
    def abort(self):
        # type: () -> Deferred[None]
        raise NotImplementedError(
            "Missing from HTTP spec: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3778"
        )

    @inlineCallbacks
    def write(self, offset, data):
        # type: (int, bytes) -> Deferred[None]
        yield self.client.immutable_write_share_chunk(
            self.storage_index, self.share_number, offset, data
        )
        returnValue(None)

    def close(self):
        # A no-op in HTTP protocol, presumes new logic in server to track how
        # much of the share has been written... which might conflict with
        # client logic that expects... zeros in unwritten chunks? Hopefully
        # just a server-side change.
        return succeed(None)


@implementer(IStorageServer)
class _AdaptStorageClientV2(object):
    """
    Wrap a new ``IStorageClientV2`` such that it implements ``IStorageServer``.

    Some day ``IStorageServer`` might go away, but for now this is the proposed
    strategy for support the new HTTP protocol.

    NOTE: For now this is just a sketch, to demonstrate this approach is
    viable.  The other part of this approach is to rename
    ``IFoolscapStorageServer`` to something more generic, and try to paper over
    the differences between Foolscap and HTTP there (e.g. use X.509 cert hash
    instead of tub ID), but ``IStorageServer`` is more fundamental so this is
    where we're starting.
    """
    def __init__(self, storage_client):
        # type: (IStorageClientV2) -> None
        self._client = storage_client

    def get_version(self):
        return self._client.get_version()

    @inlineCallbacks
    def allocate_buckets(
            self, storage_index, renew_secret, cancel_secret, sharenums,
            allocated_size, canary
    ):
        result = yield self._client.immutable_create(
            storage_index, sharenums, allocated_size, renew_secret,
            cancel_secret
        )
        returnValue(
            result.already_got,
            {
                share_num: _ClientV2BucketWriter(
                    self._client, storage_index, share_num
                )
                for share_num in result.allocated
             }
        )
