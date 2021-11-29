"""
Bring in some Eliot updates from newer versions of Eliot than we can
depend on in Python 2.  The implementations are copied from Eliot 1.14 and
only changed enough to add Python 2 compatibility.

Every API in this module (except ``eliot_json_encoder``) should be obsolete as
soon as we depend on Eliot 1.14 or newer.

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
from functools import partial

from eliot import (
    MemoryLogger as _MemoryLogger,
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
