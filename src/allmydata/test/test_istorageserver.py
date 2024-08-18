"""
Tests for the ``IStorageServer`` interface.

Keep in mind that ``IStorageServer`` is actually the storage _client_ interface.

Note that for performance, in the future we might want the same node to be
reused across tests, so each test should be careful to generate unique storage
indexes.
"""

from __future__ import annotations

from future.utils import bchr

from random import Random
from unittest import SkipTest

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.task import Clock
from foolscap.api import Referenceable, RemoteException

# A better name for this would be IStorageClient...
from allmydata.interfaces import IStorageServer

from .common_system import SystemTestMixin
from .common import AsyncTestCase
from allmydata.storage.server import StorageServer  # not a IStorageServer!!


# Use random generator with known seed, so results are reproducible if tests
# are run in the same order.
_RANDOM = Random(0)


def _randbytes(length):
    # type: (int) -> bytes
    """Return random bytes string of given length."""
    return b"".join([bchr(_RANDOM.randrange(0, 256)) for _ in range(length)])


def new_storage_index():
    # type: () -> bytes
    """Return a new random storage index."""
    return _randbytes(16)


def new_secret():
    # type: () -> bytes
    """Return a new random secret (for lease renewal or cancellation)."""
    return _randbytes(32)


class IStorageServerSharedAPIsTestsMixin(object):
    """
    Tests for ``IStorageServer``'s shared APIs.

    ``self.storage_client`` is expected to provide ``IStorageServer``.
    """

    @inlineCallbacks
    def test_version(self):
        """
        ``IStorageServer`` returns a dictionary where the key is an expected
        protocol version.
        """
        result = yield self.storage_client.get_version()
        self.assertIsInstance(result, dict)
        self.assertIn(b"http://allmydata.org/tahoe/protocols/storage/v1", result)


