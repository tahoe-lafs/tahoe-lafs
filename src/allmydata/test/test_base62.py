#!/usr/bin/env python

import random, unittest

from allmydata.util import base62, mathutil

def insecurerandstr(n):
    return ''.join(map(chr, map(random.randrange, [0]*n, [256]*n)))

class T(unittest.TestCase):
    def _test_num_octets_that_encode_to_this_many_chars(self, chars, octets):
        assert base62.num_octets_that_encode_to_this_many_chars(chars) == octets, "%s != %s <- %s" % (octets, base62.num_octets_that_encode_to_this_many_chars(chars), chars)

    def _test_ende(self, bs):
        ascii=base62.b2a(bs)
        bs2=base62.a2b(ascii)
        assert bs2 == bs, "bs2: %s:%s, bs: %s:%s, ascii: %s:%s" % (len(bs2), `bs2`, len(bs), `bs`, len(ascii), `ascii`)

    def test_num_octets_that_encode_to_this_many_chars(self):
        return self._test_num_octets_that_encode_to_this_many_chars(2, 1)
        return self._test_num_octets_that_encode_to_this_many_chars(3, 2)
        return self._test_num_octets_that_encode_to_this_many_chars(5, 3)
        return self._test_num_octets_that_encode_to_this_many_chars(6, 4)

    def test_ende_0x00(self):
        return self._test_ende('\x00')

    def test_ende_0x01(self):
        return self._test_ende('\x01')

    def test_ende_0x0100(self):
        return self._test_ende('\x01\x00')

    def test_ende_0x000000(self):
        return self._test_ende('\x00\x00\x00')

    def test_ende_0x010000(self):
        return self._test_ende('\x01\x00\x00')

    def test_ende_randstr(self):
        return self._test_ende(insecurerandstr(2**4))

    def test_ende_longrandstr(self):
        return self._test_ende(insecurerandstr(random.randrange(0, 2**10)))

    def test_odd_sizes(self):
        for j in range(2**6):
            lib = random.randrange(1, 2**8)
            numos = mathutil.div_ceil(lib, 8)
            bs = insecurerandstr(numos)
            # zero-out unused least-sig bits
            if lib%8:
                b=ord(bs[-1])
                b = b >> (8 - (lib%8))
                b = b << (8 - (lib%8))
                bs = bs[:-1] + chr(b)
            asl = base62.b2a_l(bs, lib)
            assert len(asl) == base62.num_chars_that_this_many_octets_encode_to(numos) # the size of the base-62 encoding must be just right
            bs2l = base62.a2b_l(asl, lib)
            assert len(bs2l) == numos # the size of the result must be just right
            assert bs == bs2l

def suite():
    suite = unittest.makeSuite(T, 'test')
    return suite

if __name__ == "__main__":
    unittest.main()
