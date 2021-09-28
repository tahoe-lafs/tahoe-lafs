"""
Tests for the ``IStorageServer`` interface.

Note that for performance, in the future we might want the same node to be
reused across tests, so each test should be careful to generate unique storage
indexes.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2, bchr

if PY2:
    # fmt: off
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
    # fmt: on

from random import Random

from testtools import skipIf

from twisted.internet.defer import inlineCallbacks

from foolscap.api import Referenceable, RemoteException

from allmydata.interfaces import IStorageServer
from .common_system import SystemTestMixin
from .common import AsyncTestCase


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

    ``self.storage_server`` is expected to provide ``IStorageServer``.
    """

    @inlineCallbacks
    def test_version(self):
        """
        ``IStorageServer`` returns a dictionary where the key is an expected
        protocol version.
        """
        result = yield self.storage_server.get_version()
        self.assertIsInstance(result, dict)
        self.assertIn(b"http://allmydata.org/tahoe/protocols/storage/v1", result)


class IStorageServerImmutableAPIsTestsMixin(object):
    """
    Tests for ``IStorageServer``'s immutable APIs.

    ``self.storage_server`` is expected to provide ``IStorageServer``.
    """

    @inlineCallbacks
    def test_allocate_buckets_new(self):
        """
        allocate_buckets() with a new storage index returns the matching
        shares.
        """
        (already_got, allocated) = yield self.storage_server.allocate_buckets(
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
    @skipIf(True, "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793")
    def test_allocate_buckets_repeat(self):
        """
        allocate_buckets() with the same storage index returns the same result,
        because the shares have not been written to.

        This fails due to https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (already_got, allocated) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        (already_got2, allocated2) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            set(range(5)),
            1024,
            Referenceable(),
        )
        self.assertEqual(already_got, already_got2)
        self.assertEqual(set(allocated.keys()), set(allocated2.keys()))

    @skipIf(True, "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793")
    @inlineCallbacks
    def test_allocate_buckets_more_sharenums(self):
        """
        allocate_buckets() with the same storage index but more sharenums
        acknowledges the extra shares don't exist.

        Fails due to https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        (already_got2, allocated2) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(7)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        self.assertEqual(already_got2, set())  # none were fully written
        self.assertEqual(set(allocated2.keys()), set(range(7)))

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
        (_, allocated) = yield self.storage_server.allocate_buckets(
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

        (already_got, _) = yield self.storage_server.allocate_buckets(
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
        (_, allocated) = yield self.storage_server.allocate_buckets(
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

        buckets = yield self.storage_server.get_buckets(storage_index)
        self.assertEqual(set(buckets.keys()), {1, 2})

        self.assertEqual(
            (yield buckets[1].callRemote("read", 0, 1024)), b"1" * 512 + b"2" * 512
        )
        self.assertEqual(
            (yield buckets[2].callRemote("read", 0, 1024)), b"3" * 512 + b"4" * 512
        )

    @skipIf(True, "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3801")
    def test_overlapping_writes(self):
        """
        The policy for overlapping writes is TBD:
        https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3801
        """

    @inlineCallbacks
    def test_get_buckets_skips_unfinished_buckets(self):
        """
        Buckets that are not fully written are not returned by
        ``IStorageServer.get_buckets()`` implementations.
        """
        storage_index = new_storage_index()
        (_, allocated) = yield self.storage_server.allocate_buckets(
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

        buckets = yield self.storage_server.get_buckets(storage_index)
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
        (_, allocated) = yield self.storage_server.allocate_buckets(
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

        buckets = yield self.storage_server.get_buckets(storage_index)
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
    def test_bucket_advise_corrupt_share(self):
        """
        Calling ``advise_corrupt_share()`` on a bucket returned by
        ``IStorageServer.get_buckets()`` does not result in error (other
        behavior is opaque at this level of abstraction).
        """
        storage_index = new_storage_index()
        (_, allocated) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret=new_secret(),
            cancel_secret=new_secret(),
            sharenums=set(range(1)),
            allocated_size=10,
            canary=Referenceable(),
        )

        yield allocated[0].callRemote("write", 0, b"0123456789")
        yield allocated[0].callRemote("close")

        buckets = yield self.storage_server.get_buckets(storage_index)
        yield buckets[0].callRemote("advise_corrupt_share", b"OH NO")


class IStorageServerMutableAPIsTestsMixin(object):
    """
    Tests for ``IStorageServer``'s mutable APIs.

    ``self.storage_server`` is expected to provide ``IStorageServer``.

    ``STARAW`` is short for ``slot_testv_and_readv_and_writev``.
    """

    def new_secrets(self):
        """Return a 3-tuple of secrets for STARAW calls."""
        return (new_secret(), new_secret(), new_secret())

    def staraw(self, *args, **kwargs):
        """Like ``slot_testv_and_readv_and_writev``, but less typing."""
        return self.storage_server.slot_testv_and_readv_and_writev(*args, **kwargs)

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


class _FoolscapMixin(SystemTestMixin):
    """Run tests on Foolscap version of ``IStorageServer."""

    @inlineCallbacks
    def setUp(self):
        AsyncTestCase.setUp(self)
        self.basedir = "test_istorageserver/" + self.id()
        yield SystemTestMixin.setUp(self)
        yield self.set_up_nodes(1)
        self.storage_server = next(
            iter(self.clients[0].storage_broker.get_known_servers())
        ).get_storage_server()
        self.assertTrue(IStorageServer.providedBy(self.storage_server))

    @inlineCallbacks
    def tearDown(self):
        AsyncTestCase.tearDown(self)
        yield SystemTestMixin.tearDown(self)


class FoolscapSharedAPIsTests(
    _FoolscapMixin, IStorageServerSharedAPIsTestsMixin, AsyncTestCase
):
    """Foolscap-specific tests for shared ``IStorageServer`` APIs."""


class FoolscapImmutableAPIsTests(
    _FoolscapMixin, IStorageServerImmutableAPIsTestsMixin, AsyncTestCase
):
    """Foolscap-specific tests for immutable ``IStorageServer`` APIs."""


class FoolscapMutableAPIsTests(
    _FoolscapMixin, IStorageServerMutableAPIsTestsMixin, AsyncTestCase
):
    """Foolscap-specific tests for immutable ``IStorageServer`` APIs."""
