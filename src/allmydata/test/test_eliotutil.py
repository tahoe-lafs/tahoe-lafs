"""
Tests for ``allmydata.test.eliotutil``.
"""

from __future__ import (
    unicode_literals,
    print_function,
    absolute_import,
    division,
)

from pprint import pformat
from sys import stdout
import logging

from fixtures import (
    TempDir,
)
from testtools import (
    TestCase,
)
from testtools.matchers import (
    Is,
    MatchesStructure,
    Equals,
    AfterPreprocessing,
)
from testtools.twistedsupport import (
    has_no_result,
    succeeded,
)

from eliot import (
    Message,
    FileDestination,
    start_action,
)
from eliot.twisted import DeferredContext
from eliot.testing import (
    capture_logging,
    assertHasAction,
)

from twisted.internet.defer import (
    Deferred,
    succeed,
)
from twisted.internet.task import deferLater
from twisted.internet import reactor

from .eliotutil import (
    eliot_logged_test,
)

from ..util.eliotutil import (
    eliot_friendly_generator_function,
    inline_callbacks,
    _parse_destination_description,
    _EliotLogging,
)
from .common import (
    SyncTestCase,
    AsyncTestCase,
)

class EliotLoggedTestTests(AsyncTestCase):
    @eliot_logged_test
    def test_returns_none(self):
        Message.log(hello="world")

    @eliot_logged_test
    def test_returns_fired_deferred(self):
        Message.log(hello="world")
        return succeed(None)

    @eliot_logged_test
    def test_returns_unfired_deferred(self):
        Message.log(hello="world")
        # @eliot_logged_test automatically gives us an action context but it's
        # still our responsibility to maintain it across stack-busting
        # operations.
        d = DeferredContext(deferLater(reactor, 0.0, lambda: None))
        d.addCallback(lambda ignored: Message.log(goodbye="world"))
        # We didn't start an action.  We're not finishing an action.
        return d.result



def assert_logged_messages_contain_fields(testcase, logged_messages, expected_fields):
    testcase.assertEqual(len(logged_messages), len(expected_fields))
    actual_fields = list(
        {key: msg.message[key] for key in expected if key in msg.message}
        for (msg, expected)
        in zip(logged_messages, expected_fields)
    )
    testcase.assertEqual(actual_fields, expected_fields)


def assert_logged_action_contains_messages(testcase, logger, expected_action, expected_fields):
    action = assertHasAction(
        testcase,
        logger,
        expected_action,
        True,
    )
    assert_logged_messages_contain_fields(
        testcase,
        action.children,
        expected_fields,
    )

def assert_expected_action_tree(testcase, logger, expected_action_type, expected_type_tree):
    logged_action = assertHasAction(
        testcase,
        logger,
        expected_action_type,
        True,
    )
    type_tree = logged_action.type_tree()
    testcase.assertEqual(
        {expected_action_type: expected_type_tree},
        type_tree,
        "Logger had messages:\n{}".format(pformat(logger.messages, indent=4)),
    )

def assert_generator_logs_action_tree(testcase, generator_function, logger, expected_action_type, expected_type_tree):
    list(eliot_friendly_generator_function(generator_function)())
    assert_expected_action_tree(
        testcase,
        logger,
        expected_action_type,
        expected_type_tree,
    )


