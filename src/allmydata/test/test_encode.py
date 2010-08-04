from zope.interface import implements
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python.failure import Failure
from foolscap.api import fireEventually
from allmydata import uri
from allmydata.immutable import encode, upload, checker
from allmydata.util import hashutil
from allmydata.util.assertutil import _assert
from allmydata.util.consumer import download_to_data
from allmydata.interfaces import IStorageBucketWriter, IStorageBucketReader
from allmydata.test.no_network import GridTestMixin

class LostPeerError(Exception):
    pass

def flip_bit(good): # flips the last bit
    return good[:-1] + chr(ord(good[-1]) ^ 0x01)

class FakeBucketReaderWriterProxy:
    implements(IStorageBucketWriter, IStorageBucketReader)
    # these are used for both reading and writing
    def __init__(self, mode="good", peerid="peer"):
        self.mode = mode
        self.blocks = {}
        self.plaintext_hashes = []
        self.crypttext_hashes = []
        self.block_hashes = None
        self.share_hashes = None
        self.closed = False
        self.peerid = peerid

    def get_peerid(self):
        return self.peerid

    def _start(self):
        if self.mode == "lost-early":
            f = Failure(LostPeerError("I went away early"))
            return fireEventually(f)
        return defer.succeed(self)

    def put_header(self):
        return self._start()

    def put_block(self, segmentnum, data):
        if self.mode == "lost-early":
            f = Failure(LostPeerError("I went away early"))
            return fireEventually(f)
        def _try():
            assert not self.closed
            assert segmentnum not in self.blocks
            if self.mode == "lost" and segmentnum >= 1:
                raise LostPeerError("I'm going away now")
            self.blocks[segmentnum] = data
        return defer.maybeDeferred(_try)

    def put_crypttext_hashes(self, hashes):
        def _try():
            assert not self.closed
            assert not self.crypttext_hashes
            self.crypttext_hashes = hashes
        return defer.maybeDeferred(_try)

    def put_block_hashes(self, blockhashes):
        def _try():
            assert not self.closed
            assert self.block_hashes is None
            self.block_hashes = blockhashes
        return defer.maybeDeferred(_try)

    def put_share_hashes(self, sharehashes):
        def _try():
            assert not self.closed
            assert self.share_hashes is None
            self.share_hashes = sharehashes
        return defer.maybeDeferred(_try)

    def put_uri_extension(self, uri_extension):
        def _try():
            assert not self.closed
            self.uri_extension = uri_extension
        return defer.maybeDeferred(_try)

    def close(self):
        def _try():
            assert not self.closed
            self.closed = True
        return defer.maybeDeferred(_try)

    def abort(self):
        return defer.succeed(None)

    def get_block_data(self, blocknum, blocksize, size):
        d = self._start()
        def _try(unused=None):
            assert isinstance(blocknum, (int, long))
            if self.mode == "bad block":
                return flip_bit(self.blocks[blocknum])
            return self.blocks[blocknum]
        d.addCallback(_try)
        return d

    def get_plaintext_hashes(self):
        d = self._start()
        def _try(unused=None):
            hashes = self.plaintext_hashes[:]
            return hashes
        d.addCallback(_try)
        return d

    def get_crypttext_hashes(self):
        d = self._start()
        def _try(unused=None):
            hashes = self.crypttext_hashes[:]
            if self.mode == "bad crypttext hashroot":
                hashes[0] = flip_bit(hashes[0])
            if self.mode == "bad crypttext hash":
                hashes[1] = flip_bit(hashes[1])
            return hashes
        d.addCallback(_try)
        return d

    def get_block_hashes(self, at_least_these=()):
        d = self._start()
        def _try(unused=None):
            if self.mode == "bad blockhash":
                hashes = self.block_hashes[:]
                hashes[1] = flip_bit(hashes[1])
                return hashes
            return self.block_hashes
        d.addCallback(_try)
        return d

    def get_share_hashes(self, at_least_these=()):
        d = self._start()
        def _try(unused=None):
            if self.mode == "bad sharehash":
                hashes = self.share_hashes[:]
                hashes[1] = (hashes[1][0], flip_bit(hashes[1][1]))
                return hashes
            if self.mode == "missing sharehash":
                # one sneaky attack would be to pretend we don't know our own
                # sharehash, which could manage to frame someone else.
                # download.py is supposed to guard against this case.
                return []
            return self.share_hashes
        d.addCallback(_try)
        return d

    def get_uri_extension(self):
        d = self._start()
        def _try(unused=None):
            if self.mode == "bad uri_extension":
                return flip_bit(self.uri_extension)
            return self.uri_extension
        d.addCallback(_try)
        return d


