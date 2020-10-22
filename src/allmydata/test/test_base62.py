"""
Tests for allmydata.util.base62.

Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from past.builtins import chr as byteschr

import random, unittest

from hypothesis import (
    strategies as st,
    given,
)

from allmydata.util import base62, mathutil

def insecurerandstr(n):
    return bytes(list(map(random.randrange, [0]*n, [256]*n)))

class Base62(unittest.TestCase):
    def _test_num_octets_that_encode_to_this_many_chars(self, chars, octets):
        assert base62.num_octets_that_encode_to_this_many_chars(chars) == octets, "%s != %s <- %s" % (octets, base62.num_octets_that_encode_to_this_many_chars(chars), chars)

    def _test_roundtrip(self, bs):
        encoded = base62.b2a(bs)
        decoded = base62.a2b(encoded)
        self.assertEqual(decoded, bs)
        self.assertIsInstance(encoded, bytes)
        self.assertIsInstance(bs, bytes)
        self.assertIsInstance(decoded, bytes)
        # Encoded string only uses values from the base62 allowed characters:
        self.assertFalse(set(encoded) - set(base62.chars))

    @given(input_bytes=st.binary(max_size=100))
    def test_roundtrip(self, input_bytes):
        self._test_roundtrip(input_bytes)

    def test_known_values(self):
        """Known values to ensure the algorithm hasn't changed."""

        def check_expected(plaintext, encoded):
            result1 = base62.b2a(plaintext)
            self.assertEqual(encoded, result1)
            result2 = base62.a2b(encoded)
            self.assertEqual(plaintext, result2)

        check_expected(b"hello", b'7tQLFHz')
        check_expected(b"", b'0')
        check_expected(b"zzz", b'0Xg7e')
        check_expected(b"\x36\xffWAT", b'49pq4mq')
        check_expected(b"1234 22323", b'1A0afZe9mxSZpz')
        check_expected(b"______", b'0TmAuCHJX')

    def test_num_octets_that_encode_to_this_many_chars(self):
        return self._test_num_octets_that_encode_to_this_many_chars(2, 1)
        return self._test_num_octets_that_encode_to_this_many_chars(3, 2)
        return self._test_num_octets_that_encode_to_this_many_chars(5, 3)
        return self._test_num_octets_that_encode_to_this_many_chars(6, 4)

    def test_ende_0x00(self):
        return self._test_roundtrip(b'\x00')

    def test_ende_0x01(self):
        return self._test_roundtrip(b'\x01')

    def test_ende_0x0100(self):
        return self._test_roundtrip(b'\x01\x00')

    def test_ende_0x000000(self):
        return self._test_roundtrip(b'\x00\x00\x00')

    def test_ende_0x010000(self):
        return self._test_roundtrip(b'\x01\x00\x00')

    def test_ende_randstr(self):
        return self._test_roundtrip(insecurerandstr(2**4))

    def test_ende_longrandstr(self):
        return self._test_roundtrip(insecurerandstr(random.randrange(0, 2**10)))

    def test_odd_sizes(self):
        for j in range(2**6):
            lib = random.randrange(1, 2**8)
            numos = mathutil.div_ceil(lib, 8)
            bs = insecurerandstr(numos)
            # zero-out unused least-sig bits
            if lib%8:
                b = ord(bs[-1:])
                b = b >> (8 - (lib%8))
                b = b << (8 - (lib%8))
                bs = bs[:-1] + byteschr(b)
            asl = base62.b2a_l(bs, lib)
            assert len(asl) == base62.num_chars_that_this_many_octets_encode_to(numos) # the size of the base-62 encoding must be just right
            bs2l = base62.a2b_l(asl, lib)
            assert len(bs2l) == numos # the size of the result must be just right
            assert bs == bs2l
