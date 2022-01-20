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

# TODO Make sure to use pure Python versions?
from cbor2 import dumps, loads

from .server import StorageServer
from .http_common import swissnum_auth_header, Secrets
from .common import si_a2b
from .immutable import BucketWriter
from ..util.hashutil import timing_safe_compare


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
    shares = attr.ib()  # type: Dict[int,BucketWriter]

    # The upload key.
    upload_key = attr.ib()  # type: bytes


class HTTPServer(object):
    """
    A HTTP interface to the storage server.
    """

    _app = Klein()

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
        "/v1/immutable/<string:storage_index>",
        methods=["POST"],
    )
    def allocate_buckets(self, request, authorization, storage_index):
        """Allocate buckets."""
        storage_index = si_a2b(storage_index.encode("ascii"))
        info = loads(request.content.read())
        upload_key = authorization[Secrets.UPLOAD]

        if storage_index in self._uploads:
            # Pre-existing upload.
            in_progress = self._uploads[storage_index]
            if in_progress.upload_key == upload_key:
                # Same session.
                # TODO add BucketWriters only for new shares
                pass
            else:
                # TODO Fail, since the secret doesnt match.
                pass
        else:
            # New upload.
            already_got, sharenum_to_bucket = self._storage_server.allocate_buckets(
                storage_index,
                renew_secret=authorization[Secrets.LEASE_RENEW],
                cancel_secret=authorization[Secrets.LEASE_CANCEL],
                sharenums=info["share-numbers"],
                allocated_size=info["allocated-size"],
            )
            self._uploads[storage_index] = StorageIndexUploads(
                shares=sharenum_to_bucket, upload_key=authorization[Secrets.UPLOAD]
            )
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
        "/v1/immutable/<string:storage_index>/<int:share_number>",
        methods=["PATCH"],
    )
    def write_share_data(self, request, authorization, storage_index, share_number):
        """Write data to an in-progress immutable upload."""
        storage_index = si_a2b(storage_index.encode("ascii"))
        content_range = parse_content_range_header(request.getHeader("content-range"))
        # TODO in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        # 1. Malformed header should result in error
        # 2. Non-bytes unit should result in error
        # 3. Missing header means full upload in one request
        offset = content_range.start

        # TODO basic checks on validity of start, offset, and content-range in general. also of share_number.
        # TODO basic check that body isn't infinite. require content-length? or maybe we should require content-range (it's optional now)? if so, needs to be rflected in protocol spec.

        data = request.content.read()
        try:
            bucket = self._uploads[storage_index].shares[share_number]
        except (KeyError, IndexError):
            # TODO return 404
            raise

        finished = bucket.write(offset, data)

        # TODO if raises ConflictingWriteError, return HTTP CONFLICT code.

        if finished:
            bucket.close()
            request.setResponseCode(http.CREATED)
        else:
            request.setResponseCode(http.OK)

        # TODO spec says we should return missing ranges. but client doesn't
        # actually use them? So is it actually useful?
        return b""

    @_authorized_route(
        _app,
        set(),
        "/v1/immutable/<string:storage_index>/<int:share_number>",
        methods=["GET"],
    )
    def read_share_chunk(self, request, authorization, storage_index, share_number):
        """Read a chunk for an already uploaded immutable."""
        # TODO in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860
        # 1. basic checks on validity on storage index, share number
        # 2. missing range header should have response code 200 and return whole thing
        # 3. malformed range header should result in error? or return everything?
        # 4. non-bytes range results in error
        # 5. ranges make sense semantically (positive, etc.)
        # 6. multiple ranges fails with error
        # 7. missing end of range means "to the end of share"
        storage_index = si_a2b(storage_index.encode("ascii"))
        range_header = parse_range_header(request.getHeader("range"))
        offset, end = range_header.ranges[0]
        assert end != None  # TODO support this case

        # TODO if not found, 404
        bucket = self._storage_server.get_buckets(storage_index)[share_number]
        data = bucket.read(offset, end - offset)
        request.setResponseCode(http.PARTIAL_CONTENT)
        return data
