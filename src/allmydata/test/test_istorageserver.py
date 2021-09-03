"""
Tests for the ``IStorageServer`` interface.
"""

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks

from allmydata.interfaces import IStorageServer
from .test_system import SystemTestMixin


class IStorageServerTestsMixin:
    """
    Tests for ``IStorageServer``.

    ``self.storage_server`` is expected to provide ``IStorageServer``.
    """
    @inlineCallbacks
    def test_version(self):
        yield self.storage_server.get_version()


class FoolscapIStorageServerTests(
        SystemTestMixin, IStorageServerTestsMixin, unittest.TestCase
):
    """Run tests on Foolscap version of ``IStorageServer."""

    @inlineCallbacks
    def setUp(self):
        self.basedir = "test_istorageserver/{}/{}".format(
            self.__class__.__name__, self._testMethodName
        )
        yield SystemTestMixin.setUp(self)
        yield self.set_up_nodes(1)
        self.storage_server = next(
            iter(self.clients[0].storage_broker.get_known_servers())
        ).get_storage_server()
        self.assertTrue(IStorageServer.providedBy(self.storage_server))
