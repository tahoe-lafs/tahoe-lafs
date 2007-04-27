"""
zfec -- fast forward error correction library with Python interface

maintainer web site: U{http://allmydata.com/source/zfec}

zfec web site: U{http://allmydata.com/source/zfec}
"""

from util.version import Version

# For an explanation of what the parts of the version string mean,
# please see pyutil.version.
__version__ = Version("1.0.0a5-1-STABLE")

# Please put a URL or other note here which shows where to get the branch of
# development from which this version grew.
__sources__ = ["http://allmydata.org/source/zfec",]

from _fec import Encoder, Decoder, Error
import filefec, cmdline_zfec, cmdline_zunfec

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