def make_data(length):
    data = "happy happy joy joy" * 100
    assert length <= len(data)
    return data[:length]

class ValidatedExtendedURIProxy(unittest.TestCase):
    timeout = 240 # It takes longer than 120 seconds on Francois's arm box.
    K = 4
    M = 10
    SIZE = 200
    SEGSIZE = 72
    _TMP = SIZE%SEGSIZE
    if _TMP == 0:
        _TMP = SEGSIZE
    if _TMP % K != 0:
        _TMP += (K - (_TMP % K))
    TAIL_SEGSIZE = _TMP
    _TMP = SIZE / SEGSIZE
    if SIZE % SEGSIZE != 0:
        _TMP += 1
    NUM_SEGMENTS = _TMP
    mindict = { 'segment_size': SEGSIZE,
                'crypttext_root_hash': '0'*hashutil.CRYPTO_VAL_SIZE,
                'share_root_hash': '1'*hashutil.CRYPTO_VAL_SIZE }
    optional_consistent = { 'crypttext_hash': '2'*hashutil.CRYPTO_VAL_SIZE,
                            'codec_name': "crs",
                            'codec_params': "%d-%d-%d" % (SEGSIZE, K, M),
                            'tail_codec_params': "%d-%d-%d" % (TAIL_SEGSIZE, K, M),
                            'num_segments': NUM_SEGMENTS,
                            'size': SIZE,
                            'needed_shares': K,
                            'total_shares': M,
                            'plaintext_hash': "anything",
                            'plaintext_root_hash': "anything", }
    # optional_inconsistent = { 'crypttext_hash': ('2'*(hashutil.CRYPTO_VAL_SIZE-1), "", 77),
    optional_inconsistent = { 'crypttext_hash': (77,),
                              'codec_name': ("digital fountain", ""),
                              'codec_params': ("%d-%d-%d" % (SEGSIZE, K-1, M),
                                               "%d-%d-%d" % (SEGSIZE-1, K, M),
                                               "%d-%d-%d" % (SEGSIZE, K, M-1)),
                              'tail_codec_params': ("%d-%d-%d" % (TAIL_SEGSIZE, K-1, M),
                                               "%d-%d-%d" % (TAIL_SEGSIZE-1, K, M),
                                               "%d-%d-%d" % (TAIL_SEGSIZE, K, M-1)),
                              'num_segments': (NUM_SEGMENTS-1,),
                              'size': (SIZE-1,),
                              'needed_shares': (K-1,),
                              'total_shares': (M-1,), }

    def _test(self, uebdict):
        uebstring = uri.pack_extension(uebdict)
        uebhash = hashutil.uri_extension_hash(uebstring)
        fb = FakeBucketReaderWriterProxy()
        fb.put_uri_extension(uebstring)
        verifycap = uri.CHKFileVerifierURI(storage_index='x'*16, uri_extension_hash=uebhash, needed_shares=self.K, total_shares=self.M, size=self.SIZE)
        vup = checker.ValidatedExtendedURIProxy(fb, verifycap)
        return vup.start()

    def _test_accept(self, uebdict):
        return self._test(uebdict)

    def _should_fail(self, res, expected_failures):
        if isinstance(res, Failure):
            res.trap(*expected_failures)
        else:
            self.fail("was supposed to raise %s, not get '%s'" % (expected_failures, res))

    def _test_reject(self, uebdict):
        d = self._test(uebdict)
        d.addBoth(self._should_fail, (KeyError, checker.BadURIExtension))
        return d

    def test_accept_minimal(self):
        return self._test_accept(self.mindict)

    def test_reject_insufficient(self):
        dl = []
        for k in self.mindict.iterkeys():
            insuffdict = self.mindict.copy()
            del insuffdict[k]
            d = self._test_reject(insuffdict)
        dl.append(d)
        return defer.DeferredList(dl)

    def test_accept_optional(self):
        dl = []
        for k in self.optional_consistent.iterkeys():
            mydict = self.mindict.copy()
            mydict[k] = self.optional_consistent[k]
            d = self._test_accept(mydict)
        dl.append(d)
        return defer.DeferredList(dl)

    def test_reject_optional(self):
        dl = []
        for k in self.optional_inconsistent.iterkeys():
            for v in self.optional_inconsistent[k]:
                mydict = self.mindict.copy()
                mydict[k] = v
                d = self._test_reject(mydict)
                dl.append(d)
        return defer.DeferredList(dl)

