#  Copyright (c) 2001 Autonomous Zone Industries
#  Copyright (c) 2002-2007 Bryce "Zooko" Wilcox-O'Hearn
#  This file is licensed under the
#    GNU Lesser General Public License v2.1.
#    See the file COPYING or visit http://www.gnu.org/ for details.

# ISO-8601:
# http://www.cl.cam.ac.uk/~mgk25/iso-time.html

import calendar, datetime, re, time

def iso_utc_date(now=None, t=time.time):
    if now is None:
        now = t()
    return datetime.datetime.utcfromtimestamp(now).isoformat()[:10]

def iso_utc(now=None, sep='_', t=time.time):
    if now is None:
        now = t()
    return datetime.datetime.utcfromtimestamp(now).isoformat(sep)

def iso_local(now=None, sep='_', t=time.time):
    if now is None:
        now = t()
    return datetime.datetime.fromtimestamp(now).isoformat(sep)

def iso_utc_time_to_seconds(isotime, _conversion_re=re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[T_ ](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})(?P<subsecond>\.\d+)?")):
    """
    The inverse of iso_utc().

    Real ISO-8601 is "2003-01-08T06:30:59".  We also accept the widely
    used variants "2003-01-08_06:30:59" and "2003-01-08 06:30:59".
    """
    m = _conversion_re.match(isotime)
    if not m:
        raise ValueError, (isotime, "not a complete ISO8601 timestamp")
    year, month, day = int(m.group('year')), int(m.group('month')), int(m.group('day'))
    hour, minute, second = int(m.group('hour')), int(m.group('minute')), int(m.group('second'))
    subsecstr = m.group('subsecond')
    if subsecstr:
        subsecfloat = float(subsecstr)
    else:
        subsecfloat = 0

    return calendar.timegm( (year, month, day, hour, minute, second, 0, 1, 0) ) + subsecfloat

def parse_duration(s):
    orig = s
    unit = None
    DAY = 24*60*60
    MONTH = 31*DAY
    YEAR = 365*DAY
    if s.endswith("s"):
        s = s[:-1]
    if s.endswith("day"):
        unit = DAY
        s = s[:-len("day")]
    elif s.endswith("month"):
        unit = MONTH
        s = s[:-len("month")]
    elif s.endswith("mo"):
        unit = MONTH
        s = s[:-len("mo")]
    elif s.endswith("year"):
        unit = YEAR
        s = s[:-len("YEAR")]
    else:
        raise ValueError("no unit (like day, month, or year) in '%s'" % orig)
    s = s.strip()
    return int(s) * unit

def parse_date(s):
    # return seconds-since-epoch for the UTC midnight that starts the given
    # day
    return int(iso_utc_time_to_seconds(s + "T00:00:00"))

