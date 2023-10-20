"""
HTTP client that talks to the HTTP storage server.
"""

from __future__ import annotations


from typing import (
    Union,
    Optional,
    Sequence,
    Mapping,
    BinaryIO,
    cast,
    TypedDict,
    Set,
    Dict,
    Callable,
    ClassVar,
)
from base64 import b64encode
from io import BytesIO
from os import SEEK_END

from attrs import define, asdict, frozen, field
from eliot import start_action, register_exception_extractor
from eliot.twisted import DeferredContext

# TODO Make sure to import Python version?
from cbor2 import loads, dumps
from pycddl import Schema
from collections_extended import RangeMap
from werkzeug.datastructures import Range, ContentRange
from twisted.web.http_headers import Headers
from twisted.web import http
from twisted.web.iweb import IPolicyForHTTPS, IResponse, IAgent
from twisted.internet.defer import Deferred, succeed
from twisted.internet.interfaces import (
    IOpenSSLClientConnectionCreator,
    IReactorTime,
    IDelayedCall,
)
from twisted.internet.ssl import CertificateOptions
from twisted.protocols.tls import TLSMemoryBIOProtocol
from twisted.web.client import Agent, HTTPConnectionPool
from zope.interface import implementer
from hyperlink import DecodedURL
import treq
from treq.client import HTTPClient
from treq.testing import StubTreq
from OpenSSL import SSL
from werkzeug.http import parse_content_range_header

from .http_common import (
    swissnum_auth_header,
    Secrets,
    get_content_type,
    CBOR_MIME_TYPE,
    get_spki_hash,
    response_is_not_html,
)
from ..interfaces import VersionMessage
from .common import si_b2a, si_to_human_readable
from ..util.hashutil import timing_safe_compare
from ..util.deferredutil import async_to_deferred
from ..util.tor_provider import _Provider as TorProvider
from ..util.cputhreadpool import defer_to_thread

try:
    from txtorcon import Tor  # type: ignore
except ImportError:

    class Tor:  # type: ignore[no-redef]
        pass


def _encode_si(si: bytes) -> str:
    """Encode the storage index into Unicode string."""
    return str(si_b2a(si), "ascii")


class ClientException(Exception):
    """An unexpected response code from the server."""

    def __init__(
        self, code: int, message: Optional[str] = None, body: Optional[bytes] = None
    ):
        Exception.__init__(self, code, message, body)
        self.code = code
        self.message = message
        self.body = body


register_exception_extractor(ClientException, lambda e: {"response_code": e.code})


# Schemas for server responses.
#
# Tags are of the form #6.nnn, where the number is documented at
# https://www.iana.org/assignments/cbor-tags/cbor-tags.xhtml. Notably, #6.258
# indicates a set.
_SCHEMAS: Mapping[str, Schema] = {
    "get_version": Schema(
        # Note that the single-quoted (`'`) string keys in this schema
        # represent *byte* strings - per the CDDL specification.  Text strings
        # are represented using strings with *double* quotes (`"`).
        """
        response = {'http://allmydata.org/tahoe/protocols/storage/v1' => {
                 'maximum-immutable-share-size' => uint
                 'maximum-mutable-share-size' => uint
                 'available-space' => uint
                 }
                 'application-version' => bstr
              }
    """
    ),
    "allocate_buckets": Schema(
        """
    response = {
      already-have: #6.258([0*256 uint])
      allocated: #6.258([0*256 uint])
    }
    """
    ),
    "immutable_write_share_chunk": Schema(
        """
    response = {
      required: [0* {begin: uint, end: uint}]
    }
    """
    ),
    "list_shares": Schema(
        """
    response = #6.258([0*256 uint])
    """
    ),
    "mutable_read_test_write": Schema(
        """
        response = {
          "success": bool,
          "data": {0*256 share_number: [0* bstr]}
        }
        share_number = uint
        """
    ),
    "mutable_list_shares": Schema(
        """
        response = #6.258([0*256 uint])
        """
    ),
}


@define
class _LengthLimitedCollector:
    """
    Collect data using ``treq.collect()``, with limited length.
    """

    remaining_length: int
    timeout_on_silence: IDelayedCall
    f: BytesIO = field(factory=BytesIO)

    def __call__(self, data: bytes) -> None:
        self.timeout_on_silence.reset(60)
        self.remaining_length -= len(data)
        if self.remaining_length < 0:
            raise ValueError("Response length was too long")
        self.f.write(data)


