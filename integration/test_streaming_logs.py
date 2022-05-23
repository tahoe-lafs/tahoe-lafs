"""
Ported to Python 3.
"""
from __future__ import (
    print_function,
    unicode_literals,
    absolute_import,
    division,
)

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from six import ensure_text

import json

from os.path import (
    join,
)
from urllib.parse import (
    urlsplit,
)

import attr

from twisted.internet.defer import (
    Deferred,
)
from twisted.internet.endpoints import (
    HostnameEndpoint,
)

import treq

from autobahn.twisted.websocket import (
    WebSocketClientFactory,
    WebSocketClientProtocol,
)

from allmydata.client import (
    read_config,
)
from allmydata.web.private import (
    SCHEME,
)
from allmydata.util.eliotutil import (
    inline_callbacks,
)

import pytest_twisted

def _url_to_endpoint(reactor, url):
    netloc = urlsplit(url).netloc
    host, port = netloc.split(":")
    return HostnameEndpoint(reactor, host, int(port))


class _StreamingLogClientProtocol(WebSocketClientProtocol):
    def onOpen(self):
        self.factory.on_open.callback(self)

    def onMessage(self, payload, isBinary):
        if self.on_message is None:
            # Already did our job, ignore it
            return
        on_message = self.on_message
        self.on_message = None
        on_message.callback(payload)

    def onClose(self, wasClean, code, reason):
        self.on_close.callback(reason)


def _connect_client(reactor, api_auth_token, ws_url):
    factory = WebSocketClientFactory(
        url=ws_url,
        headers={
            "Authorization": "{} {}".format(str(SCHEME, "ascii"), api_auth_token),
        }
    )
    factory.protocol = _StreamingLogClientProtocol
    factory.on_open = Deferred()

    endpoint = _url_to_endpoint(reactor, ws_url)
    return endpoint.connect(factory)


def _race(left, right):
    """
    Wait for the first result from either of two Deferreds.

    Any result, success or failure, causes the return Deferred to fire.  It
    fires with either a Left or a Right instance depending on whether the left
    or right argument fired first.

    The Deferred that loses the race is cancelled and any result it eventually
    produces is discarded.
    """
    racing = [True]
    def got_result(result, which):
        if racing:
            racing.pop()
            loser = which.pick(left, right)
            loser.cancel()
            finished.callback(which(result))

    finished = Deferred()
    left.addBoth(got_result, Left)
    right.addBoth(got_result, Right)
    return finished


@attr.s
class Left(object):
    value = attr.ib()

    @classmethod
    def pick(cls, left, right):
        return left


@attr.s
class Right(object):
    value = attr.ib()

    @classmethod
    def pick(cls, left, right):
        return right


@inline_callbacks
def _test_streaming_logs(reactor, temp_dir, alice):
    cfg = read_config(join(temp_dir, "alice"), "portnum")
    node_url = cfg.get_config_from_file("node.url")
    api_auth_token = cfg.get_private_config("api_auth_token")

    ws_url = ensure_text(node_url).replace("http://", "ws://")
    log_url = ws_url + "private/logs/v1"

    print("Connecting to {}".format(log_url))
    client = yield _connect_client(reactor, api_auth_token, log_url)
    print("Connected.")
    client.on_close = Deferred()
    client.on_message = Deferred()

    # Capture this now before on_message perhaps goes away.
    racing = _race(client.on_close, client.on_message)

    # Provoke _some_ log event.
    yield treq.get(node_url)

    result = yield racing

    assert isinstance(result, Right)
    json.loads(result.value)


@pytest_twisted.inlineCallbacks
def test_streaming_logs(reactor, temp_dir, alice):
    yield _test_streaming_logs(reactor, temp_dir, alice)
