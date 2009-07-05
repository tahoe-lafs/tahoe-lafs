
from twisted.trial import unittest
from allmydata.util.netstring import netstring, split_netstring

class Netstring(unittest.TestCase):
    def test_split(self):
        a = netstring("hello") + netstring("world")
        self.failUnlessEqual(split_netstring(a, 2), (["hello", "world"], len(a)))
        self.failUnlessEqual(split_netstring(a, 2, required_trailer=""), (["hello", "world"], len(a)))
        self.failUnlessRaises(ValueError, split_netstring, a, 3)
        self.failUnlessRaises(ValueError, split_netstring, a+" extra", 2, required_trailer="")
        self.failUnlessEqual(split_netstring(a+" extra", 2), (["hello", "world"], len(a)))
        self.failUnlessEqual(split_netstring(a+"++", 2, required_trailer="++"),
                             (["hello", "world"], len(a)+2))
        self.failUnlessRaises(ValueError,
                              split_netstring, a+"+", 2, required_trailer="not")

    def test_extra(self):
        a = netstring("hello")
        self.failUnlessEqual(split_netstring(a, 1), (["hello"], len(a)))
        b = netstring("hello") + "extra stuff"
        self.failUnlessEqual(split_netstring(b, 1),
                             (["hello"], len(a)))

    def test_nested(self):
        a = netstring("hello") + netstring("world") + "extra stuff"
        b = netstring("a") + netstring("is") + netstring(a) + netstring(".")
        (top, pos) = split_netstring(b, 4)
        self.failUnlessEqual(len(top), 4)
        self.failUnlessEqual(top[0], "a")
        self.failUnlessEqual(top[1], "is")
        self.failUnlessEqual(top[2], a)
        self.failUnlessEqual(top[3], ".")
        self.failUnlessRaises(ValueError, split_netstring, a, 2, required_trailer="")
        bottom = split_netstring(a, 2)
        self.failUnlessEqual(bottom, (["hello", "world"], len(netstring("hello")+netstring("world"))))
