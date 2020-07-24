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
    "allmydata.hashtree",
    "allmydata.util.abbreviate",
    "allmydata.util.assertutil",
    "allmydata.util.base32",
    "allmydata.util.base62",
    "allmydata.util.deferredutil",
    "allmydata.util.hashutil",
    "allmydata.util.humanreadable",
    "allmydata.util.mathutil",
    "allmydata.util.namespace",
    "allmydata.util.netstring",
    "allmydata.util.observer",
    "allmydata.util.pipeline",
    "allmydata.util.pollmixin",
    "allmydata.util._python3",
    "allmydata.util.time_format",
    "allmydata.test.common_py3",
]

PORTED_TEST_MODULES = [
    "allmydata.test.test_abbreviate",
    "allmydata.test.test_base32",
    "allmydata.test.test_base62",
    "allmydata.test.test_deferredutil",
    "allmydata.test.test_hashtree",
    "allmydata.test.test_hashutil",
    "allmydata.test.test_humanreadable",
    "allmydata.test.test_netstring",
    "allmydata.test.test_observer",
    "allmydata.test.test_pipeline",
    "allmydata.test.test_python3",
    "allmydata.test.test_time_format",
]


if __name__ == '__main__':
    from subprocess import check_call
    check_call(["trial"] + PORTED_TEST_MODULES)
