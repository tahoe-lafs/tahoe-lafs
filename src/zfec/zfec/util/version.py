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

# "1.0.0a1-55-UNSTABLE"
#  ^ ^ ^^^  ^     ^
#  | | |||  |     |
#  | | |||  |     '- tags
#  | | |||  '- nano version number
#  | | ||'- release number
#  | | |'- alpha or beta (or none)
#  | | '- micro version number
#  | '- minor version number
#  '- major version number

# The next number is the "nano version number".  It is meaningful only to
# developers.  It gets bumped whenever a developer changes anything that another
# developer might care about.

# The last part is the "tags" separated by "_".  Standard tags are
# "STABLE" and "UNSTABLE".

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
        if i:
            svstring = vstring[:i]
            estring = vstring[i+1:]
        else:
            svstring = vstring
            estring = None

        self.strictversion = version.StrictVersion(svstring)

        if estring:
            try:
                (self.nanovernum, tags,) = estring.split('-')
            except:
                print estring
                raise
            self.tags = map(Tag, tags.split('_'))
            self.tags.sort()

        self.fullstr = '-'.join([str(self.strictversion), str(self.nanovernum), '_'.join(self.tags)])
          
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

# zfec -- fast forward error correction library with Python interface
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# mailto:zooko@zooko.com
# 
# This file is part of zfec.
# 
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version, with the added permission that, if you become obligated
# to release a derived work under this licence (as per section 2.b of the
# GPL), you may delay the fulfillment of this obligation for up to 12 months.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# 
# This file is part of zfec.
# 
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version, with the added permission that, if you become obligated
# to release a derived work under this licence (as per section 2.b of the
# GPL), you may delay the fulfillment of this obligation for up to 12 months.
#
# If you would like to inquire about a commercial relationship with Allmydata,
# Inc., please contact partnerships@allmydata.com and visit
# http://allmydata.com/.
# 
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