def limited_content(
    response: IResponse,
    clock: IReactorTime,
    max_length: int = 30 * 1024 * 1024,
) -> Deferred[BinaryIO]:
    """
    Like ``treq.content()``, but limit data read from the response to a set
    length.  If the response is longer than the max allowed length, the result
    fails with a ``ValueError``.

    A potentially useful future improvement would be using a temporary file to
    store the content; since filesystem buffering means that would use memory
    for small responses and disk for large responses.

    This will time out if no data is received for 60 seconds; so long as a
    trickle of data continues to arrive, it will continue to run.
    """
    result_deferred = succeed(None)

    # Sadly, addTimeout() won't work because we need access to the IDelayedCall
    # in order to reset it on each data chunk received.
    timeout = clock.callLater(60, result_deferred.cancel)
    collector = _LengthLimitedCollector(max_length, timeout)

    with start_action(
        action_type="allmydata:storage:http-client:limited-content",
        max_length=max_length,
    ).context():
        d = DeferredContext(result_deferred)

    # Make really sure everything gets called in Deferred context, treq might
    # call collector directly...
    d.addCallback(lambda _: treq.collect(response, collector))

    def done(_: object) -> BytesIO:
        timeout.cancel()
        collector.f.seek(0)
        return collector.f

    def failed(f):
        if timeout.active():
            timeout.cancel()
        return f

    result = d.addCallbacks(done, failed)
    return result.addActionFinish()


@define
class ImmutableCreateResult(object):
    """Result of creating a storage index for an immutable."""

    already_have: set[int]
    allocated: set[int]


class _TLSContextFactory(CertificateOptions):
    """
    Create a context that validates the way Tahoe-LAFS wants to: based on a
    pinned certificate hash, rather than a certificate authority.

    Originally implemented as part of Foolscap.  To comply with the license,
    here's the original licensing terms:

    Copyright (c) 2006-2008 Brian Warner

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the "Software"),
    to deal in the Software without restriction, including without limitation
    the rights to use, copy, modify, merge, publish, distribute, sublicense,
    and/or sell copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in
    all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
    THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
    DEALINGS IN THE SOFTWARE.
    """

    def __init__(self, expected_spki_hash: bytes):
        self.expected_spki_hash = expected_spki_hash
        CertificateOptions.__init__(self)

    def getContext(self) -> SSL.Context:
        def always_validate(conn, cert, errno, depth, preverify_ok):
            # This function is called to validate the certificate received by
            # the other end. OpenSSL calls it multiple times, for each errno
            # for each certificate.

            # We do not care about certificate authorities or revocation
            # lists, we just want to know that the certificate has a valid
            # signature and follow the chain back to one which is
            # self-signed. We need to protect against forged signatures, but
            # not the usual TLS concerns about invalid CAs or revoked
            # certificates.
            things_are_ok = (
                SSL.X509VerificationCodes.OK,
                SSL.X509VerificationCodes.ERR_CERT_NOT_YET_VALID,
                SSL.X509VerificationCodes.ERR_CERT_HAS_EXPIRED,
                SSL.X509VerificationCodes.ERR_DEPTH_ZERO_SELF_SIGNED_CERT,
                SSL.X509VerificationCodes.ERR_SELF_SIGNED_CERT_IN_CHAIN,
            )
            # TODO can we do this once instead of multiple times?
            if errno in things_are_ok and timing_safe_compare(
                get_spki_hash(cert.to_cryptography()), self.expected_spki_hash
            ):
                return 1
            # TODO: log the details of the error, because otherwise they get
            # lost in the PyOpenSSL exception that will eventually be raised
            # (possibly OpenSSL.SSL.Error: certificate verify failed)
            return 0

        ctx = CertificateOptions.getContext(self)

        # VERIFY_PEER means we ask the the other end for their certificate.
        ctx.set_verify(SSL.VERIFY_PEER, always_validate)
        return ctx


@implementer(IPolicyForHTTPS)
@implementer(IOpenSSLClientConnectionCreator)
@define
class _StorageClientHTTPSPolicy:
    """
    A HTTPS policy that ensures the SPKI hash of the public key matches a known
    hash, i.e. pinning-based validation.
    """

    expected_spki_hash: bytes

    # IPolicyForHTTPS
    def creatorForNetloc(self, hostname: str, port: int) -> _StorageClientHTTPSPolicy:
        return self

    # IOpenSSLClientConnectionCreator
    def clientConnectionForTLS(
        self, tlsProtocol: TLSMemoryBIOProtocol
    ) -> SSL.Connection:
        return SSL.Connection(
            _TLSContextFactory(self.expected_spki_hash).getContext(), None
        )


