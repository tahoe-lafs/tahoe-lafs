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

from .interfaces import (
    IStorageClientV2, IStorageServer,
    TestVector, ReadVector, TestWriteVectors,
    TestVectorOperator, WriteVector,
)


@attr.s
class _FakeRemoteReference(object):
    """
    Emulate a Foolscap RemoteReference, calling a local object instead.
    """
    local_object = attr.ib(type=object)

    def callRemote(self, action, *args, **kwargs):
        return getattr(self.local_object, action)(*args, **kwargs)

    def callRemoteOnly(self, action, *args, **kwargs):
        raise RuntimeError(
            "callRemoteOnly() swallows errors, so as such should never be used."
        )


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


@attr.s
class _ClientV2BucketReader(object):
    """
    Emulate a ``RIBucketReader``.
    """
    client = attr.ib(type=IStorageClientV2)
    storage_index = attr.ib(type=bytes)
    share_number = attr.ib(type=int)

    def read(self, offset, length):
        self.client.immutable_read_share_chunk(
            self.storage_index, self.share_number, offset, length
        )

    def advise_corrupt_share(self, reason):
        reason = str(reason, "utf-8", errors="backslashreplace")
        return self.client.immutable_notify_share_corrupted(
            self.storage_index, self.share_number, reason
        )


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
                share_num: _FakeRemoteReference(_ClientV2BucketWriter(
                    self._client, storage_index, share_num
                ))
                for share_num in result.allocated
             }
        )

    def add_lease(self, storage_index, renew_secret, cancel_secret):
        return self._client.add_lease(
            storage_index, renew_secret, cancel_secret
        )

    @inlineCallbacks
    def get_buckets(self, storage_index):
        share_numbers = yield self._client.immutable_list_shares(
            storage_index
        )
        returnValue({
            share_num: _FakeRemoteReference(_ClientV2BucketReader(
                self._client, storage_index, share_num
            ))
            for share_num in share_numbers
        })

    @inlineCallbacks
    def slot_readv(self, storage_index, shares, readv):
        reads = {}
        for share_number in shares:
            share_reads = reads[share_number] = []
            for (offset, length) in readv:
                d = self._client.mutable.read_share_chunk(
                    storage_index, share_number, offset, length
                )
                share_reads.append(d)
        result = {
            share_number: [(yield d) for d in share_reads]
            for (share_number, reads) in reads.items()
        }
        returnValue(result)

    def slot_testv_and_readv_and_writev(
            self,
            storage_index,
            secrets,
            tw_vectors,
            r_vector,
    ):
        we_secret, lr_secret, lc_secret = secrets
        client_tw_vectors = {}
        for share_num, (test_vector, data_vector, new_length) in tw_vectors.items():
            assert new_length is not None, "Protocol in theory supports it, actual code seems not to"
            client_test_vectors = [
                TestVector(offset, size, TestVectorOperator[op], specimen)
                for (offset, size, op, specimen) in test_vector
            ]
            client_write_vectors = [
                WriteVector(offset, data) for (offset, data) in data_vector
            ]
            client_tw_vectors[share_num] = TestWriteVectors(
                test_vectors=client_test_vectors,
                write_vectors=client_write_vectors,
                new_length=new_length
            )
        client_read_vectors = [
            ReadVector(offset=offset, size=size)
            for (offset, size) in r_vector
        ]
        client_result = yield self._client.mutable_read_test_write_chunk(
            storage_index, we_secret, lr_secret, lc_secret, client_tw_vectors,
            client_read_vectors,
        )
        returnValue((client_result.success, client_result.reads))

    def advise_corrupt_share(self, share_type, storage_index, shnum, reason):
        reason = str(reason, "utf-8", errors="backslashreplace")
        if share_type == b"mutable":
            advise = self._client.mutable_notify_shared_corrupted
        elif share_type == b"immutable":
            advise = self._client.immutable_notify_shared_corrupted
        else:
            raise ValueError("Bad share_type: {!r}".format(share_type))
        return advise(storage_index, shnum, reason)
