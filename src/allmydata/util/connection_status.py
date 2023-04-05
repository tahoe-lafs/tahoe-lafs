"""
Parse connection status from Foolscap.
"""

from __future__ import annotations

import time
from zope.interface import implementer
from ..interfaces import IConnectionStatus
from foolscap.reconnector import Reconnector

@implementer(IConnectionStatus)
class ConnectionStatus(object):
    def __init__(self, connected, summary, non_connected_statuses,
                 last_connection_time, last_received_time):
        self.connected = connected
        self.summary = summary
        self.non_connected_statuses = non_connected_statuses
        self.last_connection_time = last_connection_time
        self.last_received_time = last_received_time

    @classmethod
    def unstarted(cls):
        """
        Create a ``ConnectionStatus`` representing a connection for which no
        attempts have yet been made.
        """
        return cls(
            connected=False,
            summary=u"unstarted",
            non_connected_statuses=[],
            last_connection_time=None,
            last_received_time=None,
        )

def _hint_statuses(which, handlers, statuses) -> dict[str, str]:
    non_connected_statuses = {}
    for hint in which:
        handler = handlers.get(hint)
        handler_dsc = " via %s" % handler if handler else ""
        dsc = statuses[hint]
        non_connected_statuses["%s%s" % (hint, handler_dsc)] = dsc
    return non_connected_statuses

def from_foolscap_reconnector(rc: Reconnector, last_received: int, time=time.time) -> ConnectionStatus:
    ri = rc.getReconnectionInfo()
    # See foolscap/reconnector.py, ReconnectionInfo, for details about possible
    # states. The returned result is a native string, it seems, so convert to
    # unicode.
    state = ri.state
    if isinstance(state, bytes):  # Python 2
        state = str(state, "ascii")
    if state == "unstarted":
        return ConnectionStatus.unstarted()

    ci = ri.connectionInfo
    connected = False
    last_connected = None
    others = set(ci.connectorStatuses.keys())
    summary = None

    if state == "connected":
        connected = True
        if ci.winningHint:
            others.remove(ci.winningHint)
            summary = "Connected to %s via %s" % (
                ci.winningHint, ci.connectionHandlers[ci.winningHint])
        else:
            summary = "Connected via listener (%s)" % ci.listenerStatus[0]
        last_connected = ci.establishedAt
    elif state == "connecting":
        # ci describes the current in-progress attempt
        summary = "Trying to connect"
    elif state == "waiting":
        now = time()
        elapsed = now - ri.lastAttempt
        delay = ri.nextAttempt - now
        summary = "Reconnecting in %d seconds (last attempt %ds ago)" % \
                  (delay, elapsed)
        # ci describes the previous (failed) attempt

    non_connected_statuses = _hint_statuses(others,
                                            ci.connectionHandlers,
                                            ci.connectorStatuses)
    cs = ConnectionStatus(connected, summary, non_connected_statuses,
                          last_connected, last_received)
    return cs
