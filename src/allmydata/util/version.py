# Copyright (c) 2004-2007 Bryce "Zooko" Wilcox-O'Hearn
# mailto:zooko@zooko.com
# http://zooko.com/repos/pyutil
# Permission is hereby granted, free of charge, to any person obtaining a copy 
# of this work to deal in this work without restriction (including the rights 
# to use, modify, distribute, sublicense, and/or sell copies).

"""
extended version number class
"""

from distutils import version

# End users see version strings like this:

# "1.0.0"
#  ^ ^ ^
#  | | |
#  | | '- micro version number
#  | '- minor version number
#  '- major version number

# The first number is "major version number".  The second number is the "minor
# version number" -- it gets bumped whenever we make a new release that adds or
# changes functionality.  The third version is the "micro version number" -- it
# gets bumped whenever we make a new release that doesn't add or change
# functionality, but just fixes bugs (including performance issues).

# Early-adopter end users see version strings like this:

# "1.0.0a1"
#  ^ ^ ^^^
#  | | |||
#  | | ||'- release number
#  | | |'- alpha or beta (or none)
#  | | '- micro version number
#  | '- minor version number
#  '- major version number

# The optional "a" or "b" stands for "alpha release" or "beta release"
# respectively.  The number after "a" or "b" gets bumped every time we
# make a new alpha or beta release. This has the same form and the same
# meaning as version numbers of releases of Python.

# Developers see "full version strings", like this:

# "1.0.0a1-55"
#  ^ ^ ^^^  ^
#  | | |||  |
#  | | |||  |
#  | | |||  '- nano version number
#  | | ||'- release number
#  | | |'- alpha or beta (or none)
#  | | '- micro version number
#  | '- minor version number
#  '- major version number

# The next number is the "nano version number".  It is meaningful only to
# developers.  It gets bumped whenever a developer changes anything that another
# developer might care about.

class Tag(str):
    def __cmp__(t1, t2):
        if t1 == t2:
            return 0
        if t1 == "UNSTABLE" and t2 == "STABLE":
            return 1
        if t1 == "STABLE" and t2 == "UNSTABLE":
            return -1
        return -2 # who knows

class Version:
    def __init__(self, vstring=None):
        if vstring:
            self.parse(vstring)

    def parse(self, vstring):
        i = vstring.find('-')
        if i != -1:
            svstring = vstring[:i]
            estring = vstring[i+1:]
        else:
            svstring = vstring
            estring = None

        self.strictversion = version.StrictVersion(svstring)

        self.nanovernum = None
        self.tags = []
        if estring:
            self.nanovernum = estring

        self.fullstr = str(self.strictversion)
        if self.nanovernum is not None:
            self.fullstr += "-" + str(self.nanovernum)
        if self.tags:
            self.fullstr += '_'.join(self.tags)

    def tags(self):
        return self.tags

    def user_str(self):
        return self.strictversion.__str__()

    def full_str(self):
        return self.fullstr

    def __str__(self):
        return self.full_str()

    def __repr__(self):
        return self.__str__()

    def __cmp__ (self, other):
        if isinstance(other, basestring):
            other = Version(other)

        res = cmp(self.strictversion, other.strictversion)
        if res != 0:
            return res

        res = cmp(self.nanovernum, other.nanovernum)
        if res != 0:
            return res

        return cmp(self.tags, other.tags)
