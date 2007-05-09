#!/usr/bin/env python

# zfec -- a fast C implementation of Reed-Solomon erasure coding with
# command-line, C, and Python interfaces

import os, sys

from util import argparse
import filefec

from zfec import __version__ as libversion
from util.version import Version
__version__ = Version("1.0.0a1-0-STABLE")

def main():
    if '-V' in sys.argv or '--version' in sys.argv:
        print "zfec library version: ", libversion
        print "zunfec command-line tool version: ", __version__
        return 0

    parser = argparse.ArgumentParser(description="Decode data from share files.")

    parser.add_argument('-o', '--outputfile', required=True, help='file to write the resulting data to, or "-" for stdout', type=str, metavar='OUTF')
    parser.add_argument('sharefiles', nargs='*', help='shares file to read the encoded data from', type=unicode, metavar='SHAREFILE')
    parser.add_argument('-v', '--verbose', help='print out messages about progress', action='store_true')
    parser.add_argument('-f', '--force', help='overwrite any file which already in place of the output file', action='store_true')
    parser.add_argument('-V', '--version', help='print out version number and exit', action='store_true')
    args = parser.parse_args()

    if len(args.sharefiles) < 2:
        print "At least two sharefiles are required."
        return 1

    if args.force:
        outf = open(args.outputfile, 'wb')
    else:
        try:
            flags = os.O_WRONLY|os.O_CREAT|os.O_EXCL | (hasattr(os, 'O_BINARY') and os.O_BINARY)
            outfd = os.open(args.outputfile, flags)
        except OSError:
            print "There is already a file named %r -- aborting.  Use --force to overwrite." % (args.outputfile,)
            return 2
        outf = os.fdopen(outfd, "wb")

    sharefs = []
    # This sort() actually matters for performance (shares with numbers < k
    # are much faster to use than the others), as well as being important for
    # reproducibility.
    args.sharefiles.sort()
    for fn in args.sharefiles:
        sharefs.append(open(fn, 'rb'))
    try:
        ret = filefec.decode_from_files(outf, sharefs, args.verbose)
    except filefec.InsufficientShareFilesError, e:
        print str(e)
        return 3

    return 0

# zfec -- fast forward error correction library with Python interface
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
# to release a derived work under this licence (as per section 2.b), you may
# delay the fulfillment of this obligation for up to 12 months.  See the file
# COPYING for details.
#
# If you would like to inquire about a commercial relationship with Allmydata,
# Inc., please contact partnerships@allmydata.com and visit
# http://allmydata.com/.