class IStorageServerImmutableAPIsTestsMixin(object):
    """
    Tests for ``IStorageServer``'s immutable APIs.

    ``self.storage_client`` is expected to provide ``IStorageServer``.

    ``self.disconnect()`` should disconnect and then reconnect, creating a new
    ``self.storage_client``.  Some implementations may wish to skip tests using
    this; HTTP has no notion of disconnection.

    ``self.server`` is expected to be the corresponding
    ``allmydata.storage.server.StorageServer`` instance.  Time should be
    instrumented, such that ``self.fake_time()`` and ``self.fake_sleep()``
    return and advance the server time, respectively.
    """

    @inlineCallbacks
    def test_allocate_buckets_new(self):
        """
        allocate_buckets() with a new storage index returns the matching
        shares.
        """
        (already_got, allocated) = yield self.storage_client.allocate_buckets(
            new_storage_index(),
            renew_secret=new_secret(),
            cancel_secret=new_secret(),
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        self.assertEqual(already_got, set())
        self.assertEqual(set(allocated.keys()), set(range(5)))
        # We validate the bucket objects' interface in a later test.

    @inlineCallbacks
    def test_allocate_buckets_repeat(self):
        """
        ``IStorageServer.allocate_buckets()`` with the same storage index does not return
        work-in-progress buckets, but will add any newly added buckets.
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (already_got, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(4)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        (already_got2, allocated2) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            set(range(5)),
            1024,
            Referenceable(),
        )
        self.assertEqual(already_got, already_got2)
        self.assertEqual(set(allocated2.keys()), {4})

    @inlineCallbacks
    def abort_or_disconnect_half_way(self, abort_or_disconnect):
        """
        If we disconnect/abort in the middle of writing to a bucket, all data
        is wiped, and it's even possible to write different data to the bucket.

        (In the real world one shouldn't do that, but writing different data is
        a good way to test that the original data really was wiped.)

        ``abort_or_disconnect`` is a callback that takes a bucket and aborts up
        load, or perhaps disconnects the whole connection.
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums={0},
            allocated_size=1024,
            canary=Referenceable(),
        )

        # Bucket 1 get some data written (but not all, or HTTP implicitly
        # finishes the upload)
        yield allocated[0].callRemote("write", 0, b"1" * 1023)

        # Disconnect or abort, depending on the test:
        yield abort_or_disconnect(allocated[0])

        # Write different data with no complaint:
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums={0},
            allocated_size=1024,
            canary=Referenceable(),
        )
        yield allocated[0].callRemote("write", 0, b"2" * 1024)

    @inlineCallbacks
    def test_written_shares_are_allocated(self):
        """
        Shares that are fully written to show up as allocated in result from
        ``IStorageServer.allocate_buckets()``.  Partially-written or empty
        shares don't.
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )

        # Bucket 1 is fully written in one go.
        yield allocated[1].callRemote("write", 0, b"1" * 1024)
        yield allocated[1].callRemote("close")

        # Bucket 2 is fully written in two steps.
        yield allocated[2].callRemote("write", 0, b"1" * 512)
        yield allocated[2].callRemote("write", 512, b"2" * 512)
        yield allocated[2].callRemote("close")

        # Bucket 0 has partial write.
        yield allocated[0].callRemote("write", 0, b"1" * 512)

        (already_got, _) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        self.assertEqual(already_got, {1, 2})

    @inlineCallbacks
    def test_written_shares_are_readable(self):
        """
        Shares that are fully written to can be read.

        The result is not affected by the order in which writes
        happened, only by their offsets.
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )

        # Bucket 1 is fully written in order
        yield allocated[1].callRemote("write", 0, b"1" * 512)
        yield allocated[1].callRemote("write", 512, b"2" * 512)
        yield allocated[1].callRemote("close")

        # Bucket 2 is fully written in reverse.
        yield allocated[2].callRemote("write", 512, b"4" * 512)
        yield allocated[2].callRemote("write", 0, b"3" * 512)
        yield allocated[2].callRemote("close")

        buckets = yield self.storage_client.get_buckets(storage_index)
        self.assertEqual(set(buckets.keys()), {1, 2})

        self.assertEqual(
            (yield buckets[1].callRemote("read", 0, 1024)), b"1" * 512 + b"2" * 512
        )
        self.assertEqual(
            (yield buckets[2].callRemote("read", 0, 1024)), b"3" * 512 + b"4" * 512
        )

    @inlineCallbacks
    def test_non_matching_overlapping_writes(self):
        """
        When doing overlapping writes in immutable uploads, non-matching writes
        fail.
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums={0},
            allocated_size=30,
            canary=Referenceable(),
        )

        yield allocated[0].callRemote("write", 0, b"1" * 25)
        # Overlapping write that doesn't match:
        with self.assertRaises(RemoteException):
            yield allocated[0].callRemote("write", 20, b"2" * 10)

    @inlineCallbacks
    def test_matching_overlapping_writes(self):
        """
        When doing overlapping writes in immutable uploads, matching writes
        succeed.
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums={0},
            allocated_size=25,
            canary=Referenceable(),
        )

        yield allocated[0].callRemote("write", 0, b"1" * 10)
        # Overlapping write that matches:
        yield allocated[0].callRemote("write", 5, b"1" * 20)
        yield allocated[0].callRemote("close")

        buckets = yield self.storage_client.get_buckets(storage_index)
        self.assertEqual(set(buckets.keys()), {0})

        self.assertEqual((yield buckets[0].callRemote("read", 0, 25)), b"1" * 25)

    def test_abort(self):
        """
        If we call ``abort`` on the ``RIBucketWriter`` to disconnect in the
        middle of writing to a bucket, all data is wiped, and it's even
        possible to write different data to the bucket.

        (In the real world one probably wouldn't do that, but writing different
        data is a good way to test that the original data really was wiped.)
        """
        return self.abort_or_disconnect_half_way(
            lambda bucket: bucket.callRemote("abort")
        )

    @inlineCallbacks
    def test_get_buckets_skips_unfinished_buckets(self):
        """
        Buckets that are not fully written are not returned by
        ``IStorageServer.get_buckets()`` implementations.
        """
        storage_index = new_storage_index()
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret=new_secret(),
            cancel_secret=new_secret(),
            sharenums=set(range(5)),
            allocated_size=10,
            canary=Referenceable(),
        )

        # Bucket 1 is fully written
        yield allocated[1].callRemote("write", 0, b"1" * 10)
        yield allocated[1].callRemote("close")

        # Bucket 2 is partially written
        yield allocated[2].callRemote("write", 0, b"1" * 5)

        buckets = yield self.storage_client.get_buckets(storage_index)
        self.assertEqual(set(buckets.keys()), {1})

    @inlineCallbacks
    def test_read_bucket_at_offset(self):
        """
        Given a read bucket returned from ``IStorageServer.get_buckets()``, it
        is possible to read at different offsets and lengths, with reads past
        the end resulting in empty bytes.
        """
        length = 256 * 17

        storage_index = new_storage_index()
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret=new_secret(),
            cancel_secret=new_secret(),
            sharenums=set(range(1)),
            allocated_size=length,
            canary=Referenceable(),
        )

        total_data = _randbytes(256 * 17)
        yield allocated[0].callRemote("write", 0, total_data)
        yield allocated[0].callRemote("close")

        buckets = yield self.storage_client.get_buckets(storage_index)
        bucket = buckets[0]
        for start, to_read in [
            (0, 250),  # fraction
            (0, length),  # whole thing
            (100, 1024),  # offset fraction
            (length + 1, 100),  # completely out of bounds
            (length - 100, 200),  # partially out of bounds
        ]:
            data = yield bucket.callRemote("read", start, to_read)
            self.assertEqual(
                data,
                total_data[start : start + to_read],
                "Didn't match for start {}, length {}".format(start, to_read),
            )

    @inlineCallbacks
    def create_share(self):
        """Create a share, return the storage index."""
        storage_index = new_storage_index()
        renew_secret = new_secret()
        cancel_secret = new_secret()
        (_, allocated) = yield self.storage_client.allocate_buckets(
            storage_index,
            renew_secret=renew_secret,
            cancel_secret=cancel_secret,
            sharenums=set(range(1)),
            allocated_size=10,
            canary=Referenceable(),
        )

        yield allocated[0].callRemote("write", 0, b"0123456789")
        yield allocated[0].callRemote("close")
        returnValue((storage_index, renew_secret, cancel_secret))

    @inlineCallbacks
    def test_bucket_advise_corrupt_share(self):
        """
        Calling ``advise_corrupt_share()`` on a bucket returned by
        ``IStorageServer.get_buckets()`` does not result in error (other
        behavior is opaque at this level of abstraction).
        """
        storage_index, _, _ = yield self.create_share()
        buckets = yield self.storage_client.get_buckets(storage_index)
        yield buckets[0].callRemote("advise_corrupt_share", b"OH NO")

    @inlineCallbacks
    def test_advise_corrupt_share(self):
        """
        Calling ``advise_corrupt_share()`` on an immutable share does not
        result in error (other behavior is opaque at this level of
        abstraction).
        """
        storage_index, _, _ = yield self.create_share()
        yield self.storage_client.advise_corrupt_share(
            b"immutable", storage_index, 0, b"ono"
        )

    @inlineCallbacks
    def test_advise_corrupt_share_unknown_share_number(self):
        """
        Calling ``advise_corrupt_share()`` on an immutable share, with an
        unknown share number, does not result in error.
        """
        storage_index, _, _ = yield self.create_share()
        yield self.storage_client.advise_corrupt_share(
            b"immutable", storage_index, 999, b"ono"
        )

    @inlineCallbacks
    def test_allocate_buckets_creates_lease(self):
        """
        When buckets are created using ``allocate_buckets()``, a lease is
        created once writing is done.
        """
        storage_index, _, _ = yield self.create_share()
        [lease] = self.server.get_leases(storage_index)
        # Lease expires in 31 days.
        self.assertTrue(
            lease.get_expiration_time() - self.fake_time() > (31 * 24 * 60 * 60 - 10)
        )

    @inlineCallbacks
    def test_add_lease_non_existent(self):
        """
        If the storage index doesn't exist, adding the lease silently does nothing.
        """
        storage_index = new_storage_index()
        self.assertEqual(list(self.server.get_leases(storage_index)), [])

        renew_secret = new_secret()
        cancel_secret = new_secret()

        # Add a lease:
        yield self.storage_client.add_lease(storage_index, renew_secret, cancel_secret)
        self.assertEqual(list(self.server.get_leases(storage_index)), [])

    @inlineCallbacks
    def test_add_lease_renewal(self):
        """
        If the lease secret is reused, ``add_lease()`` extends the existing
        lease.
        """
        storage_index, renew_secret, cancel_secret = yield self.create_share()
        [lease] = self.server.get_leases(storage_index)
        initial_expiration_time = lease.get_expiration_time()

        # Time passes:
        self.fake_sleep(178)

        # We renew the lease:
        yield self.storage_client.add_lease(storage_index, renew_secret, cancel_secret)
        [lease] = self.server.get_leases(storage_index)
        new_expiration_time = lease.get_expiration_time()
        self.assertEqual(new_expiration_time - initial_expiration_time, 178)

    @inlineCallbacks
    def test_add_new_lease(self):
        """
        If a new lease secret is used, ``add_lease()`` creates a new lease.
        """
        storage_index, _, _ = yield self.create_share()
        [lease] = self.server.get_leases(storage_index)
        initial_expiration_time = lease.get_expiration_time()

        # Time passes:
        self.fake_sleep(167)

        # We create a new lease:
        renew_secret = new_secret()
        cancel_secret = new_secret()
        yield self.storage_client.add_lease(storage_index, renew_secret, cancel_secret)
        [lease1, lease2] = self.server.get_leases(storage_index)
        self.assertEqual(lease1.get_expiration_time(), initial_expiration_time)
        self.assertEqual(lease2.get_expiration_time() - initial_expiration_time, 167)


