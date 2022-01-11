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

from base64 import b64encode
from os import urandom

from twisted.internet.defer import inlineCallbacks

from hypothesis import assume, given, strategies as st
from fixtures import Fixture, TempDir
from treq.testing import StubTreq
from klein import Klein
from hyperlink import DecodedURL

from .common import AsyncTestCase, SyncTestCase
from ..storage.server import StorageServer
from ..storage.http_server import (
    HTTPServer,
    _extract_secrets,
    Secrets,
    ClientSecretsException,
    _authorized_route,
)
from ..storage.http_client import (
    StorageClient,
    ClientException,
    StorageClientImmutables,
    ImmutableCreateResult,
)


def _post_process(params):
    secret_types, secrets = params
    secrets = {t: s for (t, s) in zip(secret_types, secrets)}
    headers = [
        "{} {}".format(
            secret_type.value, str(b64encode(secrets[secret_type]), "ascii").strip()
        )
        for secret_type in secret_types
    ]
    return secrets, headers


# Creates a tuple of ({Secret enum value: secret_bytes}, [http headers with secrets]).
SECRETS_STRATEGY = (
    st.sets(st.sampled_from(Secrets))
    .flatmap(
        lambda secret_types: st.tuples(
            st.just(secret_types),
            st.lists(
                st.binary(min_size=32, max_size=32),
                min_size=len(secret_types),
                max_size=len(secret_types),
            ),
        )
    )
    .map(_post_process)
)


class ExtractSecretsTests(SyncTestCase):
    """
    Tests for ``_extract_secrets``.
    """

    def setUp(self):
        if PY2:
            self.skipTest("Not going to bother supporting Python 2")
        super(ExtractSecretsTests, self).setUp()

    @given(secrets_to_send=SECRETS_STRATEGY)
    def test_extract_secrets(self, secrets_to_send):
        """
        ``_extract_secrets()`` returns a dictionary with the extracted secrets
        if the input secrets match the required secrets.
        """
        secrets, headers = secrets_to_send

        # No secrets needed, none given:
        self.assertEqual(_extract_secrets(headers, secrets.keys()), secrets)

    @given(
        secrets_to_send=SECRETS_STRATEGY,
        secrets_to_require=st.sets(st.sampled_from(Secrets)),
    )
    def test_wrong_number_of_secrets(self, secrets_to_send, secrets_to_require):
        """
        If the wrong number of secrets are passed to ``_extract_secrets``, a
        ``ClientSecretsException`` is raised.
        """
        secrets_to_send, headers = secrets_to_send
        assume(secrets_to_send.keys() != secrets_to_require)

        with self.assertRaises(ClientSecretsException):
            _extract_secrets(headers, secrets_to_require)

    def test_bad_secret_missing_value(self):
        """
        Missing value in ``_extract_secrets`` result in
        ``ClientSecretsException``.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-renew-secret"], {Secrets.LEASE_RENEW})

    def test_bad_secret_unknown_prefix(self):
        """
        Missing value in ``_extract_secrets`` result in
        ``ClientSecretsException``.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["FOO eA=="], {})

    def test_bad_secret_not_base64(self):
        """
        A non-base64 value in ``_extract_secrets`` result in
        ``ClientSecretsException``.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-renew-secret x"], {Secrets.LEASE_RENEW})

    def test_bad_secret_wrong_length_lease_renew(self):
        """
        Lease renewal secrets must be 32-bytes long.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-renew-secret eA=="], {Secrets.LEASE_RENEW})

    def test_bad_secret_wrong_length_lease_cancel(self):
        """
        Lease cancel secrets must be 32-bytes long.
        """
        with self.assertRaises(ClientSecretsException):
            _extract_secrets(["lease-cancel-secret eA=="], {Secrets.LEASE_RENEW})


SWISSNUM_FOR_TEST = b"abcd"


class TestApp(object):
    """HTTP API for testing purposes."""

    _app = Klein()
    _swissnum = SWISSNUM_FOR_TEST  # Match what the test client is using

    @_authorized_route(_app, {Secrets.UPLOAD}, "/upload_secret", methods=["GET"])
    def validate_upload_secret(self, request, authorization):
        if authorization == {Secrets.UPLOAD: b"MAGIC"}:
            return "GOOD SECRET"
        else:
            return "BAD: {}".format(authorization)


class RoutingTests(AsyncTestCase):
    """
    Tests for the HTTP routing infrastructure.
    """

    def setUp(self):
        if PY2:
            self.skipTest("Not going to bother supporting Python 2")
        super(RoutingTests, self).setUp()
        # Could be a fixture, but will only be used in this test class so not
        # going to bother:
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
        # Without secret, get a 400 error.
        response = yield self.client._request(
            "GET", "http://127.0.0.1/upload_secret", {}
        )
        self.assertEqual(response.code, 400)

        # With secret, we're good.
        response = yield self.client._request(
            "GET", "http://127.0.0.1/upload_secret", {Secrets.UPLOAD: b"MAGIC"}
        )
        self.assertEqual(response.code, 200)
        self.assertEqual((yield response.content()), b"GOOD SECRET")


