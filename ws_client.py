from __future__ import print_function

import sys
import json

from twisted.internet.error import ConnectError
from twisted.internet.task import react
from twisted.internet.defer import inlineCallbacks, Deferred
from twisted.internet.endpoints import HostnameEndpoint

from autobahn.twisted.websocket import (
    WebSocketClientProtocol,
    WebSocketClientFactory,
)

from allmydata.client import read_config


class TahoeLogProtocol(WebSocketClientProtocol):
    """
    """

    def onOpen(self):
        self.factory.on_open.callback(self)

    def onMessage(self, payload, isBinary):
        if False:
            log_data = json.loads(payload.decode('utf8'))
            print("eliot message:")
            for k, v in log_data.items():
                print("  {}: {}".format(k, v))
        else:
            print(payload)
            sys.stdout.flush()

    def onClose(self, *args):
        if not self.factory.on_open.called:
            self.factory.on_open.errback(
                RuntimeError("Failed: {}".format(args))
            )
        self.factory.on_close.callback(self)


@inlineCallbacks
def main(reactor):

    from twisted.python import log
    log.startLogging(sys.stdout)

    tahoe_dir = "testgrid/alice"
    cfg = read_config(tahoe_dir, "portnum")

    token = cfg.get_private_config("api_auth_token").strip()
    webport = cfg.get_config("node", "web.port")
    if webport.startswith("tcp:"):
        port = webport.split(':')[1]
    else:
        port = webport

    factory = WebSocketClientFactory(
        url=u"ws://127.0.0.1:{}/private/logs/v1".format(port),
        headers={
            "Authorization": "tahoe-lafs {}".format(token),
        }
    )
    factory.on_open = Deferred()
    factory.on_close = Deferred()

    factory.protocol = TahoeLogProtocol

    endpoint = HostnameEndpoint(reactor, "127.0.0.1", int(port))
    try:
        port = yield endpoint.connect(factory)
    except ConnectError as e:
        print("Connection failed: {}".format(e))
        return

    print("port: {}".format(port))
    yield factory.on_open
    print("opened")
    yield factory.on_close
    print("closed")



if __name__ == '__main__':
    react(main)