@define
class StorageClientFactory:
    """
    Create ``StorageClient`` instances, using appropriate
    ``twisted.web.iweb.IAgent`` for different connection methods: normal TCP,
    Tor, and eventually I2P.

    There is some caching involved since there might be shared setup work, e.g.
    connecting to the local Tor service only needs to happen once.
    """

    _default_connection_handlers: dict[str, str]
    _tor_provider: Optional[TorProvider]
    # Cache the Tor instance created by the provider, if relevant.
    _tor_instance: Optional[Tor] = None

    # If set, we're doing unit testing and we should call this with any
    # HTTPConnectionPool that gets passed/created to ``create_agent()``.
    TEST_MODE_REGISTER_HTTP_POOL: ClassVar[
        Optional[Callable[[HTTPConnectionPool], None]]
    ] = None

    @classmethod
    def start_test_mode(cls, callback: Callable[[HTTPConnectionPool], None]) -> None:
        """Switch to testing mode.

        In testing mode we register the pool with test system using the given
        callback so it can Do Things, most notably killing off idle HTTP
        connections at test shutdown and, in some tests, in the midddle of the
        test.
        """
        cls.TEST_MODE_REGISTER_HTTP_POOL = callback

    @classmethod
    def stop_test_mode(cls) -> None:
        """Stop testing mode."""
        cls.TEST_MODE_REGISTER_HTTP_POOL = None

    async def _create_agent(
        self,
        nurl: DecodedURL,
        reactor: object,
        tls_context_factory: IPolicyForHTTPS,
        pool: HTTPConnectionPool,
    ) -> IAgent:
        """Create a new ``IAgent``, possibly using Tor."""
        if self.TEST_MODE_REGISTER_HTTP_POOL is not None:
            self.TEST_MODE_REGISTER_HTTP_POOL(pool)

        # TODO default_connection_handlers should really be an object, not a
        # dict, so we can ask "is this using Tor" without poking at a
        # dictionary with arbitrary strings... See
        # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4032
        handler = self._default_connection_handlers["tcp"]

        if handler == "tcp":
            return Agent(reactor, tls_context_factory, pool=pool)
        if handler == "tor" or nurl.scheme == "pb+tor":
            assert self._tor_provider is not None
            if self._tor_instance is None:
                self._tor_instance = await self._tor_provider.get_tor_instance(reactor)
            return self._tor_instance.web_agent(
                pool=pool, tls_context_factory=tls_context_factory
            )
        else:
            # I2P support will be added here. See
            # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4037
            raise RuntimeError(f"Unsupported tcp connection handler: {handler}")

    async def create_storage_client(
        self,
        nurl: DecodedURL,
        reactor: IReactorTime,
        pool: Optional[HTTPConnectionPool] = None,
    ) -> StorageClient:
        """Create a new ``StorageClient`` for the given NURL."""
        assert nurl.fragment == "v=1"
        assert nurl.scheme in ("pb", "pb+tor")
        if pool is None:
            pool = HTTPConnectionPool(reactor)
            pool.maxPersistentPerHost = 10

        certificate_hash = nurl.user.encode("ascii")
        agent = await self._create_agent(
            nurl,
            reactor,
            _StorageClientHTTPSPolicy(expected_spki_hash=certificate_hash),
            pool,
        )
        treq_client = HTTPClient(agent)
        https_url = DecodedURL().replace(scheme="https", host=nurl.host, port=nurl.port)
        swissnum = nurl.path[0].encode("ascii")
        response_check = lambda _: None
        if self.TEST_MODE_REGISTER_HTTP_POOL is not None:
            response_check = response_is_not_html

        return StorageClient(
            https_url,
            swissnum,
            treq_client,
            pool,
            reactor,
            response_check,
        )


