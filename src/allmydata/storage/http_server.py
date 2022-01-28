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
from enum import Enum
from base64 import b64decode

from klein import Klein
from twisted.web import http

# TODO Make sure to use pure Python versions?
from cbor2 import dumps

from .server import StorageServer
from .http_client import swissnum_auth_header
from ..util.hashutil import timing_safe_compare


class Secrets(Enum):
    """Different kinds of secrets the client may send."""

    LEASE_RENEW = "lease-renew-secret"
    LEASE_CANCEL = "lease-cancel-secret"
    UPLOAD = "upload-secret"


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
        def handle_route(*args, **kwargs):
            return f(*args, **kwargs)

        return handle_route

    return decorator


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

    def get_resource(self):
        """Return twisted.web ``Resource`` for this object."""
        return self._app.resource()

    def _cbor(self, request, data):
        """Return CBOR-encoded data."""
        request.setHeader("Content-Type", "application/cbor")
        # TODO if data is big, maybe want to use a temporary file eventually...
        return dumps(data)

    ##### Generic APIs #####

    @_authorized_route(_app, set(), "/v1/version", methods=["GET"])
    def version(self, request, authorization):
        """Return version information."""
        return self._cbor(request, self._storage_server.get_version())
