"""
HTTP client that talks to the HTTP storage server.
"""

from __future__ import annotations

from typing import Union, Optional, Sequence, Mapping, BinaryIO
from base64 import b64encode
from io import BytesIO
from os import SEEK_END

from attrs import define, asdict, frozen, field

# TODO Make sure to import Python version?
from cbor2 import loads, dumps
from pycddl import Schema
from collections_extended import RangeMap
from werkzeug.datastructures import Range, ContentRange
from twisted.web.http_headers import Headers
from twisted.web import http
from twisted.web.iweb import IPolicyForHTTPS
from twisted.internet.defer import inlineCallbacks, returnValue, fail, Deferred, succeed
from twisted.internet.interfaces import IOpenSSLClientConnectionCreator
from twisted.internet.ssl import CertificateOptions
from twisted.web.client import Agent, HTTPConnectionPool
from zope.interface import implementer
from hyperlink import DecodedURL
import treq
from treq.client import HTTPClient
from treq.testing import StubTreq
from OpenSSL import SSL
from cryptography.hazmat.bindings.openssl.binding import Binding
from werkzeug.http import parse_content_range_header

from .http_common import (
    swissnum_auth_header,
    Secrets,
    get_content_type,
    CBOR_MIME_TYPE,
    get_spki_hash,
)
from .common import si_b2a
from ..util.hashutil import timing_safe_compare
from ..util.deferredutil import async_to_deferred

_OPENSSL = Binding().lib


def _encode_si(si):  # type: (bytes) -> str
    """Encode the storage index into Unicode string."""
    return str(si_b2a(si), "ascii")


class ClientException(Exception):
    """An unexpected response code from the server."""

    def __init__(self, code, *additional_args):
        Exception.__init__(self, code, *additional_args)
        self.code = code


# Schemas for server responses.
#
# Tags are of the form #6.nnn, where the number is documented at
# https://www.iana.org/assignments/cbor-tags/cbor-tags.xhtml. Notably, #6.258
# indicates a set.
_SCHEMAS = {
    "get_version": Schema(
        """
        response = {'http://allmydata.org/tahoe/protocols/storage/v1' => {
                 'maximum-immutable-share-size' => uint
                 'maximum-mutable-share-size' => uint
                 'available-space' => uint
                 'tolerates-immutable-read-overrun' => bool
                 'delete-mutable-shares-with-zero-length-writev' => bool
                 'fills-holes-with-zero-bytes' => bool
                 'prevents-read-past-end-of-share-data' => bool
                 }
                 'application-version' => bstr
              }
    """
    ),
    "allocate_buckets": Schema(
        """
    response = {
      already-have: #6.258([* uint])
      allocated: #6.258([* uint])
    }
    """
    ),
    "immutable_write_share_chunk": Schema(
        """
    response = {
      required: [* {begin: uint, end: uint}]
    }
    """
    ),
    "list_shares": Schema(
        """
    response = #6.258([* uint])
    """
    ),
    "mutable_read_test_write": Schema(
        """
        response = {
          "success": bool,
          "data": {* share_number: [* bstr]}
        }
        share_number = uint
        """
    ),
    "mutable_list_shares": Schema(
        """
        response = #6.258([* uint])
        """
    ),
}


@define
class _LengthLimitedCollector:
    """
    Collect data using ``treq.collect()``, with limited length.
    """

    remaining_length: int
    f: BytesIO = field(factory=BytesIO)

    def __call__(self, data: bytes):
        self.remaining_length -= len(data)
        if self.remaining_length < 0:
            raise ValueError("Response length was too long")
        self.f.write(data)


def limited_content(response, max_length: int = 30 * 1024 * 1024) -> Deferred[BinaryIO]:
    """
    Like ``treq.content()``, but limit data read from the response to a set
    length.  If the response is longer than the max allowed length, the result
    fails with a ``ValueError``.

    A potentially useful future improvement would be using a temporary file to
    store the content; since filesystem buffering means that would use memory
    for small responses and disk for large responses.
    """
    collector = _LengthLimitedCollector(max_length)
    # Make really sure everything gets called in Deferred context, treq might
    # call collector directly...
    d = succeed(None)
    d.addCallback(lambda _: treq.collect(response, collector))

    def done(_):
        collector.f.seek(0)
        return collector.f

    d.addCallback(done)
    return d


