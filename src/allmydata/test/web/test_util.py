"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.trial import unittest
from allmydata.web import status, common
from allmydata.dirnode import ONLY_FILES
from ..common import ShouldFailMixin
from .. import common_util as testutil

class Util(ShouldFailMixin, testutil.ReallyEqualMixin, unittest.TestCase):

    def test_parse_replace_arg(self):
        self.failUnlessReallyEqual(common.parse_replace_arg(b"true"), True)
        self.failUnlessReallyEqual(common.parse_replace_arg(b"false"), False)
        self.failUnlessReallyEqual(common.parse_replace_arg(b"only-files"),
                                   ONLY_FILES)
        self.failUnlessRaises(common.WebError, common.parse_replace_arg, b"only_fles")

    def test_abbreviate_time(self):
        self.failUnlessReallyEqual(common.abbreviate_time(None), "")
        self.failUnlessReallyEqual(common.abbreviate_time(1.234), "1.23s")
        self.failUnlessReallyEqual(common.abbreviate_time(0.123), "123ms")
        self.failUnlessReallyEqual(common.abbreviate_time(0.00123), "1.2ms")
        self.failUnlessReallyEqual(common.abbreviate_time(0.000123), "123us")
        self.failUnlessReallyEqual(common.abbreviate_time(-123000), "-123000000000us")
        self.failUnlessReallyEqual(common.abbreviate_time(2.5), "2.50s")
        self.failUnlessReallyEqual(common.abbreviate_time(0.25), "250ms")
        self.failUnlessReallyEqual(common.abbreviate_time(0.0021), "2.1ms")

        self.failUnlessReallyEqual(common.abbreviate_time(None), "")
        self.failUnlessReallyEqual(common.abbreviate_time(2.5), "2.50s")
        self.failUnlessReallyEqual(common.abbreviate_time(0.25), "250ms")
        self.failUnlessReallyEqual(common.abbreviate_time(0.0021), "2.1ms")
        self.failUnlessReallyEqual(common.abbreviate_time(0.000123), "123us")
        self.failUnlessReallyEqual(common.abbreviate_rate(None), "")
        self.failUnlessReallyEqual(common.abbreviate_rate(2500000), "2.50MBps")
        self.failUnlessReallyEqual(common.abbreviate_rate(30100), "30.1kBps")
        self.failUnlessReallyEqual(common.abbreviate_rate(123), "123Bps")

    def test_compute_rate(self):
        self.failUnlessReallyEqual(common.compute_rate(None, None), None)
        self.failUnlessReallyEqual(common.compute_rate(None, 1), None)
        self.failUnlessReallyEqual(common.compute_rate(250000, None), None)
        self.failUnlessReallyEqual(common.compute_rate(250000, 0), None)
        self.failUnlessReallyEqual(common.compute_rate(250000, 10), 25000.0)
        self.failUnlessReallyEqual(common.compute_rate(0, 10), 0.0)
        self.shouldFail(AssertionError, "test_compute_rate", "",
                        common.compute_rate, -100, 10)
        self.shouldFail(AssertionError, "test_compute_rate", "",
                        common.compute_rate, 100, -10)

        # Sanity check
        rate = common.compute_rate(10*1000*1000, 1)
        self.failUnlessReallyEqual(common.abbreviate_rate(rate), "10.00MBps")

    def test_abbreviate_rate(self):
        self.failUnlessReallyEqual(common.abbreviate_rate(None), "")
        self.failUnlessReallyEqual(common.abbreviate_rate(1234000), "1.23MBps")
        self.failUnlessReallyEqual(common.abbreviate_rate(12340), "12.3kBps")
        self.failUnlessReallyEqual(common.abbreviate_rate(123), "123Bps")
        self.failUnlessReallyEqual(common.abbreviate_rate(2500000), "2.50MBps")
        self.failUnlessReallyEqual(common.abbreviate_rate(30100), "30.1kBps")
        self.failUnlessReallyEqual(common.abbreviate_rate(123), "123Bps")

    def test_abbreviate_size(self):
        self.failUnlessReallyEqual(common.abbreviate_size(None), "")
        self.failUnlessReallyEqual(common.abbreviate_size(1.23*1000*1000*1000), "1.23GB")
        self.failUnlessReallyEqual(common.abbreviate_size(1.23*1000*1000), "1.23MB")
        self.failUnlessReallyEqual(common.abbreviate_size(1230), "1.2kB")
        self.failUnlessReallyEqual(common.abbreviate_size(123), "123B")

    def test_plural(self):
        def convert(s):
            return "%d second%s" % (s, status.plural(s))
        self.failUnlessReallyEqual(convert(0), "0 seconds")
        self.failUnlessReallyEqual(convert(1), "1 second")
        self.failUnlessReallyEqual(convert(2), "2 seconds")
        def convert2(s):
            return "has share%s: %s" % (status.plural(s), ",".join(s))
        self.failUnlessReallyEqual(convert2([]), "has shares: ")
        self.failUnlessReallyEqual(convert2(["1"]), "has share: 1")
        self.failUnlessReallyEqual(convert2(["1","2"]), "has shares: 1,2")
