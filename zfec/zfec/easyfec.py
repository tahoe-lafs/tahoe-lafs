# zfec -- a fast C implementation of Reed-Solomon erasure coding with
# command-line, C, and Python interfaces

import zfec

# div_ceil() was copied from the pyutil library.
def div_ceil(n, d):
    """
    The smallest integer k such that k*d >= n.
    """
    return (n/d) + (n%d != 0)

class Encoder(object):
    def __init__(self, k, m):
        self.fec = zfec.Encoder(k, m)

    def encode(self, data):
        """
        @param data: string
        """
        chunksize = div_ceil(len(data), self.fec.k)
        numchunks = div_ceil(len(data), chunksize)
        l = [ data[i:i+chunksize] for i in range(0, len(data), chunksize) ]
        # padding
        if len(l[-1]) != len(l[0]):
            l[-1] = l[-1] + ('\x00'*(len(l[0])-len(l[-1])))
        res = self.fec.encode(l)
        return res
        
class Decoder(object):
    def __init__(self, k, m):
        self.fec = zfec.Decoder(k, m)

    def decode(self, blocks, sharenums, padlen=0):
        blocks = self.fec.decode(blocks, sharenums)
        data = ''.join(blocks)
        if padlen:
            data = data[:-padlen]
        return data


# zfec -- a fast C implementation of Reed-Solomon erasure coding with
# command-line, C, and Python interfaces
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# mailto:zooko@zooko.com
# 
# This file is part of zfec.
# 
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.  This package also comes with the added permission that,
# in the case that you are obligated to release a derived work under this
# licence (as per section 2.b of the GPL), you may delay the fulfillment of
# this obligation for up to 12 months.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

