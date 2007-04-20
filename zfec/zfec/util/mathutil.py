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

# Copyright (c) 2005-2007 Bryce "Zooko" Wilcox-O'Hearn
# mailto:zooko@zooko.com
# http://zooko.com/repos/pyutil
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this work to deal in this work without restriction (including the rights
# to use, modify, distribute, sublicense, and/or sell copies).

