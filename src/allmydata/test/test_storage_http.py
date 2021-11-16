"""
Tests for HTTP storage client + server.
"""

from twisted.trial.unittest import TestCase
from twisted.internet.defer import inlineCallbacks

from treq.testing import StubTreq
from hyperlink import DecodedURL

from ..storage.server import StorageServer
from ..storage.http_server import HTTPServer
from ..storage.http_client import StorageClient, ClientException


class HTTPTests(TestCase):
    """
    Tests of HTTP client talking to the HTTP server.
    """

    def setUp(self):
        self.storage_server = StorageServer(self.mktemp(), b"\x00" * 20)
        # TODO what should the swissnum _actually_ be?
        self._http_server = HTTPServer(self.storage_server, b"abcd")
        self.client = StorageClient(
            DecodedURL.from_text("http://127.0.0.1"),
            b"abcd",
            treq=StubTreq(self._http_server.get_resource()),
        )

    @inlineCallbacks
    def test_bad_authentication(self):
        """
        If the wrong swissnum is used, an ``Unauthorized`` response code is
        returned.
        """
        client = StorageClient(
            DecodedURL.from_text("http://127.0.0.1"),
            b"something wrong",
            treq=StubTreq(self._http_server.get_resource()),
        )
        with self.assertRaises(ClientException) as e:
            yield client.get_version()
        self.assertEqual(e.exception.args[0], 401)

    @inlineCallbacks
    def test_version(self):
        """
        The client can return the version.
        """
        version = yield self.client.get_version()
        expected_version = self.storage_server.remote_get_version()
        self.assertEqual(version, expected_version)
