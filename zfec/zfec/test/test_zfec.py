#!/usr/bin/env python

# import bindann
# import bindann.monkeypatch.all

import cStringIO, os, random, re, sys

import zfec

try:
    from twisted.trial import unittest
except ImportError:
    # trial is unavailable, oh well
    import unittest

global VERBOSE
VERBOSE=False
if '-v' in sys.argv:
    sys.argv.pop(sys.argv.index('-v'))
    VERBOSE=True

from base64 import b32encode
def ab(x): # debuggery
    if len(x) >= 3:
        return "%s:%s" % (len(x), b32encode(x[-3:]),)
    elif len(x) == 2:
        return "%s:%s" % (len(x), b32encode(x[-2:]),)
    elif len(x) == 1:
        return "%s:%s" % (len(x), b32encode(x[-1:]),)
    elif len(x) == 0:
        return "%s:%s" % (len(x), "--empty--",)

def _h(k, m, ss):
    encer = zfec.Encoder(k, m)
    nums_and_blocks = list(enumerate(encer.encode(ss)))
    assert isinstance(nums_and_blocks, list), nums_and_blocks
    assert len(nums_and_blocks) == m, (len(nums_and_blocks), m,)
    nums_and_blocks = random.sample(nums_and_blocks, k)
    blocks = [ x[1] for x in nums_and_blocks ]
    nums = [ x[0] for x in nums_and_blocks ]
    decer = zfec.Decoder(k, m)
    decoded = decer.decode(blocks, nums)
    assert len(decoded) == len(ss), (len(decoded), len(ss),)
    assert tuple([str(s) for s in decoded]) == tuple([str(s) for s in ss]), (tuple([ab(str(s)) for s in decoded]), tuple([ab(str(s)) for s in ss]),)

def randstr(n):
    return ''.join(map(chr, map(random.randrange, [0]*n, [256]*n)))

def _help_test_random():
    m = random.randrange(1, 257)
    k = random.randrange(1, m+1)
    l = random.randrange(0, 2**10)
    ss = [ randstr(l/k) for x in range(k) ]
    _h(k, m, ss)

def _help_test_random_with_l(l):
    m = 83
    k = 19
    ss = [ randstr(l/k) for x in range(k) ]
    _h(k, m, ss)

class ZFec(unittest.TestCase):
    def test_random(self):
        for i in range(3):
            _help_test_random()
        if VERBOSE:
            print "%d randomized tests pass." % (i+1)

    def test_bad_args_enc(self):
        encer = zfec.Encoder(2, 4)
        try:
            encer.encode(["a", "b", ], ["c", "I am not an integer blocknum",])
        except zfec.Error, e:
            assert "Precondition violation: second argument is required to contain int" in str(e), e
        else:
            raise "Should have gotten zfec.Error for wrong type of second argument."

        try:
            encer.encode(["a", "b", ], 98) # not a sequence at all
        except TypeError, e:
            assert "Second argument (optional) was not a sequence" in str(e), e
        else:
            raise "Should have gotten TypeError for wrong type of second argument."

    def test_bad_args_dec(self):
        decer = zfec.Decoder(2, 4)

        try:
            decer.decode(98, [0, 1]) # first argument is not a sequence
        except TypeError, e:
            assert "First argument was not a sequence" in str(e), e
        else:
            raise "Should have gotten TypeError for wrong type of second argument."

        try:
            decer.decode(["a", "b", ], ["c", "d",])
        except zfec.Error, e:
            assert "Precondition violation: second argument is required to contain int" in str(e), e
        else:
            raise "Should have gotten zfec.Error for wrong type of second argument."

        try:
            decer.decode(["a", "b", ], 98) # not a sequence at all
        except TypeError, e:
            assert "Second argument was not a sequence" in str(e), e
        else:
            raise "Should have gotten TypeError for wrong type of second argument."

class FileFec(unittest.TestCase):
    def test_filefec_header(self):
        for m in [3, 5, 7, 9, 11, 17, 19, 33, 35, 65, 66, 67, 129, 130, 131, 254, 255, 256,]:
            for k in [2, 3, 5, 9, 17, 33, 65, 129, 255,]:
                if k >= m:
                    continue
                for pad in [0, 1, k-1,]:
                    if pad >= k:
                        continue
                    for sh in [0, 1, m-1,]:
                        if sh >= m:
                            continue
                        h = zfec.filefec._build_header(m, k, pad, sh)
                        hio = cStringIO.StringIO(h)
                        (rm, rk, rpad, rsh,) = zfec.filefec._parse_header(hio)
                        assert (rm, rk, rpad, rsh,) == (m, k, pad, sh,), h

    def _help_test_filefec(self, teststr, k, m, numshs=None):
        if numshs == None:
            numshs = m

        TESTFNAME = "testfile.txt"
        PREFIX = "test"
        SUFFIX = ".fec"

        tempdir = zfec.util.fileutil.NamedTemporaryDirectory(cleanup=False)
        try:
            tempfn = os.path.join(tempdir.name, TESTFNAME)
            tempf = open(tempfn, 'wb')
            tempf.write(teststr)
            tempf.close()
            fsize = os.path.getsize(tempfn)
            assert fsize == len(teststr)

            # encode the file
            zfec.filefec.encode_to_files(open(tempfn, 'rb'), fsize, tempdir.name, PREFIX, k, m, SUFFIX, verbose=VERBOSE)

            # select some share files
            RE=re.compile(zfec.filefec.RE_FORMAT % (PREFIX, SUFFIX,))
            fns = os.listdir(tempdir.name)
            sharefs = [ open(os.path.join(tempdir.name, fn), "rb") for fn in fns if RE.match(fn) ]
            random.shuffle(sharefs)
            del sharefs[numshs:]

            # decode from the share files
            outf = open(os.path.join(tempdir.name, 'recovered-testfile.txt'), 'wb')
            zfec.filefec.decode_from_files(outf, sharefs, verbose=VERBOSE)
            outf.close()

            tempfn = open(os.path.join(tempdir.name, 'recovered-testfile.txt'), 'rb')
            recovereddata = tempfn.read()
            assert recovereddata == teststr
        finally:
            tempdir.shutdown()

    def test_filefec_all_shares(self):
        return self._help_test_filefec("Yellow Whirled!", 3, 8)

    def test_filefec_all_shares_with_padding(self, noisy=VERBOSE):
        return self._help_test_filefec("Yellow Whirled!A", 3, 8)

    def test_filefec_min_shares_with_padding(self, noisy=VERBOSE):
        return self._help_test_filefec("Yellow Whirled!A", 3, 8, numshs=3)

if __name__ == "__main__":
    if hasattr(unittest, 'main'):
        unittest.main()
    else:
        sys.path.append(os.getcwd())
        mods = []
        fullname = os.path.realpath(os.path.abspath(__file__))
        for pathel in sys.path:
            fullnameofpathel = os.path.realpath(os.path.abspath(pathel))
            if fullname.startswith(fullnameofpathel):
                relname = fullname[len(fullnameofpathel):]
                mod = (os.path.splitext(relname)[0]).replace(os.sep, '.').strip('.')
                mods.append(mod)

        mods.sort(cmp=lambda x, y: cmp(len(x), len(y)))
        mods.reverse()
        for mod in mods:
            cmdstr = "trial %s %s" % (' '.join(sys.argv[1:]), mod)
            print cmdstr
            if os.system(cmdstr) == 0:
                break

# zfec -- fast forward error correction library with Python interface
#
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# mailto:zooko@zooko.com
#
# This file is part of zfec.
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

