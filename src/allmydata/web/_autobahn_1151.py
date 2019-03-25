"""
Implement a work-around for <https://github.com/crossbario/autobahn-python/issues/1151>.
"""


from __future__ import (
    print_function,
    unicode_literals,
    absolute_import,
    division,
)


from autobahn.websocket.protocol import WebSocketProtocol
_originalConnectionLost = WebSocketProtocol._connectionLost

def _connectionLost(self, reason):
    if self.openHandshakeTimeoutCall is not None:
        self.openHandshakeTimeoutCall.cancel()
        self.openHandshakeTimeoutCall = None
    return _originalConnectionLost(self, reason)

def patch():
    """
    Monkey-patch the proposed fix into place.
    """
    WebSocketProtocol._connectionLost = _connectionLost
