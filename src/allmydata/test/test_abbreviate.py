"""
Tests for allmydata.util.abbreviate.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from datetime import timedelta

from twisted.trial import unittest

from allmydata.util import abbreviate


class Abbreviate(unittest.TestCase):
    def test_abbrev_time_1s(self):
        diff = timedelta(seconds=1)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('1 second ago', s)

    def test_abbrev_time_25s(self):
        diff = timedelta(seconds=25)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('25 seconds ago', s)

    def test_abbrev_time_future_5_minutes(self):
        diff = timedelta(minutes=-5)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('5 minutes in the future', s)

    def test_abbrev_time_hours(self):
        diff = timedelta(hours=4)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('4 hours ago', s)

    def test_abbrev_time_day(self):
        diff = timedelta(hours=49)  # must be more than 2 days
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('2 days ago', s)

    def test_abbrev_time_month(self):
        diff = timedelta(days=91)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('3 months ago', s)

    def test_abbrev_time_year(self):
        diff = timedelta(weeks=(5 * 52) + 1)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('5 years ago', s)

    def test_time(self):
        a = abbreviate.abbreviate_time
        self.failUnlessEqual(a(None), "unknown")
        self.failUnlessEqual(a(0), "0 seconds")
        self.failUnlessEqual(a(1), "1 second")
        self.failUnlessEqual(a(2), "2 seconds")
        self.failUnlessEqual(a(119), "119 seconds")
        MIN = 60
        self.failUnlessEqual(a(2*MIN), "2 minutes")
        self.failUnlessEqual(a(60*MIN), "60 minutes")
        self.failUnlessEqual(a(179*MIN), "179 minutes")
        HOUR = 60*MIN
        self.failUnlessEqual(a(180*MIN), "3 hours")
        self.failUnlessEqual(a(4*HOUR), "4 hours")
        DAY = 24*HOUR
        MONTH = 30*DAY
        self.failUnlessEqual(a(2*DAY), "2 days")
        self.failUnlessEqual(a(2*MONTH), "2 months")
        YEAR = 365*DAY
        self.failUnlessEqual(a(5*YEAR), "5 years")

    def test_space(self):
        tests_si = [(None, "unknown"),
                    (0, "0 B"),
                    (1, "1 B"),
                    (999, "999 B"),
                    (1000, "1000 B"),
                    (1023, "1023 B"),
                    (1024, "1.02 kB"),
                    (20*1000, "20.00 kB"),
                    (1024*1024, "1.05 MB"),
                    (1000*1000, "1.00 MB"),
                    (1000*1000*1000, "1.00 GB"),
                    (1000*1000*1000*1000, "1.00 TB"),
                    (1000*1000*1000*1000*1000, "1.00 PB"),
                    (1000*1000*1000*1000*1000*1000, "1.00 EB"),
                    (1234567890123456789, "1.23 EB"),
                    ]
        for (x, expected) in tests_si:
            got = abbreviate.abbreviate_space(x, SI=True)
            self.failUnlessEqual(got, expected)

        tests_base1024 = [(None, "unknown"),
                          (0, "0 B"),
                          (1, "1 B"),
                          (999, "999 B"),
                          (1000, "1000 B"),
                          (1023, "1023 B"),
                          (1024, "1.00 kiB"),
                          (20*1024, "20.00 kiB"),
                          (1000*1000, "976.56 kiB"),
                          (1024*1024, "1.00 MiB"),
                          (1024*1024*1024, "1.00 GiB"),
                          (1024*1024*1024*1024, "1.00 TiB"),
                          (1000*1000*1000*1000*1000, "909.49 TiB"),
                          (1024*1024*1024*1024*1024, "1.00 PiB"),
                          (1024*1024*1024*1024*1024*1024, "1.00 EiB"),
                          (1234567890123456789, "1.07 EiB"),
                    ]
        for (x, expected) in tests_base1024:
            got = abbreviate.abbreviate_space(x, SI=False)
            self.failUnlessEqual(got, expected)

        self.failUnlessEqual(abbreviate.abbreviate_space_both(1234567),
                             "(1.23 MB, 1.18 MiB)")

    def test_parse_space(self):
        p = abbreviate.parse_abbreviated_size
        self.failUnlessEqual(p(""), None)
        self.failUnlessEqual(p(None), None)
        self.failUnlessEqual(p("123"), 123)
        self.failUnlessEqual(p("123B"), 123)
        self.failUnlessEqual(p("2K"), 2000)
        self.failUnlessEqual(p("2kb"), 2000)
        self.failUnlessEqual(p("2KiB"), 2048)
        self.failUnlessEqual(p("10MB"), 10*1000*1000)
        self.failUnlessEqual(p("10MiB"), 10*1024*1024)
        self.failUnlessEqual(p("5G"), 5*1000*1000*1000)
        self.failUnlessEqual(p("4GiB"), 4*1024*1024*1024)
        self.failUnlessEqual(p("3TB"), 3*1000*1000*1000*1000)
        self.failUnlessEqual(p("3TiB"), 3*1024*1024*1024*1024)
        self.failUnlessEqual(p("6PB"), 6*1000*1000*1000*1000*1000)
        self.failUnlessEqual(p("6PiB"), 6*1024*1024*1024*1024*1024)
        self.failUnlessEqual(p("9EB"), 9*1000*1000*1000*1000*1000*1000)
        self.failUnlessEqual(p("9EiB"), 9*1024*1024*1024*1024*1024*1024)

        e = self.failUnlessRaises(ValueError, p, "12 cubits")
        self.failUnlessIn("12 cubits", str(e))
        e = self.failUnlessRaises(ValueError, p, "1 BB")
        self.failUnlessIn("1 BB", str(e))
        e = self.failUnlessRaises(ValueError, p, "fhtagn")
        self.failUnlessIn("fhtagn", str(e))
