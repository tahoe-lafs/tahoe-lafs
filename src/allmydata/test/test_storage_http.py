"""
Tests for HTTP storage client + server.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2

if PY2:
    # fmt: off
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
    # fmt: on

from unittest import SkipTest
from base64 import b64encode

from twisted.trial.unittest import TestCase
from twisted.internet.defer import inlineCallbacks

from treq.testing import StubTreq
from klein import Klein
from hyperlink import DecodedURL

from ..storage.server import StorageServer
from ..storage.http_server import (
    HTTPServer, _extract_secrets, Secrets, ClientSecretsException,
    _authorized_route,
)
from ..storage.http_client import StorageClient, ClientException


class ExtractSecretsTests(TestCase):
    """
    Tests for ``_extract_secrets``.
    """
    def test_extract_secrets(self):
        """
        ``_extract_secrets()`` returns a dictionary with the extracted secrets
        if the input secrets match the required secrets.
        """
        secret1 = b"\xFF\x11ZEBRa"
        secret2 = b"\x34\xF2lalalalalala"
        lease_secret = "lease-renew-secret " + str(b64encode(secret1), "ascii").strip()
        upload_secret = "upload-secret " + str(b64encode(secret2), "ascii").strip()

        # No secrets needed, none given:
        self.assertEqual(_extract_secrets([], set()), {})

        # One secret:
        self.assertEqual(
            _extract_secrets([lease_secret],
                             {Secrets.LEASE_RENEW}),
            {Secrets.LEASE_RENEW: secret1}
        )

        # Two secrets:
        self.assertEqual(
            _extract_secrets([upload_secret, lease_secret],
                             {Secrets.LEASE_RENEW, Secrets.UPLOAD}),
            {Secrets.LEASE_RENEW: secret1, Secrets.UPLOAD: secret2}
        )

    def test_wrong_number_of_secrets(self):
        """
        If the wrong number of secrets are passed to ``_extract_secrets``, a
        ``ClientSecretsException`` is raised.
        """
        secret1 = b"\xFF\x11ZEBRa"
        lease_secret = "lease-renew-secret " + str(b64encode(secret1), "ascii").strip()

        # Missing secret:
        with self.assertRaises(ClientSecretsException):
            _extract_secrets([], {Secrets.LEASE_RENEW})

        # Wrong secret:
        with self.assertRaises(ClientSecretsException):
            _extract_secrets([lease_secret], {Secrets.UPLOAD})

        # Extra secret:
        with self.assertRaises(ClientSecretsException):
            _extract_secrets([lease_secret], {})

    def test_bad_secrets(self):
        """
        Bad inputs to ``_extract_secrets`` result in
        ``ClientSecretsException``.
        """

        # Missing value.
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-renew-secret"], {Secrets.LEASE_RENEW})

        # Garbage prefix
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["FOO eA=="], {})

        # Not base64.
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-renew-secret x"], {Secrets.LEASE_RENEW})


SWISSNUM_FOR_TEST = b"abcd"


class TestApp(object):
    """HTTP API for testing purposes."""

    _app = Klein()
    _swissnum = SWISSNUM_FOR_TEST  # Match what the test client is using

    @_authorized_route(_app, {Secrets.UPLOAD}, "/upload_secret", methods=["GET"])
    def validate_upload_secret(self, request, authorization):
        if authorization == {Secrets.UPLOAD: b"abc"}:
            return "OK"
        else:
            return "BAD: {}".format(authorization)


class RoutingTests(TestCase):
    """
    Tests for the HTTP routing infrastructure.
    """
    def setUp(self):
        self._http_server = TestApp()
        self.client = StorageClient(
        DecodedURL.from_text("http://127.0.0.1"),
        SWISSNUM_FOR_TEST,
        treq=StubTreq(self._http_server._app.resource()),
    )

    @inlineCallbacks
    def test_authorization_enforcement(self):
        """
        The requirement for secrets is enforced; if they are not given, a 400
        response code is returned.
        """
        secret = b"abc"

        # Without secret, get a 400 error.
        response = yield self.client._request(
            "GET", "http://127.0.0.1/upload_secret", {}
        )
        self.assertEqual(response.code, 400)

        # With secret, we're good.
        response = yield self.client._request(
            "GET", "http://127.0.0.1/upload_secret", {Secrets.UPLOAD: b"abc"}
        )
        self.assertEqual(response.code, 200)


def setup_http_test(self):
    """
    Setup HTTP tests; call from ``setUp``.
    """
    if PY2:
        raise SkipTest("Not going to bother supporting Python 2")
    self.storage_server = StorageServer(self.mktemp(), b"\x00" * 20)
    # TODO what should the swissnum _actually_ be?
    self._http_server = HTTPServer(self.storage_server, SWISSNUM_FOR_TEST)
    self.client = StorageClient(
        DecodedURL.from_text("http://127.0.0.1"),
        SWISSNUM_FOR_TEST,
        treq=StubTreq(self._http_server.get_resource()),
    )


class GenericHTTPAPITests(TestCase):
    """
    Tests of HTTP client talking to the HTTP server, for generic HTTP API
    endpoints and concerns.
    """

    def setUp(self):
        setup_http_test(self)

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

        We ignore available disk space and max immutable share size, since that
        might change across calls.
        """
        version = yield self.client.get_version()
        version[b"http://allmydata.org/tahoe/protocols/storage/v1"].pop(
            b"available-space"
        )
        version[b"http://allmydata.org/tahoe/protocols/storage/v1"].pop(
            b"maximum-immutable-share-size"
        )
        expected_version = self.storage_server.get_version()
        expected_version[b"http://allmydata.org/tahoe/protocols/storage/v1"].pop(
            b"available-space"
        )
        expected_version[b"http://allmydata.org/tahoe/protocols/storage/v1"].pop(
            b"maximum-immutable-share-size"
        )
        self.assertEqual(version, expected_version)