class Encode(unittest.TestCase):
    timeout = 2400 # It takes longer than 240 seconds on Zandr's ARM box.

    def do_encode(self, max_segment_size, datalen, NUM_SHARES, NUM_SEGMENTS,
                  expected_block_hashes, expected_share_hashes):
        data = make_data(datalen)
        # force use of multiple segments
        e = encode.Encoder()
        u = upload.Data(data, convergence="some convergence string")
        u.max_segment_size = max_segment_size
        u.encoding_param_k = 25
        u.encoding_param_happy = 75
        u.encoding_param_n = 100
        eu = upload.EncryptAnUploadable(u)
        d = e.set_encrypted_uploadable(eu)

        all_shareholders = []
        def _ready(res):
            k,happy,n = e.get_param("share_counts")
            _assert(n == NUM_SHARES) # else we'll be completely confused
            numsegs = e.get_param("num_segments")
            _assert(numsegs == NUM_SEGMENTS, numsegs, NUM_SEGMENTS)
            segsize = e.get_param("segment_size")
            _assert( (NUM_SEGMENTS-1)*segsize < len(data) <= NUM_SEGMENTS*segsize,
                     NUM_SEGMENTS, segsize,
                     (NUM_SEGMENTS-1)*segsize, len(data), NUM_SEGMENTS*segsize)

            shareholders = {}
            servermap = {}
            for shnum in range(NUM_SHARES):
                peer = FakeBucketReaderWriterProxy()
                shareholders[shnum] = peer
                servermap.setdefault(shnum, set()).add(peer.get_peerid())
                all_shareholders.append(peer)
            e.set_shareholders(shareholders, servermap)
            return e.start()
        d.addCallback(_ready)

        def _check(res):
            verifycap = res
            self.failUnless(isinstance(verifycap.uri_extension_hash, str))
            self.failUnlessEqual(len(verifycap.uri_extension_hash), 32)
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

    def test_send_124(self):
        # 5 segments: 25, 25, 25, 25, 24
        return self.do_encode(25, 124, 100, 5, 15, 8)
    def test_send_125(self):
        # 5 segments: 25, 25, 25, 25, 25
        return self.do_encode(25, 125, 100, 5, 15, 8)
    def test_send_101(self):
        # 5 segments: 25, 25, 25, 25, 1
        return self.do_encode(25, 101, 100, 5, 15, 8)


class Roundtrip(GridTestMixin, unittest.TestCase):

    # a series of 3*3 tests to check out edge conditions. One axis is how the
    # plaintext is divided into segments: kn+(-1,0,1). Another way to express
    # this is n%k == -1 or 0 or 1. For example, for 25-byte segments, we
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
    # trees, which gets 15 blockhashes.

    def test_74(self): return self.do_test_size(74)
    def test_75(self): return self.do_test_size(75)
    def test_51(self): return self.do_test_size(51)
    def test_99(self): return self.do_test_size(99)
    def test_100(self): return self.do_test_size(100)
    def test_76(self): return self.do_test_size(76)
    def test_124(self): return self.do_test_size(124)
    def test_125(self): return self.do_test_size(125)
    def test_101(self): return self.do_test_size(101)

    def upload(self, data):
        u = upload.Data(data, None)
        u.max_segment_size = 25
        u.encoding_param_k = 25
        u.encoding_param_happy = 1
        u.encoding_param_n = 100
        d = self.c0.upload(u)
        d.addCallback(lambda ur: self.c0.create_node_from_uri(ur.uri))
        # returns a FileNode
        return d

    def do_test_size(self, size):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        DATA = "p"*size
        d = self.upload(DATA)
        d.addCallback(lambda n: download_to_data(n))
        def _downloaded(newdata):
            self.failUnlessEqual(newdata, DATA)
        d.addCallback(_downloaded)
        return d
