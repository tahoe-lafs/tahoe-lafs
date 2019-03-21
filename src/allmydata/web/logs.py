from __future__ import (
    print_function,
    unicode_literals,
    absolute_import,
    division,
)

import json

from autobahn.twisted.resource import WebSocketResource
from autobahn.twisted.websocket import (
    WebSocketServerFactory,
    WebSocketServerProtocol,
)
from autobahn.websocket.types import ConnectionDeny

import eliot

from twisted.web.resource import (
    Resource,
)

from allmydata.util.hashutil import (
    timing_safe_compare,
)


class TokenAuthenticatedWebSocketServerProtocol(WebSocketServerProtocol):
    """
    A WebSocket protocol that looks for an `Authorization:` header
    with a `tahoe-lafs` scheme and a token matching our private config
    for `api_auth_token`.
    """

    def onConnect(self, req):
        """
        WebSocket callback
        """
        if b'authorization' in req.headers:
            auth = req.headers[b'authorization'].encode('ascii').split(b' ', 1)
            if len(auth) == 2:
                tag, token = auth
                if tag == b"tahoe-lafs":
                    if timing_safe_compare(token, self.factory.tahoe_client.get_auth_token()):
                        # we don't care what WebSocket sub-protocol is
                        # negotiated, nor do we need to send headers to the
                        # client, so we ask Autobahn to just allow this
                        # connection with the defaults. We could return a
                        # (headers, protocol) pair here instead if required.
                        return None

        # everything else -- i.e. no Authorization header, or it's
        # wrong -- means we deny the websocket connection
        raise ConnectionDeny(
            code=ConnectionDeny.NOT_ACCEPTABLE,
            reason=u"Invalid or missing token"
        )

    def _received_eliot_log(self, message):
        """
        While this WebSocket connection is open, this function is
        registered as an eliot destination
        """
        # probably want a try/except around here? what do we do if
        # transmission fails or anything else bad happens?
        self.sendMessage(json.dumps(message))

    def onOpen(self):
        """
        WebSocket callback
        """
        eliot.add_destination(self._received_eliot_log)

    def onClose(self, wasClean, code, reason):
        """
        WebSocket callback
        """
        try:
            eliot.remove_destination(self._received_eliot_log)
        except ValueError:
            pass


def create_log_streaming_resource(client):
    """
    Create a new resource that accepts WebSocket connections if they
    include a correct `Authorization: tahoe-lafs <api_auth_token>`
    header (where `api_auth_token` matches the private configuration
    value).
    """
    factory = WebSocketServerFactory()
    factory.tahoe_client = client
    factory.protocol = TokenAuthenticatedWebSocketServerProtocol
    return WebSocketResource(factory)


def create_log_resources(client):
    logs = Resource()
    logs.putChild(b"v1", create_log_streaming_resource(client))
    return logs
