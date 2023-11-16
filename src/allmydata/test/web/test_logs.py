"""
Tests for ``allmydata.web.logs``.

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

import json

from twisted.internet.defer import inlineCallbacks


from autobahn.twisted.testing import create_memory_agent, MemoryReactorClockResolver, create_pumper

from testtools.matchers import (
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)

from twisted.web.http import (
    OK,
)

from treq.client import (
    HTTPClient,
)
from treq.testing import (
    RequestTraversalAgent,
)

from .matchers import (
    has_response_code,
)

from ..common import (
    SyncTestCase,
    AsyncTestCase,
)

from ...web.logs import (
    create_log_resources,
    TokenAuthenticatedWebSocketServerProtocol,
)

from eliot import log_call

class StreamingEliotLogsTests(SyncTestCase):
    """
    Tests for the log streaming resources created by ``create_log_resources``.
    """
    def setUp(self):
        self.resource = create_log_resources()
        self.agent = RequestTraversalAgent(self.resource)
        self.client =  HTTPClient(self.agent)
        return super(StreamingEliotLogsTests, self).setUp()

    def test_v1(self):
        """
        There is a resource at *v1*.
        """
        self.assertThat(
            self.client.get(b"http:///v1"),
            succeeded(has_response_code(Equals(OK))),
        )


class TestStreamingLogs(AsyncTestCase):
    """
    Test websocket streaming of logs
    """

    def setUp(self):
        super(TestStreamingLogs, self).setUp()
        self.reactor = MemoryReactorClockResolver()
        self.pumper = create_pumper()
        self.agent = create_memory_agent(self.reactor, self.pumper, TokenAuthenticatedWebSocketServerProtocol)
        return self.pumper.start()

    def tearDown(self):
        super(TestStreamingLogs, self).tearDown()
        return self.pumper.stop()

    @inlineCallbacks
    def test_one_log(self):
        """
        Write a single Eliot log action and see it streamed via websocket.
        """

        proto = yield self.agent.open(
            transport_config=u"ws://localhost:1234/ws",
            options={},
        )

        messages = []
        def got_message(msg, is_binary=False):
            messages.append(json.loads(msg))
        proto.on("message", got_message)

        @log_call(action_type=u"test:cli:some-exciting-action")
        def do_a_thing(arguments):
            pass

        do_a_thing(arguments=[u"hello", b"good-\xff-day", 123, {"a": 35}, [None]])

        proto.transport.loseConnection()
        yield proto.is_closed

        self.assertThat(len(messages), Equals(3), messages)
        self.assertThat(messages[0]["action_type"], Equals("test:cli:some-exciting-action"))
        self.assertThat(messages[0]["arguments"],
                         Equals(["hello", "good-\\xff-day", 123, {"a": 35}, [None]]))
        self.assertThat(messages[1]["action_type"], Equals("test:cli:some-exciting-action"))
        self.assertThat("started", Equals(messages[0]["action_status"]))
        self.assertThat("succeeded", Equals(messages[1]["action_status"]))
