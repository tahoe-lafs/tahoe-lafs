"""
Tests for allmydata.util.base32.
"""

import base64

from twisted.trial import unittest

from allmydata.util import base32


class Base32(unittest.TestCase):
    def test_b2a_matches_Pythons(self):
        y = "\x12\x34\x45\x67\x89\x0a\xbc\xde\xf0"
        x = base64.b32encode(y)
        while x and x[-1] == '=':
            x = x[:-1]
        x = x.lower()
        self.failUnlessEqual(base32.b2a(y), x)
    def test_b2a(self):
        self.failUnlessEqual(base32.b2a("\x12\x34"), "ci2a")
    def test_b2a_or_none(self):
        self.failUnlessEqual(base32.b2a_or_none(None), None)
        self.failUnlessEqual(base32.b2a_or_none("\x12\x34"), "ci2a")
    def test_a2b(self):
        self.failUnlessEqual(base32.a2b("ci2a"), "\x12\x34")
        self.failUnlessRaises(AssertionError, base32.a2b, "b0gus")
