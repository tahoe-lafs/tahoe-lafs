
import os, time
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import log
from allmydata.codec import PyRSEncoder, PyRSDecoder, ReplicatingEncoder, ReplicatingDecoder
import random

class Tester:
    #enc_class = PyRSEncoder
    #dec_class = PyRSDecoder

    def do_test(self, size, required_shares, max_shares, fewer_shares=None):
        data0 = os.urandom(size)
        enc = self.enc_class()
        enc.set_params(size, required_shares, max_shares)
        serialized_params = enc.get_serialized_params()
        log.msg("serialized_params: %s" % serialized_params)
        d = enc.encode(data0)
        def _done_encoding_all(shares):
            self.failUnlessEqual(len(shares), max_shares)
            self.shares = shares
        d.addCallback(_done_encoding_all)
        if fewer_shares is not None:
            # also validate that the num_shares= parameter works
            d.addCallback(lambda res: enc.encode(data0, fewer_shares))
            def _check_fewer_shares(some_shares):
                self.failUnlessEqual(len(some_shares), fewer_shares)
            d.addCallback(_check_fewer_shares)

        def _decode(shares):
            dec = self.dec_class()
            dec.set_serialized_params(serialized_params)
            d1 = dec.decode(shares)
            return d1

        def _check_data(decoded_shares):
            data1 = "".join(decoded_shares)
            self.failUnlessEqual(len(data1), len(data0))
            self.failUnless(data1 == data0)

        def _decode_all_ordered(res):
            log.msg("_decode_all_ordered")
            # can we decode using all of the shares?
            return _decode(self.shares)
        d.addCallback(_decode_all_ordered)
        d.addCallback(_check_data)

        def _decode_all_shuffled(res):
            log.msg("_decode_all_shuffled")
            # can we decode, using all the shares, but in random order?
            shuffled_shares = self.shares[:]
            random.shuffle(shuffled_shares)
            return _decode(shuffled_shares)
        d.addCallback(_decode_all_shuffled)
        d.addCallback(_check_data)

        def _decode_some(res):
            log.msg("_decode_some")
            # decode with a minimal subset of the shares
            some_shares = self.shares[:required_shares]
            return _decode(some_shares)
        d.addCallback(_decode_some)
        d.addCallback(_check_data)

        def _decode_some_random(res):
            log.msg("_decode_some_random")
            # use a randomly-selected minimal subset
            some_shares = random.sample(self.shares, required_shares)
            return _decode(some_shares)
        d.addCallback(_decode_some_random)
        d.addCallback(_check_data)

        def _decode_multiple(res):
            log.msg("_decode_multiple")
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
        if os.uname()[1] == "slave3" and self.enc_class == PyRSEncoder:
            raise unittest.SkipTest("slave3 is really slow")
        return self.do_test(1000, 25, 100)

    def test_encode1(self):
        return self.do_test(8, 8, 16)

    def test_encode2(self):
        if os.uname()[1] == "slave3" and self.enc_class == PyRSEncoder:
            raise unittest.SkipTest("slave3 is really slow")
        return self.do_test(123, 25, 100, 90)

    def test_sizes(self):
        raise unittest.SkipTest("omg this would take forever")
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


class BenchPyRS(unittest.TestCase):
    enc_class = PyRSEncoder
    def test_big(self):
        size = 10000
        required_shares = 25
        max_shares = 100
        # this lets us use a persistent lookup table, stored outside the
        # _trial_temp directory (which is deleted each time trial is run)
        os.symlink("../ffield.lut.8", "ffield.lut.8")
        enc = self.enc_class()
        self.start()
        enc.set_params(size, required_shares, max_shares)
        serialized_params = enc.get_serialized_params()
        print "encoder ready", self.stop()
        self.start()
        data0 = os.urandom(size)
        print "data ready", self.stop()
        self.start()
        d = enc.encode(data0)
        def _done(shares):
            now_shares = time.time()
            print "shares ready", self.stop()
            self.start()
            self.failUnlessEqual(len(shares), max_shares)
        d.addCallback(_done)
        d.addCallback(lambda res: enc.encode(data0))
        d.addCallback(_done)
        d.addCallback(lambda res: enc.encode(data0))
        d.addCallback(_done)
        return d

    def start(self):
        self.start_time = time.time()

    def stop(self):
        self.end_time = time.time()
        return (self.end_time - self.start_time)


# to benchmark the encoder, delete this line
del BenchPyRS
# and then run 'make test TEST=allmydata.test.test_encode_share.BenchPyRS'
