
from twisted.trial import unittest

from zfec.util import mathutil

class Math(unittest.TestCase):
    def test_div_ceil(self):
        f = mathutil.div_ceil
        self.failUnlessEqual(f(0, 1), 0)
        self.failUnlessEqual(f(0, 2), 0)
        self.failUnlessEqual(f(0, 3), 0)
        self.failUnlessEqual(f(1, 3), 1)
        self.failUnlessEqual(f(2, 3), 1)
        self.failUnlessEqual(f(3, 3), 1)
        self.failUnlessEqual(f(4, 3), 2)
        self.failUnlessEqual(f(5, 3), 2)
        self.failUnlessEqual(f(6, 3), 2)
        self.failUnlessEqual(f(7, 3), 3)

    def test_next_multiple(self):
        f = mathutil.next_multiple
        self.failUnlessEqual(f(5, 1), 5)
        self.failUnlessEqual(f(5, 2), 6)
        self.failUnlessEqual(f(5, 3), 6)
        self.failUnlessEqual(f(5, 4), 8)
        self.failUnlessEqual(f(5, 5), 5)
        self.failUnlessEqual(f(5, 6), 6)
        self.failUnlessEqual(f(32, 1), 32)
        self.failUnlessEqual(f(32, 2), 32)
        self.failUnlessEqual(f(32, 3), 33)
        self.failUnlessEqual(f(32, 4), 32)
        self.failUnlessEqual(f(32, 5), 35)
        self.failUnlessEqual(f(32, 6), 36)
        self.failUnlessEqual(f(32, 7), 35)
        self.failUnlessEqual(f(32, 8), 32)
        self.failUnlessEqual(f(32, 9), 36)
        self.failUnlessEqual(f(32, 10), 40)
        self.failUnlessEqual(f(32, 11), 33)
        self.failUnlessEqual(f(32, 12), 36)
        self.failUnlessEqual(f(32, 13), 39)
        self.failUnlessEqual(f(32, 14), 42)
        self.failUnlessEqual(f(32, 15), 45)
        self.failUnlessEqual(f(32, 16), 32)
        self.failUnlessEqual(f(32, 17), 34)
        self.failUnlessEqual(f(32, 18), 36)
        self.failUnlessEqual(f(32, 589), 589)

    def test_pad_size(self):
        f = mathutil.pad_size
        self.failUnlessEqual(f(0, 4), 0)
        self.failUnlessEqual(f(1, 4), 3)
        self.failUnlessEqual(f(2, 4), 2)
        self.failUnlessEqual(f(3, 4), 1)
        self.failUnlessEqual(f(4, 4), 0)
        self.failUnlessEqual(f(5, 4), 3)

    def test_is_power_of_k(self):
        f = mathutil.is_power_of_k
        for i in range(1, 100):
            if i in (1, 2, 4, 8, 16, 32, 64):
                self.failUnless(f(i, 2), "but %d *is* a power of 2" % i)
            else:
                self.failIf(f(i, 2), "but %d is *not* a power of 2" % i)
        for i in range(1, 100):
            if i in (1, 3, 9, 27, 81):
                self.failUnless(f(i, 3), "but %d *is* a power of 3" % i)
            else:
                self.failIf(f(i, 3), "but %d is *not* a power of 3" % i)

    def test_next_power_of_k(self):
        f = mathutil.next_power_of_k
        self.failUnlessEqual(f(0,2), 1)
        self.failUnlessEqual(f(1,2), 1)
        self.failUnlessEqual(f(2,2), 2)
        self.failUnlessEqual(f(3,2), 4)
        self.failUnlessEqual(f(4,2), 4)
        for i in range(5, 8): self.failUnlessEqual(f(i,2), 8, "%d" % i)
        for i in range(9, 16): self.failUnlessEqual(f(i,2), 16, "%d" % i)
        for i in range(17, 32): self.failUnlessEqual(f(i,2), 32, "%d" % i)
        for i in range(33, 64): self.failUnlessEqual(f(i,2), 64, "%d" % i)
        for i in range(65, 100): self.failUnlessEqual(f(i,2), 128, "%d" % i)

        self.failUnlessEqual(f(0,3), 1)
        self.failUnlessEqual(f(1,3), 1)
        self.failUnlessEqual(f(2,3), 3)
        self.failUnlessEqual(f(3,3), 3)
        for i in range(4, 9): self.failUnlessEqual(f(i,3), 9, "%d" % i)
        for i in range(10, 27): self.failUnlessEqual(f(i,3), 27, "%d" % i)
        for i in range(28, 81): self.failUnlessEqual(f(i,3), 81, "%d" % i)
        for i in range(82, 200): self.failUnlessEqual(f(i,3), 243, "%d" % i)

    def test_ave(self):
        f = mathutil.ave
        self.failUnlessEqual(f([1,2,3]), 2)
        self.failUnlessEqual(f([0,0,0,4]), 1)
        self.failUnlessAlmostEqual(f([0.0, 1.0, 1.0]), .666666666666)

    def failUnlessEqualContents(self, a, b):
        self.failUnlessEqual(sorted(a), sorted(b))

    def test_permute(self):
        f = mathutil.permute
        self.failUnlessEqualContents(f([]), [])
        self.failUnlessEqualContents(f([1]), [[1]])
        self.failUnlessEqualContents(f([1,2]), [[1,2], [2,1]])
        self.failUnlessEqualContents(f([1,2,3]),
                                     [[1,2,3], [1,3,2],
                                      [2,1,3], [2,3,1],
                                      [3,1,2], [3,2,1]])

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
