"""
Bring in some Eliot updates from newer versions of Eliot than we can
depend on in Python 2.  The implementations are copied from Eliot 1.14 and
only changed enough to add Python 2 compatibility.

Every API in this module (except ``eliot_json_encoder``) should be obsolete as
soon as we depend on Eliot 1.14 or newer.

When that happens:

* replace ``capture_logging``
  with ``partial(eliot.testing.capture_logging, encoder_=eliot_json_encoder)``
* replace ``validateLogging``
  with ``partial(eliot.testing.validateLogging, encoder_=eliot_json_encoder)``
* replace ``MemoryLogger``
  with ``partial(eliot.MemoryLogger, encoder=eliot_json_encoder)``

Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import json as pyjson
from functools import wraps, partial

from eliot import (
    MemoryLogger as _MemoryLogger,
)

from eliot.testing import (
    check_for_errors,
    swap_logger,
)

from .jsonbytes import AnyBytesJSONEncoder

# There are currently a number of log messages that include non-UTF-8 bytes.
# Allow these, at least for now.  Later when the whole test suite has been
# converted to our SyncTestCase or AsyncTestCase it will be easier to turn
# this off and then attribute log failures to specific codepaths so they can
# be fixed (and then not regressed later) because those instances will result
# in test failures instead of only garbage being written to the eliot log.
eliot_json_encoder = AnyBytesJSONEncoder

class _CustomEncoderMemoryLogger(_MemoryLogger):
    """
    Override message validation from the Eliot-supplied ``MemoryLogger`` to
    use our chosen JSON encoder.

    This is only necessary on Python 2 where we use an old version of Eliot
    that does not parameterize the encoder.
    """
    def __init__(self, encoder=eliot_json_encoder):
        """
        @param encoder: A JSONEncoder subclass to use when encoding JSON.
        """
        self._encoder = encoder
        super(_CustomEncoderMemoryLogger, self).__init__()

    def _validate_message(self, dictionary, serializer):
        """Validate an individual message.

        As a side-effect, the message is replaced with its serialized contents.

        @param dictionary: A message C{dict} to be validated.  Might be mutated
            by the serializer!

        @param serializer: C{None} or a serializer.

        @raises TypeError: If a field name is not unicode, or the dictionary
            fails to serialize to JSON.

        @raises eliot.ValidationError: If serializer was given and validation
            failed.
        """
        if serializer is not None:
            serializer.validate(dictionary)
        for key in dictionary:
            if not isinstance(key, str):
                if isinstance(key, bytes):
                    key.decode("utf-8")
                else:
                    raise TypeError(dictionary, "%r is not unicode" % (key,))
        if serializer is not None:
            serializer.serialize(dictionary)

        try:
            pyjson.dumps(dictionary, cls=self._encoder)
        except Exception as e:
            raise TypeError("Message %s doesn't encode to JSON: %s" % (dictionary, e))

if PY2:
    MemoryLogger = partial(_CustomEncoderMemoryLogger, encoder=eliot_json_encoder)
else:
    MemoryLogger = partial(_MemoryLogger, encoder=eliot_json_encoder)

def validateLogging(
    assertion, *assertionArgs, **assertionKwargs
):
    """
    Decorator factory for L{unittest.TestCase} methods to add logging
    validation.

    1. The decorated test method gets a C{logger} keyword argument, a
       L{MemoryLogger}.
    2. All messages logged to this logger will be validated at the end of
       the test.
    3. Any unflushed logged tracebacks will cause the test to fail.

    For example:

        from unittest import TestCase
        from eliot.testing import assertContainsFields, validateLogging

        class MyTests(TestCase):
            def assertFooLogging(self, logger):
                assertContainsFields(self, logger.messages[0], {"key": 123})


    @param assertion: A callable that will be called with the
       L{unittest.TestCase} instance, the logger and C{assertionArgs} and
       C{assertionKwargs} once the actual test has run, allowing for extra
       logging-related assertions on the effects of the test. Use L{None} if you
       want the cleanup assertions registered but no custom assertions.

    @param assertionArgs: Additional positional arguments to pass to
        C{assertion}.

    @param assertionKwargs: Additional keyword arguments to pass to
        C{assertion}.

    @param encoder_: C{json.JSONEncoder} subclass to use when validating JSON.
    """
    encoder_ = assertionKwargs.pop("encoder_", eliot_json_encoder)
    def decorator(function):
        @wraps(function)
        def wrapper(self, *args, **kwargs):
            skipped = False

            kwargs["logger"] = logger = MemoryLogger(encoder=encoder_)
            self.addCleanup(check_for_errors, logger)
            # TestCase runs cleanups in reverse order, and we want this to
            # run *before* tracebacks are checked:
            if assertion is not None:
                self.addCleanup(
                    lambda: skipped
                    or assertion(self, logger, *assertionArgs, **assertionKwargs)
                )
            try:
                return function(self, *args, **kwargs)
            except self.skipException:
                skipped = True
                raise

        return wrapper

    return decorator

# PEP 8 variant:
validate_logging = validateLogging

def capture_logging(
    assertion, *assertionArgs, **assertionKwargs
):
    """
    Capture and validate all logging that doesn't specify a L{Logger}.

    See L{validate_logging} for details on the rest of its behavior.
    """
    encoder_ = assertionKwargs.pop("encoder_", eliot_json_encoder)
    def decorator(function):
        @validate_logging(
            assertion, *assertionArgs, encoder_=encoder_, **assertionKwargs
        )
        @wraps(function)
        def wrapper(self, *args, **kwargs):
            logger = kwargs["logger"]
            previous_logger = swap_logger(logger)

            def cleanup():
                swap_logger(previous_logger)

            self.addCleanup(cleanup)
            return function(self, *args, **kwargs)

        return wrapper

    return decorator
