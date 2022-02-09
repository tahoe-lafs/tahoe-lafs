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

from hypothesis import assume, given, strategies as st
from fixtures import Fixture, TempDir
from treq.testing import StubTreq
from klein import Klein
from hyperlink import DecodedURL
from collections_extended import RangeMap
from twisted.internet.task import Clock
from twisted.web import http
from twisted.web.http_headers import Headers
from werkzeug import routing
from werkzeug.exceptions import NotFound as WNotFound

from .common import SyncTestCase
from ..storage.server import StorageServer
from ..storage.http_server import (
    HTTPServer,
    _extract_secrets,
    Secrets,
    ClientSecretsException,
    _authorized_route,
    StorageIndexConverter,
)
from ..storage.http_client import (
    StorageClient,
    ClientException,
    StorageClientImmutables,
    ImmutableCreateResult,
    UploadProgress,
    StorageClientGeneral,
)
from ..storage.common import si_b2a


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


class RouteConverterTests(SyncTestCase):
    """Tests for custom werkzeug path segment converters."""

    adapter = routing.Map(
        [
            routing.Rule(
                "/<storage_index:storage_index>/", endpoint="si", methods=["GET"]
            )
        ],
        converters={"storage_index": StorageIndexConverter},
    ).bind("example.com", "/")

    @given(storage_index=st.binary(min_size=16, max_size=16))
    def test_good_storage_index_is_parsed(self, storage_index):
        """
        A valid storage index is accepted and parsed back out by
        StorageIndexConverter.
        """
        self.assertEqual(
            self.adapter.match(
                "/{}/".format(str(si_b2a(storage_index), "ascii")), method="GET"
            ),
            ("si", {"storage_index": storage_index}),
        )

    def test_long_storage_index_is_not_parsed(self):
        """An overly long storage_index string is not parsed."""
        with self.assertRaises(WNotFound):
            self.adapter.match("/{}/".format("a" * 27), method="GET")

    def test_short_storage_index_is_not_parsed(self):
        """An overly short storage_index string is not parsed."""
        with self.assertRaises(WNotFound):
            self.adapter.match("/{}/".format("a" * 25), method="GET")

    def test_bad_characters_storage_index_is_not_parsed(self):
        """An overly short storage_index string is not parsed."""
        with self.assertRaises(WNotFound):
            self.adapter.match("/{}_/".format("a" * 25), method="GET")


# TODO should be actual swissnum
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


def result_of(d):
    """
    Synchronously extract the result of a Deferred.
    """
    result = []
    error = []
    d.addCallbacks(result.append, error.append)
    if result:
        return result[0]
    if error:
        error[0].raiseException()
    raise RuntimeError(
        "We expected given Deferred to have result already, but it wasn't. "
        + "This is probably a test design issue."
    )


