import json

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks, Deferred

from eliot import log_call

from autobahn.twisted.testing import create_memory_agent, MemoryReactorClockResolver
from autobahn.twisted.websocket import WebSocketServerProtocol
from autobahn.twisted.websocket import WebSocketClientProtocol

from allmydata.web.logs import TokenAuthenticatedWebSocketServerProtocol
#_StreamingLogClientProtocol

class TestStreamingLogs(unittest.TestCase):
    """
    Test websocket streaming of logs

    Note: depends on un-merged Autobahn branch
    """

    def setUp(self):
        self.reactor = MemoryReactorClockResolver()
        self.agent = create_memory_agent(self.reactor, TokenAuthenticatedWebSocketServerProtocol)

    @inlineCallbacks
    def test_one_log(self):

        proto = yield self.agent.open(
            transport_config=u"ws://localhost:1234/ws",
            options={},
        )

        messages = []
        def got_message(msg, is_binary=False):
            messages.append(json.loads(msg))
        proto.on("message", got_message)


        @log_call(action_type=u"test:cli:magic-folder:cleanup")
        def do_a_thing():
            pass

        do_a_thing()

        proto.transport.loseConnection()
        self.agent.flush()
        yield proto.is_closed

        self.assertEqual(len(messages), 2)
        self.assertEqual("started", messages[0]["action_status"])
        self.assertEqual("succeeded", messages[1]["action_status"])
