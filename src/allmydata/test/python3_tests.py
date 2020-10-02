"""
This module defines the subset of the full test suite which is expected to
pass on Python 3 in a way which makes that suite discoverable by trial.

This module has been ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.python.reflect import (
    namedModule,
)
from twisted.trial.runner import (
    TestLoader,
)
from twisted.trial.unittest import (
    TestSuite,
)

from allmydata.util._python3 import (
    PORTED_TEST_MODULES,
)

def testSuite():
    loader = TestLoader()
    return TestSuite(list(
        loader.loadModule(namedModule(module))
        for module
        in PORTED_TEST_MODULES
    ))
