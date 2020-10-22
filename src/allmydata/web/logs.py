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
import eliot

from twisted.web.resource import (
    Resource,
)

# Hotfix work-around https://github.com/crossbario/autobahn-python/issues/1151
from . import _autobahn_1151
_autobahn_1151.patch()
del _autobahn_1151


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
        # we don't care what WebSocket sub-protocol is
        # negotiated, nor do we need to send headers to the
        # client, so we ask Autobahn to just allow this
        # connection with the defaults. We could return a
        # (headers, protocol) pair here instead if required.
        return None

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


def create_log_streaming_resource():
    factory = WebSocketServerFactory()
    factory.protocol = TokenAuthenticatedWebSocketServerProtocol
    return WebSocketResource(factory)


def create_log_resources():
    logs = Resource()
    logs.putChild(b"v1", create_log_streaming_resource())
    return logs
