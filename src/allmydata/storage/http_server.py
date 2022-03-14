"""
HTTP server for storage.
"""

from typing import Dict, List, Set, Tuple

from functools import wraps
from base64 import b64decode
import binascii

from klein import Klein
from twisted.web import http
import attr
from werkzeug.http import (
    parse_range_header,
    parse_content_range_header,
    parse_accept_header,
)
from werkzeug.routing import BaseConverter, ValidationError
from werkzeug.datastructures import ContentRange

# TODO Make sure to use pure Python versions?
from cbor2 import dumps, loads

from .server import StorageServer
from .http_common import swissnum_auth_header, Secrets
from .common import si_a2b
from .immutable import BucketWriter, ConflictingWriteError
from ..util.hashutil import timing_safe_compare
from ..util.base32 import rfc3548_alphabet


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
                request.requestHeaders.getRawHeaders("Authorization", [None])[0].encode(
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
                request.setResponseCode(400)
                return b""
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


@attr.s
class StorageIndexUploads(object):
    """
    In-progress upload to storage index.
    """

    # Map share number to BucketWriter
    shares = attr.ib(factory=dict)  # type: Dict[int,BucketWriter]

    # Map share number to the upload secret (different shares might have
    # different upload secrets).
    upload_secrets = attr.ib(factory=dict)  # type: Dict[int,bytes]


@attr.s
class UploadsInProgress(object):
    """
    Keep track of uploads for storage indexes.
    """

    # Map storage index to corresponding uploads-in-progress
    _uploads = attr.ib(type=Dict[bytes, StorageIndexUploads], factory=dict)

    # Map BucketWriter to (storage index, share number)
    _bucketwriters = attr.ib(type=Dict[BucketWriter, Tuple[bytes, int]], factory=dict)

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
        storage_index, share_number = self._bucketwriters.pop(bucket)
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
        """Return encoded data, by default using CBOR."""
        cbor_mime = "application/cbor"
        accept_headers = request.requestHeaders.getRawHeaders("accept") or [cbor_mime]
        accept = parse_accept_header(accept_headers[0])
        if accept.best == cbor_mime:
            request.setHeader("Content-Type", cbor_mime)
            # TODO if data is big, maybe want to use a temporary file eventually...
            # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3872
            return dumps(data)
        else:
            # TODO Might want to optionally send JSON someday:
            # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3861
            raise _HTTPError(http.NOT_ACCEPTABLE)

    ##### Generic APIs #####

    @_authorized_route(_app, set(), "/v1/version", methods=["GET"])
    def version(self, request, authorization):
        """Return version information."""
        return self._send_encoded(request, self._storage_server.get_version())

    ##### Immutable APIs #####

    @_authorized_route(
        _app,
        {Secrets.LEASE_RENEW, Secrets.LEASE_CANCEL, Secrets.UPLOAD},
        "/v1/immutable/<storage_index:storage_index>",
        methods=["POST"],
    )
    def allocate_buckets(self, request, authorization, storage_index):
        """Allocate buckets."""
        upload_secret = authorization[Secrets.UPLOAD]
        info = loads(request.content.read())

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
            {
                "already-have": set(already_got),
                "allocated": set(sharenum_to_bucket),
            },
        )

    @_authorized_route(
        _app,
        {Secrets.UPLOAD},
        "/v1/immutable/<storage_index:storage_index>/<int(signed=False):share_number>/abort",
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
        "/v1/immutable/<storage_index:storage_index>/<int(signed=False):share_number>",
        methods=["PATCH"],
    )
    def write_share_data(self, request, authorization, storage_index, share_number):
        """Write data to an in-progress immutable upload."""
        content_range = parse_content_range_header(request.getHeader("content-range"))
        if content_range is None or content_range.units != "bytes":
            request.setResponseCode(http.REQUESTED_RANGE_NOT_SATISFIABLE)
            return b""

        offset = content_range.start

        # TODO limit memory usage
        # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3872
        data = request.content.read(content_range.stop - content_range.start + 1)
        bucket = self._uploads.get_write_bucket(
            storage_index, share_number, authorization[Secrets.UPLOAD]
        )

        try:
            finished = bucket.write(offset, data)
        except ConflictingWriteError:
            request.setResponseCode(http.CONFLICT)
            return b""

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
        "/v1/immutable/<storage_index:storage_index>/shares",
        methods=["GET"],
    )
    def list_shares(self, request, authorization, storage_index):
        """
        List shares for the given storage index.
        """
        share_numbers = list(self._storage_server.get_buckets(storage_index).keys())
        return self._send_encoded(request, share_numbers)

    @_authorized_route(
        _app,
        set(),
        "/v1/immutable/<storage_index:storage_index>/<int(signed=False):share_number>",
        methods=["GET"],
    )
    def read_share_chunk(self, request, authorization, storage_index, share_number):
        """Read a chunk for an already uploaded immutable."""
        try:
            bucket = self._storage_server.get_buckets(storage_index)[share_number]
        except KeyError:
            request.setResponseCode(http.NOT_FOUND)
            return b""

        if request.getHeader("range") is None:
            # Return the whole thing.
            start = 0
            while True:
                # TODO should probably yield to event loop occasionally...
                # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3872
                data = bucket.read(start, start + 65536)
                if not data:
                    request.finish()
                    return
                request.write(data)
                start += len(data)

        range_header = parse_range_header(request.getHeader("range"))
        if (
            range_header is None
            or range_header.units != "bytes"
            or len(range_header.ranges) > 1  # more than one range
            or range_header.ranges[0][1] is None  # range without end
        ):
            request.setResponseCode(http.REQUESTED_RANGE_NOT_SATISFIABLE)
            return b""

        offset, end = range_header.ranges[0]

        # TODO limit memory usage
        # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3872
        data = bucket.read(offset, end - offset)

        request.setResponseCode(http.PARTIAL_CONTENT)
        if len(data):
            # For empty bodies the content-range header makes no sense since
            # the end of the range is inclusive.
            request.setHeader(
                "content-range",
                ContentRange("bytes", offset, offset + len(data)).to_header(),
            )
        return data

    @_authorized_route(
        _app,
        {Secrets.LEASE_RENEW, Secrets.LEASE_CANCEL},
        "/v1/lease/<storage_index:storage_index>",
        methods=["PUT"],
    )
    def add_or_renew_lease(self, request, authorization, storage_index):
        """Update the lease for an immutable share."""
        if not self._storage_server.get_buckets(storage_index):
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
        "/v1/immutable/<storage_index:storage_index>/<int(signed=False):share_number>/corrupt",
        methods=["POST"],
    )
    def advise_corrupt_share(self, request, authorization, storage_index, share_number):
        """Indicate that given share is corrupt, with a text reason."""
        try:
            bucket = self._storage_server.get_buckets(storage_index)[share_number]
        except KeyError:
            raise _HTTPError(http.NOT_FOUND)

        info = loads(request.content.read())
        bucket.advise_corrupt_share(info["reason"].encode("utf-8"))
        return b""
