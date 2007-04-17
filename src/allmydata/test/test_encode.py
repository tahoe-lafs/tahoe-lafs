#! /usr/bin/env python

from twisted.trial import unittest
from twisted.internet import defer
from twisted.python.failure import Failure
from foolscap import eventual
from allmydata import encode, download
from allmydata.uri import pack_uri
from cStringIO import StringIO

class FakePeer:
    def __init__(self, mode="good"):
        self.ss = FakeStorageServer(mode)

    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        return defer.maybeDeferred(_call)

    def get_service(self, sname):
        assert sname == "storageserver"
        return self.ss

class FakeStorageServer:
    def __init__(self, mode):
        self.mode = mode
    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        d = eventual.fireEventually()
        d.addCallback(lambda res: _call())
        return d
    def allocate_buckets(self, verifierid, sharenums, shareize, blocksize, canary):
        if self.mode == "full":
            return (set(), {},)
        elif self.mode == "already got them":
            return (set(sharenums), {},)
        else:
            return (set(), dict([(shnum, FakeBucketWriter(),) for shnum in sharenums]),)

class FakeBucketWriter:
    # these are used for both reading and writing
    def __init__(self, mode="good"):
        self.mode = mode
        self.blocks = {}
        self.block_hashes = None
        self.share_hashes = None
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
        assert self.block_hashes is None
        self.block_hashes = blockhashes
        
    def put_share_hashes(self, sharehashes):
        assert not self.closed
        assert self.share_hashes is None
        self.share_hashes = sharehashes

    def close(self):
        assert not self.closed
        self.closed = True

    def flip_bit(self, good):
        return good[:-1] + chr(ord(good[-1]) ^ 0x01)

    def get_block(self, blocknum):
        assert isinstance(blocknum, int)
        if self.mode == "bad block":
            return self.flip_bit(self.blocks[blocknum])
        return self.blocks[blocknum]

    def get_block_hashes(self):
        if self.mode == "bad blockhash":
            hashes = self.block_hashes[:]
            hashes[1] = self.flip_bit(hashes[1])
            return hashes
        return self.block_hashes
    def get_share_hashes(self):
        if self.mode == "bad sharehash":
            hashes = self.share_hashes[:]
            hashes[1] = (hashes[1][0], self.flip_bit(hashes[1][1]))
            return hashes
        if self.mode == "missing sharehash":
            # one sneaky attack would be to pretend we don't know our own
            # sharehash, which could manage to frame someone else.
            # download.py is supposed to guard against this case.
            return []
        return self.share_hashes


def make_data(length):
    data = "happy happy joy joy" * 100
    assert length <= len(data)
    return data[:length]

