# -*- test-case-name: allmydata.test.test_encode -*-

from zope.interface import implements
from twisted.internet import defer
from twisted.python import log
from allmydata import uri
from allmydata.hashtree import HashTree
from allmydata.Crypto.Cipher import AES
from allmydata.util import mathutil, hashutil
from allmydata.util.assertutil import _assert
from allmydata.codec import CRSEncoder
from allmydata.interfaces import IEncoder

"""

The goal of the encoder is to turn the original file into a series of
'shares'. Each share is going to a 'shareholder' (nominally each shareholder
is a different host, but for small grids there may be overlap). The number
of shares is chosen to hit our reliability goals (more shares on more
machines means more reliability), and is limited by overhead (proportional to
numshares or log(numshares)) and the encoding technology in use (Reed-Solomon
only permits 256 shares total). It is also constrained by the amount of data
we want to send to each host. For estimating purposes, think of 100 shares
out of which we need 25 to reconstruct the file.

The encoder starts by cutting the original file into segments. All segments
except the last are of equal size. The segment size is chosen to constrain
the memory footprint (which will probably vary between 1x and 4x segment
size) and to constrain the overhead (which will be proportional to either the
number of segments or log(number of segments)).


Each segment (A,B,C) is read into memory, encrypted, and encoded into
blocks. The 'share' (say, share #1) that makes it out to a host is a
collection of these blocks (block A1, B1, C1), plus some hash-tree
information necessary to validate the data upon retrieval. Only one segment
is handled at a time: all blocks for segment A are delivered before any
work is begun on segment B.

As blocks are created, we retain the hash of each one. The list of
block hashes for a single share (say, hash(A1), hash(B1), hash(C1)) is
used to form the base of a Merkle hash tree for that share (hashtrees[1]).
This hash tree has one terminal leaf per block. The complete block hash
tree is sent to the shareholder after all the data has been sent. At
retrieval time, the decoder will ask for specific pieces of this tree before
asking for blocks, whichever it needs to validate those blocks.

(Note: we don't really need to generate this whole block hash tree
ourselves. It would be sufficient to have the shareholder generate it and
just tell us the root. This gives us an extra level of validation on the
transfer, though, and it is relatively cheap to compute.)

Each of these block hash trees has a root hash. The collection of these
root hashes for all shares are collected into the 'share hash tree', which
has one terminal leaf per share. After sending the blocks and the complete
block hash tree to each shareholder, we send them the portion of the share
hash tree that is necessary to validate their share. The root of the share
hash tree is put into the URI.

"""

class NotEnoughPeersError(Exception):
    pass

KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

