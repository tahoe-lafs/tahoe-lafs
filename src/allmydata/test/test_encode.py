#! /usr/bin/env python

from twisted.trial import unittest
from twisted.internet import defer
from allmydata import encode_new
from cStringIO import StringIO

class MyEncoder(encode_new.Encoder):
    def send(self, share_num, methname, *args, **kwargs):
        if False and share_num < 10:
            print "send[%d].%s()" % (share_num, methname)
            if methname == "put_share_hashes":
                print " ", [i for i,h in args[0]]
        return defer.succeed(None)

class Encode(unittest.TestCase):
    def test_1(self):
        e = MyEncoder()
        data = StringIO("some data to encode\n")
        e.setup(data)
        d = e.start()
        return d

class FakePeer:
    def __init__(self):
        self.blocks = {}
        self.blockhashes = None
        self.sharehashes = None
        self.closed = False

    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        return defer.maybeDeferred(_call)

    def put_block(self, segmentnum, data):
        assert not self.closed
        assert segmentnum not in self.blocks
        self.blocks[segmentnum] = data
    
    def put_block_hashes(self, blockhashes):
        assert not self.closed
        assert self.blockhashes is None
        self.blockhashes = blockhashes
        
    def put_share_hashes(self, sharehashes):
        assert not self.closed
        assert self.sharehashes is None
        self.sharehashes = sharehashes

    def close(self):
        assert not self.closed
        self.closed = True


class UpDown(unittest.TestCase):
    def test_send(self):
        e = encode_new.Encoder()
        data = "happy happy joy joy" * 4
        e.setup(StringIO(data))
        NUM_SHARES = 100
        assert e.num_shares == NUM_SHARES # else we'll be completely confused
        e.segment_size = 25 # force use of multiple segments
        e.setup_codec() # need to rebuild the codec for that change
        NUM_SEGMENTS = 4
        assert (NUM_SEGMENTS-1)*e.segment_size < len(data) <= NUM_SEGMENTS*e.segment_size
        shareholders = {}
        all_shareholders = []
        for shnum in range(NUM_SHARES):
            peer = FakePeer()
            shareholders[shnum] = peer
            all_shareholders.append(peer)
        e.set_shareholders(shareholders)
        d = e.start()
        def _check(roothash):
            self.failUnless(isinstance(roothash, str))
            self.failUnlessEqual(len(roothash), 32)
            for i,peer in enumerate(all_shareholders):
                self.failUnless(peer.closed)
                self.failUnlessEqual(len(peer.blocks), NUM_SEGMENTS)
                #self.failUnlessEqual(len(peer.blockhashes), NUM_SEGMENTS)
                # that isn't true: each peer gets a full tree, so it's more
                # like 2n-1 but with rounding to a power of two
                for h in peer.blockhashes:
                    self.failUnlessEqual(len(h), 32)
                #self.failUnlessEqual(len(peer.sharehashes), NUM_SHARES)
                # that isn't true: each peer only gets the chain they need
                for (hashnum, h) in peer.sharehashes:
                    self.failUnless(isinstance(hashnum, int))
                    self.failUnlessEqual(len(h), 32)
        d.addCallback(_check)

        return d
