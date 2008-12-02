
import re

HOUR = 3600
DAY = 24*3600
WEEK = 7*DAY
MONTH = 30*DAY
YEAR = 365*DAY

def abbreviate_time(s):
    def _plural(count, unit):
        count = int(count)
        if count == 1:
            return "%d %s" % (count, unit)
        return "%d %ss" % (count, unit)
    if s is None:
        return "unknown"
    if s < 120:
        return _plural(s, "second")
    if s < 3*HOUR:
        return _plural(s/60, "minute")
    if s < 2*DAY:
        return _plural(s/HOUR, "hour")
    if s < 2*MONTH:
        return _plural(s/DAY, "day")
    if s < 4*YEAR:
        return _plural(s/MONTH, "month")
    return _plural(s/YEAR, "year")

def abbreviate_space(s, SI=True):
    if s is None:
        return "unknown"
    if SI:
        U = 1000.0
        isuffix = "B"
    else:
        U = 1024.0
        isuffix = "iB"
    def r(count, suffix):
        return "%.2f %s%s" % (count, suffix, isuffix)

    if s < 1024: # 1000-1023 get emitted as bytes, even in SI mode
        return "%d B" % s
    if s < U*U:
        return r(s/U, "k")
    if s < U*U*U:
        return r(s/(U*U), "M")
    if s < U*U*U*U:
        return r(s/(U*U*U), "G")
    if s < U*U*U*U*U:
        return r(s/(U*U*U*U), "T")
    return r(s/(U*U*U*U*U), "P")

def abbreviate_space_both(s):
    return "(%s, %s)" % (abbreviate_space(s, True),
                         abbreviate_space(s, False))

def parse_abbreviated_size(s):
    if s is None or s == "":
        return None
    m = re.match(r"^(\d+)([kKmMgG]?[iB]?[bB]?)$", s)
    if not m:
        raise ValueError("unparseable value %s" % s)
    number, suffix = m.groups()
    suffix = suffix.upper()
    if suffix.endswith("B"):
        suffix = suffix[:-1]
    multiplier = {"": 1,
                  "I": 1,
                  "K": 1000,
                  "M": 1000 * 1000,
                  "G": 1000 * 1000 * 1000,
                  "KI": 1024,
                  "MI": 1024*1024,
                  "GI": 1024*1024*1024,
                  }[suffix]
    return int(number) * multiplier