class Encoder(object):
    implements(IEncoder)
    NEEDED_SHARES = 25
    SHARES_OF_HAPPINESS = 75
    TOTAL_SHARES = 100
    MAX_SEGMENT_SIZE = 2*MiB

    def __init__(self, options={}):
        object.__init__(self)
        self.MAX_SEGMENT_SIZE = options.get("max_segment_size",
                                            self.MAX_SEGMENT_SIZE)
        k,happy,n = options.get("needed_and_happy_and_total_shares",
                                (self.NEEDED_SHARES,
                                 self.SHARES_OF_HAPPINESS,
                                 self.TOTAL_SHARES))
        self.NEEDED_SHARES = k
        self.SHARES_OF_HAPPINESS = happy
        self.TOTAL_SHARES = n
        self.uri_extension_data = {}

    def setup(self, infile, encryption_key):
        self.infile = infile
        assert isinstance(encryption_key, str)
        assert len(encryption_key) == 16 # AES-128
        self.key = encryption_key
        infile.seek(0, 2)
        self.file_size = infile.tell()
        infile.seek(0, 0)

        self.num_shares = self.TOTAL_SHARES
        self.required_shares = self.NEEDED_SHARES
        self.shares_of_happiness = self.SHARES_OF_HAPPINESS

        self.segment_size = min(self.MAX_SEGMENT_SIZE, self.file_size)
        # this must be a multiple of self.required_shares
        self.segment_size = mathutil.next_multiple(self.segment_size,
                                                   self.required_shares)
        self.setup_codec()

    def setup_codec(self):
        assert self.segment_size % self.required_shares == 0
        self._codec = CRSEncoder()
        self._codec.set_params(self.segment_size,
                               self.required_shares, self.num_shares)

        data = self.uri_extension_data
        data['codec_name'] = self._codec.get_encoder_type()
        data['codec_params'] = self._codec.get_serialized_params()

        data['size'] = self.file_size
        data['segment_size'] = self.segment_size
        data['num_segments'] = mathutil.div_ceil(self.file_size,
                                                 self.segment_size)
        data['needed_shares'] = self.required_shares
        data['total_shares'] = self.num_shares

        # the "tail" is the last segment. This segment may or may not be
        # shorter than all other segments. We use the "tail codec" to handle
        # it. If the tail is short, we use a different codec instance. In
        # addition, the tail codec must be fed data which has been padded out
        # to the right size.
        self.tail_size = self.file_size % self.segment_size
        if not self.tail_size:
            self.tail_size = self.segment_size

        # the tail codec is responsible for encoding tail_size bytes
        padded_tail_size = mathutil.next_multiple(self.tail_size,
                                                  self.required_shares)
        self._tail_codec = CRSEncoder()
        self._tail_codec.set_params(padded_tail_size,
                                    self.required_shares, self.num_shares)
        data['tail_codec_params'] = self._tail_codec.get_serialized_params()

    def set_uri_extension_data(self, uri_extension_data):
        self.uri_extension_data.update(uri_extension_data)

    def get_share_size(self):
        share_size = mathutil.div_ceil(self.file_size, self.required_shares)
        overhead = self.compute_overhead()
        return share_size + overhead
    def compute_overhead(self):
        return 0
    def get_block_size(self):
        return self._codec.get_block_size()

    def set_shareholders(self, landlords):
        assert isinstance(landlords, dict)
        for k in landlords:
            # it would be nice to:
            #assert RIBucketWriter.providedBy(landlords[k])
            pass
        self.landlords = landlords.copy()

    def start(self):
        #paddedsize = self._size + mathutil.pad_size(self._size, self.needed_shares)
        self.num_segments = mathutil.div_ceil(self.file_size,
                                              self.segment_size)
        self.share_size = mathutil.div_ceil(self.file_size,
                                            self.required_shares)
        self._plaintext_hashes = []
        self._crypttext_hashes = []
        self.setup_encryption()
        self.setup_codec() # TODO: duplicate call?
        d = defer.succeed(None)

        for i in range(self.num_segments-1):
            # note to self: this form doesn't work, because lambda only
            # captures the slot, not the value
            #d.addCallback(lambda res: self.do_segment(i))
            # use this form instead:
            d.addCallback(lambda res, i=i: self.do_segment(i))
        d.addCallback(lambda res: self.do_tail_segment(self.num_segments-1))

        d.addCallback(lambda res:
                      self.send_plaintext_hash_tree_to_all_shareholders())
        d.addCallback(lambda res:
                      self.send_crypttext_hash_tree_to_all_shareholders())
        d.addCallback(lambda res: self.send_all_subshare_hash_trees())
        d.addCallback(lambda res: self.send_all_share_hash_trees())
        d.addCallback(lambda res: self.send_uri_extension_to_all_shareholders())
        d.addCallback(lambda res: self.close_all_shareholders())
        d.addCallbacks(lambda res: self.done(), self.err)
        return d

    def setup_encryption(self):
        self.cryptor = AES.new(key=self.key, mode=AES.MODE_CTR,
                               counterstart="\x00"*16)
        self.segment_num = 0
        self.subshare_hashes = [[] for x in range(self.num_shares)]
        # subshare_hashes[i] is a list that will be accumulated and then send
        # to landlord[i]. This list contains a hash of each segment_share
        # that we sent to that landlord.
        self.share_root_hashes = [None] * self.num_shares

    def do_segment(self, segnum):
        chunks = []
        codec = self._codec
        # the ICodecEncoder API wants to receive a total of self.segment_size
        # bytes on each encode() call, broken up into a number of
        # identically-sized pieces. Due to the way the codec algorithm works,
        # these pieces need to be the same size as the share which the codec
        # will generate. Therefore we must feed it with input_piece_size that
        # equals the output share size.
        input_piece_size = codec.get_block_size()

        # as a result, the number of input pieces per encode() call will be
        # equal to the number of required shares with which the codec was
        # constructed. You can think of the codec as chopping up a
        # 'segment_size' of data into 'required_shares' shares (not doing any
        # fancy math at all, just doing a split), then creating some number
        # of additional shares which can be substituted if the primary ones
        # are unavailable

        plaintext_hasher = hashutil.plaintext_segment_hasher()
        crypttext_hasher = hashutil.crypttext_segment_hasher()

        # memory footprint: we only hold a tiny piece of the plaintext at any
        # given time. We build up a segment's worth of cryptttext, then hand
        # it to the encoder. Assuming 25-of-100 encoding (4x expansion) and
        # 2MiB max_segment_size, we get a peak memory footprint of 5*2MiB =
        # 10MiB. Lowering max_segment_size to, say, 100KiB would drop the
        # footprint to 500KiB at the expense of more hash-tree overhead.

        for i in range(self.required_shares):
            input_piece = self.infile.read(input_piece_size)
            # non-tail segments should be the full segment size
            assert len(input_piece) == input_piece_size
            plaintext_hasher.update(input_piece)
            encrypted_piece = self.cryptor.encrypt(input_piece)
            assert len(encrypted_piece) == len(input_piece)
            crypttext_hasher.update(encrypted_piece)

            chunks.append(encrypted_piece)

        self._plaintext_hashes.append(plaintext_hasher.digest())
        self._crypttext_hashes.append(crypttext_hasher.digest())

        d = codec.encode(chunks) # during this call, we hit 5*segsize memory
        del chunks
        d.addCallback(self._encoded_segment, segnum)
        return d

    def do_tail_segment(self, segnum):
        chunks = []
        codec = self._tail_codec
        input_piece_size = codec.get_block_size()

        plaintext_hasher = hashutil.plaintext_segment_hasher()
        crypttext_hasher = hashutil.crypttext_segment_hasher()

        for i in range(self.required_shares):
            input_piece = self.infile.read(input_piece_size)
            plaintext_hasher.update(input_piece)
            encrypted_piece = self.cryptor.encrypt(input_piece)
            assert len(encrypted_piece) == len(input_piece)
            crypttext_hasher.update(encrypted_piece)

            if len(encrypted_piece) < input_piece_size:
                # padding
                pad_size = (input_piece_size - len(encrypted_piece))
                encrypted_piece += ('\x00' * pad_size)

            chunks.append(encrypted_piece)

        self._plaintext_hashes.append(plaintext_hasher.digest())
        self._crypttext_hashes.append(crypttext_hasher.digest())

        d = codec.encode(chunks)
        del chunks
        d.addCallback(self._encoded_segment, segnum)
        return d

    def _encoded_segment(self, (shares, shareids), segnum):
        # To generate the URI, we must generate the roothash, so we must
        # generate all shares, even if we aren't actually giving them to
        # anybody. This means that the set of share we create will be equal
        # to or larger than the set of landlords. If we have any landlord who
        # *doesn't* have a share, that's an error.
        _assert(set(self.landlords.keys()).issubset(set(shareids)),
                shareids=shareids, landlords=self.landlords)
        dl = []
        for i in range(len(shares)):
            subshare = shares[i]
            shareid = shareids[i]
            d = self.send_subshare(shareid, segnum, subshare)
            dl.append(d)
            subshare_hash = hashutil.block_hash(subshare)
            self.subshare_hashes[shareid].append(subshare_hash)
        dl = self._gather_responses(dl)
        def _logit(res):
            log.msg("%s uploaded %s / %s bytes of your file." % (self, self.segment_size*(segnum+1), self.segment_size*self.num_segments))
            return res
        dl.addCallback(_logit)
        return dl

    def send_subshare(self, shareid, segment_num, subshare):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.callRemote("put_block", segment_num, subshare)
        d.addErrback(self._remove_shareholder, shareid,
                     "segnum=%d" % segment_num)
        return d

    def _remove_shareholder(self, why, shareid, where):
        log.msg("error while sending %s to shareholder=%d: %s" %
                (where, shareid, why)) # UNUSUAL
        if shareid in self.landlords:
            del self.landlords[shareid]
        else:
            # even more UNUSUAL
            log.msg(" weird, they weren't in our list of landlords")
        if len(self.landlords) < self.shares_of_happiness:
            msg = "lost too many shareholders during upload: %s" % why
            raise NotEnoughPeersError(msg)
        log.msg("but we can still continue with %s shares, we'll be happy "
                "with at least %s" % (len(self.landlords),
                                      self.shares_of_happiness))

    def _gather_responses(self, dl):
        d = defer.DeferredList(dl, fireOnOneErrback=True)
        def _eatNotEnoughPeersError(f):
            # all exceptions that occur while talking to a peer are handled
            # in _remove_shareholder. That might raise NotEnoughPeersError,
            # which will cause the DeferredList to errback but which should
            # otherwise be consumed. Allow non-NotEnoughPeersError exceptions
            # to pass through as an unhandled errback. We use this in lieu of
            # consumeErrors=True to allow coding errors to be logged.
            f.trap(NotEnoughPeersError)
            return None
        for d0 in dl:
            d0.addErrback(_eatNotEnoughPeersError)
        return d

    def send_plaintext_hash_tree_to_all_shareholders(self):
        log.msg("%s sending plaintext hash tree" % self)
        t = HashTree(self._plaintext_hashes)
        all_hashes = list(t)
        self.uri_extension_data["plaintext_root_hash"] = t[0]
        dl = []
        for shareid in self.landlords.keys():
            dl.append(self.send_plaintext_hash_tree(shareid, all_hashes))
        return self._gather_responses(dl)

    def send_plaintext_hash_tree(self, shareid, all_hashes):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.callRemote("put_plaintext_hashes", all_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_plaintext_hashes")
        return d

    def send_crypttext_hash_tree_to_all_shareholders(self):
        log.msg("%s sending crypttext hash tree" % self)
        t = HashTree(self._crypttext_hashes)
        all_hashes = list(t)
        self.uri_extension_data["crypttext_root_hash"] = t[0]
        dl = []
        for shareid in self.landlords.keys():
            dl.append(self.send_crypttext_hash_tree(shareid, all_hashes))
        return self._gather_responses(dl)

    def send_crypttext_hash_tree(self, shareid, all_hashes):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.callRemote("put_crypttext_hashes", all_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_crypttext_hashes")
        return d

    def send_all_subshare_hash_trees(self):
        log.msg("%s sending subshare hash trees" % self)
        dl = []
        for shareid,hashes in enumerate(self.subshare_hashes):
            # hashes is a list of the hashes of all subshares that were sent
            # to shareholder[shareid].
            dl.append(self.send_one_subshare_hash_tree(shareid, hashes))
        return self._gather_responses(dl)

    def send_one_subshare_hash_tree(self, shareid, subshare_hashes):
        t = HashTree(subshare_hashes)
        all_hashes = list(t)
        # all_hashes[0] is the root hash, == hash(ah[1]+ah[2])
        # all_hashes[1] is the left child, == hash(ah[3]+ah[4])
        # all_hashes[n] == hash(all_hashes[2*n+1] + all_hashes[2*n+2])
        self.share_root_hashes[shareid] = t[0]
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.callRemote("put_block_hashes", all_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_block_hashes")
        return d

    def send_all_share_hash_trees(self):
        # each bucket gets a set of share hash tree nodes that are needed to
        # validate their share. This includes the share hash itself, but does
        # not include the top-level hash root (which is stored securely in
        # the URI instead).
        log.msg("%s sending all share hash trees" % self)
        dl = []
        for h in self.share_root_hashes:
            assert h
        # create the share hash tree
        t = HashTree(self.share_root_hashes)
        # the root of this hash tree goes into our URI
        self.uri_extension_data['share_root_hash'] = t[0]
        # now send just the necessary pieces out to each shareholder
        for i in range(self.num_shares):
            # the HashTree is given a list of leaves: 0,1,2,3..n .
            # These become nodes A+0,A+1,A+2.. of the tree, where A=n-1
            needed_hash_indices = t.needed_hashes(i, include_leaf=True)
            hashes = [(hi, t[hi]) for hi in needed_hash_indices]
            dl.append(self.send_one_share_hash_tree(i, hashes))
        return self._gather_responses(dl)

    def send_one_share_hash_tree(self, shareid, needed_hashes):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.callRemote("put_share_hashes", needed_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_share_hashes")
        return d

    def send_uri_extension_to_all_shareholders(self):
        log.msg("%s: sending uri_extension" % self)
        uri_extension = uri.pack_extension(self.uri_extension_data)
        self.uri_extension_hash = hashutil.uri_extension_hash(uri_extension)
        dl = []
        for shareid in self.landlords.keys():
            dl.append(self.send_uri_extension(shareid, uri_extension))
        return self._gather_responses(dl)

    def send_uri_extension(self, shareid, uri_extension):
        sh = self.landlords[shareid]
        d = sh.callRemote("put_uri_extension", uri_extension)
        d.addErrback(self._remove_shareholder, shareid, "put_uri_extension")
        return d

    def close_all_shareholders(self):
        log.msg("%s: closing shareholders" % self)
        dl = []
        for shareid in self.landlords:
            d = self.landlords[shareid].callRemote("close")
            d.addErrback(self._remove_shareholder, shareid, "close")
            dl.append(d)
        return self._gather_responses(dl)

    def done(self):
        log.msg("%s: upload done" % self)
        return self.uri_extension_hash

    def err(self, f):
        log.msg("%s: upload failed: %s" % (self, f)) # UNUSUAL
        if f.check(defer.FirstError):
            return f.value.subFailure
        return f
