#!/usr/bin/env python

# zfec -- a fast C implementation of Reed-Solomon erasure coding with
# command-line, C, and Python interfaces

# The zfec and zunfec command-line tools require Python 2.5 for relative imports.

import sys

from util import argparse
import filefec

from zfec import __version__ as libversion
from util.version import Version
__version__ = Version("1.0.0a1-0-STABLE")

def main():

    if '-V' in sys.argv or '--version' in sys.argv:
        print "zfec library version: ", libversion
        print "zfec command-line tool version: ", __version__
        sys.exit(0)

    parser = argparse.ArgumentParser(description="Encode a file into a set of share files, a subset of which can later be used to recover the original file.")

    parser.add_argument('inputfile', help='file to encode or "-" for stdin', type=argparse.FileType('rb'), metavar='INF')
    parser.add_argument('-d', '--output-dir', help='directory in which share file names will be created (default ".")', default='.', metavar='D')
    parser.add_argument('-p', '--prefix', help='prefix for share file names; If omitted, the name of the input file will be used.', metavar='P')
    parser.add_argument('-s', '--suffix', help='suffix for share file names (default ".fec")', default='.fec', metavar='S')
    parser.add_argument('-m', '--totalshares', help='the total number of share files created (default 16)', default=16, type=int, metavar='M')
    parser.add_argument('-k', '--requiredshares', help='the number of share files required to reconstruct (default 4)', default=4, type=int, metavar='K')
    parser.add_argument('-f', '--force', help='overwrite any file which already in place an output file (share file)', action='store_true')
    parser.add_argument('-v', '--verbose', help='print out messages about progress', action='store_true')
    parser.add_argument('-V', '--version', help='print out version number and exit', action='store_true')
    args = parser.parse_args()

    if args.prefix is None:
        args.prefix = args.inputfile.name
        if args.prefix == "<stdin>":
            args.prefix = ""

    if args.totalshares < 3:
        print "Invalid parameters, totalshares is required to be >= 3\nPlease see the accompanying documentation."
        sys.exit(1)
    if args.totalshares > 256:
        print "Invalid parameters, totalshares is required to be <= 256\nPlease see the accompanying documentation."
        sys.exit(1)
    if args.requiredshares < 2:
        print "Invalid parameters, requiredshares is required to be >= 2\nPlease see the accompanying documentation."
        sys.exit(1)
    if args.requiredshares >= args.totalshares:
        print "Invalid parameters, requiredshares is required to be < totalshares\nPlease see the accompanying documentation."
        sys.exit(1)

    args.inputfile.seek(0, 2)
    fsize = args.inputfile.tell()
    args.inputfile.seek(0, 0)
    return filefec.encode_to_files(args.inputfile, fsize, args.output_dir, args.prefix, args.requiredshares, args.totalshares, args.suffix, args.force, args.verbose)


# zfec -- a fast C implementation of Reed-Solomon erasure coding with
# command-line, C, and Python interfaces
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
# any later version.  This package also comes with the added permission that,
# in the case that you are obligated to release a derived work under this
# licence (as per section 2.b of the GPL), you may delay the fulfillment of
# this obligation for up to 12 months.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

