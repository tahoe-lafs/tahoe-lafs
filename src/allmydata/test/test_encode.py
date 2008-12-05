from zope.interface import implements
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.internet.interfaces import IConsumer
from twisted.python.failure import Failure
from foolscap import eventual
from allmydata import hashtree, uri
from allmydata.immutable import encode, upload, download
from allmydata.util import hashutil
from allmydata.util.assertutil import _assert
from allmydata.interfaces import IStorageBucketWriter, IStorageBucketReader, NotEnoughSharesError
import common_util as testutil

class LostPeerError(Exception):
    pass

def flip_bit(good): # flips the last bit
    return good[:-1] + chr(ord(good[-1]) ^ 0x01)

class FakeClient:
    def log(self, *args, **kwargs):
        pass

class FakeBucketReaderWriterProxy:
    implements(IStorageBucketWriter, IStorageBucketReader)
    # these are used for both reading and writing
    def __init__(self, mode="good"):
        self.mode = mode
        self.blocks = {}
        self.plaintext_hashes = []
        self.crypttext_hashes = []
        self.block_hashes = None
        self.share_hashes = None
        self.closed = False

    def get_peerid(self):
        return "peerid"

    def startIfNecessary(self):
        return defer.succeed(self)
    def start(self):
        if self.mode == "lost-early":
            f = Failure(LostPeerError("I went away early"))
            return eventual.fireEventually(f)
        return defer.succeed(self)

    def put_block(self, segmentnum, data):
        if self.mode == "lost-early":
            f = Failure(LostPeerError("I went away early"))
            return eventual.fireEventually(f)
        def _try():
            assert not self.closed
            assert segmentnum not in self.blocks
            if self.mode == "lost" and segmentnum >= 1:
                raise LostPeerError("I'm going away now")
            self.blocks[segmentnum] = data
        return defer.maybeDeferred(_try)

    def put_plaintext_hashes(self, hashes):
        def _try():
            assert not self.closed
            assert not self.plaintext_hashes
            self.plaintext_hashes = hashes
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

    def get_block(self, blocknum):
        def _try():
            assert isinstance(blocknum, (int, long))
            if self.mode == "bad block":
                return flip_bit(self.blocks[blocknum])
            return self.blocks[blocknum]
        return defer.maybeDeferred(_try)

    def get_plaintext_hashes(self):
        def _try():
            hashes = self.plaintext_hashes[:]
            return hashes
        return defer.maybeDeferred(_try)

    def get_crypttext_hashes(self):
        def _try():
            hashes = self.crypttext_hashes[:]
            if self.mode == "bad crypttext hashroot":
                hashes[0] = flip_bit(hashes[0])
            if self.mode == "bad crypttext hash":
                hashes[1] = flip_bit(hashes[1])
            return hashes
        return defer.maybeDeferred(_try)

    def get_block_hashes(self):
        def _try():
            if self.mode == "bad blockhash":
                hashes = self.block_hashes[:]
                hashes[1] = flip_bit(hashes[1])
                return hashes
            return self.block_hashes
        return defer.maybeDeferred(_try)

    def get_share_hashes(self):
        def _try():
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
        return defer.maybeDeferred(_try)

    def get_uri_extension(self):
        def _try():
            if self.mode == "bad uri_extension":
                return flip_bit(self.uri_extension)
            return self.uri_extension
        return defer.maybeDeferred(_try)


def make_data(length):
    data = "happy happy joy joy" * 100
    assert length <= len(data)
    return data[:length]

