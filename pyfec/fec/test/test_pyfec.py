#!/usr/bin/env python

from pyutil.assertutil import _assert, precondition

import random
import sys

import fec

def _h(k, m, ss):
    # sys.stdout.write("k: %s, m: %s,  len(ss): %r, len(ss[0]): %r" % (k, m, len(ss), len(ss[0]),)) ; sys.stdout.flush()
    encer = fec.Encoder(k, m)
    # sys.stdout.write("constructed.\n") ; sys.stdout.flush()
    nums_and_shares = list(enumerate(encer.encode(ss)))
    # sys.stdout.write("encoded.\n") ; sys.stdout.flush()
    _assert(isinstance(nums_and_shares, list), nums_and_shares)
    _assert(len(nums_and_shares) == m, len(nums_and_shares), m)
    nums_and_shares = random.sample(nums_and_shares, k)
    shares = [ x[1] for x in nums_and_shares ]
    nums = [ x[0] for x in nums_and_shares ]
    # sys.stdout.write("about to construct Decoder.\n") ; sys.stdout.flush()
    decer = fec.Decoder(k, m)
    # sys.stdout.write("about to decode from %s.\n"%nums) ; sys.stdout.flush()
    decoded = decer.decode(shares, nums)
    # sys.stdout.write("decoded.\n") ; sys.stdout.flush()
    _assert(len(decoded) == len(ss), len(decoded), len(ss))
    _assert(tuple([str(s) for s in decoded]) == tuple([str(s) for s in ss]), tuple([str(s) for s in decoded]), tuple([str(s) for s in ss]))

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
    m = random.randrange(1, 255)
    k = random.randrange(1, m+1)
    l = random.randrange(0, 2**16)
    ss = [ randstr(l/k) + '\x00' * pad_size(l/k, k) for x in range(k) ]
    _h(k, m, ss)

def test_random():
    for i in range(2**10):
        sys.stdout.write(",")
        _test_random()
        sys.stdout.write(".")

test_random()
