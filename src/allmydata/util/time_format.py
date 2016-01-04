# ISO-8601:
# http://www.cl.cam.ac.uk/~mgk25/iso-time.html

import calendar, datetime, re, time

def format_time(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", t)

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

def format_delta(time_1, time_2):
    if time_1 is None:
        return "N/A"
    if time_1 > time_2:
        return '-'
    delta = int(time_2 - time_1)
    seconds = delta % 60
    delta  -= seconds
    minutes = (delta / 60) % 60
    delta  -= minutes * 60
    hours   = delta / (60*60) % 24
    delta  -= hours * 24
    days    = delta / (24*60*60)
    if not days:
        if not hours:
            if not minutes:
                return "%ss" % (seconds)
            else:
                return "%sm %ss" % (minutes, seconds)
        else:
            return "%sh %sm %ss" % (hours, minutes, seconds)
    else:
        return "%sd %sh %sm %ss" % (days, hours, minutes, seconds)

