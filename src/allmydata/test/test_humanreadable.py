"""Tests for allmydata.util.humanreadable."""

from twisted.trial import unittest

from allmydata.util import humanreadable




def foo(): pass # keep the line number constant


class NoArgumentException(Exception):
    def __init__(self):
        pass

class HumanReadable(unittest.TestCase):
    def test_repr(self):
        hr = humanreadable.hr
        self.failUnlessEqual(hr(foo), "<foo() at test_humanreadable.py:10>")
        self.failUnlessEqual(hr(self.test_repr),
                             "<bound method HumanReadable.test_repr of <allmydata.test.test_humanreadable.HumanReadable testMethod=test_repr>>")
        self.failUnlessEqual(hr(long(1)), "1")
        self.failUnlessEqual(hr(10**40),
                             "100000000000000000...000000000000000000")
        self.failUnlessEqual(hr(self), "<allmydata.test.test_humanreadable.HumanReadable testMethod=test_repr>")
        self.failUnlessEqual(hr([1,2]), "[1, 2]")
        self.failUnlessEqual(hr({1:2}), "{1:2}")
        try:
            raise ValueError
        except Exception as e:
            self.failUnless(
                hr(e) == "<ValueError: ()>" # python-2.4
                or hr(e) == "ValueError()") # python-2.5
        try:
            raise ValueError("oops")
        except Exception as e:
            self.failUnless(
                hr(e) == "<ValueError: 'oops'>" # python-2.4
                or hr(e) == "ValueError('oops',)") # python-2.5
        try:
            raise NoArgumentException
        except Exception as e:
            self.failUnless(
                hr(e) == "<NoArgumentException>" # python-2.4
                or hr(e) == "NoArgumentException()") # python-2.5
