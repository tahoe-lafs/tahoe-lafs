"""
Tests for allmydata.util.base32.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import base64

from twisted.trial import unittest

from hypothesis import (
    strategies as st,
    given,
)
from allmydata.util import base32


class Base32(unittest.TestCase):

    @given(input_bytes=st.binary(max_size=100))
    def test_a2b_b2a_match_Pythons(self, input_bytes):
        encoded = base32.b2a(input_bytes)
        x = base64.b32encode(input_bytes).rstrip(b"=").lower()
        self.failUnlessEqual(encoded, x)
        self.assertIsInstance(encoded, bytes)
        self.assertTrue(base32.could_be_base32_encoded(encoded))
        decoded = base32.a2b(encoded)
        self.assertEqual(decoded, input_bytes)
        self.assertIsInstance(decoded, bytes)

    def test_b2a(self):
        self.failUnlessEqual(base32.b2a(b"\x12\x34"), b"ci2a")

    def test_b2a_or_none(self):
        self.failUnlessEqual(base32.b2a_or_none(None), None)
        self.failUnlessEqual(base32.b2a_or_none(b"\x12\x34"), b"ci2a")

    def test_a2b(self):
        self.failUnlessEqual(base32.a2b(b"ci2a"), b"\x12\x34")
        self.failUnlessRaises(AssertionError, base32.a2b, b"b0gus")
        self.assertFalse(base32.could_be_base32_encoded(b"b0gus"))
