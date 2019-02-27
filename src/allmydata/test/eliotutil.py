"""
Tools aimed at the interaction between tests and Eliot.
"""

from functools import wraps

from eliot import (
    ActionType,
    Field,
)
from eliot.testing import capture_logging

from twisted.internet.defer import (
    maybeDeferred,
)

_NAME = Field.for_types(
    u"name",
    [unicode],
    u"The name of the test.",
)

RUN_TEST = ActionType(
    u"run-test",
    [_NAME],
    [],
    u"A test is run.",
)


def eliot_logged_test(f):
    """
    Decorate a test method to run in a dedicated Eliot action context.

    The action will finish after the test is done (after the returned Deferred
    fires, if a Deferred is returned).  It will note the name of the test
    being run.

    All messages emitted by the test will be validated.  They will still be
    delivered to the global logger.
    """
    # A convenient, mutable container into which nested functions can write
    # state to be shared among them.
    class storage:
        pass

    @wraps(f)
    def run_and_republish(self, *a, **kw):
        def republish():
            # This is called as a cleanup function after capture_logging has
            # restored the global/default logger to its original state.  We
            # can now emit messages that go to whatever global destinations
            # are installed.

            # Unfortunately the only way to get at the global/default
            # logger...
            from eliot._output import _DEFAULT_LOGGER as logger

            # storage.logger.serialize() seems like it would make more sense
            # than storage.logger.messages here.  However, serialize()
            # explodes, seemingly as a result of double-serializing the logged
            # messages.  I don't understand this.
            for msg in storage.logger.messages:
                logger.write(msg)

            # And now that we've re-published all of the test's messages, we
            # can finish the test's action.
            storage.action.finish()

        @capture_logging(None)
        def run(self, logger):
            # Record the MemoryLogger for later message extraction.
            storage.logger = logger
            # Give the test access to the logger as well.  It would be just
            # fine to pass this as a keyword argument to `f` but implementing
            # that now will give me conflict headaches so I'm not doing it.
            self.eliot_logger = logger
            return f(self, *a, **kw)

        # Arrange for all messages written to the memory logger that
        # `capture_logging` installs to be re-written to the global/default
        # logger so they might end up in a log file somewhere, if someone
        # wants.  This has to be done in a cleanup function (or later) because
        # capture_logging restores the original logger in a cleanup function.
        # We install our cleanup function here, before we call run, so that it
        # runs *after* the cleanup function capture_logging installs (cleanup
        # functions are a stack).
        self.addCleanup(republish)

        # Begin an action that should comprise all messages from the decorated
        # test method.
        with RUN_TEST(name=self.id().decode("utf-8")).context() as action:
            # When the test method Deferred fires, the RUN_TEST action is
            # done.  However, we won't have re-published the MemoryLogger
            # messages into the global/default logger when this Deferred
            # fires.  So we need to delay finishing the action until that has
            # happened.  Record the action so we can do that.
            storage.action = action

            # Support both Deferred-returning and non-Deferred-returning
            # tests.
            d = maybeDeferred(run, self)

            # Let the test runner do its thing.
            return d

    return run_and_republish