class ValidatedExtendedURIProxy(unittest.TestCase):
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
        vup = download.ValidatedExtendedURIProxy(fb, verifycap)
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
        d.addBoth(self._should_fail, (KeyError, download.BadURIExtension))
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
            for shnum in range(NUM_SHARES):
                peer = FakeBucketReaderWriterProxy()
                shareholders[shnum] = peer
                all_shareholders.append(peer)
            e.set_shareholders(shareholders)
            return e.start()
        d.addCallback(_ready)

        def _check(res):
            (uri_extension_hash, required_shares, num_shares, file_size) = res
            self.failUnless(isinstance(uri_extension_hash, str))
            self.failUnlessEqual(len(uri_extension_hash), 32)
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

    def test_send_124(self):
        # 5 segments: 25, 25, 25, 25, 24
        return self.do_encode(25, 124, 100, 5, 15, 8)
    def test_send_125(self):
        # 5 segments: 25, 25, 25, 25, 25
        return self.do_encode(25, 125, 100, 5, 15, 8)
    def test_send_101(self):
        # 5 segments: 25, 25, 25, 25, 1
        return self.do_encode(25, 101, 100, 5, 15, 8)

class PausingTarget(download.Data):
    implements(IConsumer)
    def __init__(self):
        download.Data.__init__(self)
        self.size = 0
        self.writes = 0
    def write(self, data):
        self.size += len(data)
        self.writes += 1
        if self.writes <= 2:
            # we happen to use 4 segments, and want to avoid pausing on the
            # last one (since then the _unpause timer will still be running)
            self.producer.pauseProducing()
            reactor.callLater(0.1, self._unpause)
        return download.Data.write(self, data)
    def _unpause(self):
        self.producer.resumeProducing()
    def registerProducer(self, producer, streaming):
        self.producer = producer
    def unregisterProducer(self):
        self.producer = None

class PausingAndStoppingTarget(PausingTarget):
    def write(self, data):
        self.producer.pauseProducing()
        reactor.callLater(0.5, self._stop)
    def _stop(self):
        self.producer.stopProducing()

class StoppingTarget(PausingTarget):
    def write(self, data):
        self.producer.stopProducing()