def _decode_cbor(response, schema: Schema):
    """Given HTTP response, return decoded CBOR body."""

    def got_content(f: BinaryIO):
        data = f.read()
        schema.validate_cbor(data)
        return loads(data)

    if response.code > 199 and response.code < 300:
        content_type = get_content_type(response.headers)
        if content_type == CBOR_MIME_TYPE:
            return limited_content(response).addCallback(got_content)
        else:
            raise ClientException(-1, "Server didn't send CBOR")
    else:
        return treq.content(response).addCallback(
            lambda data: fail(ClientException(response.code, response.phrase, data))
        )


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
                _OPENSSL.X509_V_OK,
                _OPENSSL.X509_V_ERR_CERT_NOT_YET_VALID,
                _OPENSSL.X509_V_ERR_CERT_HAS_EXPIRED,
                _OPENSSL.X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT,
                _OPENSSL.X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN,
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
    def creatorForNetloc(self, hostname, port):
        return self

    # IOpenSSLClientConnectionCreator
    def clientConnectionForTLS(self, tlsProtocol):
        return SSL.Connection(
            _TLSContextFactory(self.expected_spki_hash).getContext(), None
        )


@define
class StorageClient(object):
    """
    Low-level HTTP client that talks to the HTTP storage server.
    """

    # The URL is a HTTPS URL ("https://...").  To construct from a NURL, use
    # ``StorageClient.from_nurl()``.
    _base_url: DecodedURL
    _swissnum: bytes
    _treq: Union[treq, StubTreq, HTTPClient]

    @classmethod
    def from_nurl(
        cls, nurl: DecodedURL, reactor, persistent: bool = True
    ) -> StorageClient:
        """
        Create a ``StorageClient`` for the given NURL.

        ``persistent`` indicates whether to use persistent HTTP connections.
        """
        assert nurl.fragment == "v=1"
        assert nurl.scheme == "pb"
        swissnum = nurl.path[0].encode("ascii")
        certificate_hash = nurl.user.encode("ascii")

        treq_client = HTTPClient(
            Agent(
                reactor,
                _StorageClientHTTPSPolicy(expected_spki_hash=certificate_hash),
                pool=HTTPConnectionPool(reactor, persistent=persistent),
            )
        )

        https_url = DecodedURL().replace(scheme="https", host=nurl.host, port=nurl.port)
        return cls(https_url, swissnum, treq_client)

    def relative_url(self, path):
        """Get a URL relative to the base URL."""
        return self._base_url.click(path)

    def _get_headers(self, headers):  # type: (Optional[Headers]) -> Headers
        """Return the basic headers to be used by default."""
        if headers is None:
            headers = Headers()
        headers.addRawHeader(
            "Authorization",
            swissnum_auth_header(self._swissnum),
        )
        return headers

    def request(
        self,
        method,
        url,
        lease_renew_secret=None,
        lease_cancel_secret=None,
        upload_secret=None,
        write_enabler_secret=None,
        headers=None,
        message_to_serialize=None,
        **kwargs,
    ):
        """
        Like ``treq.request()``, but with optional secrets that get translated
        into corresponding HTTP headers.

        If ``message_to_serialize`` is set, it will be serialized (by default
        with CBOR) and set as the request body.
        """
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
            kwargs["data"] = dumps(message_to_serialize)
            headers.addRawHeader("Content-Type", CBOR_MIME_TYPE)

        return self._treq.request(method, url, headers=headers, **kwargs)


