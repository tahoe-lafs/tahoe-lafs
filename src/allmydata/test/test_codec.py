"""
Tests for allmydata.codec.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os
from twisted.trial import unittest
from twisted.python import log
from allmydata.codec import CRSEncoder, CRSDecoder, parse_params
import random
from allmydata.util import mathutil

class T(unittest.TestCase):
    def do_test(self, size, required_shares, max_shares, fewer_shares=None):
        data0s = [os.urandom(mathutil.div_ceil(size, required_shares)) for i in range(required_shares)]
        enc = CRSEncoder()
        enc.set_params(size, required_shares, max_shares)
        params = enc.get_params()
        assert params == (size, required_shares, max_shares)
        serialized_params = enc.get_serialized_params()
        self.assertEqual(parse_params(serialized_params), params)
        log.msg("params: %s" % (params,))
        d = enc.encode(data0s)
        def _done_encoding_all(shares_and_shareids):
            (shares, shareids) = shares_and_shareids
            self.failUnlessEqual(len(shares), max_shares)
            self.shares = shares
            self.shareids = shareids
        d.addCallback(_done_encoding_all)
        if fewer_shares is not None:
            # also validate that the desired_shareids= parameter works
            desired_shareids = random.sample(list(range(max_shares)), fewer_shares)
            d.addCallback(lambda res: enc.encode(data0s, desired_shareids))
            def _check_fewer_shares(some_shares_and_their_shareids):
                (some_shares, their_shareids) = some_shares_and_their_shareids
                self.failUnlessEqual(tuple(their_shareids), tuple(desired_shareids))
            d.addCallback(_check_fewer_shares)

        def _decode(shares_and_shareids):
            (shares, shareids) = shares_and_shareids
            dec = CRSDecoder()
            dec.set_params(*params)
            d1 = dec.decode(shares, shareids)
            return d1

        def _check_data(decoded_shares):
            self.failUnlessEqual(len(b''.join(decoded_shares)), len(b''.join(data0s)))
            self.failUnlessEqual(len(decoded_shares), len(data0s))
            for (i, (x, y)) in enumerate(zip(data0s, decoded_shares)):
                self.failUnlessEqual(x, y, "%s: %r != %r....  first share was %r" % (str(i), x, y, data0s[0],))
            self.failUnless(b''.join(decoded_shares) == b''.join(data0s), "%s" % ("???",))
            # 0data0sclipped = tuple(data0s)
            # data0sclipped[-1] =
            # self.failUnless(tuple(decoded_shares) == tuple(data0s))

        def _decode_some(res):
            log.msg("_decode_some")
            # decode with a minimal subset of the shares
            some_shares = self.shares[:required_shares]
            some_shareids = self.shareids[:required_shares]
            return _decode((some_shares, some_shareids))
        d.addCallback(_decode_some)
        d.addCallback(_check_data)

        def _decode_some_random(res):
            log.msg("_decode_some_random")
            # use a randomly-selected minimal subset
            l = random.sample(list(zip(self.shares, self.shareids)), required_shares)
            some_shares = [ x[0] for x in l ]
            some_shareids = [ x[1] for x in l ]
            return _decode((some_shares, some_shareids))
        d.addCallback(_decode_some_random)
        d.addCallback(_check_data)

        def _decode_multiple(res):
            log.msg("_decode_multiple")
            # make sure we can re-use the decoder object
            shares1 = random.sample(self.shares, required_shares)
            sharesl1 = random.sample(list(zip(self.shares, self.shareids)), required_shares)
            shares1 = [ x[0] for x in sharesl1 ]
            shareids1 = [ x[1] for x in sharesl1 ]
            sharesl2 = random.sample(list(zip(self.shares, self.shareids)), required_shares)
            shares2 = [ x[0] for x in sharesl2 ]
            shareids2 = [ x[1] for x in sharesl2 ]
            dec = CRSDecoder()
            dec.set_params(*params)
            d1 = dec.decode(shares1, shareids1)
            d1.addCallback(_check_data)
            d1.addCallback(lambda res: dec.decode(shares2, shareids2))
            d1.addCallback(_check_data)
            return d1
        d.addCallback(_decode_multiple)

        return d

    def test_encode(self):
        return self.do_test(1000, 25, 100)

    def test_encode1(self):
        return self.do_test(8, 8, 16)

    def test_encode2(self):
        return self.do_test(125, 25, 100, 90)
