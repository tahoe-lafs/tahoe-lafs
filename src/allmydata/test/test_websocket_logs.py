import json

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks

from eliot import log_call

from autobahn.twisted.testing import create_memory_agent, MemoryReactorClockResolver, create_pumper

from allmydata.web.logs import TokenAuthenticatedWebSocketServerProtocol


class TestStreamingLogs(unittest.TestCase):
    """
    Test websocket streaming of logs
    """

    def setUp(self):
        self.reactor = MemoryReactorClockResolver()
        self.pumper = create_pumper()
        self.agent = create_memory_agent(self.reactor, self.pumper, TokenAuthenticatedWebSocketServerProtocol)
        return self.pumper.start()

    def tearDown(self):
        return self.pumper.stop()

    @inlineCallbacks
    def test_one_log(self):
        """
        write a single Eliot log and see it streamed via websocket
        """

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
        yield proto.is_closed

        self.assertEqual(len(messages), 2)
        self.assertEqual("started", messages[0]["action_status"])
        self.assertEqual("succeeded", messages[1]["action_status"])