@define(hash=True)
class StorageClient(object):
    """
    Low-level HTTP client that talks to the HTTP storage server.

    Create using a ``StorageClientFactory`` instance.
    """

    # The URL should be a HTTPS URL ("https://...")
    _base_url: DecodedURL
    _swissnum: bytes
    _treq: Union[treq, StubTreq, HTTPClient]
    _pool: HTTPConnectionPool
    _clock: IReactorTime
    # Are we running unit tests?
    _analyze_response: Callable[[IResponse], None] = lambda _: None

    def relative_url(self, path: str) -> DecodedURL:
        """Get a URL relative to the base URL."""
        return self._base_url.click(path)

    def _get_headers(self, headers: Optional[Headers]) -> Headers:
        """Return the basic headers to be used by default."""
        if headers is None:
            headers = Headers()
        headers.addRawHeader(
            "Authorization",
            swissnum_auth_header(self._swissnum),
        )
        return headers

    @async_to_deferred
    async def request(
        self,
        method: str,
        url: DecodedURL,
        lease_renew_secret: Optional[bytes] = None,
        lease_cancel_secret: Optional[bytes] = None,
        upload_secret: Optional[bytes] = None,
        write_enabler_secret: Optional[bytes] = None,
        headers: Optional[Headers] = None,
        message_to_serialize: object = None,
        timeout: float = 60,
        **kwargs,
    ) -> IResponse:
        """
        Like ``treq.request()``, but with optional secrets that get translated
        into corresponding HTTP headers.

        If ``message_to_serialize`` is set, it will be serialized (by default
        with CBOR) and set as the request body.  It should not be mutated
        during execution of this function!

        Default timeout is 60 seconds.
        """
        with start_action(
            action_type="allmydata:storage:http-client:request",
            method=method,
            url=url.to_text(),
            timeout=timeout,
        ) as ctx:
            response = await self._request(
                method,
                url,
                lease_renew_secret,
                lease_cancel_secret,
                upload_secret,
                write_enabler_secret,
                headers,
                message_to_serialize,
                timeout,
                **kwargs,
            )
            ctx.add_success_fields(response_code=response.code)
            return response

    async def _request(
        self,
        method: str,
        url: DecodedURL,
        lease_renew_secret: Optional[bytes] = None,
        lease_cancel_secret: Optional[bytes] = None,
        upload_secret: Optional[bytes] = None,
        write_enabler_secret: Optional[bytes] = None,
        headers: Optional[Headers] = None,
        message_to_serialize: object = None,
        timeout: float = 60,
        **kwargs,
    ) -> IResponse:
        """The implementation of request()."""
        headers = self._get_headers(headers)

        # Add secrets:
        for secret, value in [
            (Secrets.LEASE_RENEW, lease_renew_secret),
            (Secrets.LEASE_CANCEL, lease_cancel_secret),
            (Secrets.UPLOAD, upload_secret),
            (Secrets.WRITE_ENABLER, write_enabler_secret),
        ]:
            if value is None:
                continue
            headers.addRawHeader(
                "X-Tahoe-Authorization",
                b"%s %s" % (secret.value.encode("ascii"), b64encode(value).strip()),
            )

        # Note we can accept CBOR:
        headers.addRawHeader("Accept", CBOR_MIME_TYPE)

        # If there's a request message, serialize it and set the Content-Type
        # header:
        if message_to_serialize is not None:
            if "data" in kwargs:
                raise TypeError(
                    "Can't use both `message_to_serialize` and `data` "
                    "as keyword arguments at the same time"
                )
            kwargs["data"] = await defer_to_thread(dumps, message_to_serialize)
            headers.addRawHeader("Content-Type", CBOR_MIME_TYPE)

        response = await self._treq.request(
            method, url, headers=headers, timeout=timeout, **kwargs
        )
        self._analyze_response(response)

        return response

    async def decode_cbor(self, response: IResponse, schema: Schema) -> object:
        """Given HTTP response, return decoded CBOR body."""
        with start_action(action_type="allmydata:storage:http-client:decode-cbor"):
            if response.code > 199 and response.code < 300:
                content_type = get_content_type(response.headers)
                if content_type == CBOR_MIME_TYPE:
                    f = await limited_content(response, self._clock)
                    data = f.read()

                    def validate_and_decode():
                        schema.validate_cbor(data)
                        return loads(data)

                    return await defer_to_thread(validate_and_decode)
                else:
                    raise ClientException(
                        -1,
                        "Server didn't send CBOR, content type is {}".format(
                            content_type
                        ),
                    )
            else:
                data = (
                    await limited_content(response, self._clock, max_length=10_000)
                ).read()
                raise ClientException(response.code, response.phrase, data)

    def shutdown(self) -> Deferred[object]:
        """Shutdown any connections."""
        return self._pool.closeCachedConnections()


