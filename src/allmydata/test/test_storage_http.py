"""
Tests for HTTP storage client + server.

The tests here are synchronous and don't involve running a real reactor.  This
works, but has some caveats when it comes to testing HTTP endpoints:

* Some HTTP endpoints are synchronous, some are not.
* For synchronous endpoints, the result is immediately available on the
  ``Deferred`` coming out of ``StubTreq``.
* For asynchronous endpoints, you need to use ``StubTreq.flush()`` and
  iterate the fake in-memory clock/reactor to advance time .

So for HTTP endpoints, you should use ``HttpTestFixture.result_of_with_flush()``
which handles both, and patches and moves forward the global Twisted
``Cooperator`` since that is used to drive pull producers. This is,
sadly, an internal implementation detail of Twisted being leaked to tests...

For definitely synchronous calls, you can just use ``result_of()``.
"""

import time
from base64 import b64encode
from contextlib import contextmanager
from os import urandom
from typing import Union, Callable, Tuple, Iterable
from queue import Queue
from cbor2 import dumps
from pycddl import ValidationError as CDDLValidationError
from hypothesis import assume, given, strategies as st, settings as hypothesis_settings
from fixtures import Fixture, TempDir, MonkeyPatch
from treq.testing import StubTreq
from klein import Klein
from hyperlink import DecodedURL
from collections_extended import RangeMap
from twisted.internet.task import Clock, Cooperator
from twisted.internet.interfaces import IReactorTime, IReactorFromThreads
from twisted.internet.defer import CancelledError, Deferred, ensureDeferred
from twisted.web import http
from twisted.web.http_headers import Headers
from werkzeug import routing
from werkzeug.exceptions import NotFound as WNotFound
from testtools.matchers import Equals
from zope.interface import implementer

from ..util.deferredutil import async_to_deferred
from ..util.cputhreadpool import disable_thread_pool_for_test
from .common import SyncTestCase
from ..storage.http_common import (
    get_content_type,
    CBOR_MIME_TYPE,
    response_is_not_html,
)
from ..storage.common import si_b2a
from ..storage.lease import LeaseInfo
from ..storage.server import StorageServer
from ..storage.http_server import (
    HTTPServer,
    _extract_secrets,
    Secrets,
    ClientSecretsException,
    _authorized_route,
    StorageIndexConverter,
    _add_error_handling,
    read_encoded,
    _SCHEMAS as SERVER_SCHEMAS,
    BaseApp,
)
from ..storage.http_client import (
    StorageClient,
    StorageClientFactory,
    ClientException,
    StorageClientImmutables,
    ImmutableCreateResult,
    UploadProgress,
    StorageClientGeneral,
    _encode_si,
    StorageClientMutables,
    TestWriteVectors,
    WriteVector,
    ReadVector,
    ReadTestWriteResult,
    TestVector,
    limited_content,
)


class HTTPUtilities(SyncTestCase):
    """Tests for HTTP common utilities."""

    def test_get_content_type(self):
        """``get_content_type()`` extracts the content-type from the header."""

        def assert_header_values_result(values, expected_content_type):
            headers = Headers()
            if values:
                headers.setRawHeaders("Content-Type", values)
            content_type = get_content_type(headers)
            self.assertEqual(content_type, expected_content_type)

        assert_header_values_result(["text/html"], "text/html")
        assert_header_values_result([], None)
        assert_header_values_result(["text/plain", "application/json"], "text/plain")
        assert_header_values_result(["text/html;encoding=utf-8"], "text/html")


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
            _extract_secrets(["FOO eA=="], set())

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
        """A storage_index string with bad characters is not parsed."""
        with self.assertRaises(WNotFound):
            self.adapter.match("/{}_/".format("a" * 25), method="GET")

    def test_invalid_storage_index_is_not_parsed(self):
        """An invalid storage_index string is not parsed."""
        with self.assertRaises(WNotFound):
            self.adapter.match("/nomd2a65ylxjbqzsw7gcfh4ivr/", method="GET")


# TODO should be actual swissnum
SWISSNUM_FOR_TEST = b"abcd"


