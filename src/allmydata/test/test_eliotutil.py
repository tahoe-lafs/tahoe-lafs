"""
Tests for ``allmydata.test.eliotutil``.
"""

from __future__ import (
    unicode_literals,
    print_function,
    absolute_import,
    division,
)

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
    IsInstance,
    MatchesStructure,
    Equals,
    AfterPreprocessing,
)
from testtools.twistedsupport import (
    succeeded,
    failed,
)

from eliot import (
    Message,
    FileDestination,
)
from eliot.twisted import DeferredContext
from eliot.testing import (
    capture_logging,
    assertHasAction,
)

from twisted.internet.defer import (
    succeed,
)
from twisted.internet.task import deferLater
from twisted.internet import reactor

from ..util.eliotutil import (
    log_call_deferred,
    _parse_destination_description,
    _EliotLogging,
)
from .common import (
    SyncTestCase,
    AsyncTestCase,
)

class EliotLoggedTestTests(AsyncTestCase):
    def test_returns_none(self):
        Message.log(hello="world")

    def test_returns_fired_deferred(self):
        Message.log(hello="world")
        return succeed(None)

    def test_returns_unfired_deferred(self):
        Message.log(hello="world")
        # @eliot_logged_test automatically gives us an action context but it's
        # still our responsibility to maintain it across stack-busting
        # operations.
        d = DeferredContext(deferLater(reactor, 0.0, lambda: None))
        d.addCallback(lambda ignored: Message.log(goodbye="world"))
        # We didn't start an action.  We're not finishing an action.
        return d.result



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
