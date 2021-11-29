"""
Bring in some Eliot updates from newer versions of Eliot than we can
depend on in Python 2.

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

from .jsonbytes import AnyBytesJSONEncoder

# There are currently a number of log messages that include non-UTF-8 bytes.
# Allow these, at least for now.  Later when the whole test suite has been
# converted to our SyncTestCase or AsyncTestCase it will be easier to turn
# this off and then attribute log failures to specific codepaths so they can
# be fixed (and then not regressed later) because those instances will result
# in test failures instead of only garbage being written to the eliot log.
eliot_json_encoder = AnyBytesJSONEncoder
