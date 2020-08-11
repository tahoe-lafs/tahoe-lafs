"""
Tests for allmydata.util.connection_status.

Port to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import mock

from twisted.trial import unittest

from ..util import connection_status

class Status(unittest.TestCase):
    def test_hint_statuses(self):
        ncs = connection_status._hint_statuses(["h2","h1"],
                                               {"h1": "hand1", "h4": "hand4"},
                                               {"h1": "st1", "h2": "st2",
                                                "h3": "st3"})
        self.assertEqual(ncs, {"h1 via hand1": "st1",
                               "h2": "st2"})

    def test_reconnector_connected(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1"}
        ci.connectionHandlers = {"h1": "hand1"}
        ci.winningHint = "h1"
        ci.establishedAt = 120
        ri = mock.Mock()
        ri.state = "connected"
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected to h1 via hand1")
        self.assertEqual(cs.non_connected_statuses, {})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connected_others(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1", "h2": "st2"}
        ci.connectionHandlers = {"h1": "hand1"}
        ci.winningHint = "h1"
        ci.establishedAt = 120
        ri = mock.Mock()
        ri.state = "connected"
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected to h1 via hand1")
        self.assertEqual(cs.non_connected_statuses, {"h2": "st2"})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connected_listener(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1", "h2": "st2"}
        ci.connectionHandlers = {"h1": "hand1"}
        ci.listenerStatus = ("listener1", "successful")
        ci.winningHint = None
        ci.establishedAt = 120
        ri = mock.Mock()
        ri.state = "connected"
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, True)
        self.assertEqual(cs.summary, "Connected via listener (listener1)")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, 120)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_connecting(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1", "h2": "st2"}
        ci.connectionHandlers = {"h1": "hand1"}
        ri = mock.Mock()
        ri.state = "connecting"
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        cs = connection_status.from_foolscap_reconnector(rc, 123)
        self.assertEqual(cs.connected, False)
        self.assertEqual(cs.summary, "Trying to connect")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, None)
        self.assertEqual(cs.last_received_time, 123)

    def test_reconnector_waiting(self):
        ci = mock.Mock()
        ci.connectorStatuses = {"h1": "st1", "h2": "st2"}
        ci.connectionHandlers = {"h1": "hand1"}
        ri = mock.Mock()
        ri.state = "waiting"
        ri.lastAttempt = 10
        ri.nextAttempt = 20
        ri.connectionInfo = ci
        rc = mock.Mock
        rc.getReconnectionInfo = mock.Mock(return_value=ri)
        with mock.patch("time.time", return_value=12):
            cs = connection_status.from_foolscap_reconnector(rc, 5)
        self.assertEqual(cs.connected, False)
        self.assertEqual(cs.summary,
                         "Reconnecting in 8 seconds (last attempt 2s ago)")
        self.assertEqual(cs.non_connected_statuses,
                         {"h1 via hand1": "st1", "h2": "st2"})
        self.assertEqual(cs.last_connection_time, None)
        self.assertEqual(cs.last_received_time, 5)
