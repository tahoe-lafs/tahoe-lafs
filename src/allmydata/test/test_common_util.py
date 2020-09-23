"""
This module has been ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import random
import unittest

from allmydata.test.common_util import flip_one_bit


class TestFlipOneBit(unittest.TestCase):

    def setUp(self):
        random.seed(42)  # I tried using version=1 on PY3 to avoid the if below, to no avail.

    def test_accepts_byte_string(self):
        actual = flip_one_bit(b'foo')
        self.assertEqual(actual, b'fno' if PY2 else b'fom')

    def test_rejects_unicode_string(self):
        self.assertRaises(AssertionError, flip_one_bit, u'foo')
