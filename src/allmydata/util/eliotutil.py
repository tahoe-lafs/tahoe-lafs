"""
Tools aimed at the interaction between Tahoe-LAFS implementation and Eliot.
"""

from sys import exc_info
from functools import wraps
from contextlib import contextmanager

from eliot import (
    Message,
)

from twisted.internet.defer import (
    inlineCallbacks,
)

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
