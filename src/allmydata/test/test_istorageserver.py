"""
Tests for the ``IStorageServer`` interface.

Note that for performance, in the future we might want the same node to be
reused across tests, so each test should be careful to generate unique storage
indexes.
"""

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.trial.unittest import TestCase

from allmydata.interfaces import IStorageServer
from .test_system import SystemTestMixin


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
    # TODO allocate_buckets on a new storage index
    # TODO allocate_buckets on existing bucket with same sharenums
    # TODO allocate_buckets with smaller sharenums
    # TODO allocate_buckets with larger sharenums
    # TODO writes to bucket can happen in any order (write then read)
    # TODO overlapping writes ignore already-written data (write then read)


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