@define(hash=True)
class StorageClientGeneral(object):
    """
    High-level HTTP APIs that aren't immutable- or mutable-specific.
    """

    _client: StorageClient

    @async_to_deferred
    async def get_version(self) -> VersionMessage:
        """
        Return the version metadata for the server.
        """
        with start_action(
            action_type="allmydata:storage:http-client:get-version",
        ):
            return await self._get_version()

    async def _get_version(self) -> VersionMessage:
        """Implementation of get_version()."""
        url = self._client.relative_url("/storage/v1/version")
        response = await self._client.request("GET", url)
        decoded_response = cast(
            Dict[bytes, object],
            await self._client.decode_cbor(response, _SCHEMAS["get_version"]),
        )
        # Add some features we know are true because the HTTP API
        # specification requires them and because other parts of the storage
        # client implementation assumes they will be present.
        cast(
            Dict[bytes, object],
            decoded_response[b"http://allmydata.org/tahoe/protocols/storage/v1"],
        ).update(
            {
                b"tolerates-immutable-read-overrun": True,
                b"delete-mutable-shares-with-zero-length-writev": True,
                b"fills-holes-with-zero-bytes": True,
                b"prevents-read-past-end-of-share-data": True,
            }
        )
        return decoded_response

    @async_to_deferred
    async def add_or_renew_lease(
        self, storage_index: bytes, renew_secret: bytes, cancel_secret: bytes
    ) -> None:
        """
        Add or renew a lease.

        If the renewal secret matches an existing lease, it is renewed.
        Otherwise a new lease is added.
        """
        with start_action(
            action_type="allmydata:storage:http-client:add-or-renew-lease",
            storage_index=si_to_human_readable(storage_index),
        ):
            return await self._add_or_renew_lease(
                storage_index, renew_secret, cancel_secret
            )

    async def _add_or_renew_lease(
        self, storage_index: bytes, renew_secret: bytes, cancel_secret: bytes
    ) -> None:
        url = self._client.relative_url(
            "/storage/v1/lease/{}".format(_encode_si(storage_index))
        )
        response = await self._client.request(
            "PUT",
            url,
            lease_renew_secret=renew_secret,
            lease_cancel_secret=cancel_secret,
        )

        if response.code == http.NO_CONTENT:
            return
        else:
            raise ClientException(response.code)


@define
class UploadProgress(object):
    """
    Progress of immutable upload, per the server.
    """

    # True when upload has finished.
    finished: bool
    # Remaining ranges to upload.
    required: RangeMap


@async_to_deferred
async def read_share_chunk(
    client: StorageClient,
    share_type: str,
    storage_index: bytes,
    share_number: int,
    offset: int,
    length: int,
) -> bytes:
    """
    Download a chunk of data from a share.

    TODO https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3857 Failed downloads
    should be transparently retried and redownloaded by the implementation a
    few times so that if a failure percolates up, the caller can assume the
    failure isn't a short-term blip.

    NOTE: the underlying HTTP protocol is somewhat more flexible than this API,
    insofar as it doesn't always require a range.  In practice a range is
    always provided by the current callers.
    """
    url = client.relative_url(
        "/storage/v1/{}/{}/{}".format(
            share_type, _encode_si(storage_index), share_number
        )
    )
    # The default 60 second timeout is for getting the response, so it doesn't
    # include the time it takes to download the body... so we will will deal
    # with that later, via limited_content().
    response = await client.request(
        "GET",
        url,
        headers=Headers(
            # Ranges in HTTP are _inclusive_, Python's convention is exclusive,
            # but Range constructor does that the conversion for us.
            {"range": [Range("bytes", [(offset, offset + length)]).to_header()]}
        ),
        unbuffered=True,  # Don't buffer the response in memory.
    )

    if response.code == http.NO_CONTENT:
        return b""

    content_type = get_content_type(response.headers)
    if content_type != "application/octet-stream":
        raise ValueError(
            f"Content-type was wrong: {content_type}, should be application/octet-stream"
        )

    if response.code == http.PARTIAL_CONTENT:
        content_range = parse_content_range_header(
            response.headers.getRawHeaders("content-range")[0] or ""
        )
        if (
            content_range is None
            or content_range.stop is None
            or content_range.start is None
        ):
            raise ValueError(
                "Content-Range was missing, invalid, or in format we don't support"
            )
        supposed_length = content_range.stop - content_range.start
        if supposed_length > length:
            raise ValueError("Server sent more than we asked for?!")
        # It might also send less than we asked for. That's (probably) OK, e.g.
        # if we went past the end of the file.
        body = await limited_content(response, client._clock, supposed_length)
        body.seek(0, SEEK_END)
        actual_length = body.tell()
        if actual_length != supposed_length:
            # Most likely a mutable that got changed out from under us, but
            # conceivably could be a bug...
            raise ValueError(
                f"Length of response sent from server ({actual_length}) "
                + f"didn't match Content-Range header ({supposed_length})"
            )
        body.seek(0)
        return body.read()
    else:
        # Technically HTTP allows sending an OK with full body under these
        # circumstances, but the server is not designed to do that so we ignore
        # that possibility for now...
        raise ClientException(response.code)


