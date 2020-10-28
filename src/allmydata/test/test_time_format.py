"""
Tests for allmydata.util.time_format.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from past.builtins import long

import time

from twisted.trial import unittest

from allmydata.test.common_util import TimezoneMixin
from allmydata.util import time_format


class TimeFormat(unittest.TestCase, TimezoneMixin):
    def test_epoch(self):
        return self._help_test_epoch()

    def test_epoch_in_London(self):
        # Europe/London is a particularly troublesome timezone.  Nowadays, its
        # offset from GMT is 0.  But in 1970, its offset from GMT was 1.
        # (Apparently in 1970 Britain had redefined standard time to be GMT+1
        # and stayed in standard time all year round, whereas today
        # Europe/London standard time is GMT and Europe/London Daylight
        # Savings Time is GMT+1.)  The current implementation of
        # time_format.iso_utc_time_to_localseconds() breaks if the timezone is
        # Europe/London.  (As soon as this unit test is done then I'll change
        # that implementation to something that works even in this case...)

        if not self.have_working_tzset():
            raise unittest.SkipTest("This test can't be run on a platform without time.tzset().")

        self.setTimezone("Europe/London")
        return self._help_test_epoch()

    def _help_test_epoch(self):
        origtzname = time.tzname
        s = time_format.iso_utc_time_to_seconds("1970-01-01T00:00:01")
        self.failUnlessEqual(s, 1.0)
        s = time_format.iso_utc_time_to_seconds("1970-01-01_00:00:01")
        self.failUnlessEqual(s, 1.0)
        s = time_format.iso_utc_time_to_seconds("1970-01-01 00:00:01")
        self.failUnlessEqual(s, 1.0)

        self.failUnlessEqual(time_format.iso_utc(1.0), "1970-01-01_00:00:01")
        self.failUnlessEqual(time_format.iso_utc(1.0, sep=" "),
                             "1970-01-01 00:00:01")

        now = time.time()
        isostr = time_format.iso_utc(now)
        timestamp = time_format.iso_utc_time_to_seconds(isostr)
        self.failUnlessEqual(int(timestamp), int(now))

        def my_time():
            return 1.0
        self.failUnlessEqual(time_format.iso_utc(t=my_time),
                             "1970-01-01_00:00:01")
        e = self.failUnlessRaises(ValueError,
                                  time_format.iso_utc_time_to_seconds,
                                  "invalid timestring")
        self.failUnless("not a complete ISO8601 timestamp" in str(e))
        s = time_format.iso_utc_time_to_seconds("1970-01-01_00:00:01.500")
        self.failUnlessEqual(s, 1.5)

        # Look for daylight-savings-related errors.
        thatmomentinmarch = time_format.iso_utc_time_to_seconds("2009-03-20 21:49:02.226536")
        self.failUnlessEqual(thatmomentinmarch, 1237585742.226536)
        self.failUnlessEqual(origtzname, time.tzname)

    def test_iso_utc(self):
        when = 1266760143.7841301
        out = time_format.iso_utc_date(when)
        self.failUnlessEqual(out, "2010-02-21")
        out = time_format.iso_utc_date(t=lambda: when)
        self.failUnlessEqual(out, "2010-02-21")
        out = time_format.iso_utc(when)
        self.failUnlessEqual(out, "2010-02-21_13:49:03.784130")
        out = time_format.iso_utc(when, sep="-")
        self.failUnlessEqual(out, "2010-02-21-13:49:03.784130")

    def test_parse_duration(self):
        p = time_format.parse_duration
        DAY = 24*60*60
        MONTH = 31*DAY
        YEAR = 365*DAY
        self.failUnlessEqual(p("1 day"), DAY)
        self.failUnlessEqual(p("2 days"), 2*DAY)
        self.failUnlessEqual(p("3 months"), 3*MONTH)
        self.failUnlessEqual(p("4 mo"), 4*MONTH)
        self.failUnlessEqual(p("5 years"), 5*YEAR)
        e = self.failUnlessRaises(ValueError, p, "123")
        self.failUnlessIn("no unit (like day, month, or year) in '123'",
                          str(e))
        self.failUnlessEqual(p("7days"), 7*DAY)
        self.failUnlessEqual(p("31day"), 31*DAY)
        self.failUnlessEqual(p("60 days"), 60*DAY)
        self.failUnlessEqual(p("2mo"), 2*MONTH)
        self.failUnlessEqual(p("3 month"), 3*MONTH)
        self.failUnlessEqual(p("2years"), 2*YEAR)
        e = self.failUnlessRaises(ValueError, p, "2kumquats")
        self.failUnlessIn("no unit (like day, month, or year) in '2kumquats'", str(e))

    def test_parse_date(self):
        p = time_format.parse_date
        self.failUnlessEqual(p("2010-02-21"), 1266710400)
        self.failUnless(isinstance(p("2009-03-18"), (int, long)), p("2009-03-18"))
        self.failUnlessEqual(p("2009-03-18"), 1237334400)

    def test_format_time(self):
        self.failUnlessEqual(time_format.format_time(time.gmtime(0)), '1970-01-01 00:00:00')
        self.failUnlessEqual(time_format.format_time(time.gmtime(60)), '1970-01-01 00:01:00')
        self.failUnlessEqual(time_format.format_time(time.gmtime(60*60)), '1970-01-01 01:00:00')
        seconds_per_day = 60*60*24
        leap_years_1970_to_2014_inclusive = ((2012 - 1968) // 4)
        self.failUnlessEqual(time_format.format_time(time.gmtime(seconds_per_day*((2015 - 1970)*365+leap_years_1970_to_2014_inclusive))), '2015-01-01 00:00:00')

    def test_format_time_y2038(self):
        seconds_per_day = 60*60*24
        leap_years_1970_to_2047_inclusive = ((2044 - 1968) // 4)
        t = (seconds_per_day*
             ((2048 - 1970)*365 + leap_years_1970_to_2047_inclusive))
        try:
            gm_t = time.gmtime(t)
        except ValueError:
            raise unittest.SkipTest("Note: this system cannot handle dates after 2037.")
        self.failUnlessEqual(time_format.format_time(gm_t),
                             '2048-01-01 00:00:00')

    def test_format_delta(self):
        time_1 = 1389812723
        time_5s_delta = 1389812728
        time_28m7s_delta = 1389814410
        time_1h_delta = 1389816323
        time_1d21h46m49s_delta = 1389977532

        self.failUnlessEqual(
            time_format.format_delta(time_1, time_1), '0s')

        self.failUnlessEqual(
            time_format.format_delta(time_1, time_5s_delta), '5s')
        self.failUnlessEqual(
            time_format.format_delta(time_1, time_28m7s_delta), '28m 7s')
        self.failUnlessEqual(
            time_format.format_delta(time_1, time_1h_delta), '1h 0m 0s')
        self.failUnlessEqual(
            time_format.format_delta(time_1, time_1d21h46m49s_delta), '1d 21h 46m 49s')

        self.failUnlessEqual(
            time_format.format_delta(time_1d21h46m49s_delta, time_1), '-')

        # time_1 with a decimal fraction will make the delta 1s less
        time_1decimal = 1389812723.383963

        self.failUnlessEqual(
            time_format.format_delta(time_1decimal, time_5s_delta), '4s')
        self.failUnlessEqual(
            time_format.format_delta(time_1decimal, time_28m7s_delta), '28m 6s')
        self.failUnlessEqual(
            time_format.format_delta(time_1decimal, time_1h_delta), '59m 59s')
        self.failUnlessEqual(
            time_format.format_delta(time_1decimal, time_1d21h46m49s_delta), '1d 21h 46m 48s')
