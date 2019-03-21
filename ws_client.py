from __future__ import print_function

import json
import sys

from twisted.internet.task import react
from twisted.internet.defer import inlineCallbacks, Deferred

from autobahn.twisted.websocket import (
    WebSocketClientProtocol,
    WebSocketClientFactory,
)


class TahoeLogProtocol(WebSocketClientProtocol):
    """
    """

    def onOpen(self):
        pass#print("connected")

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
        print("bye", args)


@inlineCallbacks
def main(reactor):

    with open("testgrid/alice/private/api_auth_token", "r") as f:
    #with open("alice/private/api_auth_token", "r") as f:
        token = f.read().strip()

    factory = WebSocketClientFactory(
        url=u"ws://127.0.0.1:8890/logs_v1",
        headers={
            "Authorization": "tahoe-lafs {}".format(token),
        }
    )
    factory.protocol = TahoeLogProtocol
    port = yield reactor.connectTCP("127.0.0.1", 8890, factory)
    if False:
        print("port {}".format(port))
        print(dir(port))
        print(port.getDestination())
        print(port.transport)
        print(dir(port.transport))
        print(port.transport.protocol)
        # can we like 'listen' for this connection/etc to die?
    yield Deferred()


if __name__ == '__main__':
    react(main)