class RoutingTests(SyncTestCase):
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

    def test_authorization_enforcement(self):
        """
        The requirement for secrets is enforced; if they are not given, a 400
        response code is returned.
        """
        # Without secret, get a 400 error.
        response = result_of(
            self.client.request(
                "GET",
                "http://127.0.0.1/upload_secret",
            )
        )
        self.assertEqual(response.code, 400)

        # With secret, we're good.
        response = result_of(
            self.client.request(
                "GET", "http://127.0.0.1/upload_secret", upload_secret=b"MAGIC"
            )
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(result_of(response.content()), b"GOOD SECRET")


class HttpTestFixture(Fixture):
    """
    Setup HTTP tests' infrastructure, the storage server and corresponding
    client.
    """

    def _setUp(self):
        self.clock = Clock()
        self.tempdir = self.useFixture(TempDir())
        self.storage_server = StorageServer(
            self.tempdir.path, b"\x00" * 20, clock=self.clock
        )
        self.http_server = HTTPServer(self.storage_server, SWISSNUM_FOR_TEST)
        self.client = StorageClient(
            DecodedURL.from_text("http://127.0.0.1"),
            SWISSNUM_FOR_TEST,
            treq=StubTreq(self.http_server.get_resource()),
        )


class StorageClientWithHeadersOverride(object):
    """Wrap ``StorageClient`` and override sent headers."""

    def __init__(self, storage_client, add_headers):
        self.storage_client = storage_client
        self.add_headers = add_headers

    def __getattr__(self, attr):
        return getattr(self.storage_client, attr)

    def request(self, *args, headers=None, **kwargs):
        if headers is None:
            headers = Headers()
        for key, value in self.add_headers.items():
            headers.setRawHeaders(key, [value])
        return self.storage_client.request(*args, headers=headers, **kwargs)


class GenericHTTPAPITests(SyncTestCase):
    """
    Tests of HTTP client talking to the HTTP server, for generic HTTP API
    endpoints and concerns.
    """

    def setUp(self):
        if PY2:
            self.skipTest("Not going to bother supporting Python 2")
        super(GenericHTTPAPITests, self).setUp()
        self.http = self.useFixture(HttpTestFixture())

    def test_bad_authentication(self):
        """
        If the wrong swissnum is used, an ``Unauthorized`` response code is
        returned.
        """
        client = StorageClientGeneral(
            StorageClient(
                DecodedURL.from_text("http://127.0.0.1"),
                b"something wrong",
                treq=StubTreq(self.http.http_server.get_resource()),
            )
        )
        with self.assertRaises(ClientException) as e:
            result_of(client.get_version())
        self.assertEqual(e.exception.args[0], 401)

    def test_version(self):
        """
        The client can return the version.

        We ignore available disk space and max immutable share size, since that
        might change across calls.
        """
        client = StorageClientGeneral(self.http.client)
        version = result_of(client.get_version())
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


class ImmutableHTTPAPITests(SyncTestCase):
    """
    Tests for immutable upload/download APIs.
    """

    def setUp(self):
        if PY2:
            self.skipTest("Not going to bother supporting Python 2")
        super(ImmutableHTTPAPITests, self).setUp()
        self.http = self.useFixture(HttpTestFixture())
        self.im_client = StorageClientImmutables(self.http.client)

    def create_upload(self, share_numbers, length):
        """
        Create a write bucket on server, return:

            (upload_secret, lease_secret, storage_index, result)
        """
        upload_secret = urandom(32)
        lease_secret = urandom(32)
        storage_index = urandom(16)
        created = result_of(
            self.im_client.create(
                storage_index,
                share_numbers,
                length,
                upload_secret,
                lease_secret,
                lease_secret,
            )
        )
        return (upload_secret, lease_secret, storage_index, created)

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

        # Create a upload:
        (upload_secret, _, storage_index, created) = self.create_upload({1}, 100)
        self.assertEqual(
            created, ImmutableCreateResult(already_have=set(), allocated={1})
        )

        remaining = RangeMap()
        remaining.set(True, 0, 100)

        # Three writes: 10-19, 30-39, 50-59. This allows for a bunch of holes.
        def write(offset, length):
            remaining.empty(offset, offset + length)
            return self.im_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                offset,
                expected_data[offset : offset + length],
            )

        upload_progress = result_of(write(10, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )
        upload_progress = result_of(write(30, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )
        upload_progress = result_of(write(50, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )

        # Then, an overlapping write with matching data (15-35):
        upload_progress = result_of(write(15, 20))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )

        # Now fill in the holes:
        upload_progress = result_of(write(0, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )
        upload_progress = result_of(write(40, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )
        upload_progress = result_of(write(60, 40))
        self.assertEqual(
            upload_progress, UploadProgress(finished=True, required=RangeMap())
        )

        # We can now read:
        for offset, length in [(0, 100), (10, 19), (99, 1), (49, 200)]:
            downloaded = result_of(
                self.im_client.read_share_chunk(storage_index, 1, offset, length)
            )
            self.assertEqual(downloaded, expected_data[offset : offset + length])

    def test_allocate_buckets_second_time_wrong_upload_key(self):
        """
        If allocate buckets endpoint is called second time with wrong upload
        key on the same shares, the result is an error.
        """
        # Create a upload:
        (upload_secret, lease_secret, storage_index, _) = self.create_upload(
            {1, 2, 3}, 100
        )
        with self.assertRaises(ClientException) as e:
            result_of(
                self.im_client.create(
                    storage_index, {2, 3}, 100, b"x" * 32, lease_secret, lease_secret
                )
            )
        self.assertEqual(e.exception.args[0], http.UNAUTHORIZED)

    def test_allocate_buckets_second_time_different_shares(self):
        """
        If allocate buckets endpoint is called second time with different
        upload key on different shares, that creates the buckets.
        """
        # Create a upload:
        (upload_secret, lease_secret, storage_index, created) = self.create_upload(
            {1, 2, 3}, 100
        )

        # Add same shares:
        created2 = result_of(
            self.im_client.create(
                storage_index, {4, 6}, 100, b"x" * 2, lease_secret, lease_secret
            )
        )
        self.assertEqual(created2.allocated, {4, 6})

    def test_list_shares(self):
        """
        Once a share is finished uploading, it's possible to list it.
        """
        (upload_secret, _, storage_index, created) = self.create_upload({1, 2, 3}, 10)

        # Initially there are no shares:
        self.assertEqual(result_of(self.im_client.list_shares(storage_index)), set())

        # Upload shares 1 and 3:
        for share_number in [1, 3]:
            progress = result_of(
                self.im_client.write_share_chunk(
                    storage_index,
                    share_number,
                    upload_secret,
                    0,
                    b"0123456789",
                )
            )
            self.assertTrue(progress.finished)

        # Now shares 1 and 3 exist:
        self.assertEqual(result_of(self.im_client.list_shares(storage_index)), {1, 3})

    def test_upload_bad_content_range(self):
        """
        Malformed or invalid Content-Range headers to the immutable upload
        endpoint result in a 416 error.
        """
        (upload_secret, _, storage_index, created) = self.create_upload({1}, 10)

        def check_invalid(bad_content_range_value):
            client = StorageClientImmutables(
                StorageClientWithHeadersOverride(
                    self.http.client, {"content-range": bad_content_range_value}
                )
            )
            with self.assertRaises(ClientException) as e:
                result_of(
                    client.write_share_chunk(
                        storage_index,
                        1,
                        upload_secret,
                        0,
                        b"0123456789",
                    )
                )
            self.assertEqual(e.exception.code, http.REQUESTED_RANGE_NOT_SATISFIABLE)

        check_invalid("not a valid content-range header at all")
        check_invalid("bytes -1-9/10")
        check_invalid("bytes 0--9/10")
        check_invalid("teapots 0-9/10")

    def test_list_shares_unknown_storage_index(self):
        """
        Listing unknown storage index's shares results in empty list of shares.
        """
        storage_index = b"".join(bytes([i]) for i in range(16))
        self.assertEqual(result_of(self.im_client.list_shares(storage_index)), set())

    def test_upload_non_existent_storage_index(self):
        """
        Uploading to a non-existent storage index or share number results in
        404.
        """
        (upload_secret, _, storage_index, _) = self.create_upload({1}, 10)

        def unknown_check(storage_index, share_number):
            with self.assertRaises(ClientException) as e:
                result_of(
                    self.im_client.write_share_chunk(
                        storage_index,
                        share_number,
                        upload_secret,
                        0,
                        b"0123456789",
                    )
                )
            self.assertEqual(e.exception.code, http.NOT_FOUND)

        # Wrong share number:
        unknown_check(storage_index, 7)
        # Wrong storage index:
        unknown_check(b"X" * 16, 7)

    def test_multiple_shares_uploaded_to_different_place(self):
        """
        If a storage index has multiple shares, uploads to different shares are
        stored separately and can be downloaded separately.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """

    def test_bucket_allocated_with_new_shares(self):
        """
        If some shares already exist, allocating shares indicates only the new
        ones were created.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """

    def test_bucket_allocation_new_upload_secret(self):
        """
        If a bucket was allocated with one upload secret, and a different upload
        key is used to allocate the bucket again, the second allocation fails.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """

    def test_upload_with_wrong_upload_secret_fails(self):
        """
        Uploading with a key that doesn't match the one used to allocate the
        bucket will fail.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """

    def test_mismatching_upload_fails(self):
        """
        If an uploaded chunk conflicts with an already uploaded chunk, a
        CONFLICT error is returned.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """

    def test_read_of_wrong_storage_index_fails(self):
        """
        Reading from unknown storage index results in 404.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """

    def test_read_of_wrong_share_number_fails(self):
        """
        Reading from unknown storage index results in 404.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """

    def test_read_with_negative_offset_fails(self):
        """
        The offset for reads cannot be negative.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """

    def test_read_with_negative_length_fails(self):
        """
        The length for reads cannot be negative.

        TBD in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        """