@async_to_deferred
async def advise_corrupt_share(
    client: StorageClient,
    share_type: str,
    storage_index: bytes,
    share_number: int,
    reason: str,
) -> None:
    assert isinstance(reason, str)
    url = client.relative_url(
        "/storage/v1/{}/{}/{}/corrupt".format(
            share_type, _encode_si(storage_index), share_number
        )
    )
    message = {"reason": reason}
    response = await client.request("POST", url, message_to_serialize=message)
    if response.code == http.OK:
        return
    else:
        raise ClientException(
            response.code,
        )


@define(hash=True)
class StorageClientImmutables(object):
    """
    APIs for interacting with immutables.
    """

    _client: StorageClient

    @async_to_deferred
    async def create(
        self,
        storage_index: bytes,
        share_numbers: set[int],
        allocated_size: int,
        upload_secret: bytes,
        lease_renew_secret: bytes,
        lease_cancel_secret: bytes,
    ) -> ImmutableCreateResult:
        """
        Create a new storage index for an immutable.

        TODO https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3857 retry
        internally on failure, to ensure the operation fully succeeded.  If
        sufficient number of failures occurred, the result may fire with an
        error, but there's no expectation that user code needs to have a
        recovery codepath; it will most likely just report an error to the
        user.

        Result fires when creating the storage index succeeded, if creating the
        storage index failed the result will fire with an exception.
        """
        with start_action(
            action_type="allmydata:storage:http-client:immutable:create",
            storage_index=si_to_human_readable(storage_index),
            share_numbers=share_numbers,
            allocated_size=allocated_size,
        ) as ctx:
            result = await self._create(
                storage_index,
                share_numbers,
                allocated_size,
                upload_secret,
                lease_renew_secret,
                lease_cancel_secret,
            )
            ctx.add_success_fields(
                already_have=result.already_have, allocated=result.allocated
            )
            return result

    async def _create(
        self,
        storage_index: bytes,
        share_numbers: set[int],
        allocated_size: int,
        upload_secret: bytes,
        lease_renew_secret: bytes,
        lease_cancel_secret: bytes,
    ) -> ImmutableCreateResult:
        """Implementation of create()."""
        url = self._client.relative_url(
            "/storage/v1/immutable/" + _encode_si(storage_index)
        )
        message = {"share-numbers": share_numbers, "allocated-size": allocated_size}

        response = await self._client.request(
            "POST",
            url,
            lease_renew_secret=lease_renew_secret,
            lease_cancel_secret=lease_cancel_secret,
            upload_secret=upload_secret,
            message_to_serialize=message,
        )
        decoded_response = cast(
            Mapping[str, Set[int]],
            await self._client.decode_cbor(response, _SCHEMAS["allocate_buckets"]),
        )
        return ImmutableCreateResult(
            already_have=decoded_response["already-have"],
            allocated=decoded_response["allocated"],
        )

    @async_to_deferred
    async def abort_upload(
        self, storage_index: bytes, share_number: int, upload_secret: bytes
    ) -> None:
        """Abort the upload."""
        with start_action(
            action_type="allmydata:storage:http-client:immutable:abort-upload",
            storage_index=si_to_human_readable(storage_index),
            share_number=share_number,
        ):
            return await self._abort_upload(storage_index, share_number, upload_secret)

    async def _abort_upload(
        self, storage_index: bytes, share_number: int, upload_secret: bytes
    ) -> None:
        """Implementation of ``abort_upload()``."""
        url = self._client.relative_url(
            "/storage/v1/immutable/{}/{}/abort".format(
                _encode_si(storage_index), share_number
            )
        )
        response = await self._client.request(
            "PUT",
            url,
            upload_secret=upload_secret,
        )

        if response.code == http.OK:
            return
        else:
            raise ClientException(
                response.code,
            )

    @async_to_deferred
    async def write_share_chunk(
        self,
        storage_index: bytes,
        share_number: int,
        upload_secret: bytes,
        offset: int,
        data: bytes,
    ) -> UploadProgress:
        """
        Upload a chunk of data for a specific share.

        TODO https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3857 The
        implementation should retry failed uploads transparently a number of
        times, so that if a failure percolates up, the caller can assume the
        failure isn't a short-term blip.

        Result fires when the upload succeeded, with a boolean indicating
        whether the _complete_ share (i.e. all chunks, not just this one) has
        been uploaded.
        """
        with start_action(
            action_type="allmydata:storage:http-client:immutable:write-share-chunk",
            storage_index=si_to_human_readable(storage_index),
            share_number=share_number,
            offset=offset,
            data_len=len(data),
        ) as ctx:
            result = await self._write_share_chunk(
                storage_index, share_number, upload_secret, offset, data
            )
            ctx.add_success_fields(finished=result.finished)
            return result

    async def _write_share_chunk(
        self,
        storage_index: bytes,
        share_number: int,
        upload_secret: bytes,
        offset: int,
        data: bytes,
    ) -> UploadProgress:
        """Implementation of ``write_share_chunk()``."""
        url = self._client.relative_url(
            "/storage/v1/immutable/{}/{}".format(
                _encode_si(storage_index), share_number
            )
        )
        response = await self._client.request(
            "PATCH",
            url,
            upload_secret=upload_secret,
            data=data,
            headers=Headers(
                {
                    "content-range": [
                        ContentRange("bytes", offset, offset + len(data)).to_header()
                    ]
                }
            ),
        )

        if response.code == http.OK:
            # Upload is still unfinished.
            finished = False
        elif response.code == http.CREATED:
            # Upload is done!
            finished = True
        else:
            raise ClientException(
                response.code,
            )
        body = cast(
            Mapping[str, Sequence[Mapping[str, int]]],
            await self._client.decode_cbor(
                response, _SCHEMAS["immutable_write_share_chunk"]
            ),
        )
        remaining = RangeMap()
        for chunk in body["required"]:
            remaining.set(True, chunk["begin"], chunk["end"])
        return UploadProgress(finished=finished, required=remaining)

    @async_to_deferred
    async def read_share_chunk(
        self, storage_index: bytes, share_number: int, offset: int, length: int
    ) -> bytes:
        """
        Download a chunk of data from a share.
        """
        with start_action(
            action_type="allmydata:storage:http-client:immutable:read-share-chunk",
            storage_index=si_to_human_readable(storage_index),
            share_number=share_number,
            offset=offset,
            length=length,
        ) as ctx:
            result = await read_share_chunk(
                self._client, "immutable", storage_index, share_number, offset, length
            )
            ctx.add_success_fields(data_len=len(result))
            return result

    @async_to_deferred
    async def list_shares(self, storage_index: bytes) -> Set[int]:
        """
        Return the set of shares for a given storage index.
        """
        with start_action(
            action_type="allmydata:storage:http-client:immutable:list-shares",
            storage_index=si_to_human_readable(storage_index),
        ) as ctx:
            result = await self._list_shares(storage_index)
            ctx.add_success_fields(shares=result)
            return result

    async def _list_shares(self, storage_index: bytes) -> Set[int]:
        """Implementation of ``list_shares()``."""
        url = self._client.relative_url(
            "/storage/v1/immutable/{}/shares".format(_encode_si(storage_index))
        )
        response = await self._client.request(
            "GET",
            url,
        )
        if response.code == http.OK:
            return cast(
                Set[int],
                await self._client.decode_cbor(response, _SCHEMAS["list_shares"]),
            )
        else:
            raise ClientException(response.code)

    @async_to_deferred
    async def advise_corrupt_share(
        self,
        storage_index: bytes,
        share_number: int,
        reason: str,
    ) -> None:
        """Indicate a share has been corrupted, with a human-readable message."""
        with start_action(
            action_type="allmydata:storage:http-client:immutable:advise-corrupt-share",
            storage_index=si_to_human_readable(storage_index),
            share_number=share_number,
            reason=reason,
        ):
            await advise_corrupt_share(
                self._client, "immutable", storage_index, share_number, reason
            )


