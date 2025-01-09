"""
Time formatting utilities.

ISO-8601:
http://www.cl.cam.ac.uk/~mgk25/iso-time.html
"""

import calendar, datetime, re, time
from typing import Optional
from enum import Enum


class ParseDurationUnitFormat(Enum):
    SECONDS0 = "s"
    SECONDS1 = "second"
    SECONDS2 = "seconds"
    DAYS0 = "day"
    DAYS1 = "days"
    MONTHS0 = "mo"
    MONTHS1 = "month"
    MONTHS2 = "months"
    YEARS0 = "year"
    YEARS1 = "years"

    @classmethod
    def list_values(cls):
        return list(map(lambda c: c.value, cls))


def format_time(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", t)

def iso_utc_date(
    now: Optional[float] = None,
    t=time.time
) -> str:
    if now is None:
        now = t()
    return datetime.datetime.utcfromtimestamp(now).isoformat()[:10]

def iso_utc(
    now: Optional[float] = None,
    sep: str = '_',
    t=time.time
) -> str:
    if now is None:
        now = t()
    sep = str(sep)  # should already be a str
    return datetime.datetime.utcfromtimestamp(now).isoformat(sep)

def iso_utc_time_to_seconds(isotime, _conversion_re=re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[T_ ](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})(?P<subsecond>\.\d+)?")):
    """
    The inverse of iso_utc().

    Real ISO-8601 is "2003-01-08T06:30:59".  We also accept the widely
    used variants "2003-01-08_06:30:59" and "2003-01-08 06:30:59".
    """
    m = _conversion_re.match(isotime)
    if not m:
        raise ValueError(isotime, "not a complete ISO8601 timestamp")
    year, month, day = int(m.group('year')), int(m.group('month')), int(m.group('day'))
    hour, minute, second = int(m.group('hour')), int(m.group('minute')), int(m.group('second'))
    subsecstr = m.group('subsecond')
    if subsecstr:
        subsecfloat = float(subsecstr)
    else:
        subsecfloat = 0

    return calendar.timegm( (year, month, day, hour, minute, second, 0, 1, 0) ) + subsecfloat


def parse_duration(s):
    """
    Parses a duration string and converts it to seconds. The unit format is case insensitive

    Args:
        s (str): The duration string to parse. Expected format: `<number><unit>`
                 where `unit` can be one of the values defined in `ParseDurationUnitFormat`.

    Returns:
        int: The duration in seconds.

    Raises:
        ValueError: If the input string does not match the expected format or contains invalid units.
    """
    SECOND = 1
    DAY = 24*60*60
    MONTH = 31*DAY
    YEAR = 365*DAY
    time_map = {
        ParseDurationUnitFormat.SECONDS0: SECOND,
        ParseDurationUnitFormat.SECONDS1: SECOND,
        ParseDurationUnitFormat.SECONDS2: SECOND,
        ParseDurationUnitFormat.DAYS0: DAY,
        ParseDurationUnitFormat.DAYS1: DAY,
        ParseDurationUnitFormat.MONTHS0: MONTH,
        ParseDurationUnitFormat.MONTHS1: MONTH,
        ParseDurationUnitFormat.MONTHS2: MONTH,
        ParseDurationUnitFormat.YEARS0: YEAR,
        ParseDurationUnitFormat.YEARS1: YEAR,
    }

    # Build a regex pattern dynamically from the list of valid values
    unit_pattern = "|".join(re.escape(unit) for unit in ParseDurationUnitFormat.list_values())
    pattern = rf"^\s*(\d+)\s*({unit_pattern})\s*$"

    # case-insensitive regex matching
    match = re.match(pattern, s, re.IGNORECASE)
    if not match:
        # Generate dynamic error message
        valid_units = ", ".join(f"'{value}'" for value in ParseDurationUnitFormat.list_values())
        raise ValueError(f"No valid unit in '{s}'. Expected one of: ({valid_units})")

    number = int(match.group(1))  # Extract the numeric value
    unit = match.group(2).lower()  # Extract the unit & normalize the unit to lowercase

    return number * time_map[unit]

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
    minutes = (delta // 60) % 60
    delta  -= minutes * 60
    hours   = delta // (60*60) % 24
    delta  -= hours * 24
    days    = delta // (24*60*60)
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

