"""
Tools aimed at the interaction between tests and Eliot.
"""

from sys import exc_info
from functools import wraps
from contextlib import contextmanager

from eliot import (
    Message,
    ActionType,
    Field,
)
from eliot.testing import capture_logging

from twisted.internet.defer import (
    maybeDeferred,
    inlineCallbacks,
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


@contextmanager
def _substitute_stack(substitute, target):
    # Save whatever is there to begin with, making a copy ensures we don't get
    # affected by any mutations that might happen while the substitute is in
    # place.
    saved = list(target)
    # Put the substitute in place.  Preserve the identity of the target for no
    # concrete reason but maybe it's a good idea.
    target[:] = substitute
    try:
        # Let some code run.
        yield
    finally:
        # Save whatever substitute state we ended up with back to the
        # substitute.  Copying again, here.
        substitute[:] = list(target)
        # Restore the target to its original state.  Again, preserving
        # identity.
        target[:] = saved


def eliot_friendly_generator_function(original):
    """
    Decorate a generator function so that the Eliot action context is
    preserved across ``yield`` expressions.
    """
    @wraps(original)
    def wrapper(*a, **kw):
        # Keep track of whether the next value to deliver to the generator is
        # a non-exception or an exception.
        ok = True

        # Keep track of the next value to deliver to the generator.
        value_in = None

        # Start tracking our desired inward-facing action context stack.  This
        # really wants some more help from Eliot.
        from eliot._action import _context
        context_in = list(_context._get_stack())

        # Create the generator with a call to the generator function.  This
        # happens with whatever Eliot action context happens to be active,
        # which is fine and correct and also irrelevant because no code in the
        # generator function can run until we call send or throw on it.
        gen = original(*a, **kw)
        try:
            while True:
                try:
                    # Whichever way we invoke the generator, we will do it
                    # with the Eliot action context stack we've saved for it.
                    # Then the context manager will re-save it and restore the
                    # "outside" stack for us.
                    with _substitute_stack(context_in, _context._get_stack()):
                        if ok:
                            value_out = gen.send(value_in)
                        else:
                            value_out = gen.throw(*value_in)
                        # We have obtained a value from the generator.  In
                        # giving it to us, it has given up control.  Note this
                        # fact here.  Importantly, this is within the
                        # generator's action context so that we get a good
                        # indication of where the yield occurred.
                        #
                        # This might be too noisy, consider dropping it or
                        # making it optional.
                        Message.log(message_type=u"yielded")
                except StopIteration:
                    # When the generator raises this, it is signaling
                    # completion.  Leave the loop.
                    break
                else:
                    try:
                        # Pass the generator's result along to whoever is
                        # driving.  Capture the result as the next value to
                        # send inward.
                        value_in = yield value_out
                    except:
                        # Or capture the exception if that's the flavor of the
                        # next value.
                        ok = False
                        value_in = exc_info()
                    else:
                        ok = True
        except GeneratorExit:
            # Is this the right scope for handling this exception?  Something
            # to check on.  Anyhow, if we get it, propagate it inward so the
            # generator we're driving knows we're done with it.
            gen.close()

    return wrapper

def inline_callbacks(original):
    """
    Decorate a function like ``inlineCallbacks`` would but in a more
    Eliot-friendly way.  Use it just like ``inlineCallbacks`` but where you
    want Eliot action contexts to Do The Right Thing inside the decorated
    function.
    """
    return inlineCallbacks(
        eliot_friendly_generator_function(original)
    )