class EliotFriendlyGeneratorFunctionTests(SyncTestCase):
    # Get our custom assertion failure messages *and* the standard ones.
    longMessage = True

    @capture_logging(None)
    def test_yield_none(self, logger):
        @eliot_friendly_generator_function
        def g():
            Message.log(message_type=u"hello")
            yield
            Message.log(message_type=u"goodbye")

        with start_action(action_type=u"the-action"):
            list(g())

        assert_expected_action_tree(
            self,
            logger,
            u"the-action",
            [u"hello", u"yielded", u"goodbye"],
        )

    @capture_logging(None)
    def test_yield_value(self, logger):
        expected = object()

        @eliot_friendly_generator_function
        def g():
            Message.log(message_type=u"hello")
            yield expected
            Message.log(message_type=u"goodbye")

        with start_action(action_type=u"the-action"):
            self.assertEqual([expected], list(g()))

        assert_expected_action_tree(
            self,
            logger,
            u"the-action",
            [u"hello", u"yielded", u"goodbye"],
        )

    @capture_logging(None)
    def test_yield_inside_another_action(self, logger):
        @eliot_friendly_generator_function
        def g():
            Message.log(message_type=u"a")
            with start_action(action_type=u"confounding-factor"):
                Message.log(message_type=u"b")
                yield None
                Message.log(message_type=u"c")
            Message.log(message_type=u"d")

        with start_action(action_type=u"the-action"):
            list(g())

        assert_expected_action_tree(
            self,
            logger,
            u"the-action",
            [u"a",
             {u"confounding-factor": [u"b", u"yielded", u"c"]},
             u"d",
            ],
        )

    @capture_logging(None)
    def test_yield_inside_nested_actions(self, logger):
        @eliot_friendly_generator_function
        def g():
            Message.log(message_type=u"a")
            with start_action(action_type=u"confounding-factor"):
                Message.log(message_type=u"b")
                yield None
                with start_action(action_type=u"double-confounding-factor"):
                    yield None
                    Message.log(message_type=u"c")
                Message.log(message_type=u"d")
            Message.log(message_type=u"e")

        with start_action(action_type=u"the-action"):
            list(g())

        assert_expected_action_tree(
            self,
            logger,
            u"the-action", [
                u"a",
                {u"confounding-factor": [
                    u"b",
                    u"yielded",
                    {u"double-confounding-factor": [
                        u"yielded",
                        u"c",
                    ]},
                    u"d",
                ]},
                u"e",
            ],
        )

    @capture_logging(None)
    def test_generator_and_non_generator(self, logger):
        @eliot_friendly_generator_function
        def g():
            Message.log(message_type=u"a")
            yield
            with start_action(action_type=u"action-a"):
                Message.log(message_type=u"b")
                yield
                Message.log(message_type=u"c")

            Message.log(message_type=u"d")
            yield

        with start_action(action_type=u"the-action"):
            generator = g()
            next(generator)
            Message.log(message_type=u"0")
            next(generator)
            Message.log(message_type=u"1")
            next(generator)
            Message.log(message_type=u"2")
            self.assertRaises(StopIteration, lambda: next(generator))

        assert_expected_action_tree(
            self,
            logger,
            u"the-action", [
                u"a",
                u"yielded",
                u"0",
                {
                    u"action-a": [
                        u"b",
                        u"yielded",
                        u"c",
                    ],
                },
                u"1",
                u"d",
                u"yielded",
                u"2",
            ],
        )

    @capture_logging(None)
    def test_concurrent_generators(self, logger):
        @eliot_friendly_generator_function
        def g(which):
            Message.log(message_type=u"{}-a".format(which))
            with start_action(action_type=which):
                Message.log(message_type=u"{}-b".format(which))
                yield
                Message.log(message_type=u"{}-c".format(which))
            Message.log(message_type=u"{}-d".format(which))

        gens = [g(u"1"), g(u"2")]
        with start_action(action_type=u"the-action"):
            while gens:
                for g in gens[:]:
                    try:
                        next(g)
                    except StopIteration:
                        gens.remove(g)

        assert_expected_action_tree(
            self,
            logger,
            u"the-action", [
                u"1-a",
                {u"1": [
                    u"1-b",
                    u"yielded",
                    u"1-c",
                ]},
                u"2-a",
                {u"2": [
                    u"2-b",
                    u"yielded",
                    u"2-c",
                ]},
                u"1-d",
                u"2-d",
            ],
        )

    @capture_logging(None)
    def test_close_generator(self, logger):
        @eliot_friendly_generator_function
        def g():
            Message.log(message_type=u"a")
            try:
                yield
                Message.log(message_type=u"b")
            finally:
                Message.log(message_type=u"c")


        with start_action(action_type=u"the-action"):
            gen = g()
            next(gen)
            gen.close()

        assert_expected_action_tree(
            self,
            logger,
            u"the-action", [
                u"a",
                u"yielded",
                u"c",
            ],
        )

    @capture_logging(None)
    def test_nested_generators(self, logger):
        @eliot_friendly_generator_function
        def g(recurse):
            with start_action(action_type=u"a-recurse={}".format(recurse)):
                Message.log(message_type=u"m-recurse={}".format(recurse))
                if recurse:
                    set(g(False))
                else:
                    yield

        with start_action(action_type=u"the-action"):
            set(g(True))

        assert_expected_action_tree(
            self,
            logger,
            u"the-action", [{
                u"a-recurse=True": [
                    u"m-recurse=True", {
                        u"a-recurse=False": [
                            u"m-recurse=False",
                            u"yielded",
                        ],
                    },
                ],
            }],
        )


