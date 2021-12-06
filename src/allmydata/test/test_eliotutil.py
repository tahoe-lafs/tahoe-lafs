"""
Tests for ``allmydata.util.eliotutil``.

Ported to Python 3.
"""

from __future__ import (
    unicode_literals,
    print_function,
    absolute_import,
    division,
)

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from sys import stdout
import logging

from unittest import (
    skip,
)

from fixtures import (
    TempDir,
)
from testtools import (
    TestCase,
    TestResult,
)
from testtools.matchers import (
    Is,
    Contains,
    IsInstance,
    Not,
    MatchesStructure,
    Equals,
    HasLength,
    AfterPreprocessing,
)
from testtools.twistedsupport import (
    succeeded,
    failed,
)

from eliot import (
    Message,
    MessageType,
    fields,
    FileDestination,
    MemoryLogger,
)
from eliot.twisted import DeferredContext
from eliot.testing import (
    capture_logging,
    assertHasAction,
    swap_logger,
    assertContainsFields,
)

from twisted.internet.defer import (
    succeed,
)
from twisted.internet.task import deferLater
from twisted.internet import reactor, defer

from ..util.eliotutil import (
    eliot_json_encoder,
    log_call_deferred,
    _parse_destination_description,
    _EliotLogging,
)

from .common import (
    SyncTestCase,
    AsyncTestCase,
)


def passes():
    """
    Create a matcher that matches a ``TestCase`` that runs without failures or
    errors.
    """
    def run(case):
        result = TestResult()
        case.run(result)
        return result.wasSuccessful()
    return AfterPreprocessing(run, Equals(True))


class EliotLoggedTestTests(TestCase):
    """
    Tests for the automatic log-related provided by ``AsyncTestCase``.

    This class uses ``testtools.TestCase`` because it is inconvenient to nest
    ``AsyncTestCase`` inside ``AsyncTestCase`` (in particular, Eliot messages
    emitted by the inner test case get observed by the outer test case and if
    an inner case emits invalid messages they cause the outer test case to
    fail).
    """
    def test_fails(self):
        """
        A test method of an ``AsyncTestCase`` subclass can fail.
        """
        class UnderTest(AsyncTestCase):
            def test_it(self):
                self.fail("make sure it can fail")

        self.assertThat(UnderTest("test_it"), Not(passes()))

    def test_unserializable_fails(self):
        """
        A test method of an ``AsyncTestCase`` subclass that logs an unserializable
        value with Eliot fails.
        """
        class world(object):
            """
            an unserializable object
            """

        class UnderTest(AsyncTestCase):
            def test_it(self):
                Message.log(hello=world)

        self.assertThat(UnderTest("test_it"), Not(passes()))

    def test_logs_non_utf_8_byte(self):
        """
        A test method of an ``AsyncTestCase`` subclass can log a message that
        contains a non-UTF-8 byte string and return ``None`` and pass.
        """
        class UnderTest(AsyncTestCase):
            def test_it(self):
                Message.log(hello=b"\xFF")

        self.assertThat(UnderTest("test_it"), passes())

    def test_returns_none(self):
        """
        A test method of an ``AsyncTestCase`` subclass can log a message and
        return ``None`` and pass.
        """
        class UnderTest(AsyncTestCase):
            def test_it(self):
                Message.log(hello="world")

        self.assertThat(UnderTest("test_it"), passes())

    def test_returns_fired_deferred(self):
        """
        A test method of an ``AsyncTestCase`` subclass can log a message and
        return an already-fired ``Deferred`` and pass.
        """
        class UnderTest(AsyncTestCase):
            def test_it(self):
                Message.log(hello="world")
                return succeed(None)

        self.assertThat(UnderTest("test_it"), passes())

    def test_returns_unfired_deferred(self):
        """
        A test method of an ``AsyncTestCase`` subclass can log a message and
        return an unfired ``Deferred`` and pass when the ``Deferred`` fires.
        """
        class UnderTest(AsyncTestCase):
            def test_it(self):
                Message.log(hello="world")
                # @eliot_logged_test automatically gives us an action context
                # but it's still our responsibility to maintain it across
                # stack-busting operations.
                d = DeferredContext(deferLater(reactor, 0.0, lambda: None))
                d.addCallback(lambda ignored: Message.log(goodbye="world"))
                # We didn't start an action.  We're not finishing an action.
                return d.result

        self.assertThat(UnderTest("test_it"), passes())