class Roundtrip(unittest.TestCase, testutil.ShouldFailMixin):
    def send_and_recover(self, k_and_happy_and_n=(25,75,100),
                         AVAILABLE_SHARES=None,
                         datalen=76,
                         max_segment_size=25,
                         bucket_modes={},
                         recover_mode="recover",
                         target=None,
                         ):
        if AVAILABLE_SHARES is None:
            AVAILABLE_SHARES = k_and_happy_and_n[2]
        data = make_data(datalen)
        d = self.send(k_and_happy_and_n, AVAILABLE_SHARES,
                      max_segment_size, bucket_modes, data)
        # that fires with (uri_extension_hash, e, shareholders)
        d.addCallback(self.recover, AVAILABLE_SHARES, recover_mode,
                      target=target)
        # that fires with newdata
        def _downloaded((newdata, fd)):
            self.failUnless(newdata == data)
            return fd
        d.addCallback(_downloaded)
        return d

    def send(self, k_and_happy_and_n, AVAILABLE_SHARES, max_segment_size,
             bucket_modes, data):
        k, happy, n = k_and_happy_and_n
        NUM_SHARES = k_and_happy_and_n[2]
        if AVAILABLE_SHARES is None:
            AVAILABLE_SHARES = NUM_SHARES
        e = encode.Encoder()
        u = upload.Data(data, convergence="some convergence string")
        # force use of multiple segments by using a low max_segment_size
        u.max_segment_size = max_segment_size
        u.encoding_param_k = k
        u.encoding_param_happy = happy
        u.encoding_param_n = n
        eu = upload.EncryptAnUploadable(u)
        d = e.set_encrypted_uploadable(eu)

        shareholders = {}
        def _ready(res):
            k,happy,n = e.get_param("share_counts")
            assert n == NUM_SHARES # else we'll be completely confused
            all_peers = []
            for shnum in range(NUM_SHARES):
                mode = bucket_modes.get(shnum, "good")
                peer = FakeBucketReaderWriterProxy(mode)
                shareholders[shnum] = peer
            e.set_shareholders(shareholders)
            return e.start()
        d.addCallback(_ready)
        def _sent(res):
            d1 = u.get_encryption_key()
            d1.addCallback(lambda key: (res, key, shareholders))
            return d1
        d.addCallback(_sent)
        return d

    def recover(self, (res, key, shareholders), AVAILABLE_SHARES,
                recover_mode, target=None):
        (uri_extension_hash, required_shares, num_shares, file_size) = res

        if "corrupt_key" in recover_mode:
            # we corrupt the key, so that the decrypted data is corrupted and
            # will fail the plaintext hash check. Since we're manually
            # attaching shareholders, the fact that the storage index is also
            # corrupted doesn't matter.
            key = flip_bit(key)

        u = uri.CHKFileURI(key=key,
                           uri_extension_hash=uri_extension_hash,
                           needed_shares=required_shares,
                           total_shares=num_shares,
                           size=file_size)

        client = FakeClient()
        if not target:
            target = download.Data()
        fd = download.FileDownloader(client, u, target)

        # we manually cycle the FileDownloader through a number of steps that
        # would normally be sequenced by a Deferred chain in
        # FileDownloader.start(), to give us more control over the process.
        # In particular, by bypassing _get_all_shareholders, we skip
        # permuted-peerlist selection.
        for shnum, bucket in shareholders.items():
            if shnum < AVAILABLE_SHARES and bucket.closed:
                fd.add_share_bucket(shnum, bucket)
        fd._got_all_shareholders(None)

        # Make it possible to obtain uri_extension from the shareholders.
        # Arrange for shareholders[0] to be the first, so we can selectively
        # corrupt the data it returns.
        uri_extension_sources = shareholders.values()
        uri_extension_sources.remove(shareholders[0])
        uri_extension_sources.insert(0, shareholders[0])

        d = defer.succeed(None)

        # have the FileDownloader retrieve a copy of uri_extension itself
        d.addCallback(fd._obtain_uri_extension)

        if "corrupt_crypttext_hashes" in recover_mode:
            # replace everybody's crypttext hash trees with a different one
            # (computed over a different file), then modify our uri_extension
            # to reflect the new crypttext hash tree root
            def _corrupt_crypttext_hashes(unused):
                assert isinstance(fd._vup, download.ValidatedExtendedURIProxy), fd._vup
                assert fd._vup.crypttext_root_hash, fd._vup
                badhash = hashutil.tagged_hash("bogus", "data")
                bad_crypttext_hashes = [badhash] * fd._vup.num_segments
                badtree = hashtree.HashTree(bad_crypttext_hashes)
                for bucket in shareholders.values():
                    bucket.crypttext_hashes = list(badtree)
                fd._crypttext_hash_tree = hashtree.IncompleteHashTree(fd._vup.num_segments)
                fd._crypttext_hash_tree.set_hashes({0: badtree[0]})
                return fd._vup
            d.addCallback(_corrupt_crypttext_hashes)

        # also have the FileDownloader ask for hash trees
        d.addCallback(fd._get_crypttext_hash_tree)

        d.addCallback(fd._download_all_segments)
        d.addCallback(fd._done)
        def _done(newdata):
            return (newdata, fd)
        d.addCallback(_done)
        return d

    def test_not_enough_shares(self):
        d = self.send_and_recover((4,8,10), AVAILABLE_SHARES=2)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(NotEnoughSharesError))
        d.addBoth(_done)
        return d

    def test_one_share_per_peer(self):
        return self.send_and_recover()

    def test_74(self):
        return self.send_and_recover(datalen=74)
    def test_75(self):
        return self.send_and_recover(datalen=75)
    def test_51(self):
        return self.send_and_recover(datalen=51)

    def test_99(self):
        return self.send_and_recover(datalen=99)
    def test_100(self):
        return self.send_and_recover(datalen=100)
    def test_76(self):
        return self.send_and_recover(datalen=76)

    def test_124(self):
        return self.send_and_recover(datalen=124)
    def test_125(self):
        return self.send_and_recover(datalen=125)
    def test_101(self):
        return self.send_and_recover(datalen=101)

    def test_pause(self):
        # use a DownloadTarget that does pauseProducing/resumeProducing a few
        # times, then finishes
        t = PausingTarget()
        d = self.send_and_recover(target=t)
        return d

    def test_pause_then_stop(self):
        # use a DownloadTarget that pauses, then stops.
        t = PausingAndStoppingTarget()
        d = self.shouldFail(download.DownloadStopped, "test_pause_then_stop",
                            "our Consumer called stopProducing()",
                            self.send_and_recover, target=t)
        return d

    def test_stop(self):
        # use a DownloadTarget that does an immediate stop (ticket #473)
        t = StoppingTarget()
        d = self.shouldFail(download.DownloadStopped, "test_stop",
                            "our Consumer called stopProducing()",
                            self.send_and_recover, target=t)
        return d

    # the following tests all use 4-out-of-10 encoding

    def test_bad_blocks(self):
        # the first 6 servers have bad blocks, which will be caught by the
        # blockhashes
        modemap = dict([(i, "bad block")
                        for i in range(6)]
                       + [(i, "good")
                          for i in range(6, 10)])
        return self.send_and_recover((4,8,10), bucket_modes=modemap)

    def test_bad_blocks_failure(self):
        # the first 7 servers have bad blocks, which will be caught by the
        # blockhashes, and the download will fail
        modemap = dict([(i, "bad block")
                        for i in range(7)]
                       + [(i, "good")
                          for i in range(7, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(NotEnoughSharesError))
        d.addBoth(_done)
        return d

    def test_bad_blockhashes(self):
        # the first 6 servers have bad block hashes, so the blockhash tree
        # will not validate
        modemap = dict([(i, "bad blockhash")
                        for i in range(6)]
                       + [(i, "good")
                          for i in range(6, 10)])
        return self.send_and_recover((4,8,10), bucket_modes=modemap)

    def test_bad_blockhashes_failure(self):
        # the first 7 servers have bad block hashes, so the blockhash tree
        # will not validate, and the download will fail
        modemap = dict([(i, "bad blockhash")
                        for i in range(7)]
                       + [(i, "good")
                          for i in range(7, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(NotEnoughSharesError), res)
        d.addBoth(_done)
        return d

    def test_bad_sharehashes(self):
        # the first 6 servers have bad block hashes, so the sharehash tree
        # will not validate
        modemap = dict([(i, "bad sharehash")
                        for i in range(6)]
                       + [(i, "good")
                          for i in range(6, 10)])
        return self.send_and_recover((4,8,10), bucket_modes=modemap)

    def assertFetchFailureIn(self, fd, where):
        expected = {"uri_extension": 0,
                    "crypttext_hash_tree": 0,
                    }
        if where is not None:
            expected[where] += 1
        self.failUnlessEqual(fd._fetch_failures, expected)

    def test_good(self):
        # just to make sure the test harness works when we aren't
        # intentionally causing failures
        modemap = dict([(i, "good") for i in range(0, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        d.addCallback(self.assertFetchFailureIn, None)
        return d

    def test_bad_uri_extension(self):
        # the first server has a bad uri_extension block, so we will fail
        # over to a different server.
        modemap = dict([(i, "bad uri_extension") for i in range(1)] +
                       [(i, "good") for i in range(1, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        d.addCallback(self.assertFetchFailureIn, "uri_extension")
        return d

    def test_bad_crypttext_hashroot(self):
        # the first server has a bad crypttext hashroot, so we will fail
        # over to a different server.
        modemap = dict([(i, "bad crypttext hashroot") for i in range(1)] +
                       [(i, "good") for i in range(1, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        d.addCallback(self.assertFetchFailureIn, "crypttext_hash_tree")
        return d

    def test_bad_crypttext_hashes(self):
        # the first server has a bad crypttext hash block, so we will fail
        # over to a different server.
        modemap = dict([(i, "bad crypttext hash") for i in range(1)] +
                       [(i, "good") for i in range(1, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        d.addCallback(self.assertFetchFailureIn, "crypttext_hash_tree")
        return d

    def test_bad_crypttext_hashes_failure(self):
        # to test that the crypttext merkle tree is really being applied, we
        # sneak into the download process and corrupt two things: we replace
        # everybody's crypttext hashtree with a bad version (computed over
        # bogus data), and we modify the supposedly-validated uri_extension
        # block to match the new crypttext hashtree root. The download
        # process should notice that the crypttext coming out of FEC doesn't
        # match the tree, and fail.

        modemap = dict([(i, "good") for i in range(0, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap,
                                  recover_mode=("corrupt_crypttext_hashes"))
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(hashtree.BadHashError), res)
        d.addBoth(_done)
        return d

    def OFF_test_bad_plaintext(self):
        # faking a decryption failure is easier: just corrupt the key
        modemap = dict([(i, "good") for i in range(0, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap,
                                  recover_mode=("corrupt_key"))
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(hashtree.BadHashError), res)
        d.addBoth(_done)
        return d

    def test_bad_sharehashes_failure(self):
        # the first 7 servers have bad block hashes, so the sharehash tree
        # will not validate, and the download will fail
        modemap = dict([(i, "bad sharehash")
                        for i in range(7)]
                       + [(i, "good")
                          for i in range(7, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(NotEnoughSharesError))
        d.addBoth(_done)
        return d

    def test_missing_sharehashes(self):
        # the first 6 servers are missing their sharehashes, so the
        # sharehash tree will not validate
        modemap = dict([(i, "missing sharehash")
                        for i in range(6)]
                       + [(i, "good")
                          for i in range(6, 10)])
        return self.send_and_recover((4,8,10), bucket_modes=modemap)

    def test_missing_sharehashes_failure(self):
        # the first 7 servers are missing their sharehashes, so the
        # sharehash tree will not validate, and the download will fail
        modemap = dict([(i, "missing sharehash")
                        for i in range(7)]
                       + [(i, "good")
                          for i in range(7, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(NotEnoughSharesError))
        d.addBoth(_done)
        return d

    def test_lost_one_shareholder(self):
        # we have enough shareholders when we start, but one segment in we
        # lose one of them. The upload should still succeed, as long as we
        # still have 'shares_of_happiness' peers left.
        modemap = dict([(i, "good") for i in range(9)] +
                       [(i, "lost") for i in range(9, 10)])
        return self.send_and_recover((4,8,10), bucket_modes=modemap)

    def test_lost_one_shareholder_early(self):
        # we have enough shareholders when we choose peers, but just before
        # we send the 'start' message, we lose one of them. The upload should
        # still succeed, as long as we still have 'shares_of_happiness' peers
        # left.
        modemap = dict([(i, "good") for i in range(9)] +
                       [(i, "lost-early") for i in range(9, 10)])
        return self.send_and_recover((4,8,10), bucket_modes=modemap)

    def test_lost_many_shareholders(self):
        # we have enough shareholders when we start, but one segment in we
        # lose all but one of them. The upload should fail.
        modemap = dict([(i, "good") for i in range(1)] +
                       [(i, "lost") for i in range(1, 10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(NotEnoughSharesError), res)
        d.addBoth(_done)
        return d

    def test_lost_all_shareholders(self):
        # we have enough shareholders when we start, but one segment in we
        # lose all of them. The upload should fail.
        modemap = dict([(i, "lost") for i in range(10)])
        d = self.send_and_recover((4,8,10), bucket_modes=modemap)
        def _done(res):
            self.failUnless(isinstance(res, Failure))
            self.failUnless(res.check(NotEnoughSharesError))
        d.addBoth(_done)
        return d