class Encode(unittest.TestCase):

    def do_encode(self, max_segment_size, datalen, NUM_SHARES, NUM_SEGMENTS,
                  expected_block_hashes, expected_share_hashes):
        data = make_data(datalen)
        # force use of multiple segments
        options = {"max_segment_size": max_segment_size}
        e = encode.Encoder(options)
        e.setup(StringIO(data))
        assert e.num_shares == NUM_SHARES # else we'll be completely confused
        e.setup_codec() # need to rebuild the codec for that change
        assert (NUM_SEGMENTS-1)*e.segment_size < len(data) <= NUM_SEGMENTS*e.segment_size
        shareholders = {}
        all_shareholders = []
        for shnum in range(NUM_SHARES):
            peer = FakeBucketWriter()
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
                # each peer gets a full tree of block hashes. For 3 or 4
                # segments, that's 7 hashes. For 5 segments it's 15 hashes.
                self.failUnlessEqual(len(peer.block_hashes),
                                     expected_block_hashes)
                for h in peer.block_hashes:
                    self.failUnlessEqual(len(h), 32)
                # each peer also gets their necessary chain of share hashes.
                # For 100 shares (rounded up to 128 leaves), that's 8 hashes
                self.failUnlessEqual(len(peer.share_hashes),
                                     expected_share_hashes)
                for (hashnum, h) in peer.share_hashes:
                    self.failUnless(isinstance(hashnum, int))
                    self.failUnlessEqual(len(h), 32)
        d.addCallback(_check)

        return d

    # a series of 3*3 tests to check out edge conditions. One axis is how the
    # plaintext is divided into segments: kn+(-1,0,1). Another way to express
    # that is that n%k == -1 or 0 or 1. For example, for 25-byte segments, we
    # might test 74 bytes, 75 bytes, and 76 bytes.

    # on the other axis is how many leaves in the block hash tree we wind up
    # with, relative to a power of 2, so 2^a+(-1,0,1). Each segment turns
    # into a single leaf. So we'd like to check out, e.g., 3 segments, 4
    # segments, and 5 segments.

    # that results in the following series of data lengths:
    #  3 segs: 74, 75, 51
    #  4 segs: 99, 100, 76
    #  5 segs: 124, 125, 101

    # all tests encode to 100 shares, which means the share hash tree will
    # have 128 leaves, which means that buckets will be given an 8-long share
    # hash chain
    
    # all 3-segment files will have a 4-leaf blockhashtree, and thus expect
    # to get 7 blockhashes. 4-segment files will also get 4-leaf block hash
    # trees and 7 blockhashes. 5-segment files will get 8-leaf block hash
    # trees, which get 15 blockhashes.

    def test_send_74(self):
        # 3 segments (25, 25, 24)
        return self.do_encode(25, 74, 100, 3, 7, 8)
    def test_send_75(self):
        # 3 segments (25, 25, 25)
        return self.do_encode(25, 75, 100, 3, 7, 8)
    def test_send_51(self):
        # 3 segments (25, 25, 1)
        return self.do_encode(25, 51, 100, 3, 7, 8)

    def test_send_76(self):
        # encode a 76 byte file (in 4 segments: 25,25,25,1) to 100 shares
        return self.do_encode(25, 76, 100, 4, 7, 8)
    def test_send_99(self):
        # 4 segments: 25,25,25,24
        return self.do_encode(25, 99, 100, 4, 7, 8)
    def test_send_100(self):
        # 4 segments: 25,25,25,25
        return self.do_encode(25, 100, 100, 4, 7, 8)

    def test_send_101(self):
        # encode a 101 byte file (in 5 segments: 25,25,25,25,1) to 100 shares
        return self.do_encode(25, self.make_data(101), 100, 5, 15, 8)

    def test_send_124(self):
        # 5 segments: 25, 25, 25, 25, 24
        return self.do_encode(25, 124, 100, 5, 15, 8)
    def test_send_125(self):
        # 5 segments: 25, 25, 25, 25, 25
        return self.do_encode(25, 125, 100, 5, 15, 8)
    def test_send_101(self):
        # 5 segments: 25, 25, 25, 25, 1
        return self.do_encode(25, 101, 100, 5, 15, 8)