class ParseDestinationDescriptionTests(SyncTestCase):
    """
    Tests for ``_parse_destination_description``.
    """
    def test_stdout(self):
        """
        A ``file:`` description with a path of ``-`` causes logs to be written to
        stdout.
        """
        reactor = object()
        self.assertThat(
            _parse_destination_description("file:-")(reactor),
            Equals(FileDestination(stdout, encoder=eliot_json_encoder)),
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

    def test_validation_failure(self):
        """
        If a test emits a log message that fails validation then an error is added
        to the result.
        """
        # Make sure we preserve the original global Eliot state.
        original = swap_logger(MemoryLogger())
        self.addCleanup(lambda: swap_logger(original))

        class ValidationFailureProbe(SyncTestCase):
            def test_bad_message(self):
                # This message does not validate because "Hello" is not an
                # int.
                MSG = MessageType("test:eliotutil", fields(foo=int))
                MSG(foo="Hello").write()

        result = TestResult()
        case = ValidationFailureProbe("test_bad_message")
        case.run(result)

        self.assertThat(
            result.errors,
            HasLength(1),
        )

    def test_skip_cleans_up(self):
        """
        After a skipped test the global Eliot logging state is restored.
        """
        # Save the logger that's active before we do anything so that we can
        # restore it later.  Also install another logger so we can compare it
        # to the active logger later.
        expected = MemoryLogger()
        original = swap_logger(expected)

        # Restore it, whatever else happens.
        self.addCleanup(lambda: swap_logger(original))

        class SkipProbe(SyncTestCase):
            @skip("It's a skip test.")
            def test_skipped(self):
                pass

        case = SkipProbe("test_skipped")
        case.run()

        # Retrieve the logger that's active now that the skipped test is done
        # so we can check it against the expected value.
        actual = swap_logger(MemoryLogger())
        self.assertThat(
            actual,
            Is(expected),
        )



class LogCallDeferredTests(TestCase):
    """
    Tests for ``log_call_deferred``.
    """
    @capture_logging(
        lambda self, logger:
        assertHasAction(self, logger, u"the-action", succeeded=True),
    )
    def test_return_value(self, logger):
        """
        The decorated function's return value is passed through.
        """
        result = object()
        @log_call_deferred(action_type=u"the-action")
        def f():
            return result
        self.assertThat(f(), succeeded(Is(result)))

    @capture_logging(
        lambda self, logger:
        assertHasAction(self, logger, u"the-action", succeeded=False),
    )
    def test_raise_exception(self, logger):
        """
        An exception raised by the decorated function is passed through.
        """
        class Result(Exception):
            pass
        @log_call_deferred(action_type=u"the-action")
        def f():
            raise Result()
        self.assertThat(
            f(),
            failed(
                AfterPreprocessing(
                    lambda f: f.value,
                    IsInstance(Result),
                ),
            ),
        )

    @capture_logging(
        lambda self, logger:
        assertHasAction(self, logger, u"the-action", succeeded=True),
    )
    def test_gets_args(self, logger):
        """
        Check that positional arguments are logged when using ``log_call_deferred``
        """
        @log_call_deferred(action_type=u"the-action")
        def f(a):
            return a ** 2
        self.assertThat(
            f(4), succeeded(Equals(16)))
        msg = logger.messages[0]
        assertContainsFields(self, msg, {"args": (4,)})

    @capture_logging(
        lambda self, logger:
        assertHasAction(self, logger, u"the-action", succeeded=True),
    )
    def test_gets_kwargs(self, logger):
        """
        Check that keyword arguments are logged when using ``log_call_deferred``
        """
        @log_call_deferred(action_type=u"the-action")
        def f(base, exp):
            return base ** exp
        self.assertThat(f(exp=2,base=10), succeeded(Equals(100)))
        msg = logger.messages[0]
        assertContainsFields(self, msg, {"kwargs": {"base": 10, "exp": 2}})


    @capture_logging(
        lambda self, logger:
        assertHasAction(self, logger, u"the-action", succeeded=True),
    )
    def test_gets_kwargs_and_args(self, logger):
        """
        Check that both keyword and positional arguments are logged when using ``log_call_deferred``
        """
        @log_call_deferred(action_type=u"the-action")
        def f(base, exp, message):
            return base ** exp
        self.assertThat(f(10, 2, message="an exponential function"), succeeded(Equals(100)))
        msg = logger.messages[0]
        assertContainsFields(self, msg, {"args": (10, 2)})
        assertContainsFields(self, msg, {"kwargs": {"message": "an exponential function"}})


    @capture_logging(
        lambda self, logger:
        assertHasAction(self, logger, u"the-action", succeeded=True),
    )
    def test_gets_unserializable_kwargs_and_args(self, logger):
        """
        Check that both keyword and positional arguments are logged when using ``log_call_deferred``
        """
        @log_call_deferred(action_type=u"the-action")
        def f(base, exp, not_serialiable, unserializable):
            return base ** exp
        d = defer.succeed('deferred objects are unserializable')
        self.assertThat(f(10, 2, d, unserializable=b"good-\xff-day"), succeeded(Equals(100)))
        msg = logger.messages[0]
        self.assertThat(msg['args'][2], Contains("deferred objects are unserializable"))
        assertContainsFields(self, msg, {'kwargs': {'unserializable': "b'good-\\xff-day'"}})


    @capture_logging(
        lambda self, logger:
        assertHasAction(self, logger, u"the-action", succeeded=True),
    )
    def test_kwargs_dont_repeat_in_start_action(self, logger):
        """
        Check that kwargs passed to ``log_call_deferred`` don't repeat in ``start_action``
        """
        @log_call_deferred(action_type=u"the-action")
        def f(base, exp, kwargs, args):
            return base ** exp
        self.assertThat(
            f(10, 2, kwargs={"kwarg_1": "value_1", "kwarg_2": 2}, args=(1, 2, 3)),
            succeeded(Equals(100)),
        )
        msg = logger.messages[0]
        assertContainsFields(self, msg, {"args": (10, 2)})
        assertContainsFields(
            self,
            msg,
            {"kwargs": {"args": (1, 2, 3), "kwargs": {"kwarg_1": "value_1", "kwarg_2": 2}}},
        )
