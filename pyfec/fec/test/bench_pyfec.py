# pyfec -- fast forward error correction library with Python interface
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# mailto:zooko@zooko.com
# 
# This file is part of pyfec.
# 
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.  This program also comes with the added permission that, in the case
# that you are obligated to release a derived work under this licence (as per
# section 2.b of the GPL), you may delay the fulfillment of this obligation for
# up to 12 months.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

import fec

import array, random

def bench_encode_to_files_shuffle_decode_from_files(verbose=False):
    FILESIZE=1000000
    CHUNKSIZE=4096
    PREFIX="testshare"
    K=25
    M=100
    import os, time
    left=FILESIZE
    outfile = open("tmpranddata", "wb")
    try:
        while left:
            d = os.urandom(min(left, CHUNKSIZE))
            outfile.write(d)
            left -= len(d)
        outfile.flush()
        outfile = None
        infile = open("tmpranddata", "rb")
        st = time.time()
        fec.filefec.encode_to_files(infile, PREFIX, K, M)
        so = time.time()
        if verbose:
            print "Encoded %s byte file into %d share files in %0.2f seconds, or %0.2f million bytes per second" % (FILESIZE, M, so-st, FILESIZE/((so-st)*1000000),)
        enctime = so-st
        # Now delete m-k of the tempfiles at random.
        tempfs = [ f for f in os.listdir(".") if f.startswith(PREFIX) ]
        random.shuffle(tempfs)
        for victimtempf in tempfs[:M-K]:
            os.remove(victimtempf)
        recoveredfile = open("tmpranddata-recovered", "wb")
        st = time.time()
        fec.filefec.decode_from_files(recoveredfile, 1000000, PREFIX, K, M)
        so = time.time()
        if verbose:
            print "Decoded %s byte file from %d share files in %0.2f seconds, or %0.2f million bytes per second" % (FILESIZE, K, so-st, FILESIZE/((so-st)*1000000),)
        return enctime + (so-st)
    finally:
        # os.remove("tmpranddata")
        pass

def bench_read_encode_and_drop():
    FILESIZE=1000000
    CHUNKSIZE=4096
    import os, time
    left=FILESIZE
    outfile = open("tmpranddata", "wb")
    try:
        while left:
            d = os.urandom(min(left, CHUNKSIZE))
            outfile.write(d)
            left -= len(d)
        outfile.flush()
        outfile = None
        infile = open("tmpranddata", "rb")
        def cb(s, l):
            pass
        st = time.time()
        fec.filefec.encode_file(infile, cb, 25, 100, 4096)
        so = time.time()
        print "Encoded %s byte file in %0.2f seconds, or %0.2f million bytes per second" % (FILESIZE, so-st, FILESIZE/((so-st)*1000000),)
        return so-st
    finally:
        os.remove("tmpranddata")

if __name__ == "__main__":
    bench_encode_to_files_shuffle_decode_from_files()