class IStorageServerMutableAPIsTestsMixin(object):
    """
    Tests for ``IStorageServer``'s mutable APIs.

    ``self.storage_client`` is expected to provide ``IStorageServer``.

    ``self.server`` is expected to be the corresponding
    ``allmydata.storage.server.StorageServer`` instance.

    ``STARAW`` is short for ``slot_testv_and_readv_and_writev``.
    """

    def new_secrets(self):
        """Return a 3-tuple of secrets for STARAW calls."""
        return (new_secret(), new_secret(), new_secret())

    def staraw(self, *args, **kwargs):
        """Like ``slot_testv_and_readv_and_writev``, but less typing."""
        return self.storage_client.slot_testv_and_readv_and_writev(*args, **kwargs)

    @inlineCallbacks
    def test_STARAW_reads_after_write(self):
        """
        When data is written with
        ``IStorageServer.slot_testv_and_readv_and_writev``, it can then be read
        by a separate call using that API.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"abcdefg")], 7),
                1: ([], [(0, b"0123"), (4, b"456")], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        (_, reads) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={},
            # Whole thing, partial, going beyond the edge, completely outside
            # range:
            r_vector=[(0, 7), (2, 3), (6, 8), (100, 10)],
        )
        self.assertEqual(
            reads,
            {0: [b"abcdefg", b"cde", b"g", b""], 1: [b"0123456", b"234", b"6", b""]},
        )

    @inlineCallbacks
    def test_SATRAW_reads_happen_before_writes_in_single_query(self):
        """
        If a ``IStorageServer.slot_testv_and_readv_and_writev`` command
        contains both reads and writes, the read returns results that precede
        the write.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"abcdefg")], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # Read and write in same command; read happens before write:
        (written, reads) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"X" * 7)], 7),
            },
            r_vector=[(0, 7)],
        )
        self.assertEqual(written, True)
        self.assertEqual(reads, {0: [b"abcdefg"]})

        # The write is available in next read:
        (_, reads) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={},
            r_vector=[(0, 7)],
        )
        self.assertEqual(reads, {0: [b"X" * 7]})

    @inlineCallbacks
    def test_SATRAW_writes_happens_only_if_test_matches(self):
        """
        If a ``IStorageServer.slot_testv_and_readv_and_writev`` includes both a
        test and a write, the write succeeds if the test matches, and fails if
        the test does not match.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"1" * 7)], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # Test matches, so write happens:
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: (
                    [(0, 3, b"1" * 3), (3, 4, b"1" * 4)],
                    [(0, b"2" * 7)],
                    7,
                ),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)
        (_, reads) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={},
            r_vector=[(0, 7)],
        )
        self.assertEqual(reads, {0: [b"2" * 7]})

        # Test does not match, so write does not happen:
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([(0, 7, b"1" * 7)], [(0, b"3" * 7)], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, False)
        (_, reads) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={},
            r_vector=[(0, 7)],
        )
        self.assertEqual(reads, {0: [b"2" * 7]})

    @inlineCallbacks
    def test_SATRAW_tests_past_end_of_data(self):
        """
        If a ``IStorageServer.slot_testv_and_readv_and_writev`` includes a test
        vector that reads past the end of the data, the result is limited to
        actual available data.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()

        # Since there is no data on server, the test vector will return empty
        # string, which matches expected result, so write will succeed.
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([(0, 10, b"")], [(0, b"1" * 7)], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # Now the test vector is a 10-read off of a 7-byte value, but expected
        # value is still 7 bytes, so the write will again succeed.
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([(0, 10, b"1" * 7)], [(0, b"2" * 7)], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

    @inlineCallbacks
    def test_SATRAW_reads_past_end_of_data(self):
        """
        If a ``IStorageServer.slot_testv_and_readv_and_writev`` reads past the
        end of the data, the result is limited to actual available data.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()

        # Write some data
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"12345")], 5),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # Reads past end.
        (_, reads) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={},
            r_vector=[(0, 100), (2, 50)],
        )
        self.assertEqual(reads, {0: [b"12345", b"345"]})

    @inlineCallbacks
    def test_STARAW_write_enabler_must_match(self):
        """
        If the write enabler secret passed to
        ``IStorageServer.slot_testv_and_readv_and_writev`` doesn't match
        previous writes, the write fails.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"1" * 7)], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # Write enabler secret does not match, so write does not happen:
        bad_secrets = (new_secret(),) + secrets[1:]
        with self.assertRaises(RemoteException):
            yield self.staraw(
                storage_index,
                bad_secrets,
                tw_vectors={
                    0: ([], [(0, b"2" * 7)], 7),
                },
                r_vector=[],
            )
        (_, reads) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={},
            r_vector=[(0, 7)],
        )
        self.assertEqual(reads, {0: [b"1" * 7]})

    @inlineCallbacks
    def test_STARAW_zero_new_length_deletes(self):
        """
        A zero new length passed to
        ``IStorageServer.slot_testv_and_readv_and_writev`` deletes the share.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"1" * 7)], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # Write with new length of 0:
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"1" * 7)], 0),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # It's gone!
        (_, reads) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={},
            r_vector=[(0, 7)],
        )
        self.assertEqual(reads, {})

    @inlineCallbacks
    def test_slot_readv(self):
        """
        Data written with ``IStorageServer.slot_testv_and_readv_and_writev()``
        can be read using ``IStorageServer.slot_readv()``.  Reads can't go past
        the end of the data.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"abcdefg")], 7),
                1: ([], [(0, b"0123"), (4, b"456")], 7),
                # This will never get read from, just here to show we only read
                # from shares explicitly requested by slot_readv:
                2: ([], [(0, b"XYZW")], 4),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        reads = yield self.storage_client.slot_readv(
            storage_index,
            shares=[0, 1],
            # Whole thing, partial, going beyond the edge, completely outside
            # range:
            readv=[(0, 7), (2, 3), (6, 8), (100, 10)],
        )
        self.assertEqual(
            reads,
            {0: [b"abcdefg", b"cde", b"g", b""], 1: [b"0123456", b"234", b"6", b""]},
        )

    @inlineCallbacks
    def test_slot_readv_no_shares(self):
        """
        With no shares given, ``IStorageServer.slot_readv()`` reads from all shares.
        """
        secrets = self.new_secrets()
        storage_index = new_storage_index()
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"abcdefg")], 7),
                1: ([], [(0, b"0123456")], 7),
                2: ([], [(0, b"9876543")], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        reads = yield self.storage_client.slot_readv(
            storage_index,
            shares=[],
            readv=[(0, 7)],
        )
        self.assertEqual(
            reads,
            {0: [b"abcdefg"], 1: [b"0123456"], 2: [b"9876543"]},
        )

    @inlineCallbacks
    def test_slot_readv_unknown_storage_index(self):
        """
        With unknown storage index, ``IStorageServer.slot_readv()`` returns
        empty dict.
        """
        storage_index = new_storage_index()
        reads = yield self.storage_client.slot_readv(
            storage_index,
            shares=[],
            readv=[(0, 7)],
        )
        self.assertEqual(
            reads,
            {},
        )

    @inlineCallbacks
    def create_slot(self):
        """Create a slot with sharenum 0."""
        secrets = self.new_secrets()
        storage_index = new_storage_index()
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"abcdefg")], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)
        returnValue((secrets, storage_index))

    @inlineCallbacks
    def test_advise_corrupt_share(self):
        """
        Calling ``advise_corrupt_share()`` on a mutable share does not
        result in error (other behavior is opaque at this level of
        abstraction).
        """
        secrets, storage_index = yield self.create_slot()

        yield self.storage_client.advise_corrupt_share(
            b"mutable", storage_index, 0, b"ono"
        )

    @inlineCallbacks
    def test_advise_corrupt_share_unknown_share_number(self):
        """
        Calling ``advise_corrupt_share()`` on a mutable share with an unknown
        share number does not result in error (other behavior is opaque at this
        level of abstraction).
        """
        secrets, storage_index = yield self.create_slot()

        yield self.storage_client.advise_corrupt_share(
            b"mutable", storage_index, 999, b"ono"
        )

    @inlineCallbacks
    def test_STARAW_create_lease(self):
        """
        When STARAW creates a new slot, it also creates a lease.
        """
        _, storage_index = yield self.create_slot()
        [lease] = self.server.get_slot_leases(storage_index)
        # Lease expires in 31 days.
        self.assertTrue(
            lease.get_expiration_time() - self.fake_time() > (31 * 24 * 60 * 60 - 10)
        )

    @inlineCallbacks
    def test_STARAW_renews_lease(self):
        """
        When STARAW is run on an existing slot with same renewal secret, it
        renews the lease.
        """
        secrets, storage_index = yield self.create_slot()
        [lease] = self.server.get_slot_leases(storage_index)
        initial_expire = lease.get_expiration_time()

        # Time passes...
        self.fake_sleep(17)

        # We do another write:
        (written, _) = yield self.staraw(
            storage_index,
            secrets,
            tw_vectors={
                0: ([], [(0, b"1234567")], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # The lease has been renewed:
        [lease] = self.server.get_slot_leases(storage_index)
        self.assertEqual(lease.get_expiration_time() - initial_expire, 17)

    @inlineCallbacks
    def test_STARAW_new_lease(self):
        """
        When STARAW is run with a new renewal secret on an existing slot, it
        adds a new lease.
        """
        secrets, storage_index = yield self.create_slot()
        [lease] = self.server.get_slot_leases(storage_index)
        initial_expire = lease.get_expiration_time()

        # Time passes...
        self.fake_sleep(19)

        # We do another write:
        (written, _) = yield self.staraw(
            storage_index,
            (secrets[0], new_secret(), new_secret()),
            tw_vectors={
                0: ([], [(0, b"1234567")], 7),
            },
            r_vector=[],
        )
        self.assertEqual(written, True)

        # A new lease was added:
        [lease1, lease2] = self.server.get_slot_leases(storage_index)
        self.assertEqual(lease1.get_expiration_time(), initial_expire)
        self.assertEqual(lease2.get_expiration_time() - initial_expire, 19)

    @inlineCallbacks
    def test_add_lease_renewal(self):
        """
        If the lease secret is reused, ``add_lease()`` extends the existing
        lease.
        """
        secrets, storage_index = yield self.create_slot()
        [lease] = self.server.get_slot_leases(storage_index)
        initial_expiration_time = lease.get_expiration_time()

        # Time passes:
        self.fake_sleep(178)

        # We renew the lease:
        yield self.storage_client.add_lease(storage_index, secrets[1], secrets[2])
        [lease] = self.server.get_slot_leases(storage_index)
        new_expiration_time = lease.get_expiration_time()
        self.assertEqual(new_expiration_time - initial_expiration_time, 178)

    @inlineCallbacks
    def test_add_new_lease(self):
        """
        If a new lease secret is used, ``add_lease()`` creates a new lease.
        """
        secrets, storage_index = yield self.create_slot()
        [lease] = self.server.get_slot_leases(storage_index)
        initial_expiration_time = lease.get_expiration_time()

        # Time passes:
        self.fake_sleep(167)

        # We create a new lease:
        renew_secret = new_secret()
        cancel_secret = new_secret()
        yield self.storage_client.add_lease(storage_index, renew_secret, cancel_secret)
        [lease1, lease2] = self.server.get_slot_leases(storage_index)
        self.assertEqual(lease1.get_expiration_time(), initial_expiration_time)
        self.assertEqual(lease2.get_expiration_time() - initial_expiration_time, 167)


class _SharedMixin(SystemTestMixin):
    """Base class for Foolscap and HTTP mixins."""

    SKIP_TESTS : set[str] = set()

    def _get_istorage_server(self):
        native_server = next(iter(self.clients[0].storage_broker.get_known_servers()))
        client = native_server.get_storage_server()
        self.assertTrue(IStorageServer.providedBy(client))
        return client

    @inlineCallbacks
    def setUp(self):
        if self._testMethodName in self.SKIP_TESTS:
            raise SkipTest(
                "Test {} is still not supported".format(self._testMethodName)
            )

        AsyncTestCase.setUp(self)

        self.basedir = "test_istorageserver/" + self.id()
        yield SystemTestMixin.setUp(self)
        yield self.set_up_nodes(1)
        self.server = None
        for s in self.clients[0].services:
            if isinstance(s, StorageServer):
                self.server = s
                break
        assert self.server is not None, "Couldn't find StorageServer"
        self._clock = Clock()
        self._clock.advance(123456)
        self.server._clock = self._clock
        self.storage_client = self._get_istorage_server()

    def fake_time(self):
        """Return the current fake, test-controlled, time."""
        return self._clock.seconds()

    def fake_sleep(self, seconds):
        """Advance the fake time by the given number of seconds."""
        self._clock.advance(seconds)

    @inlineCallbacks
    def tearDown(self):
        AsyncTestCase.tearDown(self)
        yield SystemTestMixin.tearDown(self)


class FoolscapSharedAPIsTests(
    _SharedMixin, IStorageServerSharedAPIsTestsMixin, AsyncTestCase
):
    """Foolscap-specific tests for shared ``IStorageServer`` APIs."""

    FORCE_FOOLSCAP_FOR_STORAGE = True


class HTTPSharedAPIsTests(
    _SharedMixin, IStorageServerSharedAPIsTestsMixin, AsyncTestCase
):
    """HTTP-specific tests for shared ``IStorageServer`` APIs."""

    FORCE_FOOLSCAP_FOR_STORAGE = False


class FoolscapImmutableAPIsTests(
    _SharedMixin, IStorageServerImmutableAPIsTestsMixin, AsyncTestCase
):
    """Foolscap-specific tests for immutable ``IStorageServer`` APIs."""

    FORCE_FOOLSCAP_FOR_STORAGE = True

    def test_disconnection(self):
        """
        If we disconnect in the middle of writing to a bucket, all data is
        wiped, and it's even possible to write different data to the bucket.

        (In the real world one shouldn't do that, but writing different data is
        a good way to test that the original data really was wiped.)

        HTTP protocol doesn't need this test, since disconnection is a
        meaningless concept; this is more about testing the implicit contract
        the Foolscap implementation depends on doesn't change as we refactor
        things.
        """
        return self.abort_or_disconnect_half_way(lambda _: self.disconnect())

    @inlineCallbacks
    def disconnect(self):
        """
        Disconnect and then reconnect with a new ``IStorageServer``.
        """
        current = self.storage_client
        yield self.bounce_client(0)
        self.storage_client = self._get_istorage_server()
        assert self.storage_client is not current


class HTTPImmutableAPIsTests(
    _SharedMixin, IStorageServerImmutableAPIsTestsMixin, AsyncTestCase
):
    """HTTP-specific tests for immutable ``IStorageServer`` APIs."""

    FORCE_FOOLSCAP_FOR_STORAGE = False


class FoolscapMutableAPIsTests(
    _SharedMixin, IStorageServerMutableAPIsTestsMixin, AsyncTestCase
):
    """Foolscap-specific tests for mutable ``IStorageServer`` APIs."""

    FORCE_FOOLSCAP_FOR_STORAGE = True


class HTTPMutableAPIsTests(
    _SharedMixin, IStorageServerMutableAPIsTestsMixin, AsyncTestCase
):
    """HTTP-specific tests for mutable ``IStorageServer`` APIs."""

    FORCE_FOOLSCAP_FOR_STORAGE = False
