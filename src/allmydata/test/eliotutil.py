"""
Tools aimed at the interaction between tests and Eliot.

Ported to Python 3.
"""

from six import ensure_text

__all__ = [
    "RUN_TEST",
    "EliotLoggedRunTest",
]

from typing import Callable
from functools import (
    partial,
    wraps,
)

import attr

from zope.interface import (
    implementer,
)

from eliot import (
    ActionType,
    Field,
    ILogger,
)
from eliot.testing import (
    MemoryLogger,
    swap_logger,
    check_for_errors,
)

from twisted.python.monkey import (
    MonkeyPatcher,
)

from ..util.jsonbytes import (
    AnyBytesJSONEncoder
)

_NAME = Field.for_types(
    u"name",
    [str],
    u"The name of the test.",
)

RUN_TEST = ActionType(
    u"run-test",
    [_NAME],
    [],
    u"A test is run.",
)


@attr.s
class EliotLoggedRunTest(object):
    """
    A *RunTest* implementation which surrounds test invocation with an
    Eliot-based action.

    This *RunTest* composes with another for convenience.

    :ivar case: The test case to run.

    :ivar handlers: Pass-through for the wrapped *RunTest*.
    :ivar last_resort: Pass-through for the wrapped *RunTest*.

    :ivar _run_tests_with_factory: A factory for the other *RunTest*.
    """
    _run_tests_with_factory = attr.ib()
    case = attr.ib()
    handlers = attr.ib(default=None)
    last_resort = attr.ib(default=None)

    @classmethod
    def make_factory(cls, delegated_run_test_factory):
        return partial(cls, delegated_run_test_factory)

    @property
    def eliot_logger(self):
        return self.case.eliot_logger

    @eliot_logger.setter
    def eliot_logger(self, value):
        self.case.eliot_logger = value

    def addCleanup(self, *a, **kw):
        return self.case.addCleanup(*a, **kw)

    def id(self):
        return self.case.id()

    def run(self, result):
        """
        Run the test case in the context of a distinct Eliot action.

        The action will finish after the test is done.  It will note the name of
        the test being run.

        All messages emitted by the test will be validated.  They will still be
        delivered to the global logger.
        """
        # The idea here is to decorate the test method itself so that all of
        # the extra logic happens at the point where test/application logic is
        # expected to be.  This `run` method is more like test infrastructure
        # and things do not go well when we add too much extra behavior here.
        # For example, exceptions raised here often just kill the whole
        # runner.
        patcher = MonkeyPatcher()

        # So, grab the test method.
        name = self.case._testMethodName
        original = getattr(self.case, name)
        decorated = with_logging(ensure_text(self.case.id()), original)
        patcher.addPatch(self.case, name, decorated)
        try:
            # Patch it in
            patcher.patch()
            # Then use the rest of the machinery to run it.
            return self._run_tests_with_factory(
                self.case,
                self.handlers,
                self.last_resort,
            ).run(result)
        finally:
            # Clean up the patching for idempotency or something.
            patcher.restore()


def with_logging(
        test_id: str,
        test_method: Callable,
):
    """
    Decorate a test method with additional log-related behaviors.

    1. The test method will run in a distinct Eliot action.
    2. Typed log messages will be validated.
    3. Logged tracebacks will be added as errors.

    :param test_id: The full identifier of the test being decorated.
    :param test_method: The method itself.
    """
    @wraps(test_method)
    def run_with_logging(*args, **kwargs):
        validating_logger = MemoryLogger(encoder=AnyBytesJSONEncoder)
        original = swap_logger(None)
        try:
            swap_logger(_TwoLoggers(original, validating_logger))
            with RUN_TEST(name=test_id):
                try:
                    return test_method(*args, **kwargs)
                finally:
                    check_for_errors(validating_logger)
        finally:
            swap_logger(original)
    return run_with_logging


@implementer(ILogger)
class _TwoLoggers(object):
    """
    Log to two loggers.

    A single logger can have multiple destinations so this isn't typically a
    useful thing to do.  However, MemoryLogger has inline validation instead
    of destinations.  That means this *is* useful to simultaneously write to
    the normal places and validate all written log messages.
    """
    def __init__(self, a, b):
        """
        :param ILogger a: One logger
        :param ILogger b: Another logger
        """
        self._a = a # type: ILogger
        self._b = b # type: ILogger

    def write(self, dictionary, serializer=None):
        self._a.write(dictionary, serializer)
        self._b.write(dictionary, serializer)
