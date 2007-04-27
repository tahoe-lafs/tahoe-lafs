"""
A few commonly needed functions.
"""

import math

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

def is_power_of_k(n, k):
    return k**int(math.log(n, k) + 0.5) == n

def next_power_of_k(n, k):
    p = 1
    while p < n:
        p *= k
    return p

def ave(l):
    return sum(l) / len(l)

def log_ceil(n, b):
    """
    The smallest integer k such that b^k >= n.

    log_ceil(n, 2) is the number of bits needed to store any of n values, e.g.
    the number of bits needed to store any of 128 possible values is 7.
    """
    p = 1
    k = 0
    while p < n:
        p *= b
        k += 1
    return k

def permute(l):
    """
    Return all possible permutations of l.

    @type l: sequence
    @rtype: a list of sequences
    """
    if len(l) == 1:
        return [l,]

    res = []
    for i in range(len(l)):
        l2 = list(l[:])
        x = l2.pop(i)
        for l3 in permute(l2):
            l3.append(x)
            res.append(l3)

    return res

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
