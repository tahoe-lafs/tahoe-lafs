# Copyright (c) 2005-2007 Bryce "Zooko" Wilcox-O'Hearn
# mailto:zooko@zooko.com
# http://zooko.com/repos/pyutil
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this work to deal in this work without restriction (including the rights
# to use, modify, distribute, sublicense, and/or sell copies).

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