def gen_bytes(length: int) -> bytes:
    """Generate bytes to the given length."""
    result = (b"0123456789abcdef" * ((length // 16) + 1))[:length]
    assert len(result) == length
    return result


class TestApp(BaseApp):
    """HTTP API for testing purposes."""

    clock: IReactorTime
    _app = Klein()
    _add_error_handling(_app)
    _swissnum = SWISSNUM_FOR_TEST  # Match what the test client is using

    @_authorized_route(_app, set(), "/noop", methods=["GET"])
    def noop(self, request, authorization):
        return "noop"

    @_authorized_route(_app, {Secrets.UPLOAD}, "/upload_secret", methods=["GET"])
    def validate_upload_secret(self, request, authorization):
        if authorization == {Secrets.UPLOAD: b"MAGIC"}:
            return "GOOD SECRET"
        else:
            return "BAD: {}".format(authorization)

    @_authorized_route(_app, set(), "/storage/v1/version", methods=["GET"])
    def bad_version(self, request, authorization):
        """Return version result that violates the expected schema."""
        request.setHeader("content-type", CBOR_MIME_TYPE)
        return dumps({"garbage": 123})

    @_authorized_route(_app, set(), "/bytes/<int:length>", methods=["GET"])
    def generate_bytes(self, request, authorization, length):
        """Return bytes to the given length using ``gen_bytes()``."""
        return gen_bytes(length)

    @_authorized_route(_app, set(), "/slowly_never_finish_result", methods=["GET"])
    def slowly_never_finish_result(self, request, authorization):
        """
        Send data immediately, after 59 seconds, after another 59 seconds, and then
        never again, without finishing the response.
        """
        request.write(b"a")
        self.clock.callLater(59, request.write, b"b")
        self.clock.callLater(59 + 59, request.write, b"c")
        return Deferred()

    @_authorized_route(_app, set(), "/die_unfinished", methods=["GET"])
    def die(self, request, authorization):
        """
        Dies half-way.
        """
        request.transport.loseConnection()
        return Deferred()

    @_authorized_route(_app, set(), "/read_body", methods=["POST"])
    @async_to_deferred
    async def read_body(self, request, authorization):
        """
        Accept an advise_corrupt_share message, return the reason.

        I.e. exercise codepaths used for reading CBOR from the body.
        """
        data = await read_encoded(
            self.clock, request, SERVER_SCHEMAS["advise_corrupt_share"]
        )
        return data["reason"]


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


class CustomHTTPServerTests(SyncTestCase):
    """
    Tests that use a custom HTTP server.
    """

    def setUp(self):
        super(CustomHTTPServerTests, self).setUp()
        disable_thread_pool_for_test(self)
        StorageClientFactory.start_test_mode(
            lambda pool: self.addCleanup(pool.closeCachedConnections)
        )
        self.addCleanup(StorageClientFactory.stop_test_mode)
        # Could be a fixture, but will only be used in this test class so not
        # going to bother:
        self._http_server = TestApp()
        treq = StubTreq(self._http_server._app.resource())
        self.client = StorageClient(
            DecodedURL.from_text("http://127.0.0.1"),
            SWISSNUM_FOR_TEST,
            treq=treq,
            pool=None,
            # We're using a Treq private API to get the reactor, alas, but only
            # in a test, so not going to worry about it too much. This would be
            # fixed if https://github.com/twisted/treq/issues/226 were ever
            # fixed.
            clock=treq._agent._memoryReactor,
            analyze_response=response_is_not_html,
        )
        self._http_server.clock = self.client._clock

    def test_bad_swissnum_from_client(self) -> None:
        """
        If the swissnum is invalid, a BAD REQUEST response code is returned.
        """
        headers = Headers()
        # The value is not UTF-8.
        headers.addRawHeader("Authorization", b"\x00\xFF\x00\xFF")
        response = result_of(
            self.client._treq.request(
                "GET",
                DecodedURL.from_text("http://127.0.0.1/noop"),
                headers=headers,
            )
        )
        self.assertEqual(response.code, 400)

    def test_bad_secret(self) -> None:
        """
        If the secret is invalid (not base64), a BAD REQUEST
        response code is returned.
        """
        bad_secret = b"upload-secret []<>"
        headers = Headers()
        headers.addRawHeader(
            "X-Tahoe-Authorization",
            bad_secret,
        )
        response = result_of(
            self.client.request(
                "GET",
                DecodedURL.from_text("http://127.0.0.1/upload_secret"),
                headers=headers,
            )
        )
        self.assertEqual(response.code, 400)

    def test_authorization_enforcement(self):
        """
        The requirement for secrets is enforced by the ``_authorized_route``
        decorator; if they are not given, a 400 response code is returned.

        Note that this refers to ``X-Tahoe-Authorization``, not the
        ``Authorization`` header used for the swissnum.
        """
        # Without secret, get a 400 error.
        response = result_of(
            self.client.request(
                "GET",
                DecodedURL.from_text("http://127.0.0.1/upload_secret"),
            )
        )
        self.assertEqual(response.code, 400)

        # With secret, we're good.
        response = result_of(
            self.client.request(
                "GET",
                DecodedURL.from_text("http://127.0.0.1/upload_secret"),
                upload_secret=b"MAGIC",
            )
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(result_of(response.content()), b"GOOD SECRET")

    def test_client_side_schema_validation(self):
        """
        The client validates returned CBOR message against a schema.
        """
        client = StorageClientGeneral(self.client)
        with self.assertRaises(CDDLValidationError):
            result_of(client.get_version())

    @given(length=st.integers(min_value=1, max_value=1_000_000))
    # On Python 3.12 we're getting weird deadline issues in CI, so disabling
    # for now.
    @hypothesis_settings(deadline=None)
    def test_limited_content_fits(self, length):
        """
        ``http_client.limited_content()`` returns the body if it is less than
        the max length.
        """
        for at_least_length in (length, length + 1, length + 1000, length + 100_000):
            response = result_of(
                self.client.request(
                    "GET",
                    DecodedURL.from_text(f"http://127.0.0.1/bytes/{length}"),
                )
            )

            self.assertEqual(
                result_of(
                    limited_content(response, self._http_server.clock, at_least_length)
                ).read(),
                gen_bytes(length),
            )

    @given(length=st.integers(min_value=10, max_value=1_000_000))
    def test_limited_content_does_not_fit(self, length):
        """
        If the body is longer than than max length,
        ``http_client.limited_content()`` fails with a ``ValueError``.
        """
        for too_short in (length - 1, 5):
            response = result_of(
                self.client.request(
                    "GET",
                    DecodedURL.from_text(f"http://127.0.0.1/bytes/{length}"),
                )
            )

            with self.assertRaises(ValueError):
                result_of(limited_content(response, self._http_server.clock, too_short))

    def test_limited_content_silence_causes_timeout(self):
        """
        ``http_client.limited_content() times out if it receives no data for 60
        seconds.
        """
        response = result_of(
            self.client.request(
                "GET",
                DecodedURL.from_text("http://127.0.0.1/slowly_never_finish_result"),
            )
        )

        body_deferred = limited_content(response, self._http_server.clock, 4)
        result = []
        error = []
        body_deferred.addCallbacks(result.append, error.append)

        for i in range(59 + 59 + 60):
            self.assertEqual((result, error), ([], []))
            self._http_server.clock.advance(1)
            # Push data between in-memory client and in-memory server:
            self.client._treq._agent.flush()

        # After 59 (second write) + 59 (third write) + 60 seconds (quiescent
        # timeout) the limited_content() response times out.
        self.assertTrue(error)
        with self.assertRaises(CancelledError):
            error[0].raiseException()

    def test_limited_content_cancels_timeout_on_failed_response(self):
        """
        If the response fails somehow, the timeout is still cancelled.
        """
        response = result_of(
            self.client.request(
                "GET",
                DecodedURL.from_text("http://127.0.0.1/die"),
            )
        )

        d = limited_content(response, self._http_server.clock, 4)
        with self.assertRaises(ValueError):
            result_of(d)
        self.assertEqual(len(self._http_server.clock.getDelayedCalls()), 0)

    def test_request_with_no_content_type_same_as_cbor(self):
        """
        If no ``Content-Type`` header is set when sending a body, it is assumed
        to be CBOR.
        """
        response = result_of(
            self.client.request(
                "POST",
                DecodedURL.from_text("http://127.0.0.1/read_body"),
                data=dumps({"reason": "test"}),
            )
        )
        self.assertEqual(
            result_of(limited_content(response, self._http_server.clock, 100)).read(),
            b"test",
        )

    def test_request_with_wrong_content(self):
        """
        If a non-CBOR ``Content-Type`` header is set when sending a body, the
        server complains appropriatly.
        """
        headers = Headers()
        headers.setRawHeaders("content-type", ["some/value"])
        response = result_of(
            self.client.request(
                "POST",
                DecodedURL.from_text("http://127.0.0.1/read_body"),
                data=dumps({"reason": "test"}),
                headers=headers,
            )
        )
        self.assertEqual(response.code, http.UNSUPPORTED_MEDIA_TYPE)


@implementer(IReactorFromThreads)
class Reactor(Clock):
    """
    Fake reactor that supports time APIs and callFromThread.

    Advancing the clock also runs any callbacks scheduled via callFromThread.
    """

    def __init__(self):
        Clock.__init__(self)
        self._queue = Queue()

    def callFromThread(self, callable, *args, **kwargs):
        self._queue.put((callable, args, kwargs))

    def advance(self, *args, **kwargs):
        Clock.advance(self, *args, **kwargs)
        while not self._queue.empty():
            f, args, kwargs = self._queue.get()
            f(*args, **kwargs)


class HttpTestFixture(Fixture):
    """
    Setup HTTP tests' infrastructure, the storage server and corresponding
    client.
    """

    def _setUp(self):
        StorageClientFactory.start_test_mode(
            lambda pool: self.addCleanup(pool.closeCachedConnections)
        )
        self.addCleanup(StorageClientFactory.stop_test_mode)
        self.clock = Reactor()
        self.tempdir = self.useFixture(TempDir())
        # The global Cooperator used by Twisted (a) used by pull producers in
        # twisted.web, (b) is driven by a real reactor. We want to push time
        # forward ourselves since we rely on pull producers in the HTTP storage
        # server.
        self.mock = self.useFixture(
            MonkeyPatch(
                "twisted.internet.task._theCooperator",
                Cooperator(scheduler=lambda c: self.clock.callLater(0.000001, c)),
            )
        )
        self.storage_server = StorageServer(
            self.tempdir.path, b"\x00" * 20, clock=self.clock
        )
        self.http_server = HTTPServer(
            self.clock, self.storage_server, SWISSNUM_FOR_TEST
        )
        self.treq = StubTreq(self.http_server.get_resource())
        self.client = StorageClient(
            DecodedURL.from_text("http://127.0.0.1"),
            SWISSNUM_FOR_TEST,
            treq=self.treq,
            pool=None,
            clock=self.clock,
            analyze_response=response_is_not_html,
        )

    def result_of_with_flush(self, d):
        """
        Like ``result_of``, but supports fake reactor and ``treq`` testing
        infrastructure necessary to support asynchronous HTTP server endpoints.
        """
        d = ensureDeferred(d)
        result = []
        error = []
        d.addCallbacks(result.append, error.append)

        # Check for synchronous HTTP endpoint handler:
        if result:
            return result[0]
        if error:
            error[0].raiseException()

        # OK, no result yet, probably async HTTP endpoint handler, so advance
        # time, flush treq, and try again:
        for i in range(10_000):
            self.clock.advance(0.001)
            self.treq.flush()
            if result:
                break
            # By putting the sleep at the end, tests that are completely
            # synchronous and don't use threads will have already broken out of
            # the loop, and so will finish without any sleeps. This allows them
            # to run as quickly as possible.
            #
            # However, some tests do talk to APIs that use a thread pool on the
            # backend, so we need to allow actual time to pass for those.
            time.sleep(0.001)

        if result:
            return result[0]
        if error:
            error[0].raiseException()

        raise RuntimeError(
            "We expected given Deferred to have result already, but it wasn't. "
            + "This is probably a test design issue."
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


@contextmanager
def assert_fails_with_http_code(test_case: SyncTestCase, code: int):
    """
    Context manager that asserts the code fails with the given HTTP response
    code.
    """
    with test_case.assertRaises(ClientException) as e:
        try:
            yield
        finally:
            pass
    test_case.assertEqual(e.exception.code, code)


class GenericHTTPAPITests(SyncTestCase):
    """
    Tests of HTTP client talking to the HTTP server, for generic HTTP API
    endpoints and concerns.
    """

    def setUp(self):
        super(GenericHTTPAPITests, self).setUp()
        disable_thread_pool_for_test(self)
        self.http = self.useFixture(HttpTestFixture())

    def test_missing_authentication(self) -> None:
        """
        If nothing is given in the ``Authorization`` header at all an
        ``Unauthorized`` response is returned.
        """
        client = StubTreq(self.http.http_server.get_resource())
        response = self.http.result_of_with_flush(
            client.request(
                "GET",
                "http://127.0.0.1/storage/v1/version",
            ),
        )
        self.assertThat(response.code, Equals(http.UNAUTHORIZED))

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
                pool=None,
                clock=self.http.clock,
                analyze_response=response_is_not_html,
            )
        )
        with assert_fails_with_http_code(self, http.UNAUTHORIZED):
            self.http.result_of_with_flush(client.get_version())

    def test_unsupported_mime_type(self):
        """
        The client can request mime types other than CBOR, and if they are
        unsupported a NOT ACCEPTABLE (406) error will be returned.
        """
        client = StorageClientGeneral(
            StorageClientWithHeadersOverride(self.http.client, {"accept": "image/gif"})
        )
        with assert_fails_with_http_code(self, http.NOT_ACCEPTABLE):
            self.http.result_of_with_flush(client.get_version())

    def test_version(self):
        """
        The client can return the version.

        We ignore available disk space and max immutable share size, since that
        might change across calls.
        """
        client = StorageClientGeneral(self.http.client)
        version = self.http.result_of_with_flush(client.get_version())
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

    def test_server_side_schema_validation(self):
        """
        Ensure that schema validation is happening: invalid CBOR should result
        in bad request response code (error 400).

        We don't bother checking every single request, the API on the
        server-side is designed to require a schema, so it validates
        everywhere.  But we check at least one to ensure we get correct
        response code on bad input, so we know validation happened.
        """
        upload_secret = urandom(32)
        lease_secret = urandom(32)
        storage_index = urandom(16)
        url = self.http.client.relative_url(
            "/storage/v1/immutable/" + _encode_si(storage_index)
        )
        message = {"bad-message": "missing expected keys"}

        response = self.http.result_of_with_flush(
            self.http.client.request(
                "POST",
                url,
                lease_renew_secret=lease_secret,
                lease_cancel_secret=lease_secret,
                upload_secret=upload_secret,
                message_to_serialize=message,
            )
        )
        self.assertEqual(response.code, http.BAD_REQUEST)


class ImmutableHTTPAPITests(SyncTestCase):
    """
    Tests for immutable upload/download APIs.
    """

    def setUp(self):
        super(ImmutableHTTPAPITests, self).setUp()
        disable_thread_pool_for_test(self)
        self.http = self.useFixture(HttpTestFixture())
        self.imm_client = StorageClientImmutables(self.http.client)
        self.general_client = StorageClientGeneral(self.http.client)

    def create_upload(self, share_numbers, length):
        """
        Create a write bucket on server, return:

            (upload_secret, lease_secret, storage_index, result)
        """
        upload_secret = urandom(32)
        lease_secret = urandom(32)
        storage_index = urandom(16)
        created = self.http.result_of_with_flush(
            self.imm_client.create(
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
        expected_data = bytes(range(100))

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
            return self.imm_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                offset,
                expected_data[offset : offset + length],
            )

        upload_progress = self.http.result_of_with_flush(write(10, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )
        upload_progress = self.http.result_of_with_flush(write(30, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )
        upload_progress = self.http.result_of_with_flush(write(50, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )

        # Then, an overlapping write with matching data (15-35):
        upload_progress = self.http.result_of_with_flush(write(15, 20))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )

        # Now fill in the holes:
        upload_progress = self.http.result_of_with_flush(write(0, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )
        upload_progress = self.http.result_of_with_flush(write(40, 10))
        self.assertEqual(
            upload_progress, UploadProgress(finished=False, required=remaining)
        )
        upload_progress = self.http.result_of_with_flush(write(60, 40))
        self.assertEqual(
            upload_progress, UploadProgress(finished=True, required=RangeMap())
        )

        # We can now read:
        for offset, length in [(0, 100), (10, 19), (99, 1), (49, 200)]:
            downloaded = self.http.result_of_with_flush(
                self.imm_client.read_share_chunk(storage_index, 1, offset, length)
            )
            self.assertEqual(downloaded, expected_data[offset : offset + length])

    def test_write_with_wrong_upload_key(self):
        """
        A write with an upload key that is different than the original upload
        key will fail.
        """
        (upload_secret, _, storage_index, _) = self.create_upload({1}, 100)
        with assert_fails_with_http_code(self, http.UNAUTHORIZED):
            self.http.result_of_with_flush(
                self.imm_client.write_share_chunk(
                    storage_index,
                    1,
                    upload_secret + b"X",
                    0,
                    b"123",
                )
            )

    def test_allocate_buckets_second_time_different_shares(self):
        """
        If allocate buckets endpoint is called second time with different
        upload key on potentially different shares, that creates the buckets on
        those shares that are different.
        """
        # Create a upload:
        (upload_secret, lease_secret, storage_index, created) = self.create_upload(
            {1, 2, 3}, 100
        )

        # Write half of share 1
        self.http.result_of_with_flush(
            self.imm_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                0,
                b"a" * 50,
            )
        )

        # Add same shares with a different upload key share 1 overlaps with
        # existing shares, this call shouldn't overwrite the existing
        # work-in-progress.
        upload_secret2 = b"x" * 2
        created2 = self.http.result_of_with_flush(
            self.imm_client.create(
                storage_index,
                {1, 4, 6},
                100,
                upload_secret2,
                lease_secret,
                lease_secret,
            )
        )
        self.assertEqual(created2.allocated, {4, 6})

        # Write second half of share 1
        self.assertTrue(
            self.http.result_of_with_flush(
                self.imm_client.write_share_chunk(
                    storage_index,
                    1,
                    upload_secret,
                    50,
                    b"b" * 50,
                )
            ).finished
        )

        # The upload of share 1 succeeded, demonstrating that second create()
        # call didn't overwrite work-in-progress.
        downloaded = self.http.result_of_with_flush(
            self.imm_client.read_share_chunk(storage_index, 1, 0, 100)
        )
        self.assertEqual(downloaded, b"a" * 50 + b"b" * 50)

        # We can successfully upload the shares created with the second upload secret.
        self.assertTrue(
            self.http.result_of_with_flush(
                self.imm_client.write_share_chunk(
                    storage_index,
                    4,
                    upload_secret2,
                    0,
                    b"x" * 100,
                )
            ).finished
        )

    def test_list_shares(self):
        """
        Once a share is finished uploading, it's possible to list it.
        """
        (upload_secret, _, storage_index, created) = self.create_upload({1, 2, 3}, 10)

        # Initially there are no shares:
        self.assertEqual(
            self.http.result_of_with_flush(self.imm_client.list_shares(storage_index)),
            set(),
        )

        # Upload shares 1 and 3:
        for share_number in [1, 3]:
            progress = self.http.result_of_with_flush(
                self.imm_client.write_share_chunk(
                    storage_index,
                    share_number,
                    upload_secret,
                    0,
                    b"0123456789",
                )
            )
            self.assertTrue(progress.finished)

        # Now shares 1 and 3 exist:
        self.assertEqual(
            self.http.result_of_with_flush(self.imm_client.list_shares(storage_index)),
            {1, 3},
        )

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
            with assert_fails_with_http_code(
                self, http.REQUESTED_RANGE_NOT_SATISFIABLE
            ):
                self.http.result_of_with_flush(
                    client.write_share_chunk(
                        storage_index,
                        1,
                        upload_secret,
                        0,
                        b"0123456789",
                    )
                )

        check_invalid("not a valid content-range header at all")
        check_invalid("bytes -1-9/10")
        check_invalid("bytes 0--9/10")
        check_invalid("teapots 0-9/10")

    def test_list_shares_unknown_storage_index(self):
        """
        Listing unknown storage index's shares results in empty list of shares.
        """
        storage_index = bytes(range(16))
        self.assertEqual(
            self.http.result_of_with_flush(self.imm_client.list_shares(storage_index)),
            set(),
        )

    def test_upload_non_existent_storage_index(self):
        """
        Uploading to a non-existent storage index or share number results in
        404.
        """
        (upload_secret, _, storage_index, _) = self.create_upload({1}, 10)

        def unknown_check(storage_index, share_number):
            with assert_fails_with_http_code(self, http.NOT_FOUND):
                self.http.result_of_with_flush(
                    self.imm_client.write_share_chunk(
                        storage_index,
                        share_number,
                        upload_secret,
                        0,
                        b"0123456789",
                    )
                )

        # Wrong share number:
        unknown_check(storage_index, 7)
        # Wrong storage index:
        unknown_check(b"X" * 16, 7)

    def test_multiple_shares_uploaded_to_different_place(self):
        """
        If a storage index has multiple shares, uploads to different shares are
        stored separately and can be downloaded separately.
        """
        (upload_secret, _, storage_index, _) = self.create_upload({1, 2}, 10)
        self.http.result_of_with_flush(
            self.imm_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                0,
                b"1" * 10,
            )
        )
        self.http.result_of_with_flush(
            self.imm_client.write_share_chunk(
                storage_index,
                2,
                upload_secret,
                0,
                b"2" * 10,
            )
        )
        self.assertEqual(
            self.http.result_of_with_flush(
                self.imm_client.read_share_chunk(storage_index, 1, 0, 10)
            ),
            b"1" * 10,
        )
        self.assertEqual(
            self.http.result_of_with_flush(
                self.imm_client.read_share_chunk(storage_index, 2, 0, 10)
            ),
            b"2" * 10,
        )

    def test_mismatching_upload_fails(self):
        """
        If an uploaded chunk conflicts with an already uploaded chunk, a
        CONFLICT error is returned.
        """
        (upload_secret, _, storage_index, created) = self.create_upload({1}, 100)

        # Write:
        self.http.result_of_with_flush(
            self.imm_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                0,
                b"0" * 10,
            )
        )

        # Conflicting write:
        with assert_fails_with_http_code(self, http.CONFLICT):
            self.http.result_of_with_flush(
                self.imm_client.write_share_chunk(
                    storage_index,
                    1,
                    upload_secret,
                    0,
                    b"0123456789",
                )
            )

    def test_timed_out_upload_allows_reupload(self):
        """
        If an in-progress upload times out, it is cancelled altogether,
        allowing a new upload to occur.
        """
        self._test_abort_or_timed_out_upload_to_existing_storage_index(
            lambda **kwargs: self.http.clock.advance(30 * 60 + 1)
        )

    def test_abort_upload_allows_reupload(self):
        """
        If an in-progress upload is aborted, it is cancelled altogether,
        allowing a new upload to occur.
        """

        def abort(storage_index, share_number, upload_secret):
            return self.http.result_of_with_flush(
                self.imm_client.abort_upload(storage_index, share_number, upload_secret)
            )

        self._test_abort_or_timed_out_upload_to_existing_storage_index(abort)

    def _test_abort_or_timed_out_upload_to_existing_storage_index(self, cancel_upload):
        """Start uploading to an existing storage index that then times out or aborts.

        Re-uploading should work.
        """
        # Start an upload:
        (upload_secret, _, storage_index, _) = self.create_upload({1}, 100)
        self.http.result_of_with_flush(
            self.imm_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                0,
                b"123",
            )
        )

        # Now, the upload is cancelled somehow:
        cancel_upload(
            storage_index=storage_index, upload_secret=upload_secret, share_number=1
        )

        # Now we can create a new share with the same storage index without
        # complaint:
        upload_secret = urandom(32)
        lease_secret = urandom(32)
        created = self.http.result_of_with_flush(
            self.imm_client.create(
                storage_index,
                {1},
                100,
                upload_secret,
                lease_secret,
                lease_secret,
            )
        )
        self.assertEqual(created.allocated, {1})

        # And write to it, too:
        self.http.result_of_with_flush(
            self.imm_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                0,
                b"ABC",
            )
        )

    def test_unknown_aborts(self):
        """
        Aborting uploads with an unknown storage index or share number will
        result 404 HTTP response code.
        """
        (upload_secret, _, storage_index, _) = self.create_upload({1}, 100)

        for si, num in [(storage_index, 3), (b"x" * 16, 1)]:
            with assert_fails_with_http_code(self, http.NOT_FOUND):
                self.http.result_of_with_flush(
                    self.imm_client.abort_upload(si, num, upload_secret)
                )

    def test_unauthorized_abort(self):
        """
        An abort with the wrong key will return an unauthorized error, and will
        not abort the upload.
        """
        (upload_secret, _, storage_index, _) = self.create_upload({1}, 100)

        # Failed to abort becaues wrong upload secret:
        with assert_fails_with_http_code(self, http.UNAUTHORIZED):
            self.http.result_of_with_flush(
                self.imm_client.abort_upload(storage_index, 1, upload_secret + b"X")
            )

        # We can still write to it:
        self.http.result_of_with_flush(
            self.imm_client.write_share_chunk(
                storage_index,
                1,
                upload_secret,
                0,
                b"ABC",
            )
        )

    def test_too_late_abort(self):
        """
        An abort of an already-fully-uploaded immutable will result in 405
        error and will not affect the immutable.
        """
        uploaded_data = b"123"
        (upload_secret, _, storage_index, _) = self.create_upload({0}, 3)
        self.http.result_of_with_flush(
            self.imm_client.write_share_chunk(
                storage_index,
                0,
                upload_secret,
                0,
                uploaded_data,
            )
        )

        # Can't abort, we finished upload:
        with assert_fails_with_http_code(self, http.NOT_ALLOWED):
            self.http.result_of_with_flush(
                self.imm_client.abort_upload(storage_index, 0, upload_secret)
            )

        # Abort didn't prevent reading:
        self.assertEqual(
            uploaded_data,
            self.http.result_of_with_flush(
                self.imm_client.read_share_chunk(
                    storage_index,
                    0,
                    0,
                    3,
                )
            ),
        )

    def test_lease_on_unknown_storage_index(self):
        """
        An attempt to renew an unknown storage index will result in a HTTP 404.
        """
        storage_index = urandom(16)
        secret = b"A" * 32
        with assert_fails_with_http_code(self, http.NOT_FOUND):
            self.http.result_of_with_flush(
                self.general_client.add_or_renew_lease(storage_index, secret, secret)
            )


class MutableHTTPAPIsTests(SyncTestCase):
    """Tests for mutable APIs."""

    def setUp(self):
        super(MutableHTTPAPIsTests, self).setUp()
        disable_thread_pool_for_test(self)
        self.http = self.useFixture(HttpTestFixture())
        self.mut_client = StorageClientMutables(self.http.client)

    def create_upload(self, data=b"abcdef"):
        """
        Utility that creates shares 0 and 1 with bodies
        ``{data}-{share_number}``.
        """
        write_secret = urandom(32)
        lease_secret = urandom(32)
        storage_index = urandom(16)
        self.http.result_of_with_flush(
            self.mut_client.read_test_write_chunks(
                storage_index,
                write_secret,
                lease_secret,
                lease_secret,
                {
                    0: TestWriteVectors(
                        write_vectors=[WriteVector(offset=0, data=data + b"-0")]
                    ),
                    1: TestWriteVectors(
                        write_vectors=[
                            WriteVector(offset=0, data=data),
                            WriteVector(offset=len(data), data=b"-1"),
                        ]
                    ),
                },
                [],
            )
        )
        return storage_index, write_secret, lease_secret

    def test_write_can_be_read_small_data(self):
        """
        Small written data can be read using ``read_share_chunk``.
        """
        self.write_can_be_read(b"abcdef")

    def test_write_can_be_read_large_data(self):
        """
        Large written data (50MB) can be read using ``read_share_chunk``.
        """
        self.write_can_be_read(b"abcdefghij" * 5 * 1024 * 1024)

    def write_can_be_read(self, data):
        """
        Written data can be read using ``read_share_chunk``.
        """
        lease_secret = urandom(32)
        storage_index = urandom(16)
        self.http.result_of_with_flush(
            self.mut_client.read_test_write_chunks(
                storage_index,
                urandom(32),
                lease_secret,
                lease_secret,
                {
                    0: TestWriteVectors(
                        write_vectors=[WriteVector(offset=0, data=data)]
                    ),
                },
                [],
            )
        )
        read_data = self.http.result_of_with_flush(
            self.mut_client.read_share_chunk(storage_index, 0, 0, len(data))
        )
        self.assertEqual(read_data, data)

    def test_read_before_write(self):
        """In combo read/test/write operation, reads happen before writes."""
        storage_index, write_secret, lease_secret = self.create_upload()
        result = self.http.result_of_with_flush(
            self.mut_client.read_test_write_chunks(
                storage_index,
                write_secret,
                lease_secret,
                lease_secret,
                {
                    0: TestWriteVectors(
                        write_vectors=[WriteVector(offset=1, data=b"XYZ")]
                    ),
                },
                [ReadVector(0, 8)],
            )
        )
        # Reads are from before the write:
        self.assertEqual(
            result,
            ReadTestWriteResult(
                success=True, reads={0: [b"abcdef-0"], 1: [b"abcdef-1"]}
            ),
        )
        # But the write did happen:
        data0 = self.http.result_of_with_flush(
            self.mut_client.read_share_chunk(storage_index, 0, 0, 8)
        )
        data1 = self.http.result_of_with_flush(
            self.mut_client.read_share_chunk(storage_index, 1, 0, 8)
        )
        self.assertEqual((data0, data1), (b"aXYZef-0", b"abcdef-1"))

    def test_conditional_write(self):
        """Uploads only happen if the test passes."""
        storage_index, write_secret, lease_secret = self.create_upload()
        result_failed = self.http.result_of_with_flush(
            self.mut_client.read_test_write_chunks(
                storage_index,
                write_secret,
                lease_secret,
                lease_secret,
                {
                    0: TestWriteVectors(
                        test_vectors=[TestVector(1, 4, b"FAIL")],
                        write_vectors=[WriteVector(offset=1, data=b"XYZ")],
                    ),
                },
                [],
            )
        )
        self.assertFalse(result_failed.success)

        # This time the test matches:
        result = self.http.result_of_with_flush(
            self.mut_client.read_test_write_chunks(
                storage_index,
                write_secret,
                lease_secret,
                lease_secret,
                {
                    0: TestWriteVectors(
                        test_vectors=[TestVector(1, 4, b"bcde")],
                        write_vectors=[WriteVector(offset=1, data=b"XYZ")],
                    ),
                },
                [ReadVector(0, 8)],
            )
        )
        self.assertTrue(result.success)
        self.assertEqual(
            self.http.result_of_with_flush(
                self.mut_client.read_share_chunk(storage_index, 0, 0, 8)
            ),
            b"aXYZef-0",
        )

    def test_list_shares(self):
        """``list_shares()`` returns the shares for a given storage index."""
        storage_index, _, _ = self.create_upload()
        self.assertEqual(
            self.http.result_of_with_flush(self.mut_client.list_shares(storage_index)),
            {0, 1},
        )

    def test_non_existent_list_shares(self):
        """A non-existent storage index errors when shares are listed."""
        with self.assertRaises(ClientException) as exc:
            self.http.result_of_with_flush(self.mut_client.list_shares(urandom(32)))
        self.assertEqual(exc.exception.code, http.NOT_FOUND)

    def test_wrong_write_enabler(self):
        """Writes with the wrong write enabler fail, and are not processed."""
        storage_index, write_secret, lease_secret = self.create_upload()
        with self.assertRaises(ClientException) as exc:
            self.http.result_of_with_flush(
                self.mut_client.read_test_write_chunks(
                    storage_index,
                    urandom(32),
                    lease_secret,
                    lease_secret,
                    {
                        0: TestWriteVectors(
                            write_vectors=[WriteVector(offset=1, data=b"XYZ")]
                        ),
                    },
                    [ReadVector(0, 8)],
                )
            )
        self.assertEqual(exc.exception.code, http.UNAUTHORIZED)

        # The write did not happen:
        self.assertEqual(
            self.http.result_of_with_flush(
                self.mut_client.read_share_chunk(storage_index, 0, 0, 8)
            ),
            b"abcdef-0",
        )


class SharedImmutableMutableTestsMixin:
    """
    Shared tests for mutables and immutables where the API is the same.
    """

    KIND: str  # either "mutable" or "immutable"
    general_client: StorageClientGeneral
    client: Union[StorageClientImmutables, StorageClientMutables]
    clientFactory: Callable[
        [StorageClient], Union[StorageClientImmutables, StorageClientMutables]
    ]

    def upload(self, share_number: int, data_length=26) -> Tuple[bytes, bytes, bytes]:
        """
        Create a share, return (storage_index, uploaded_data, lease secret).
        """
        raise NotImplementedError

    def get_leases(self, storage_index: bytes) -> Iterable[LeaseInfo]:
        """Get leases for the storage index."""
        raise NotImplementedError()

    def test_advise_corrupt_share(self):
        """
        Advising share was corrupted succeeds from HTTP client's perspective,
        and calls appropriate method on server.
        """
        corrupted = []
        self.http.storage_server.advise_corrupt_share = lambda *args: corrupted.append(
            args
        )

        storage_index, _, _ = self.upload(13)
        reason = "OHNO \u1235"
        self.http.result_of_with_flush(
            self.client.advise_corrupt_share(storage_index, 13, reason)
        )

        self.assertEqual(
            corrupted,
            [(self.KIND.encode("ascii"), storage_index, 13, reason.encode("utf-8"))],
        )

    def test_advise_corrupt_share_unknown(self):
        """
        Advising an unknown share was corrupted results in 404.
        """
        storage_index, _, _ = self.upload(13)
        reason = "OHNO \u1235"
        self.http.result_of_with_flush(
            self.client.advise_corrupt_share(storage_index, 13, reason)
        )

        for si, share_number in [(storage_index, 11), (urandom(16), 13)]:
            with assert_fails_with_http_code(self, http.NOT_FOUND):
                self.http.result_of_with_flush(
                    self.client.advise_corrupt_share(si, share_number, reason)
                )

    def test_lease_renew_and_add(self):
        """
        It's possible the renew the lease on an uploaded mutable/immutable, by
        using the same renewal secret, or add a new lease by choosing a
        different renewal secret.
        """
        # Create a storage index:
        storage_index, _, lease_secret = self.upload(0)

        [lease] = self.get_leases(storage_index)
        initial_expiration_time = lease.get_expiration_time()

        # Time passes:
        self.http.clock.advance(167)

        # We renew the lease:
        self.http.result_of_with_flush(
            self.general_client.add_or_renew_lease(
                storage_index, lease_secret, lease_secret
            )
        )

        # More time passes:
        self.http.clock.advance(10)

        # We create a new lease:
        lease_secret2 = urandom(32)
        self.http.result_of_with_flush(
            self.general_client.add_or_renew_lease(
                storage_index, lease_secret2, lease_secret2
            )
        )

        [lease1, lease2] = self.get_leases(storage_index)
        self.assertEqual(lease1.get_expiration_time(), initial_expiration_time + 167)
        self.assertEqual(lease2.get_expiration_time(), initial_expiration_time + 177)

    def test_read_of_wrong_storage_index_fails(self):
        """
        Reading from unknown storage index results in 404.
        """
        with assert_fails_with_http_code(self, http.NOT_FOUND):
            self.http.result_of_with_flush(
                self.client.read_share_chunk(
                    b"1" * 16,
                    1,
                    0,
                    10,
                )
            )

    def test_read_of_wrong_share_number_fails(self):
        """
        Reading from unknown storage index results in 404.
        """
        storage_index, _, _ = self.upload(1)
        with assert_fails_with_http_code(self, http.NOT_FOUND):
            self.http.result_of_with_flush(
                self.client.read_share_chunk(
                    storage_index,
                    7,  # different share number
                    0,
                    10,
                )
            )

    def test_read_with_negative_offset_fails(self):
        """
        Malformed or unsupported Range headers result in 416 (requested range
        not satisfiable) error.
        """
        storage_index, _, _ = self.upload(1)

        def check_bad_range(bad_range_value):
            client = self.clientFactory(
                StorageClientWithHeadersOverride(
                    self.http.client, {"range": bad_range_value}
                )
            )

            with assert_fails_with_http_code(
                self, http.REQUESTED_RANGE_NOT_SATISFIABLE
            ):
                self.http.result_of_with_flush(
                    client.read_share_chunk(
                        storage_index,
                        1,
                        0,
                        10,
                    )
                )

        # Bad unit
        check_bad_range("molluscs=0-9")
        # Negative offsets
        check_bad_range("bytes=-2-9")
        check_bad_range("bytes=0--10")
        # Negative offset no endpoint
        check_bad_range("bytes=-300-")
        check_bad_range("bytes=")
        # Multiple ranges are currently unsupported, even if they're
        # semantically valid under HTTP:
        check_bad_range("bytes=0-5, 6-7")
        # Ranges without an end are currently unsupported, even if they're
        # semantically valid under HTTP.
        check_bad_range("bytes=0-")

    def _read_with_no_range_test(self, data_length):
        """
        A read with no range returns the whole mutable/immutable.

        Actual test is defined in subclasses, to fix complaints from Hypothesis
        about the method having different executors.
        """
        storage_index, uploaded_data, _ = self.upload(1, data_length)
        response = self.http.result_of_with_flush(
            self.http.client.request(
                "GET",
                self.http.client.relative_url(
                    "/storage/v1/{}/{}/1".format(self.KIND, _encode_si(storage_index))
                ),
            )
        )
        self.assertEqual(response.code, http.OK)
        self.assertEqual(
            self.http.result_of_with_flush(response.content()), uploaded_data
        )

    def test_validate_content_range_response_to_read(self):
        """
        The server responds to ranged reads with an appropriate Content-Range
        header.
        """
        storage_index, _, _ = self.upload(1, 26)

        def check_range(requested_range, expected_response):
            headers = Headers()
            headers.setRawHeaders("range", [requested_range])
            response = self.http.result_of_with_flush(
                self.http.client.request(
                    "GET",
                    self.http.client.relative_url(
                        "/storage/v1/{}/{}/1".format(
                            self.KIND, _encode_si(storage_index)
                        )
                    ),
                    headers=headers,
                )
            )
            self.assertEqual(
                response.headers.getRawHeaders("content-range"), [expected_response]
            )

        check_range("bytes=0-10", "bytes 0-10/*")
        check_range("bytes=3-17", "bytes 3-17/*")
        # TODO re-enable in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3907
        # Can't go beyond the end of the mutable/immutable!
        # check_range("bytes=10-100", "bytes 10-25/*")


class ImmutableSharedTests(SharedImmutableMutableTestsMixin, SyncTestCase):
    """Shared tests, running on immutables."""

    KIND = "immutable"
    clientFactory = StorageClientImmutables

    def setUp(self):
        super(ImmutableSharedTests, self).setUp()
        disable_thread_pool_for_test(self)
        self.http = self.useFixture(HttpTestFixture())
        self.client = self.clientFactory(self.http.client)
        self.general_client = StorageClientGeneral(self.http.client)

    def upload(self, share_number, data_length=26):
        """
        Create a share, return (storage_index, uploaded_data, lease_secret).
        """
        uploaded_data = (b"abcdefghijklmnopqrstuvwxyz" * ((data_length // 26) + 1))[
            :data_length
        ]
        upload_secret = urandom(32)
        lease_secret = urandom(32)
        storage_index = urandom(16)
        self.http.result_of_with_flush(
            self.client.create(
                storage_index,
                {share_number},
                data_length,
                upload_secret,
                lease_secret,
                lease_secret,
            )
        )
        self.http.result_of_with_flush(
            self.client.write_share_chunk(
                storage_index,
                share_number,
                upload_secret,
                0,
                uploaded_data,
            )
        )
        return storage_index, uploaded_data, lease_secret

    def get_leases(self, storage_index):
        return self.http.storage_server.get_leases(storage_index)

    @given(data_length=st.integers(min_value=1, max_value=300000))
    def test_read_with_no_range(self, data_length):
        """
        A read with no range returns the whole immutable.
        """
        return self._read_with_no_range_test(data_length)


class MutableSharedTests(SharedImmutableMutableTestsMixin, SyncTestCase):
    """Shared tests, running on mutables."""

    KIND = "mutable"
    clientFactory = StorageClientMutables

    def setUp(self):
        super(MutableSharedTests, self).setUp()
        disable_thread_pool_for_test(self)
        self.http = self.useFixture(HttpTestFixture())
        self.client = self.clientFactory(self.http.client)
        self.general_client = StorageClientGeneral(self.http.client)

    def upload(self, share_number, data_length=26):
        """
        Create a share, return (storage_index, uploaded_data, lease_secret).
        """
        data = (b"abcdefghijklmnopqrstuvwxyz" * ((data_length // 26) + 1))[:data_length]
        write_secret = urandom(32)
        lease_secret = urandom(32)
        storage_index = urandom(16)
        self.http.result_of_with_flush(
            self.client.read_test_write_chunks(
                storage_index,
                write_secret,
                lease_secret,
                lease_secret,
                {
                    share_number: TestWriteVectors(
                        write_vectors=[WriteVector(offset=0, data=data)]
                    ),
                },
                [],
            )
        )
        return storage_index, data, lease_secret

    def get_leases(self, storage_index):
        return self.http.storage_server.get_slot_leases(storage_index)

    @given(data_length=st.integers(min_value=1, max_value=300000))
    def test_read_with_no_range(self, data_length):
        """
        A read with no range returns the whole mutable.
        """
        return self._read_with_no_range_test(data_length)
