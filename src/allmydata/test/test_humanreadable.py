"""
Tests for allmydata.util.humanreadable.

This module has been ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from past.builtins import long

from twisted.trial import unittest

from allmydata.util import humanreadable



def foo(): pass # FYI foo()'s line number is used in the test below


class NoArgumentException(Exception):
    def __init__(self):
        pass

class HumanReadable(unittest.TestCase):
    def test_repr(self):
        hr = humanreadable.hr
        self.failUnlessEqual(hr(foo), "<foo() at test_humanreadable.py:24>")
        self.failUnlessEqual(hr(self.test_repr),
                             "<bound method HumanReadable.test_repr of <allmydata.test.test_humanreadable.HumanReadable testMethod=test_repr>>")
        self.failUnlessEqual(hr(long(1)), "1")
        self.assertIn(hr(10**40),
                      ["100000000000000000...000000000000000000",
                       "100000000000000000...0000000000000000000"])
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
                or hr(e) == "ValueError('oops',)" # python-2.5
                or hr(e) == "ValueError(u'oops',)" # python 2 during py3 transition
            )
        try:
            raise NoArgumentException
        except Exception as e:
            self.failUnless(
                hr(e) == "<NoArgumentException>" # python-2.4
                or hr(e) == "NoArgumentException()" # python-2.5
                or hr(e) == "<NoArgumentException: ()>", hr(e)) # python-3
