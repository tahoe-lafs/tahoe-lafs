import json

from autobahn.twisted.resource import WebSocketResource
from autobahn.twisted.websocket import WebSocketServerFactory
from autobahn.twisted.websocket import WebSocketServerProtocol
from autobahn.websocket.types import ConnectionDeny

from twisted.web import resource, server
from twisted.python.failure import Failure

import eliot

from allmydata.util.hashutil import timing_safe_compare
from .common import humanize_failure


class TokenAuthenticatedWebSocketServerProtocol(WebSocketServerProtocol):
    """
    """

    def onConnect(self, req):
        if 'authorization' in req.headers:
            auth = req.headers['authorization'].encode('ascii').split(' ', 1)
            if len(auth) == 2:
                tag, token = auth
                if tag == "tahoe-lafs":
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
        # probably want a try/except around here? what do we do if
        # transmission fails or anything else bad?
        self.sendMessage(json.dumps(message))

    def onOpen(self):
        # self.factory.tahoe_client.add_log_streaming_client(self)
        # hmm, instead of something like ^ maybe we just add eliot
        # stuff ourselves...
        eliot.add_destination(self._received_eliot_log)

    def onClose(self, wasClean, code, reason):
        #self.factory.tahoe_client.remove_log_streaming_client(self)
        try:
            eliot.remove_destination(self._received_eliot_log)
        except ValueError:
            pass


def create_log_streaming_resource(client, websocket_url):
    """
    Create a new resource that accepts WebSocket connections if they
    include a correct `Authorization: tahoe-lafs <api_auth_token>`
    header (where `api_auth_token` matches the private configuration
    value).
    """
    factory = WebSocketServerFactory(websocket_url)
    factory.tahoe_client = client
    factory.protocol = TokenAuthenticatedWebSocketServerProtocol
    return WebSocketResource(factory)


def _create_log_streaming_resource(client):
    factory = WebSocketServerFactory(u"ws://127.0.0.1:6301/logs_v1")
    factory.protocol = WebSocketServerProtocol
    if False:
        res = WebSocketResource(factory)
    else:
        res = WebSocketResource(factory)
    return res

