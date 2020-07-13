"""
Tests for allmydata.util.base32.
"""

import base64

from twisted.trial import unittest

from allmydata.util import base32


class Base32(unittest.TestCase):
    def test_b2a_matches_Pythons(self):
        y = b"\x12\x34\x45\x67\x89\x0a\xbc\xde\xf0"
        x = base64.b32encode(y)
        x = x.rstrip(b"=")
        x = x.lower()
        result = base32.b2a(y)
        self.failUnlessEqual(result, x)
        self.assertIsInstance(result, bytes)

    def test_b2a(self):
        self.failUnlessEqual(base32.b2a(b"\x12\x34"), b"ci2a")

    def test_b2a_or_none(self):
        self.failUnlessEqual(base32.b2a_or_none(None), None)
        self.failUnlessEqual(base32.b2a_or_none(b"\x12\x34"), b"ci2a")

    def test_a2b(self):
        self.failUnlessEqual(base32.a2b(b"ci2a"), b"\x12\x34")
        self.failUnlessRaises(AssertionError, base32.a2b, b"b0gus")