@frozen
class WriteVector:
    """Data to write to a chunk."""

    offset: int
    data: bytes


@frozen
class TestVector:
    """Checks to make on a chunk before writing to it."""

    offset: int
    size: int
    specimen: bytes


@frozen
class ReadVector:
    """
    Reads to do on chunks, as part of a read/test/write operation.
    """

    offset: int
    size: int


@frozen
class TestWriteVectors:
    """Test and write vectors for a specific share."""

    test_vectors: Sequence[TestVector] = field(factory=list)
    write_vectors: Sequence[WriteVector] = field(factory=list)
    new_length: Optional[int] = None

    def asdict(self) -> dict:
        """Return dictionary suitable for sending over CBOR."""
        d = asdict(self)
        d["test"] = d.pop("test_vectors")
        d["write"] = d.pop("write_vectors")
        d["new-length"] = d.pop("new_length")
        return d


@frozen
class ReadTestWriteResult:
    """Result of sending read-test-write vectors."""

    success: bool
    # Map share numbers to reads corresponding to the request's list of
    # ReadVectors:
    reads: Mapping[int, Sequence[bytes]]


# Result type for mutable read/test/write HTTP response. Can't just use
# dict[int,list[bytes]] because on Python 3.8 that will error out.
MUTABLE_RTW = TypedDict(
    "MUTABLE_RTW", {"success": bool, "data": Mapping[int, Sequence[bytes]]}
)


