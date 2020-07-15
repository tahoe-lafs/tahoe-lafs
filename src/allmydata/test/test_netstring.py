"""
Tests for allmydata.util.netstring.
"""

from twisted.trial import unittest

from allmydata.util.netstring import netstring, split_netstring


class Netstring(unittest.TestCase):
    def test_encode(self):
        """netstring() correctly encodes the given bytes."""
        self.assertEqual(netstring(b"abc"), b"3:abc,")

    def test_split(self):
        a = netstring(b"hello") + netstring(b"world")
        self.failUnlessEqual(split_netstring(a, 2), ([b"hello", b"world"], len(a)))
        self.failUnlessEqual(split_netstring(a, 2, required_trailer=""), ([b"hello", b"world"], len(a)))
        self.failUnlessRaises(ValueError, split_netstring, a, 3)
        self.failUnlessRaises(ValueError, split_netstring, a+b" extra", 2, required_trailer="")
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