class StorageClientGeneral(object):
    """
    High-level HTTP APIs that aren't immutable- or mutable-specific.
    """

    def __init__(self, client):  # type: (StorageClient) -> None
        self._client = client

    @inlineCallbacks
    def get_version(self):
        """
        Return the version metadata for the server.
        """
        url = self._client.relative_url("/v1/version")
        response = yield self._client.request("GET", url)
        decoded_response = yield _decode_cbor(response, _SCHEMAS["get_version"])
        returnValue(decoded_response)

    @inlineCallbacks
    def add_or_renew_lease(
        self, storage_index: bytes, renew_secret: bytes, cancel_secret: bytes
    ) -> Deferred[None]:
        """
        Add or renew a lease.

        If the renewal secret matches an existing lease, it is renewed.
        Otherwise a new lease is added.
        """
        url = self._client.relative_url(
            "/v1/lease/{}".format(_encode_si(storage_index))
        )
        response = yield self._client.request(
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


@inlineCallbacks
def read_share_chunk(
    client: StorageClient,
    share_type: str,
    storage_index: bytes,
    share_number: int,
    offset: int,
    length: int,
) -> Deferred[bytes]:
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
        "/v1/{}/{}/{}".format(share_type, _encode_si(storage_index), share_number)
    )
    response = yield client.request(
        "GET",
        url,
        headers=Headers(
            # Ranges in HTTP are _inclusive_, Python's convention is exclusive,
            # but Range constructor does that the conversion for us.
            {"range": [Range("bytes", [(offset, offset + length)]).to_header()]}
        ),
    )

    if response.code == http.NO_CONTENT:
        return b""

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
        body = yield limited_content(response, supposed_length)
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
):
    assert isinstance(reason, str)
    url = client.relative_url(
        "/v1/{}/{}/{}/corrupt".format(
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


@define
class StorageClientImmutables(object):
    """
    APIs for interacting with immutables.
    """

    _client: StorageClient

    @inlineCallbacks
    def create(
        self,
        storage_index,
        share_numbers,
        allocated_size,
        upload_secret,
        lease_renew_secret,
        lease_cancel_secret,
    ):  # type: (bytes, set[int], int, bytes, bytes, bytes) -> Deferred[ImmutableCreateResult]
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
        url = self._client.relative_url("/v1/immutable/" + _encode_si(storage_index))
        message = {"share-numbers": share_numbers, "allocated-size": allocated_size}

        response = yield self._client.request(
            "POST",
            url,
            lease_renew_secret=lease_renew_secret,
            lease_cancel_secret=lease_cancel_secret,
            upload_secret=upload_secret,
            message_to_serialize=message,
        )
        decoded_response = yield _decode_cbor(response, _SCHEMAS["allocate_buckets"])
        returnValue(
            ImmutableCreateResult(
                already_have=decoded_response["already-have"],
                allocated=decoded_response["allocated"],
            )
        )

    @inlineCallbacks
    def abort_upload(
        self, storage_index: bytes, share_number: int, upload_secret: bytes
    ) -> Deferred[None]:
        """Abort the upload."""
        url = self._client.relative_url(
            "/v1/immutable/{}/{}/abort".format(_encode_si(storage_index), share_number)
        )
        response = yield self._client.request(
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

    @inlineCallbacks
    def write_share_chunk(
        self, storage_index, share_number, upload_secret, offset, data
    ):  # type: (bytes, int, bytes, int, bytes) -> Deferred[UploadProgress]
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
        url = self._client.relative_url(
            "/v1/immutable/{}/{}".format(_encode_si(storage_index), share_number)
        )
        response = yield self._client.request(
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
        body = yield _decode_cbor(response, _SCHEMAS["immutable_write_share_chunk"])
        remaining = RangeMap()
        for chunk in body["required"]:
            remaining.set(True, chunk["begin"], chunk["end"])
        returnValue(UploadProgress(finished=finished, required=remaining))

    def read_share_chunk(
        self, storage_index, share_number, offset, length
    ):  # type: (bytes, int, int, int) -> Deferred[bytes]
        """
        Download a chunk of data from a share.
        """
        return read_share_chunk(
            self._client, "immutable", storage_index, share_number, offset, length
        )

    @inlineCallbacks
    def list_shares(self, storage_index: bytes) -> Deferred[set[int]]:
        """
        Return the set of shares for a given storage index.
        """
        url = self._client.relative_url(
            "/v1/immutable/{}/shares".format(_encode_si(storage_index))
        )
        response = yield self._client.request(
            "GET",
            url,
        )
        if response.code == http.OK:
            body = yield _decode_cbor(response, _SCHEMAS["list_shares"])
            returnValue(set(body))
        else:
            raise ClientException(response.code)

    def advise_corrupt_share(
        self,
        storage_index: bytes,
        share_number: int,
        reason: str,
    ):
        """Indicate a share has been corrupted, with a human-readable message."""
        return advise_corrupt_share(
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
        url = self._client.relative_url(
            "/v1/mutable/{}/read-test-write".format(_encode_si(storage_index))
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
            result = await _decode_cbor(response, _SCHEMAS["mutable_read_test_write"])
            return ReadTestWriteResult(success=result["success"], reads=result["data"])
        else:
            raise ClientException(response.code, (await response.content()))

    def read_share_chunk(
        self,
        storage_index: bytes,
        share_number: int,
        offset: int,
        length: int,
    ) -> Deferred[bytes]:
        """
        Download a chunk of data from a share.
        """
        return read_share_chunk(
            self._client, "mutable", storage_index, share_number, offset, length
        )

    @async_to_deferred
    async def list_shares(self, storage_index: bytes) -> set[int]:
        """
        List the share numbers for a given storage index.
        """
        url = self._client.relative_url(
            "/v1/mutable/{}/shares".format(_encode_si(storage_index))
        )
        response = await self._client.request("GET", url)
        if response.code == http.OK:
            return await _decode_cbor(response, _SCHEMAS["mutable_list_shares"])
        else:
            raise ClientException(response.code)

    def advise_corrupt_share(
        self,
        storage_index: bytes,
        share_number: int,
        reason: str,
    ):
        """Indicate a share has been corrupted, with a human-readable message."""
        return advise_corrupt_share(
            self._client, "mutable", storage_index, share_number, reason
        )