@frozen
class StorageClientMutables:
    """
    APIs for interacting with mutables.
    """

    _client: StorageClient

    @async_to_deferred
    async def read_test_write_chunks(
        self,
        storage_index: bytes,
        write_enabler_secret: bytes,
        lease_renew_secret: bytes,
        lease_cancel_secret: bytes,
        testwrite_vectors: dict[int, TestWriteVectors],
        read_vector: list[ReadVector],
    ) -> ReadTestWriteResult:
        """
        Read, test, and possibly write chunks to a particular mutable storage
        index.

        Reads are done before writes.

        Given a mapping between share numbers and test/write vectors, the tests
        are done and if they are valid the writes are done.
        """
        with start_action(
            action_type="allmydata:storage:http-client:mutable:read-test-write",
            storage_index=si_to_human_readable(storage_index),
        ):
            return await self._read_test_write_chunks(
                storage_index,
                write_enabler_secret,
                lease_renew_secret,
                lease_cancel_secret,
                testwrite_vectors,
                read_vector,
            )

    async def _read_test_write_chunks(
        self,
        storage_index: bytes,
        write_enabler_secret: bytes,
        lease_renew_secret: bytes,
        lease_cancel_secret: bytes,
        testwrite_vectors: dict[int, TestWriteVectors],
        read_vector: list[ReadVector],
    ) -> ReadTestWriteResult:
        """Implementation of ``read_test_write_chunks()``."""
        url = self._client.relative_url(
            "/storage/v1/mutable/{}/read-test-write".format(_encode_si(storage_index))
        )
        message = {
            "test-write-vectors": {
                share_number: twv.asdict()
                for (share_number, twv) in testwrite_vectors.items()
            },
            "read-vector": [asdict(r) for r in read_vector],
        }
        response = await self._client.request(
            "POST",
            url,
            write_enabler_secret=write_enabler_secret,
            lease_renew_secret=lease_renew_secret,
            lease_cancel_secret=lease_cancel_secret,
            message_to_serialize=message,
        )
        if response.code == http.OK:
            result = cast(
                MUTABLE_RTW,
                await self._client.decode_cbor(
                    response, _SCHEMAS["mutable_read_test_write"]
                ),
            )
            return ReadTestWriteResult(success=result["success"], reads=result["data"])
        else:
            raise ClientException(response.code, (await response.content()))

    @async_to_deferred
    async def read_share_chunk(
        self,
        storage_index: bytes,
        share_number: int,
        offset: int,
        length: int,
    ) -> bytes:
        """
        Download a chunk of data from a share.
        """
        with start_action(
            action_type="allmydata:storage:http-client:mutable:read-share-chunk",
            storage_index=si_to_human_readable(storage_index),
            share_number=share_number,
            offset=offset,
            length=length,
        ) as ctx:
            result = await read_share_chunk(
                self._client, "mutable", storage_index, share_number, offset, length
            )
            ctx.add_success_fields(data_len=len(result))
            return result

    @async_to_deferred
    async def list_shares(self, storage_index: bytes) -> Set[int]:
        """
        List the share numbers for a given storage index.
        """
        with start_action(
            action_type="allmydata:storage:http-client:mutable:list-shares",
            storage_index=si_to_human_readable(storage_index),
        ) as ctx:
            result = await self._list_shares(storage_index)
            ctx.add_success_fields(shares=result)
            return result

    async def _list_shares(self, storage_index: bytes) -> Set[int]:
        """Implementation of ``list_shares()``."""
        url = self._client.relative_url(
            "/storage/v1/mutable/{}/shares".format(_encode_si(storage_index))
        )
        response = await self._client.request("GET", url)
        if response.code == http.OK:
            return cast(
                Set[int],
                await self._client.decode_cbor(
                    response,
                    _SCHEMAS["mutable_list_shares"],
                ),
            )
        else:
            raise ClientException(response.code)

    @async_to_deferred
    async def advise_corrupt_share(
        self,
        storage_index: bytes,
        share_number: int,
        reason: str,
    ) -> None:
        """Indicate a share has been corrupted, with a human-readable message."""
        with start_action(
            action_type="allmydata:storage:http-client:mutable:advise-corrupt-share",
            storage_index=si_to_human_readable(storage_index),
            share_number=share_number,
            reason=reason,
        ):
            await advise_corrupt_share(
                self._client, "mutable", storage_index, share_number, reason
            )
