
import os
from twisted.trial import unittest
from twisted.internet import defer
from allmydata.encode import PyRSEncoder, PyRSDecoder, ReplicatingEncoder, ReplicatingDecoder
import random

class Tester:
    #enc_class = PyRSEncoder
    #dec_class = PyRSDecoder

    def do_test(self, size, required_shares, total_shares):
        data0 = os.urandom(size)
        enc = self.enc_class()
        enc.set_params(size, required_shares, total_shares)
        serialized_params = enc.get_serialized_params()
        d = enc.encode(data0)
        def _done(shares):
            self.failUnlessEqual(len(shares), total_shares)
            self.shares = shares
        d.addCallback(_done)

        def _decode(shares):
            dec = self.dec_class()
            dec.set_serialized_params(serialized_params)
            d1 = dec.decode(shares)
            return d1

        def _check_data(data1):
            self.failUnlessEqual(len(data1), len(data0))
            self.failUnless(data1 == data0)

        def _decode_all_ordered(res):
            # can we decode using all of the shares?
            return _decode(self.shares)
        d.addCallback(_decode_all_ordered)
        d.addCallback(_check_data)

        def _decode_all_shuffled(res):
            # can we decode, using all the shares, but in random order?
            shuffled_shares = self.shares[:]
            random.shuffle(shuffled_shares)
            return _decode(shuffled_shares)
        d.addCallback(_decode_all_shuffled)
        d.addCallback(_check_data)
        
        def _decode_some(res):
            # decode with a minimal subset of the shares
            some_shares = self.shares[:required_shares]
            return _decode(some_shares)
        d.addCallback(_decode_some)
        d.addCallback(_check_data)

        def _decode_some_random(res):
            # use a randomly-selected minimal subset
            some_shares = random.sample(self.shares, required_shares)
            return _decode(some_shares)
        d.addCallback(_decode_some_random)
        d.addCallback(_check_data)

        def _decode_multiple(res):
            # make sure we can re-use the decoder object
            shares1 = random.sample(self.shares, required_shares)
            shares2 = random.sample(self.shares, required_shares)
            dec = self.dec_class()
            dec.set_serialized_params(serialized_params)
            d1 = dec.decode(shares1)
            d1.addCallback(_check_data)
            d1.addCallback(lambda res: dec.decode(shares2))
            d1.addCallback(_check_data)
            return d1
        d.addCallback(_decode_multiple)

        return d

    def test_encode(self):
        return self.do_test(1000, 25, 100)

    def test_sizes(self):
        d = defer.succeed(None)
        for i in range(1, 100):
            d.addCallback(lambda res,size: self.do_test(size, 4, 10), i)
        return d

class PyRS(unittest.TestCase, Tester):
    enc_class = PyRSEncoder
    dec_class = PyRSDecoder


class Replicating(unittest.TestCase, Tester):
    enc_class = ReplicatingEncoder
    dec_class = ReplicatingDecoder
