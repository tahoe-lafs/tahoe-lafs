"""
HTTP server for storage.
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
else:
    from typing import Dict, List, Set

from functools import wraps
from base64 import b64decode

from klein import Klein
from twisted.web import http
import attr
from werkzeug.http import parse_range_header, parse_content_range_header
from werkzeug.routing import BaseConverter
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

    # Mape share number to the upload secret (different shares might have
    # different upload secrets).
    upload_secrets = attr.ib(factory=dict)  # type: Dict[int,bytes]

    def add_upload(self, share_number, upload_secret, bucket):
        self.shares[share_number] = bucket
        self.upload_secrets[share_number] = upload_secret


class StorageIndexConverter(BaseConverter):
    """Parser/validator for storage index URL path segments."""

    regex = "[" + str(rfc3548_alphabet, "ascii") + "]{26}"

    def to_python(self, value):
        return si_a2b(value.encode("ascii"))


class HTTPServer(object):
    """
    A HTTP interface to the storage server.
    """

    _app = Klein()
    _app.url_map.converters["storage_index"] = StorageIndexConverter

    def __init__(
        self, storage_server, swissnum
    ):  # type: (StorageServer, bytes) -> None
        self._storage_server = storage_server
        self._swissnum = swissnum
        # Maps storage index to StorageIndexUploads:
        self._uploads = {}  # type: Dict[bytes,StorageIndexUploads]

    def get_resource(self):
        """Return twisted.web ``Resource`` for this object."""
        return self._app.resource()

    def _cbor(self, request, data):
        """Return CBOR-encoded data."""
        # TODO Might want to optionally send JSON someday, based on Accept
        # headers, see https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3861
        request.setHeader("Content-Type", "application/cbor")
        # TODO if data is big, maybe want to use a temporary file eventually...
        return dumps(data)

    ##### Generic APIs #####

    @_authorized_route(_app, set(), "/v1/version", methods=["GET"])
    def version(self, request, authorization):
        """Return version information."""
        return self._cbor(request, self._storage_server.get_version())

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

        if storage_index in self._uploads:
            for share_number in info["share-numbers"]:
                in_progress = self._uploads[storage_index]
                # For pre-existing upload, make sure password matches.
                if (
                    share_number in in_progress.upload_secrets
                    and not timing_safe_compare(
                        in_progress.upload_secrets[share_number], upload_secret
                    )
                ):
                    request.setResponseCode(http.UNAUTHORIZED)
                    return b""

        already_got, sharenum_to_bucket = self._storage_server.allocate_buckets(
            storage_index,
            renew_secret=authorization[Secrets.LEASE_RENEW],
            cancel_secret=authorization[Secrets.LEASE_CANCEL],
            sharenums=info["share-numbers"],
            allocated_size=info["allocated-size"],
        )
        uploads = self._uploads.setdefault(storage_index, StorageIndexUploads())
        for share_number, bucket in sharenum_to_bucket.items():
            uploads.add_upload(share_number, upload_secret, bucket)

        return self._cbor(
            request,
            {
                "already-have": set(already_got),
                "allocated": set(sharenum_to_bucket),
            },
        )

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
        try:
            bucket = self._uploads[storage_index].shares[share_number]
        except (KeyError, IndexError):
            request.setResponseCode(http.NOT_FOUND)
            return b""

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
        return self._cbor(request, {"required": required})

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
        return self._cbor(request, share_numbers)

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
        request.setHeader(
            "content-range",
            ContentRange("bytes", offset, offset + len(data)).to_header(),
        )
        return data
