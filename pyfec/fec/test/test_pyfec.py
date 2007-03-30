#!/usr/bin/env python

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

import random
import sys

import fec

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
    # sys.stdout.write("k: %s, m: %s,  len(ss): %r, len(ss[0]): %r" % (k, m, len(ss), len(ss[0]),)) ; sys.stdout.flush()
    encer = fec.Encoder(k, m)
    # sys.stdout.write("constructed.\n") ; sys.stdout.flush()
    nums_and_blocks = list(enumerate(encer.encode(ss)))
    # sys.stdout.write("encoded.\n") ; sys.stdout.flush()
    assert isinstance(nums_and_blocks, list), nums_and_blocks
    assert len(nums_and_blocks) == m, (len(nums_and_blocks), m,)
    nums_and_blocks = random.sample(nums_and_blocks, k)
    blocks = [ x[1] for x in nums_and_blocks ]
    nums = [ x[0] for x in nums_and_blocks ]
    # sys.stdout.write("about to construct Decoder.\n") ; sys.stdout.flush()
    decer = fec.Decoder(k, m)
    # sys.stdout.write("about to decode from %s.\n"%nums) ; sys.stdout.flush()
    decoded = decer.decode(blocks, nums)
    # sys.stdout.write("decoded.\n") ; sys.stdout.flush()
    assert len(decoded) == len(ss), (len(decoded), len(ss),)
    assert tuple([str(s) for s in decoded]) == tuple([str(s) for s in ss]), (tuple([ab(str(s)) for s in decoded]), tuple([ab(str(s)) for s in ss]),)

def randstr(n):
    return ''.join(map(chr, map(random.randrange, [0]*n, [256]*n)))

def div_ceil(n, d):
    """
    The smallest integer k such that k*d >= n.
    """
    return (n/d) + (n%d != 0)

def next_multiple(n, k):
    """
    The smallest multiple of k which is >= n.
    """
    return div_ceil(n, k) * k

def pad_size(n, k):
    """
    The smallest number that has to be added to n so that n is a multiple of k.
    """
    if n%k:
        return k - n%k
    else:
        return 0

def _test_random():
    m = random.randrange(1, 257)
    k = random.randrange(1, m+1)
    l = random.randrange(0, 2**16)
    ss = [ randstr(l/k) for x in range(k) ]
    _h(k, m, ss)

def test_random():
    for i in range(2**5):
        # sys.stdout.write(",")
        _test_random()
        # sys.stdout.write(".")
    print "%d randomized tests pass." % (i+1)

def test_bad_args_enc():
    encer = fec.Encoder(2, 4)
    try:
        encer.encode(["a", "b", ], ["c", "I am not an integer blocknum",])
    except fec.Error, e:
        assert "Precondition violation: second argument is required to contain int" in str(e), e
    else:
        raise "Should have gotten fec.Error for wrong type of second argument."

    try:
        encer.encode(["a", "b", ], 98) # not a sequence at all
    except TypeError, e:
        assert "Second argument (optional) was not a sequence" in str(e), e
    else:
        raise "Should have gotten TypeError for wrong type of second argument."

def test_bad_args_dec():
    decer = fec.Decoder(2, 4)

    try:
        decer.decode(98, [0, 1]) # first argument is not a sequence
    except TypeError, e:
        assert "First argument was not a sequence" in str(e), e
    else:
        raise "Should have gotten TypeError for wrong type of second argument."

    try:
        decer.decode(["a", "b", ], ["c", "d",])
    except fec.Error, e:
        assert "Precondition violation: second argument is required to contain int" in str(e), e
    else:
        raise "Should have gotten fec.Error for wrong type of second argument."

    try:
        decer.decode(["a", "b", ], 98) # not a sequence at all
    except TypeError, e:
        assert "Second argument was not a sequence" in str(e), e
    else:
        raise "Should have gotten TypeError for wrong type of second argument."
    

if __name__ == "__main__":
    test_bad_args_dec()
    test_bad_args_enc()
    test_random()