class InlineCallbacksTests(SyncTestCase):
    # Get our custom assertion failure messages *and* the standard ones.
    longMessage = True

    def _a_b_test(self, logger, g):
        with start_action(action_type=u"the-action"):
            self.assertThat(g(), succeeded(Is(None)))
        assert_expected_action_tree(
            self,
            logger,
            u"the-action", [
                u"a",
                u"yielded",
                u"b",
            ],
        )

    @capture_logging(None)
    def test_yield_none(self, logger):
        @inline_callbacks
        def g():
            Message.log(message_type=u"a")
            yield
            Message.log(message_type=u"b")

        self._a_b_test(logger, g)

    @capture_logging(None)
    def test_yield_fired_deferred(self, logger):
        @inline_callbacks
        def g():
            Message.log(message_type=u"a")
            yield succeed(None)
            Message.log(message_type=u"b")

        self._a_b_test(logger, g)

    @capture_logging(None)
    def test_yield_unfired_deferred(self, logger):
        waiting = Deferred()

        @inline_callbacks
        def g():
            Message.log(message_type=u"a")
            yield waiting
            Message.log(message_type=u"b")

        with start_action(action_type=u"the-action"):
            d = g()
            self.assertThat(waiting, has_no_result())
            waiting.callback(None)
            self.assertThat(d, succeeded(Is(None)))
        assert_expected_action_tree(
            self,
            logger,
            u"the-action", [
                u"a",
                u"yielded",
                u"b",
            ],
        )


class  ParseDestinationDescriptionTests(SyncTestCase):
    def test_stdout(self):
        """
        A ``file:`` description with a path of ``-`` causes logs to be written to
        stdout.
        """
        reactor = object()
        self.assertThat(
            _parse_destination_description("file:-")(reactor),
            Equals(FileDestination(stdout)),
        )


    def test_regular_file(self):
        """
        A ``file:`` description with any path other than ``-`` causes logs to be
        written to a file with that name.
        """
        tempdir = TempDir()
        self.useFixture(tempdir)

        reactor = object()
        path = tempdir.join("regular_file")

        self.assertThat(
            _parse_destination_description("file:{}".format(path))(reactor),
            MatchesStructure(
                file=MatchesStructure(
                    path=Equals(path),
                    rotateLength=AfterPreprocessing(bool, Equals(True)),
                    maxRotatedFiles=AfterPreprocessing(bool, Equals(True)),
                ),
            ),
        )


# Opt out of the great features of common.SyncTestCase because we're
# interacting with Eliot in a very obscure, particular, fragile way. :/
class EliotLoggingTests(TestCase):
    """
    Tests for ``_EliotLogging``.
    """
    def test_stdlib_event_relayed(self):
        """
        An event logged using the stdlib logging module is delivered to the Eliot
        destination.
        """
        collected = []
        service = _EliotLogging([collected.append])
        service.startService()
        self.addCleanup(service.stopService)

        # The first destination added to the global log destinations gets any
        # buffered messages delivered to it.  We don't care about those.
        # Throw them on the floor.  Sorry.
        del collected[:]

        logging.critical("oh no")
        self.assertThat(
            collected,
            AfterPreprocessing(
                len,
                Equals(1),
            ),
        )

    def test_twisted_event_relayed(self):
        """
        An event logged with a ``twisted.logger.Logger`` is delivered to the Eliot
        destination.
        """
        collected = []
        service = _EliotLogging([collected.append])
        service.startService()
        self.addCleanup(service.stopService)

        from twisted.logger import Logger
        Logger().critical("oh no")
        self.assertThat(
            collected,
            AfterPreprocessing(
                len, Equals(1),
            ),
        )
