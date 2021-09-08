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

from future.utils import PY2

if PY2:
    from future.builtins import (
        filter,
        map,
        zip,
        ascii,
        chr,
        hex,
        input,
        next,
        oct,
        open,
        pow,
        round,
        super,
        bytes,
        dict,
        list,
        object,
        range,
        str,
        max,
        min,
    )  # noqa: F401

from random import randrange
from unittest import expectedFailure

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.trial.unittest import TestCase

from foolscap.api import Referenceable

from allmydata.interfaces import IStorageServer, RIBucketWriter
from .test_system import SystemTestMixin


def _randbytes(length):
    # type: (int) -> bytes
    """Return random bytes string of given length."""
    return bytes([randrange(0, 256) for _ in range(length)])


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
        # TODO get_version() returns a dict-like thing with some of the
        # expected fields.
        yield self.storage_server.get_version()


class IStorageServerImmutableAPIsTestsMixin(object):
    """
    Tests for ``IStorageServer``'s immutable APIs.

    ``self.storage_server`` is expected to provide ``IStorageServer``.
    """

    # TODO === allocate_buckets + RIBucketWriter ===
    # DONE allocate_buckets on a new storage index
    # PROG allocate_buckets on existing bucket with same sharenums
    # TODO allocate_buckets with smaller sharenums
    # TODO allocate_buckets with larger sharenums
    # TODO writes to bucket can happen in any order (write then read)
    # TODO overlapping writes ignore already-written data (write then read)

    @inlineCallbacks
    def test_allocate_buckets_new(self):
        """
        allocate_buckets() with a new storage index returns the matching
        shares.
        """
        (already_got, allocated) = yield self.storage_server.allocate_buckets(
            new_storage_index(),
            new_secret(),
            new_secret(),
            set(range(5)),
            1024,
            Referenceable(),
        )
        self.assertEqual(already_got, set())
        self.assertEqual(allocated.keys(), set(range(5)))
        # We validate the bucket objects' interface in a later test.

    @inlineCallbacks
    def test_allocate_buckets_repeat(self):
        """
        allocate_buckets() with the same storage index returns the same result,
        because the shares have not been written to.

        This fails due to https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793
        """
        si, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (already_got, allocated) = yield self.storage_server.allocate_buckets(
            si,
            renew_secret,
            cancel_secret,
            set(range(5)),
            1024,
            Referenceable(),
        )
        (already_got2, allocated2) = yield self.storage_server.allocate_buckets(
            si,
            renew_secret,
            cancel_secret,
            set(range(5)),
            1024,
            Referenceable(),
        )
        self.assertEqual(already_got, already_got2)
        self.assertEqual(allocated.keys(), allocated2.keys())

    test_allocate_buckets_repeat.todo = (
        "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793"
    )

    @expectedFailure
    @inlineCallbacks
    def test_allocate_buckets_more_sharenums(self):
        """
        allocate_buckets() with the same storage index but more sharenums
        acknowledges the extra shares don't exist.

        Fails due to https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793
        """
        si, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        yield self.storage_server.allocate_buckets(
            si,
            renew_secret,
            cancel_secret,
            set(range(5)),
            1024,
            Referenceable(),
        )
        (already_got2, allocated2) = yield self.storage_server.allocate_buckets(
            si,
            renew_secret,
            cancel_secret,
            set(range(7)),
            1024,
            Referenceable(),
        )
        self.assertEqual(already_got2, set())  # none were fully written
        self.assertEqual(allocated2.keys(), set(range(7)))

    test_allocate_buckets_more_sharenums.todo = (
        "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793"
    )


class _FoolscapMixin(SystemTestMixin):
    """Run tests on Foolscap version of ``IStorageServer."""

    @inlineCallbacks
    def setUp(self):
        self.basedir = "test_istorageserver/" + self.id()
        yield SystemTestMixin.setUp(self)
        yield self.set_up_nodes(1)
        self.storage_server = next(
            iter(self.clients[0].storage_broker.get_known_servers())
        ).get_storage_server()
        self.assertTrue(IStorageServer.providedBy(self.storage_server))


class FoolscapSharedAPIsTests(
    _FoolscapMixin, IStorageServerSharedAPIsTestsMixin, TestCase
):
    """Foolscap-specific tests for shared ``IStorageServer`` APIs."""


class FoolscapImmutableAPIsTests(
    _FoolscapMixin, IStorageServerImmutableAPIsTestsMixin, TestCase
):
    """Foolscap-specific tests for immutable ``IStorageServer`` APIs."""
