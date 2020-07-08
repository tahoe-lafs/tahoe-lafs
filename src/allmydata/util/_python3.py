"""
Track the port to Python 3.

This module has been ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, int, list, object, range, str, max, min  # noqa: F401

# Keep these sorted alphabetically, to reduce merge conflicts:
PORTED_MODULES = [
    "allmydata.util.assertutil",
    "allmydata.util.humanreadable",
    "allmydata.util.namespace",
    "allmydata.util._python3",
]

PORTED_TEST_MODULES = [
    "allmydata.test.test_humanreadable",
    "allmydata.test.test_python3",
]
