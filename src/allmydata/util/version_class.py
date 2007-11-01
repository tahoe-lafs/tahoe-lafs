# Copyright (c) 2004-2007 Bryce "Zooko" Wilcox-O'Hearn
# mailto:zooko@zooko.com
# http://zooko.com/repos/pyutil
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this work to deal in this work without restriction (including the rights
# to use, modify, distribute, sublicense, and/or sell copies).

"""
extended version number class
"""

# from setuptools, but intended to be included in future version of Python Standard Library (PEP 365)
try:
    import pkg_resources
except ImportError:
    import distutils.version
    def cmp_version(v1, v2):
        return cmp(distutils.version.LooseVersion(str(v1)), distutils.version.LooseVersion(str(v2)))
else:
    def cmp_version(v1, v2):
        return cmp(pkg_resources.parse_version(str(v1)), pkg_resources.parse_version(str(v2)))

# Python Standard Library
import re

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
#  | | |'- a=alpha, b=beta, c=release candidate, or none
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
#  | | |||  '- nano version number
#  | | ||'- release number
#  | | |'- a=alpha, b=beta, c=release candidate or none
#  | | '- micro version number
#  | '- minor version number
#  '- major version number

# The presence of the nano version number means that this is a development
# version.  There are no guarantees about compatibility, etc.  This version is
# considered to be more recent than the version without this field
# (e.g. "1.0.0a1").

# The nano version number is meaningful only to developers.  It gets generated
# automatically from darcs revision control history by "make-version.py".  It
# is the count of patches that have been applied since the last version number
# tag was applied.

VERSION_BASE_RE_STR="(\d+)\.(\d+)(\.(\d+))?((a|b|c)(\d+))?"
VERSION_RE_STR=VERSION_BASE_RE_STR + "(-(\d+))?"
VERSION_RE=re.compile("^" + VERSION_RE_STR + "$")

class Version(object):
    def __init__(self, vstring=None):
        self.major = None
        self.minor = None
        self.micro = None
        self.prereleasetag = None
        self.prerelease = None
        self.nano = None
        self.leftovers = ''
        if vstring:
            try:
                self.parse(vstring)
            except ValueError, le:
                le.args = tuple(le.args + ('vstring:', vstring,))
                raise

    def parse(self, vstring):
        mo = VERSION_RE.search(vstring)
        if not mo:
            raise ValueError, "Not a valid version string for allmydata.util.version_class.Version(): %r" % (vstring,)

        self.major = int(mo.group(1))
        self.minor = int(mo.group(2))
        self.micro = int(mo.group(4))
        reltag = mo.group(5)
        if reltag:
            reltagnum = int(mo.group(6))
            self.prereleasetag = reltag
            self.prerelease = reltagnum

        if mo.group(8):
            self.nano = int(mo.group(9))

        self.fullstr = "%d.%d.%d%s%s" % (self.major, self.minor, self.micro, self.prereleasetag and "%s%d" % (self.prereleasetag, self.prerelease,) or "", self.nano and "-%d" % (self.nano,) or "",)

    def user_str(self):
        return self.strictversion.__str__()

    def full_str(self):
        if hasattr(self, 'fullstr'):
            return self.fullstr
        else:
            return 'None'

    def __str__(self):
        return self.full_str()

    def __repr__(self):
        return self.__str__()

    def __cmp__ (self, other):
        return cmp_version(self, other)
