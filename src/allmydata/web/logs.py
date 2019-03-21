from autobahn.twisted.resource import WebSocketResource
from autobahn.twisted.websocket import WebSocketServerFactory
from autobahn.twisted.websocket import WebSocketServerProtocol
from autobahn.websocket.types import ConnectionDeny

from twisted.web import resource, server
from twisted.python.failure import Failure

from allmydata.util.hashutil import timing_safe_compare
from .common import humanize_failure


class TokenAuthenticatedWebSocketServerProtocol(WebSocketServerProtocol):
    """
    """

    def onConnect(self, req):
        if 'authorization' in req.headers:
            token = req.headers['authorization'].encode('ascii')
            if timing_safe_compare(token, self.factory.tahoe_client.get_auth_token()):
                print("we're here, it's fine")
                # we don't care what WebSocket sub-protocol is
                # negotiated, nor do we need to send headers to the
                # client, so we ask Autobahn to just allow this
                # connectino with the defaults. We could return a
                # (headers, protocol) pair here instead if required.
                return None

        # everything else -- i.e. no Authorization header, or it's
        # wrong -- means we deny the websocket connection
        raise ConnectionDeny(
            code=406,
            reason=u"Invalid or missing token"
        )


class LogStreamingWebSocket(resource.Resource):
    """
    """

    def __init__(self, client):
        self._client = client
        self._factory = WebSocketServerFactory(u"ws://127.0.0.1:6301/logs_v1")
        self._factory.tahoe_client = client
        self._factory.protocol = TokenAuthenticatedWebSocketServerProtocol
        self._ws_resource = WebSocketResource(self._factory)

    def render(self, req):
        print(req)
        print(dir(req.headers))
        print(req.headers.keys())
        return self._ws_resource.render(req)



def create_log_streaming_resource(client):
    return LogStreamingWebSocket(client)


def _create_log_streaming_resource(client):
    factory = WebSocketServerFactory(u"ws://127.0.0.1:6301/logs_v1")
    factory.protocol = WebSocketServerProtocol
    if False:
        res = WebSocketResource(factory)
    else:
        res = WebSocketResource(factory)
    return res

