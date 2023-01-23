"""
HTTP server for storage.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple, Any, Callable, Union, cast
from functools import wraps
from base64 import b64decode
import binascii
from tempfile import TemporaryFile
from os import SEEK_END, SEEK_SET
import mmap
from importlib.metadata import version as get_package_version, PackageNotFoundError

from cryptography.x509 import Certificate as CryptoCertificate
from zope.interface import implementer
from klein import Klein
from twisted.web import http
from twisted.internet.interfaces import (
    IListeningPort,
    IStreamServerEndpoint,
    IPullProducer,
)
from twisted.internet import reactor
from twisted.internet.task import deferToThread
from twisted.internet.address import IPv4Address, IPv6Address
from twisted.internet.defer import Deferred
from twisted.internet.ssl import CertificateOptions, Certificate, PrivateCertificate
from twisted.web.server import Site, Request
from twisted.protocols.tls import TLSMemoryBIOFactory
from twisted.python.filepath import FilePath

from attrs import define, field, Factory
from werkzeug.http import (
    parse_range_header,
    parse_content_range_header,
    parse_accept_header,
)
from werkzeug.routing import BaseConverter, ValidationError
from werkzeug.datastructures import ContentRange
from hyperlink import DecodedURL
from cryptography.x509 import load_pem_x509_certificate


# TODO Make sure to use pure Python versions?
import cbor2
from pycddl import Schema, ValidationError as CDDLValidationError
from .server import StorageServer
from .http_common import (
    swissnum_auth_header,
    Secrets,
    get_content_type,
    CBOR_MIME_TYPE,
    get_spki_hash,
)

from .common import si_a2b
from .immutable import BucketWriter, ConflictingWriteError
from ..util.hashutil import timing_safe_compare
from ..util.base32 import rfc3548_alphabet
from ..util.deferredutil import async_to_deferred
from allmydata.interfaces import BadWriteEnablerError


# Until we figure out Nix (https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3963),
# need to support old pycddl which can only take bytes:
from distutils.version import LooseVersion

try:
    PYCDDL_BYTES_ONLY = LooseVersion(get_package_version("pycddl")) < LooseVersion(
        "0.4"
    )
except PackageNotFoundError:
    # This can happen when building PyInstaller distribution. We'll just assume
    # you installed a modern pycddl, cause why wouldn't you?
    PYCDDL_BYTES_ONLY = False


class ClientSecretsException(Exception):
    """The client did not send the appropriate secrets."""


def _extract_secrets(
    header_values, required_secrets
):  # type: (List[str], Set[Secrets]) -> Dict[Secrets, bytes]
    """
    Given list of values of ``X-Tahoe-Authorization`` headers, and required
    secrets, return dictionary mapping secrets to decoded values.

    If too few secrets were given, or too many, a ``ClientSecretsException`` is
    raised.
    """
    string_key_to_enum = {e.value: e for e in Secrets}
    result = {}
    try:
        for header_value in header_values:
            string_key, string_value = header_value.strip().split(" ", 1)
            key = string_key_to_enum[string_key]
            value = b64decode(string_value)
            if key in (Secrets.LEASE_CANCEL, Secrets.LEASE_RENEW) and len(value) != 32:
                raise ClientSecretsException("Lease secrets must be 32 bytes long")
            result[key] = value
    except (ValueError, KeyError):
        raise ClientSecretsException("Bad header value(s): {}".format(header_values))
    if result.keys() != required_secrets:
        raise ClientSecretsException(
            "Expected {} secrets, got {}".format(required_secrets, result.keys())
        )
    return result


def _authorization_decorator(required_secrets):
    """
    Check the ``Authorization`` header, and extract ``X-Tahoe-Authorization``
    headers and pass them in.
    """

    def decorator(f):
        @wraps(f)
        def route(self, request, *args, **kwargs):
            if not timing_safe_compare(
                request.requestHeaders.getRawHeaders("Authorization", [""])[0].encode(
                    "utf-8"
                ),
                swissnum_auth_header(self._swissnum),
            ):
                request.setResponseCode(http.UNAUTHORIZED)
                return b""
            authorization = request.requestHeaders.getRawHeaders(
                "X-Tahoe-Authorization", []
            )
            try:
                secrets = _extract_secrets(authorization, required_secrets)
            except ClientSecretsException:
                request.setResponseCode(http.BAD_REQUEST)
                return b"Missing required secrets"
            return f(self, request, secrets, *args, **kwargs)

        return route

    return decorator


def _authorized_route(app, required_secrets, *route_args, **route_kwargs):
    """
    Like Klein's @route, but with additional support for checking the
    ``Authorization`` header as well as ``X-Tahoe-Authorization`` headers.  The
    latter will get passed in as second argument to wrapped functions, a
    dictionary mapping a ``Secret`` value to the uploaded secret.

    :param required_secrets: Set of required ``Secret`` types.
    """

    def decorator(f):
        @app.route(*route_args, **route_kwargs)
        @_authorization_decorator(required_secrets)
        @wraps(f)
        def handle_route(*args, **kwargs):
            return f(*args, **kwargs)

        return handle_route

    return decorator


@define
class StorageIndexUploads(object):
    """
    In-progress upload to storage index.
    """

    # Map share number to BucketWriter
    shares: dict[int, BucketWriter] = Factory(dict)

    # Map share number to the upload secret (different shares might have
    # different upload secrets).
    upload_secrets: dict[int, bytes] = Factory(dict)


@define
class UploadsInProgress(object):
    """
    Keep track of uploads for storage indexes.
    """

    # Map storage index to corresponding uploads-in-progress
    _uploads: dict[bytes, StorageIndexUploads] = Factory(dict)

    # Map BucketWriter to (storage index, share number)
    _bucketwriters: dict[BucketWriter, Tuple[bytes, int]] = Factory(dict)

    def add_write_bucket(
        self,
        storage_index: bytes,
        share_number: int,
        upload_secret: bytes,
        bucket: BucketWriter,
    ):
        """Add a new ``BucketWriter`` to be tracked."""
        si_uploads = self._uploads.setdefault(storage_index, StorageIndexUploads())
        si_uploads.shares[share_number] = bucket
        si_uploads.upload_secrets[share_number] = upload_secret
        self._bucketwriters[bucket] = (storage_index, share_number)

    def get_write_bucket(
        self, storage_index: bytes, share_number: int, upload_secret: bytes
    ) -> BucketWriter:
        """Get the given in-progress immutable share upload."""
        self.validate_upload_secret(storage_index, share_number, upload_secret)
        try:
            return self._uploads[storage_index].shares[share_number]
        except (KeyError, IndexError):
            raise _HTTPError(http.NOT_FOUND)

    def remove_write_bucket(self, bucket: BucketWriter):
        """Stop tracking the given ``BucketWriter``."""
        try:
            storage_index, share_number = self._bucketwriters.pop(bucket)
        except KeyError:
            # This is probably a BucketWriter created by Foolscap, so just
            # ignore it.
            return
        uploads_index = self._uploads[storage_index]
        uploads_index.shares.pop(share_number)
        uploads_index.upload_secrets.pop(share_number)
        if not uploads_index.shares:
            self._uploads.pop(storage_index)

    def validate_upload_secret(
        self, storage_index: bytes, share_number: int, upload_secret: bytes
    ):
        """
        Raise an unauthorized-HTTP-response exception if the given
        storage_index+share_number have a different upload secret than the
        given one.

        If the given upload doesn't exist at all, nothing happens.
        """
        if storage_index in self._uploads:
            in_progress = self._uploads[storage_index]
            # For pre-existing upload, make sure password matches.
            if share_number in in_progress.upload_secrets and not timing_safe_compare(
                in_progress.upload_secrets[share_number], upload_secret
            ):
                raise _HTTPError(http.UNAUTHORIZED)


class StorageIndexConverter(BaseConverter):
    """Parser/validator for storage index URL path segments."""

    regex = "[" + str(rfc3548_alphabet, "ascii") + "]{26}"

    def to_python(self, value):
        try:
            return si_a2b(value.encode("ascii"))
        except (AssertionError, binascii.Error, ValueError):
            raise ValidationError("Invalid storage index")


class _HTTPError(Exception):
    """
    Raise from ``HTTPServer`` endpoint to return the given HTTP response code.
    """

    def __init__(self, code: int):
        self.code = code


# CDDL schemas.
#
# Tags are of the form #6.nnn, where the number is documented at
# https://www.iana.org/assignments/cbor-tags/cbor-tags.xhtml. Notably, #6.258
# indicates a set.
#
# Somewhat arbitrary limits are set to reduce e.g. number of shares, number of
# vectors, etc.. These may need to be iterated on in future revisions of the
# code.
_SCHEMAS = {
    "allocate_buckets": Schema(
        """
    request = {
      share-numbers: #6.258([0*256 uint])
      allocated-size: uint
    }
    """
    ),
    "advise_corrupt_share": Schema(
        """
    request = {
      reason: tstr
    }
    """
    ),
    "mutable_read_test_write": Schema(
        """
        request = {
            "test-write-vectors": {
                0*256 share_number : {
                    "test": [0*30 {"offset": uint, "size": uint, "specimen": bstr}]
                    "write": [* {"offset": uint, "data": bstr}]
                    "new-length": uint / null
                }
            }
            "read-vector": [0*30 {"offset": uint, "size": uint}]
        }
        share_number = uint
        """
    ),
}


# Callable that takes offset and length, returns the data at that range.
ReadData = Callable[[int, int], bytes]


@implementer(IPullProducer)
@define
class _ReadAllProducer:
    """
    Producer that calls a read function repeatedly to read all the data, and
    writes to a request.
    """

    request: Request
    read_data: ReadData
    result: Deferred = Factory(Deferred)
    start: int = field(default=0)

    @classmethod
    def produce_to(cls, request: Request, read_data: ReadData) -> Deferred:
        """
        Create and register the producer, returning ``Deferred`` that should be
        returned from a HTTP server endpoint.
        """
        producer = cls(request, read_data)
        request.registerProducer(producer, False)
        return producer.result

    def resumeProducing(self):
        data = self.read_data(self.start, 65536)
        if not data:
            self.request.unregisterProducer()
            d = self.result
            del self.result
            d.callback(b"")
            return
        self.request.write(data)
        self.start += len(data)

    def pauseProducing(self):
        pass

    def stopProducing(self):
        pass


@implementer(IPullProducer)
@define
class _ReadRangeProducer:
    """
    Producer that calls a read function to read a range of data, and writes to
    a request.
    """

    request: Request
    read_data: ReadData
    result: Deferred
    start: int
    remaining: int

    def resumeProducing(self):
        to_read = min(self.remaining, 65536)
        data = self.read_data(self.start, to_read)
        assert len(data) <= to_read

        if not data and self.remaining > 0:
            d, self.result = self.result, None
            d.errback(
                ValueError(
                    f"Should be {self.remaining} bytes left, but we got an empty read"
                )
            )
            self.stopProducing()
            return

        if len(data) > self.remaining:
            d, self.result = self.result, None
            d.errback(
                ValueError(
                    f"Should be {self.remaining} bytes left, but we got more than that ({len(data)})!"
                )
            )
            self.stopProducing()
            return

        self.start += len(data)
        self.remaining -= len(data)
        assert self.remaining >= 0

        self.request.write(data)

        if self.remaining == 0:
            self.stopProducing()

    def pauseProducing(self):
        pass

    def stopProducing(self):
        if self.request is not None:
            self.request.unregisterProducer()
            self.request = None
        if self.result is not None:
            d = self.result
            self.result = None
            d.callback(b"")


def read_range(
    request: Request, read_data: ReadData, share_length: int
) -> Union[Deferred, bytes]:
    """
    Read an optional ``Range`` header, reads data appropriately via the given
    callable, writes the data to the request.

    Only parses a subset of ``Range`` headers that we support: must be set,
    bytes only, only a single range, the end must be explicitly specified.
    Raises a ``_HTTPError(http.REQUESTED_RANGE_NOT_SATISFIABLE)`` if parsing is
    not possible or the header isn't set.

    Takes a function that will do the actual reading given the start offset and
    a length to read.

    The resulting data is written to the request.
    """

    def read_data_with_error_handling(offset: int, length: int) -> bytes:
        try:
            return read_data(offset, length)
        except _HTTPError as e:
            request.setResponseCode(e.code)
            # Empty read means we're done.
            return b""

    if request.getHeader("range") is None:
        return _ReadAllProducer.produce_to(request, read_data_with_error_handling)

    range_header = parse_range_header(request.getHeader("range"))
    if (
        range_header is None  # failed to parse
        or range_header.units != "bytes"
        or len(range_header.ranges) > 1  # more than one range
        or range_header.ranges[0][1] is None  # range without end
    ):
        raise _HTTPError(http.REQUESTED_RANGE_NOT_SATISFIABLE)

    offset, end = range_header.ranges[0]
    # If we're being ask to read beyond the length of the share, just read
    # less:
    end = min(end, share_length)
    if offset >= end:
        # Basically we'd need to return an empty body. However, the
        # Content-Range header can't actually represent empty lengths... so
        # (mis)use 204 response code to indicate that.
        raise _HTTPError(http.NO_CONTENT)

    request.setResponseCode(http.PARTIAL_CONTENT)

    # Actual conversion from Python's exclusive ranges to inclusive ranges is
    # handled by werkzeug.
    request.setHeader(
        "content-range",
        ContentRange("bytes", offset, end).to_header(),
    )

    d = Deferred()
    request.registerProducer(
        _ReadRangeProducer(
            request, read_data_with_error_handling, d, offset, end - offset
        ),
        False,
    )
    return d


class HTTPServer(object):
    """
    A HTTP interface to the storage server.
    """

    _app = Klein()
    _app.url_map.converters["storage_index"] = StorageIndexConverter

    @_app.handle_errors(_HTTPError)
    def _http_error(self, request, failure):
        """Handle ``_HTTPError`` exceptions."""
        request.setResponseCode(failure.value.code)
        return b""

    @_app.handle_errors(CDDLValidationError)
    def _cddl_validation_error(self, request, failure):
        """Handle CDDL validation errors."""
        request.setResponseCode(http.BAD_REQUEST)
        return str(failure.value).encode("utf-8")

    def __init__(
        self, storage_server, swissnum
    ):  # type: (StorageServer, bytes) -> None
        self._storage_server = storage_server
        self._swissnum = swissnum
        # Maps storage index to StorageIndexUploads:
        self._uploads = UploadsInProgress()

        # When an upload finishes successfully, gets aborted, or times out,
        # make sure it gets removed from our tracking datastructure:
        self._storage_server.register_bucket_writer_close_handler(
            self._uploads.remove_write_bucket
        )

    def get_resource(self):
        """Return twisted.web ``Resource`` for this object."""
        return self._app.resource()

    def _send_encoded(self, request, data):
        """
        Return encoded data suitable for writing as the HTTP body response, by
        default using CBOR.

        Also sets the appropriate ``Content-Type`` header on the response.
        """
        accept_headers = request.requestHeaders.getRawHeaders("accept") or [
            CBOR_MIME_TYPE
        ]
        accept = parse_accept_header(accept_headers[0])
        if accept.best == CBOR_MIME_TYPE:
            request.setHeader("Content-Type", CBOR_MIME_TYPE)
            f = TemporaryFile()
            cbor2.dump(data, f)

            def read_data(offset: int, length: int) -> bytes:
                f.seek(offset)
                return f.read(length)

            return _ReadAllProducer.produce_to(request, read_data)
        else:
            # TODO Might want to optionally send JSON someday:
            # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3861
            raise _HTTPError(http.NOT_ACCEPTABLE)

    async def _read_encoded(
        self, request, schema: Schema, max_size: int = 1024 * 1024
    ) -> Any:
        """
        Read encoded request body data, decoding it with CBOR by default.

        Somewhat arbitrarily, limit body size to 1MiB by default.
        """
        content_type = get_content_type(request.requestHeaders)
        if content_type != CBOR_MIME_TYPE:
            raise _HTTPError(http.UNSUPPORTED_MEDIA_TYPE)

        # Make sure it's not too large:
        request.content.seek(SEEK_END, 0)
        size = request.content.tell()
        if size > max_size:
            raise _HTTPError(http.REQUEST_ENTITY_TOO_LARGE)
        request.content.seek(SEEK_SET, 0)

        # We don't want to load the whole message into memory, cause it might
        # be quite large. The CDDL validator takes a read-only bytes-like
        # thing. Luckily, for large request bodies twisted.web will buffer the
        # data in a file, so we can use mmap() to get a memory view. The CDDL
        # validator will not make a copy, so it won't increase memory usage
        # beyond that.
        try:
            fd = request.content.fileno()
        except (ValueError, OSError):
            fd = -1
        if fd >= 0 and not PYCDDL_BYTES_ONLY:
            # It's a file, so we can use mmap() to save memory.
            message = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
        else:
            message = request.content.read()

        # Pycddl will release the GIL when validating larger documents, so
        # let's take advantage of multiple CPUs:
        if size > 10_000:
            await deferToThread(reactor, schema.validate_cbor(message))
        else:
            schema.validate_cbor(message)

        # The CBOR parser will allocate more memory, but at least we can feed
        # it the file-like object, so that if it's large it won't be make two
        # copies.
        request.content.seek(SEEK_SET, 0)
        # Typically deserialization to Python will not release the GIL, and
        # indeed as of Jan 2023 cbor2 didn't have any code to release the GIL
        # in the decode path. As such, running it in a different thread has no benefit.
        return cbor2.load(request.content)

    ##### Generic APIs #####

    @_authorized_route(_app, set(), "/storage/v1/version", methods=["GET"])
    def version(self, request, authorization):
        """Return version information."""
        return self._send_encoded(request, self._storage_server.get_version())

    ##### Immutable APIs #####

    @_authorized_route(
        _app,
        {Secrets.LEASE_RENEW, Secrets.LEASE_CANCEL, Secrets.UPLOAD},
        "/storage/v1/immutable/<storage_index:storage_index>",
        methods=["POST"],
    )
    @async_to_deferred
    async def allocate_buckets(self, request, authorization, storage_index):
        """Allocate buckets."""
        upload_secret = authorization[Secrets.UPLOAD]
        info = await self._read_encoded(request, _SCHEMAS["allocate_buckets"])

        # We do NOT validate the upload secret for existing bucket uploads.
        # Another upload may be happening in parallel, with a different upload
        # key. That's fine! If a client tries to _write_ to that upload, they
        # need to have an upload key. That does mean we leak the existence of
        # these parallel uploads, but if you know storage index you can
        # download them once upload finishes, so it's not a big deal to leak
        # that information.

        already_got, sharenum_to_bucket = self._storage_server.allocate_buckets(
            storage_index,
            renew_secret=authorization[Secrets.LEASE_RENEW],
            cancel_secret=authorization[Secrets.LEASE_CANCEL],
            sharenums=info["share-numbers"],
            allocated_size=info["allocated-size"],
        )
        for share_number, bucket in sharenum_to_bucket.items():
            self._uploads.add_write_bucket(
                storage_index, share_number, upload_secret, bucket
            )

        return self._send_encoded(
            request,
            {"already-have": set(already_got), "allocated": set(sharenum_to_bucket)},
        )

    @_authorized_route(
        _app,
        {Secrets.UPLOAD},
        "/storage/v1/immutable/<storage_index:storage_index>/<int(signed=False):share_number>/abort",
        methods=["PUT"],
    )
    def abort_share_upload(self, request, authorization, storage_index, share_number):
        """Abort an in-progress immutable share upload."""
        try:
            bucket = self._uploads.get_write_bucket(
                storage_index, share_number, authorization[Secrets.UPLOAD]
            )
        except _HTTPError as e:
            if e.code == http.NOT_FOUND:
                # It may be we've already uploaded this, in which case error
                # should be method not allowed (405).
                try:
                    self._storage_server.get_buckets(storage_index)[share_number]
                except KeyError:
                    pass
                else:
                    # Already uploaded, so we can't abort.
                    raise _HTTPError(http.NOT_ALLOWED)
            raise

        # Abort the upload; this should close it which will eventually result
        # in self._uploads.remove_write_bucket() being called.
        bucket.abort()

        return b""

    @_authorized_route(
        _app,
        {Secrets.UPLOAD},
        "/storage/v1/immutable/<storage_index:storage_index>/<int(signed=False):share_number>",
        methods=["PATCH"],
    )
    def write_share_data(self, request, authorization, storage_index, share_number):
        """Write data to an in-progress immutable upload."""
        content_range = parse_content_range_header(request.getHeader("content-range"))
        if content_range is None or content_range.units != "bytes":
            request.setResponseCode(http.REQUESTED_RANGE_NOT_SATISFIABLE)
            return b""

        bucket = self._uploads.get_write_bucket(
            storage_index, share_number, authorization[Secrets.UPLOAD]
        )
        offset = content_range.start
        remaining = content_range.stop - content_range.start
        finished = False

        while remaining > 0:
            data = request.content.read(min(remaining, 65536))
            assert data, "uploaded data length doesn't match range"

            try:
                finished = bucket.write(offset, data)
            except ConflictingWriteError:
                request.setResponseCode(http.CONFLICT)
                return b""
            remaining -= len(data)
            offset += len(data)

        if finished:
            bucket.close()
            request.setResponseCode(http.CREATED)
        else:
            request.setResponseCode(http.OK)

        required = []
        for start, end, _ in bucket.required_ranges().ranges():
            required.append({"begin": start, "end": end})
        return self._send_encoded(request, {"required": required})

    @_authorized_route(
        _app,
        set(),
        "/storage/v1/immutable/<storage_index:storage_index>/shares",
        methods=["GET"],
    )
    def list_shares(self, request, authorization, storage_index):
        """
        List shares for the given storage index.
        """
        share_numbers = set(self._storage_server.get_buckets(storage_index).keys())
        return self._send_encoded(request, share_numbers)

    @_authorized_route(
        _app,
        set(),
        "/storage/v1/immutable/<storage_index:storage_index>/<int(signed=False):share_number>",
        methods=["GET"],
    )
    def read_share_chunk(self, request, authorization, storage_index, share_number):
        """Read a chunk for an already uploaded immutable."""
        try:
            bucket = self._storage_server.get_buckets(storage_index)[share_number]
        except KeyError:
            request.setResponseCode(http.NOT_FOUND)
            return b""

        return read_range(request, bucket.read, bucket.get_length())

    @_authorized_route(
        _app,
        {Secrets.LEASE_RENEW, Secrets.LEASE_CANCEL},
        "/storage/v1/lease/<storage_index:storage_index>",
        methods=["PUT"],
    )
    def add_or_renew_lease(self, request, authorization, storage_index):
        """Update the lease for an immutable or mutable share."""
        if not list(self._storage_server.get_shares(storage_index)):
            raise _HTTPError(http.NOT_FOUND)

        # Checking of the renewal secret is done by the backend.
        self._storage_server.add_lease(
            storage_index,
            authorization[Secrets.LEASE_RENEW],
            authorization[Secrets.LEASE_CANCEL],
        )

        request.setResponseCode(http.NO_CONTENT)
        return b""

    @_authorized_route(
        _app,
        set(),
        "/storage/v1/immutable/<storage_index:storage_index>/<int(signed=False):share_number>/corrupt",
        methods=["POST"],
    )
    @async_to_deferred
    async def advise_corrupt_share_immutable(
        self, request, authorization, storage_index, share_number
    ):
        """Indicate that given share is corrupt, with a text reason."""
        try:
            bucket = self._storage_server.get_buckets(storage_index)[share_number]
        except KeyError:
            raise _HTTPError(http.NOT_FOUND)

        info = await self._read_encoded(request, _SCHEMAS["advise_corrupt_share"])
        bucket.advise_corrupt_share(info["reason"].encode("utf-8"))
        return b""

    ##### Mutable APIs #####

    @_authorized_route(
        _app,
        {Secrets.LEASE_RENEW, Secrets.LEASE_CANCEL, Secrets.WRITE_ENABLER},
        "/storage/v1/mutable/<storage_index:storage_index>/read-test-write",
        methods=["POST"],
    )
    @async_to_deferred
    async def mutable_read_test_write(self, request, authorization, storage_index):
        """Read/test/write combined operation for mutables."""
        rtw_request = await self._read_encoded(
            request, _SCHEMAS["mutable_read_test_write"], max_size=2**48
        )
        secrets = (
            authorization[Secrets.WRITE_ENABLER],
            authorization[Secrets.LEASE_RENEW],
            authorization[Secrets.LEASE_CANCEL],
        )
        try:
            success, read_data = self._storage_server.slot_testv_and_readv_and_writev(
                storage_index,
                secrets,
                {
                    k: (
                        [
                            (d["offset"], d["size"], b"eq", d["specimen"])
                            for d in v["test"]
                        ],
                        [(d["offset"], d["data"]) for d in v["write"]],
                        v["new-length"],
                    )
                    for (k, v) in rtw_request["test-write-vectors"].items()
                },
                [(d["offset"], d["size"]) for d in rtw_request["read-vector"]],
            )
        except BadWriteEnablerError:
            raise _HTTPError(http.UNAUTHORIZED)
        return self._send_encoded(request, {"success": success, "data": read_data})

    @_authorized_route(
        _app,
        set(),
        "/storage/v1/mutable/<storage_index:storage_index>/<int(signed=False):share_number>",
        methods=["GET"],
    )
    def read_mutable_chunk(self, request, authorization, storage_index, share_number):
        """Read a chunk from a mutable."""

        try:
            share_length = self._storage_server.get_mutable_share_length(
                storage_index, share_number
            )
        except KeyError:
            raise _HTTPError(http.NOT_FOUND)

        def read_data(offset, length):
            try:
                return self._storage_server.slot_readv(
                    storage_index, [share_number], [(offset, length)]
                )[share_number][0]
            except KeyError:
                raise _HTTPError(http.NOT_FOUND)

        return read_range(request, read_data, share_length)

    @_authorized_route(
        _app,
        set(),
        "/storage/v1/mutable/<storage_index:storage_index>/shares",
        methods=["GET"],
    )
    def enumerate_mutable_shares(self, request, authorization, storage_index):
        """List mutable shares for a storage index."""
        shares = self._storage_server.enumerate_mutable_shares(storage_index)
        return self._send_encoded(request, shares)

    @_authorized_route(
        _app,
        set(),
        "/storage/v1/mutable/<storage_index:storage_index>/<int(signed=False):share_number>/corrupt",
        methods=["POST"],
    )
    @async_to_deferred
    async def advise_corrupt_share_mutable(
        self, request, authorization, storage_index, share_number
    ):
        """Indicate that given share is corrupt, with a text reason."""
        if share_number not in {
            shnum for (shnum, _) in self._storage_server.get_shares(storage_index)
        }:
            raise _HTTPError(http.NOT_FOUND)

        info = await self._read_encoded(request, _SCHEMAS["advise_corrupt_share"])
        self._storage_server.advise_corrupt_share(
            b"mutable", storage_index, share_number, info["reason"].encode("utf-8")
        )
        return b""


@implementer(IStreamServerEndpoint)
@define
class _TLSEndpointWrapper(object):
    """
    Wrap an existing endpoint with the server-side storage TLS policy.  This is
    useful because not all Tahoe-LAFS endpoints might be plain TCP+TLS, for
    example there's Tor and i2p.
    """

    endpoint: IStreamServerEndpoint
    context_factory: CertificateOptions

    @classmethod
    def from_paths(
        cls, endpoint, private_key_path: FilePath, cert_path: FilePath
    ) -> "_TLSEndpointWrapper":
        """
        Create an endpoint with the given private key and certificate paths on
        the filesystem.
        """
        certificate = Certificate.loadPEM(cert_path.getContent()).original
        private_key = PrivateCertificate.loadPEM(
            cert_path.getContent() + b"\n" + private_key_path.getContent()
        ).privateKey.original
        certificate_options = CertificateOptions(
            privateKey=private_key, certificate=certificate
        )
        return cls(endpoint=endpoint, context_factory=certificate_options)

    def listen(self, factory):
        return self.endpoint.listen(
            TLSMemoryBIOFactory(self.context_factory, False, factory)
        )


def build_nurl(
    hostname: str, port: int, swissnum: str, certificate: CryptoCertificate
) -> DecodedURL:
    """
    Construct a HTTPS NURL, given the hostname, port, server swissnum, and x509
    certificate for the server.  Clients can then connect to the server using
    this NURL.
    """
    return DecodedURL().replace(
        fragment="v=1",  # how we know this NURL is HTTP-based (i.e. not Foolscap)
        host=hostname,
        port=port,
        path=(swissnum,),
        userinfo=(
            str(
                get_spki_hash(certificate),
                "ascii",
            ),
        ),
        scheme="pb",
    )


def listen_tls(
    server: HTTPServer,
    hostname: str,
    endpoint: IStreamServerEndpoint,
    private_key_path: FilePath,
    cert_path: FilePath,
) -> Deferred[Tuple[DecodedURL, IListeningPort]]:
    """
    Start a HTTPS storage server on the given port, return the NURL and the
    listening port.

    The hostname is the external IP or hostname clients will connect to, used
    to constrtuct the NURL; it does not modify what interfaces the server
    listens on.

    This will likely need to be updated eventually to handle Tor/i2p.
    """
    endpoint = _TLSEndpointWrapper.from_paths(endpoint, private_key_path, cert_path)

    def get_nurl(listening_port: IListeningPort) -> DecodedURL:
        address = cast(Union[IPv4Address, IPv6Address], listening_port.getHost())
        return build_nurl(
            hostname,
            address.port,
            str(server._swissnum, "ascii"),
            load_pem_x509_certificate(cert_path.getContent()),
        )

    return endpoint.listen(Site(server.get_resource())).addCallback(
        lambda listening_port: (get_nurl(listening_port), listening_port)
    )
