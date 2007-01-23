#!/usr/bin/env python

from pyutil.assertutil import _assert, precondition

import random
import sys

import fec

def shuffle(nums_and_shares):
    """ Make sure that if nums_and_shares[i][0] < len(nums_and_shares), that i == nums_and_shares[i][0]. """
    i = 0
    while i < len(nums_and_shares):
        num, share = nums_and_shares[i]
        if num >= len(nums_and_shares) or num == i:
            i += 1
        else:
            nums_and_shares[i] = nums_and_shares[num]
            nums_and_shares[num] = (num, share,)
    _assert([ (i, (num, share,),) for (i, (num, share,),) in enumerate(nums_and_shares) if num < len(nums_and_shares) and num != i ] == [], [ (i, (num, share,),) for (i, (num, share,),) in enumerate(nums_and_shares) if num < len(nums_and_shares) and num != i ])

def _h(k, m, ss):
    # sys.stdout.write("k: %s, m: %s,  len(ss): %r, len(ss[0]): %r" % (k, m, len(ss), len(ss[0]),)) ; sys.stdout.flush()
    encer = fec.Encoder(k, m)
    # sys.stdout.write("constructed.\n") ; sys.stdout.flush()
    nums_and_shares = list(enumerate(encer.encode(ss)))
    # sys.stdout.write("encoded.\n") ; sys.stdout.flush()
    _assert(isinstance(nums_and_shares, list), nums_and_shares)
    _assert(len(nums_and_shares) == m, len(nums_and_shares), m)
    nums_and_shares = random.sample(nums_and_shares, k)
    shuffle(nums_and_shares)
    shares = [ x[1] for x in nums_and_shares ]
    nums = [ x[0] for x in nums_and_shares ]
    # sys.stdout.write("about to construct Decoder.\n") ; sys.stdout.flush()
    decer = fec.Decoder(k, m)
    # sys.stdout.write("about to decode.\n") ; sys.stdout.flush()
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
    # m = random.randrange(1, 255)
    m = 99
    # k = random.randrange(1, m+1)
    k = 33
    # l = random.randrange(0, 2**16)
    l = 2**12
    ss = [ randstr(l/k) + '\x00' * pad_size(l/k, k) for x in range(k) ]
    _h(k, m, ss)

def test_random():
    for i in range(2**9):
        sys.stdout.write(",")
        _test_random()
        sys.stdout.write(".")

test_random()
