"""
Tests for allmydata.util.connection_status.
"""

from __future__ import annotations

from typing import Optional

from foolscap.reconnector import ReconnectionInfo, Reconnector
from foolscap.info import ConnectionInfo

from ..util import connection_status
from .common import SyncTestCase

def reconnector(info: ReconnectionInfo) -> Reconnector:
    rc = Reconnector(None, None, (), {}) # type: ignore[no-untyped-call]
    rc._reconnectionInfo = info
    return rc

def connection_info(
        statuses: dict[str, str],
        handlers: dict[str, str],
        winningHint: Optional[str],
        establishedAt: Optional[int],
) -> ConnectionInfo:
    ci = ConnectionInfo() # type: ignore[no-untyped-call]
    ci.connectorStatuses = statuses
    ci.connectionHandlers = handlers
    ci.winningHint = winningHint
    ci.establishedAt = establishedAt
    return ci

def reconnection_info(
        state: str,
        connection_info: ConnectionInfo,
) -> ReconnectionInfo:
    ri = ReconnectionInfo() # type: ignore[no-untyped-call]
    ri.state = state
    ri.connectionInfo = connection_info
    return ri

class Status(SyncTestCase):
    def test_hint_statuses(self) -> None:
        ncs = connection_status._hint_statuses(["h2","h1"],
                                               {"h1": "hand1", "h4": "hand4"},
                                               {"h1": "st1", "h2": "st2",
                                                "h3": "st3"})
        self.assertEqual(ncs, {"h1 via hand1": "st1",
                               "h2": "st2"})

    def test_reconnector_connected(self) -> None:
        ci = connection_info({"h1": "st1"}, {"h1": "hand1"}, "h1", 120)
        ri = reconnection_info("connected", ci)
        rc = reconnector(ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected to h1 via hand1")
        self.assertEqual(cs.non_connected_statuses, {})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connected_others(self) -> None:
        ci = connection_info({"h1": "st1", "h2": "st2"}, {"h1": "hand1"}, "h1", 120)
        ri = reconnection_info("connected", ci)
        rc = reconnector(ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected to h1 via hand1")
        self.assertEqual(cs.non_connected_statuses, {"h2": "st2"})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connected_listener(self) -> None:
        ci = connection_info({"h1": "st1", "h2": "st2"}, {"h1": "hand1"}, None, 120)
        ci.listenerStatus = ("listener1", "successful")
        ri = reconnection_info("connected", ci)
        rc = reconnector(ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected via listener (listener1)")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connecting(self) -> None:
        ci = connection_info({"h1": "st1", "h2": "st2"}, {"h1": "hand1"}, None, None)
        ri = reconnection_info("connecting", ci)
        rc = reconnector(ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, False)
        self.assertEqual(cs.summary, "Trying to connect")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, None)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_waiting(self) -> None:
        ci = connection_info({"h1": "st1", "h2": "st2"}, {"h1": "hand1"}, None, None)
        ri = reconnection_info("waiting", ci)
        ri.lastAttempt = 10
        ri.nextAttempt = 20
        rc = reconnector(ri)
        cs = connection_status.from_foolscap_reconnector(rc, 5, time=lambda: 12)
        self.assertEqual(cs.connected, False)
        self.assertEqual(cs.summary,
                         "Reconnecting in 8 seconds (last attempt 2s ago)")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, None)
        self.assertEqual(cs.last_received_time, 5)
