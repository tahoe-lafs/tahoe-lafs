
import os
from twisted.trial import unittest
from twisted.python import log
from allmydata.codec import ReplicatingEncoder, ReplicatingDecoder, CRSEncoder, CRSDecoder
import random
from allmydata.util import mathutil

class Tester:
    def do_test(self, size, required_shares, max_shares, fewer_shares=None):
        data0s = [os.urandom(mathutil.div_ceil(size, required_shares)) for i in range(required_shares)]
        enc = self.enc_class()
        enc.set_params(size, required_shares, max_shares)
        serialized_params = enc.get_serialized_params()
        log.msg("serialized_params: %s" % serialized_params)
        d = enc.encode(data0s)
        def _done_encoding_all((shares, shareids)):
            self.failUnlessEqual(len(shares), max_shares)
            self.shares = shares
            self.shareids = shareids
        d.addCallback(_done_encoding_all)
        if fewer_shares is not None:
            # also validate that the desired_shareids= parameter works
            desired_shareids = random.sample(range(max_shares), fewer_shares)
            d.addCallback(lambda res: enc.encode(data0s, desired_shareids))
            def _check_fewer_shares((some_shares, their_shareids)):
                self.failUnlessEqual(tuple(their_shareids), tuple(desired_shareids))
            d.addCallback(_check_fewer_shares)

        def _decode((shares, shareids)):
            dec = self.dec_class()
            dec.set_serialized_params(serialized_params)
            d1 = dec.decode(shares, shareids)
            return d1

        def _check_data(decoded_shares):
            self.failUnlessEqual(len(''.join(decoded_shares)), len(''.join(data0s)))
            self.failUnlessEqual(len(decoded_shares), len(data0s))
            for (i, (x, y)) in enumerate(zip(data0s, decoded_shares)):
                self.failUnlessEqual(x, y, "%s: %r != %r....  first share was %r" % (str(i), x, y, data0s[0],))
            self.failUnless(''.join(decoded_shares) == ''.join(data0s), "%s" % ("???",))
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
            l = random.sample(zip(self.shares, self.shareids), required_shares)
            some_shares = [ x[0] for x in l ]
            some_shareids = [ x[1] for x in l ]
            return _decode((some_shares, some_shareids))
        d.addCallback(_decode_some_random)
        d.addCallback(_check_data)

        def _decode_multiple(res):
            log.msg("_decode_multiple")
            # make sure we can re-use the decoder object
            shares1 = random.sample(self.shares, required_shares)
            sharesl1 = random.sample(zip(self.shares, self.shareids), required_shares)
            shares1 = [ x[0] for x in sharesl1 ]
            shareids1 = [ x[1] for x in sharesl1 ]
            sharesl2 = random.sample(zip(self.shares, self.shareids), required_shares)
            shares2 = [ x[0] for x in sharesl2 ]
            shareids2 = [ x[1] for x in sharesl2 ]
            dec = self.dec_class()
            dec.set_serialized_params(serialized_params)
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

class Replicating(unittest.TestCase, Tester):
    enc_class = ReplicatingEncoder
    dec_class = ReplicatingDecoder

class CRS(unittest.TestCase, Tester):
    enc_class = CRSEncoder
    dec_class = CRSDecoder

