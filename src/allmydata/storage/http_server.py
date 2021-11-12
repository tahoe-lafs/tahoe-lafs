"""
HTTP server for storage.
"""

from functools import wraps

from klein import Klein
from twisted.web import http

# TODO Make sure to use pure Python versions?
from cbor2 import loads, dumps

from .server import StorageServer


def _authorization_decorator(f):
    """
    Check the ``Authorization`` header, and (TODO: in later revision of code)
    extract ``X-Tahoe-Authorization`` headers and pass them in.
    """

    @wraps(f)
    def route(self, request, *args, **kwargs):
        if (
            request.requestHeaders.getRawHeaders("Authorization", [None])[0]
            != self._swissnum
        ):
            request.setResponseCode(http.NOT_ALLOWED)
            return b""
        # authorization = request.requestHeaders.getRawHeaders("X-Tahoe-Authorization", [])
        # For now, just a placeholder:
        authorization = None
        return f(self, request, authorization, *args, **kwargs)

    return route


def _route(app, *route_args, **route_kwargs):
    """
    Like Klein's @route, but with additional support for checking the
    ``Authorization`` header as well as ``X-Tahoe-Authorization`` headers.  The
    latter will (TODO: in later revision of code) get passed in as second
    argument to wrapped functions.
    """

    def decorator(f):
        @app.route(*route_args, **route_kwargs)
        @_authorization_decorator
        def handle_route(*args, **kwargs):
            return f(*args, **kwargs)

        return handle_route

    return decorator


class HTTPServer(object):
    """
    A HTTP interface to the storage server.

    TODO returning CBOR should set CBOR content-type
    """

    _app = Klein()

    def __init__(self, storage_server: StorageServer, swissnum):
        self._storage_server = storage_server
        self._swissnum = swissnum

    def get_resource(self):
        """Return twisted.web Resource for this object."""
        return self._app.resource()

    @_route(_app, "/v1/version", methods=["GET"])
    def version(self, request, authorization):
        return dumps(self._storage_server.remote_get_version())
