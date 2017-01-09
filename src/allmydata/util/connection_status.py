import time
from zope.interface import implementer
from ..interfaces import IConnectionStatus

@implementer(IConnectionStatus)
class ConnectionStatus:
    def __init__(self, connected, summary, non_connected_statuses,
                 last_connection_time, last_received_time):
        self.connected = connected
        self.summary = summary
        self.non_connected_statuses = non_connected_statuses
        self.last_connection_time = last_connection_time
        self.last_received_time = last_received_time

def _hint_statuses(which, handlers, statuses):
    non_connected_statuses = {}
    for hint in which:
        handler = handlers.get(hint)
        handler_dsc = " via %s" % handler if handler else ""
        dsc = statuses[hint]
        non_connected_statuses["%s%s" % (hint, handler_dsc)] = dsc
    return non_connected_statuses

def from_foolscap_reconnector(rc, last_received):
    ri = rc.getReconnectionInfo()
    state = ri.state
    # the Reconnector shouldn't even be exposed until it is started, so we
    # should never see "unstarted"
    assert state in ("connected", "connecting", "waiting"), state
    ci = ri.connectionInfo
    connected = False
    last_connected = None
    others = set(ci.connectorStatuses.keys())

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
        now = time.time()
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