class Roundtrip(unittest.TestCase):
    def send_and_recover(self, NUM_SHARES,
                         AVAILABLE_SHARES=None,
                         DATA_LENGTH=76,
                         bucket_modes={}):
        if AVAILABLE_SHARES is None:
            AVAILABLE_SHARES = NUM_SHARES
        options = {"max_segment_size": 25} # force use of multiple segments
        e = encode.Encoder(options)
        data = "happy happy joy joy" * 10
        assert DATA_LENGTH <= len(data)
        data = data[:DATA_LENGTH]
        e.setup(StringIO(data))

        assert e.num_shares == NUM_SHARES # else we'll be completely confused
        e.setup_codec() # need to rebuild the codec for that change

        shareholders = {}
        all_shareholders = []
        all_peers = []
        for shnum in range(NUM_SHARES):
            mode = bucket_modes.get(shnum, "good")
            peer = FakeBucketWriter(mode)
            shareholders[shnum] = peer
            all_shareholders.append(peer)
        e.set_shareholders(shareholders)
        d = e.start()
        def _uploaded(roothash):
            URI = pack_uri(e._codec.get_encoder_type(),
                           e._codec.get_serialized_params(),
                           e._tail_codec.get_serialized_params(),
                           "V" * 20,
                           roothash,
                           e.required_shares,
                           e.num_shares,
                           e.file_size,
                           e.segment_size)
            client = None
            target = download.Data()
            fd = download.FileDownloader(client, URI, target)
            for shnum in range(AVAILABLE_SHARES):
                bucket = all_shareholders[shnum]
                fd.add_share_bucket(shnum, bucket)
            fd._got_all_shareholders(None)
            d2 = fd._download_all_segments(None)
            d2.addCallback(fd._done)
            return d2
        d.addCallback(_uploaded)
        def _downloaded(newdata):
            self.failUnless(newdata == data)
        d.addCallback(_downloaded)

        return d

    def test_not_enough_shares(self):
        d = self.send_and_recover(100, AVAILABLE_SHARES=10)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(download.NotEnoughPeersError))
        d.addBoth(_done)
        return d

    def test_one_share_per_peer(self):
        return self.send_and_recover(100)

    def test_multiple_of_segsize(self):
        return self.send_and_recover(100, DATA_LENGTH=75)

    def test_bad_blocks(self):
        # the first 74 servers have bad blocks, which will be caught by the
        # blockhashes
        modemap = dict([(i, "bad block")
                        for i in range(74)]
                       + [(i, "good")
                          for i in range(74, 100)])
        return self.send_and_recover(100, bucket_modes=modemap)

    def test_bad_blocks_failure(self):
        # the first 76 servers have bad blocks, which will be caught by the
        # blockhashes, and the download will fail
        modemap = dict([(i, "bad block")
                        for i in range(76)]
                       + [(i, "good")
                          for i in range(76, 100)])
        d = self.send_and_recover(100, bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(download.NotEnoughPeersError))
        d.addBoth(_done)
        return d

    def test_bad_blockhashes(self):
        # the first 74 servers have bad block hashes, so the blockhash tree
        # will not validate
        modemap = dict([(i, "bad blockhash")
                        for i in range(74)]
                       + [(i, "good")
                          for i in range(74, 100)])
        return self.send_and_recover(100, bucket_modes=modemap)

    def test_bad_blockhashes_failure(self):
        # the first 76 servers have bad block hashes, so the blockhash tree
        # will not validate, and the download will fail
        modemap = dict([(i, "bad blockhash")
                        for i in range(76)]
                       + [(i, "good")
                          for i in range(76, 100)])
        d = self.send_and_recover(100, bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(download.NotEnoughPeersError))
        d.addBoth(_done)
        return d

    def test_bad_sharehashes(self):
        # the first 74 servers have bad block hashes, so the sharehash tree
        # will not validate
        modemap = dict([(i, "bad sharehash")
                        for i in range(74)]
                       + [(i, "good")
                          for i in range(74, 100)])
        return self.send_and_recover(100, bucket_modes=modemap)

    def test_bad_sharehashes_failure(self):
        # the first 76 servers have bad block hashes, so the sharehash tree
        # will not validate, and the download will fail
        modemap = dict([(i, "bad sharehash")
                        for i in range(76)]
                       + [(i, "good")
                          for i in range(76, 100)])
        d = self.send_and_recover(100, bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(download.NotEnoughPeersError))
        d.addBoth(_done)
        return d

    def test_missing_sharehashes(self):
        # the first 74 servers are missing their sharehashes, so the
        # sharehash tree will not validate
        modemap = dict([(i, "missing sharehash")
                        for i in range(74)]
                       + [(i, "good")
                          for i in range(74, 100)])
        return self.send_and_recover(100, bucket_modes=modemap)

    def test_missing_sharehashes_failure(self):
        # the first 76 servers are missing their sharehashes, so the
        # sharehash tree will not validate, and the download will fail
        modemap = dict([(i, "missing sharehash")
                        for i in range(76)]
                       + [(i, "good")
                          for i in range(76, 100)])
        d = self.send_and_recover(100, bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(download.NotEnoughPeersError))
        d.addBoth(_done)
        return d