class HttpTestFixture(Fixture):
    """
    Setup HTTP tests' infrastructure, the storage server and corresponding
    client.
    """

    def _setUp(self):
        self.tempdir = self.useFixture(TempDir())
        self.storage_server = StorageServer(self.tempdir.path, b"\x00" * 20)
        # TODO what should the swissnum _actually_ be?
        self.http_server = HTTPServer(self.storage_server, SWISSNUM_FOR_TEST)
        self.client = StorageClient(
            DecodedURL.from_text("http://127.0.0.1"),
            SWISSNUM_FOR_TEST,
            treq=StubTreq(self.http_server.get_resource()),
        )


class GenericHTTPAPITests(AsyncTestCase):
    """
    Tests of HTTP client talking to the HTTP server, for generic HTTP API
    endpoints and concerns.
    """

    def setUp(self):
        if PY2:
            self.skipTest("Not going to bother supporting Python 2")
        super(GenericHTTPAPITests, self).setUp()
        self.http = self.useFixture(HttpTestFixture())

    @inlineCallbacks
    def test_bad_authentication(self):
        """
        If the wrong swissnum is used, an ``Unauthorized`` response code is
        returned.
        """
        client = StorageClient(
            DecodedURL.from_text("http://127.0.0.1"),
            b"something wrong",
            treq=StubTreq(self.http.http_server.get_resource()),
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
        version = yield self.http.client.get_version()
        version[b"http://allmydata.org/tahoe/protocols/storage/v1"].pop(
            b"available-space"
        )
        version[b"http://allmydata.org/tahoe/protocols/storage/v1"].pop(
            b"maximum-immutable-share-size"
        )
        expected_version = self.http.storage_server.get_version()
        expected_version[b"http://allmydata.org/tahoe/protocols/storage/v1"].pop(
            b"available-space"
        )
        expected_version[b"http://allmydata.org/tahoe/protocols/storage/v1"].pop(
            b"maximum-immutable-share-size"
        )
        self.assertEqual(version, expected_version)


class ImmutableHTTPAPITests(AsyncTestCase):
    """
    Tests for immutable upload/download APIs.
    """

    def setUp(self):
        if PY2:
            self.skipTest("Not going to bother supporting Python 2")
        super(ImmutableHTTPAPITests, self).setUp()
        self.http = self.useFixture(HttpTestFixture())

    @inlineCallbacks
    def test_upload_can_be_downloaded(self):
        """
        A single share can be uploaded in (possibly overlapping) chunks, and
        then a random chunk can be downloaded, and it will match the original
        file.

        We don't exercise the full variation of overlapping chunks because
        that's already done in test_storage.py.
        """
        length = 100
        expected_data = b"".join(bytes([i]) for i in range(100))

        im_client = StorageClientImmutables(self.http.client)

        # Create a upload:
        upload_secret = urandom(32)
        lease_secret = urandom(32)
        storage_index = b"".join(bytes([i]) for i in range(16))
        created = yield im_client.create(
            storage_index, [1], 100, upload_secret, lease_secret, lease_secret
        )
        self.assertEqual(
            created, ImmutableCreateResult(already_have=set(), allocated={1})
        )

        # Three writes: 10-19, 30-39, 50-59. This allows for a bunch of holes.
        def write(offset, length):
            return im_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                offset,
                expected_data[offset : offset + length],
            )

        finished = yield write(10, 10)
        self.assertFalse(finished)
        finished = yield write(30, 10)
        self.assertFalse(finished)
        finished = yield write(50, 10)
        self.assertFalse(finished)

        # Then, an overlapping write with matching data (15-35):
        finished = yield write(15, 20)
        self.assertFalse(finished)

        # Now fill in the holes:
        finished = yield write(0, 10)
        self.assertFalse(finished)
        finished = yield write(40, 10)
        self.assertFalse(finished)
        finished = yield write(60, 40)
        self.assertTrue(finished)

        # We can now read:
        for offset, length in [(0, 100), (10, 19), (99, 0), (49, 200)]:
            downloaded = yield im_client.read_share_chunk(
                storage_index, 1, upload_secret, offset, length
            )
            self.assertEqual(downloaded, expected_data[offset : offset + length])

    def test_multiple_shares_uploaded_to_different_place(self):
        """
        If a storage index has multiple shares, uploads to different shares are
        stored separately and can be downloaded separately.
        """

    def test_bucket_allocated_with_new_shares(self):
        """
        If some shares already exist, allocating shares indicates only the new
        ones were created.
        """

    def test_bucket_allocation_new_upload_key(self):
        """
        If a bucket was allocated with one upload key, and a different upload
        key is used to allocate the bucket again, the previous download is
        cancelled.
        """

    def test_upload_with_wrong_upload_key_fails(self):
        """
        Uploading with a key that doesn't match the one used to allocate the
        bucket will fail.
        """

    def test_upload_offset_cannot_be_negative(self):
        """
        A negative upload offset will be rejected.
        """

    def test_mismatching_upload_fails(self):
        """
        If an uploaded chunk conflicts with an already uploaded chunk, a
        CONFLICT error is returned.
        """

    def test_read_of_wrong_storage_index_fails(self):
        """
        Reading from unknown storage index results in 404.
        """

    def test_read_of_wrong_share_number_fails(self):
        """
        Reading from unknown storage index results in 404.
        """

    def test_read_with_negative_offset_fails(self):
        """
        The offset for reads cannot be negative.
        """

    def test_read_with_negative_length_fails(self):
        """
        The length for reads cannot be negative.
        """
