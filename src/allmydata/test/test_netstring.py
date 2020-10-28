"""
Tests for allmydata.util.netstring.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.trial import unittest

from allmydata.util.netstring import netstring, split_netstring


class Netstring(unittest.TestCase):
    def test_encode(self):
        """netstring() correctly encodes the given bytes."""
        result = netstring(b"abc")
        self.assertEqual(result, b"3:abc,")
        self.assertIsInstance(result, bytes)

    def test_split(self):
        a = netstring(b"hello") + netstring(b"world")
        for s in split_netstring(a, 2)[0]:
            self.assertIsInstance(s, bytes)
        self.failUnlessEqual(split_netstring(a, 2), ([b"hello", b"world"], len(a)))
        self.failUnlessEqual(split_netstring(a, 2, required_trailer=b""), ([b"hello", b"world"], len(a)))
        self.failUnlessRaises(ValueError, split_netstring, a, 3)
        self.failUnlessRaises(ValueError, split_netstring, a+b" extra", 2, required_trailer=b"")
        self.failUnlessEqual(split_netstring(a+b" extra", 2), ([b"hello", b"world"], len(a)))
        self.failUnlessEqual(split_netstring(a+b"++", 2, required_trailer=b"++"),
                             ([b"hello", b"world"], len(a)+2))
        self.failUnlessRaises(ValueError,
                              split_netstring, a+b"+", 2, required_trailer=b"not")

    def test_extra(self):
        a = netstring(b"hello")
        self.failUnlessEqual(split_netstring(a, 1), ([b"hello"], len(a)))
        b = netstring(b"hello") + b"extra stuff"
        self.failUnlessEqual(split_netstring(b, 1),
                             ([b"hello"], len(a)))

    def test_nested(self):
        a = netstring(b"hello") + netstring(b"world") + b"extra stuff"
        b = netstring(b"a") + netstring(b"is") + netstring(a) + netstring(b".")
        (top, pos) = split_netstring(b, 4)
        self.failUnlessEqual(len(top), 4)
        self.failUnlessEqual(top[0], b"a")
        self.failUnlessEqual(top[1], b"is")
        self.failUnlessEqual(top[2], a)
        self.failUnlessEqual(top[3], b".")
        self.failUnlessRaises(ValueError, split_netstring, a, 2, required_trailer=b"")
        bottom = split_netstring(a, 2)
        self.failUnlessEqual(bottom, ([b"hello", b"world"], len(netstring(b"hello")+netstring(b"world"))))
