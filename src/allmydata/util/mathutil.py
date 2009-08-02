#  Copyright (c) 2002-2009 Zooko Wilcox-O'Hearn
#  This file is part of pyutil; see README.txt for licensing terms.

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
    if n == 0:
        x = 0
    else:
        x = int(math.log(n, k) + 0.5)
    r = k**x
    if k**x < n:
        return k**(x+1)
    else:
        return k**x

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

def log_floor(n, b):
    """
    The largest integer k such that b^k <= n.
    """
    p = 1
    k = 0
    while p <= n:
        p *= b
        k += 1
    return k - 1

def round_sigfigs(f, n):
    fmt = "%." + str(n-1) + "e"
    return float(fmt % f)
