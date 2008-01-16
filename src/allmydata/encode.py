# -*- test-case-name: allmydata.test.test_encode -*-

from zope.interface import implements
from twisted.internet import defer
from foolscap import eventual
from allmydata import uri
from allmydata.hashtree import HashTree
from allmydata.util import mathutil, hashutil, idlib, log
from allmydata.util.assertutil import _assert
from allmydata.codec import CRSEncoder
from allmydata.interfaces import IEncoder, IStorageBucketWriter, \
     IEncryptedUploadable

"""
The goal of the encoder is to turn the original file into a series of
'shares'. Each share is going to a 'shareholder' (nominally each shareholder
is a different host, but for small grids there may be overlap). The number
of shares is chosen to hit our reliability goals (more shares on more
machines means more reliability), and is limited by overhead (proportional to
numshares or log(numshares)) and the encoding technology in use (zfec permits
only 256 shares total). It is also constrained by the amount of data
we want to send to each host. For estimating purposes, think of 10 shares
out of which we need 3 to reconstruct the file.

The encoder starts by cutting the original file into segments. All segments
except the last are of equal size. The segment size is chosen to constrain
the memory footprint (which will probably vary between 1x and 4x segment
size) and to constrain the overhead (which will be proportional to
log(number of segments)).


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

class UploadAborted(Exception):
    pass

KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

class Encoder(object):
    implements(IEncoder)

    def __init__(self, parent=None):
        object.__init__(self)
        self.uri_extension_data = {}
        self._codec = None
        self._parent = parent
        if self._parent:
            self._log_number = self._parent.log("creating Encoder %s" % self)
        self._aborted = False

    def __repr__(self):
        if hasattr(self, "_storage_index"):
            return "<Encoder for %s>" % idlib.b2a(self._storage_index)[:6]
        return "<Encoder for unknown storage index>"

    def log(self, *args, **kwargs):
        if not self._parent:
            return
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        return self._parent.log(*args, **kwargs)

    def set_encrypted_uploadable(self, uploadable):
        eu = self._uploadable = IEncryptedUploadable(uploadable)
        d = eu.get_size()
        def _got_size(size):
            self.file_size = size
        d.addCallback(_got_size)
        d.addCallback(lambda res: eu.get_all_encoding_parameters())
        d.addCallback(self._got_all_encoding_parameters)
        d.addCallback(lambda res: eu.get_storage_index())
        def _done(storage_index):
            self._storage_index = storage_index
            return self
        d.addCallback(_done)
        return d

    def _got_all_encoding_parameters(self, params):
        assert not self._codec
        k, happy, n, segsize = params
        self.required_shares = k
        self.shares_of_happiness = happy
        self.num_shares = n
        self.segment_size = segsize
        self.log("got encoding parameters: %d/%d/%d %d" % (k,happy,n, segsize))
        self.log("now setting up codec")

        assert self.segment_size % self.required_shares == 0

        self.num_segments = mathutil.div_ceil(self.file_size,
                                              self.segment_size)

        self._codec = CRSEncoder()
        self._codec.set_params(self.segment_size,
                               self.required_shares, self.num_shares)

        data = self.uri_extension_data
        data['codec_name'] = self._codec.get_encoder_type()
        data['codec_params'] = self._codec.get_serialized_params()

        data['size'] = self.file_size
        data['segment_size'] = self.segment_size
        self.share_size = mathutil.div_ceil(self.file_size,
                                            self.required_shares)
        data['num_segments'] = self.num_segments
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

    def _get_share_size(self):
        share_size = mathutil.div_ceil(self.file_size, self.required_shares)
        overhead = self._compute_overhead()
        return share_size + overhead

    def _compute_overhead(self):
        return 0

    def get_param(self, name):
        assert self._codec

        if name == "storage_index":
            return self._storage_index
        elif name == "share_counts":
            return (self.required_shares, self.shares_of_happiness,
                    self.num_shares)
        elif name == "num_segments":
            return self.num_segments
        elif name == "segment_size":
            return self.segment_size
        elif name == "block_size":
            return self._codec.get_block_size()
        elif name == "share_size":
            return self._get_share_size()
        elif name == "serialized_params":
            return self._codec.get_serialized_params()
        else:
            raise KeyError("unknown parameter name '%s'" % name)

    def set_shareholders(self, landlords):
        assert isinstance(landlords, dict)
        for k in landlords:
            assert IStorageBucketWriter.providedBy(landlords[k])
        self.landlords = landlords.copy()

    def start(self):
        if self._parent:
            self._log_number = self._parent.log("%s starting" % (self,))
        #paddedsize = self._size + mathutil.pad_size(self._size, self.needed_shares)
        assert self._codec
        self._crypttext_hasher = hashutil.crypttext_hasher()
        self._crypttext_hashes = []
        self.segment_num = 0
        self.subshare_hashes = [[] for x in range(self.num_shares)]
        # subshare_hashes[i] is a list that will be accumulated and then send
        # to landlord[i]. This list contains a hash of each segment_share
        # that we sent to that landlord.
        self.share_root_hashes = [None] * self.num_shares

        d = eventual.fireEventually()

        for l in self.landlords.values():
            d.addCallback(lambda res, l=l: l.start())

        for i in range(self.num_segments-1):
            # note to self: this form doesn't work, because lambda only
            # captures the slot, not the value
            #d.addCallback(lambda res: self.do_segment(i))
            # use this form instead:
            d.addCallback(lambda res, i=i: self._encode_segment(i))
            d.addCallback(self._send_segment, i)
            d.addCallback(self._turn_barrier)
        last_segnum = self.num_segments - 1
        d.addCallback(lambda res: self._encode_tail_segment(last_segnum))
        d.addCallback(self._send_segment, last_segnum)
        d.addCallback(self._turn_barrier)

        d.addCallback(lambda res: self.finish_hashing())

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

    def abort(self):
        self.log("aborting upload")
        assert self._codec, "don't call abort before start"
        self._aborted = True
        # the next segment read (in _gather_data inside _encode_segment) will
        # raise UploadAborted(), which will bypass the rest of the upload
        # chain. If we've sent the final segment's shares, it's too late to
        # abort. TODO: allow abort any time up to close_all_shareholders.

    def _turn_barrier(self, res):
        # putting this method in a Deferred chain imposes a guaranteed
        # reactor turn between the pre- and post- portions of that chain.
        # This can be useful to limit memory consumption: since Deferreds do
        # not do tail recursion, code which uses defer.succeed(result) for
        # consistency will cause objects to live for longer than you might
        # normally expect.

        return eventual.fireEventually(res)

    def _encode_segment(self, segnum):
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

        crypttext_segment_hasher = hashutil.crypttext_segment_hasher()

        # memory footprint: we only hold a tiny piece of the plaintext at any
        # given time. We build up a segment's worth of cryptttext, then hand
        # it to the encoder. Assuming 3-of-10 encoding (3.3x expansion) and
        # 1MiB max_segment_size, we get a peak memory footprint of 4.3*1MiB =
        # 4.3MiB. Lowering max_segment_size to, say, 100KiB would drop the
        # footprint to 430KiB at the expense of more hash-tree overhead.

        d = self._gather_data(self.required_shares, input_piece_size,
                              crypttext_segment_hasher)
        def _done(chunks):
            for c in chunks:
                assert len(c) == input_piece_size
            self._crypttext_hashes.append(crypttext_segment_hasher.digest())
            # during this call, we hit 5*segsize memory
            return codec.encode(chunks)
        d.addCallback(_done)
        return d

    def _encode_tail_segment(self, segnum):

        codec = self._tail_codec
        input_piece_size = codec.get_block_size()

        crypttext_segment_hasher = hashutil.crypttext_segment_hasher()

        d = self._gather_data(self.required_shares, input_piece_size,
                              crypttext_segment_hasher,
                              allow_short=True)
        def _done(chunks):
            for c in chunks:
                # a short trailing chunk will have been padded by
                # _gather_data
                assert len(c) == input_piece_size
            self._crypttext_hashes.append(crypttext_segment_hasher.digest())
            return codec.encode(chunks)
        d.addCallback(_done)
        return d

    def _gather_data(self, num_chunks, input_chunk_size,
                     crypttext_segment_hasher,
                     allow_short=False,
                     previous_chunks=[]):
        """Return a Deferred that will fire when the required number of
        chunks have been read (and hashed and encrypted). The Deferred fires
        with the combination of any 'previous_chunks' and the new chunks
        which were gathered."""

        if self._aborted:
            raise UploadAborted()

        if not num_chunks:
            return defer.succeed(previous_chunks)

        d = self._uploadable.read_encrypted(input_chunk_size)
        def _got(data):
            if self._aborted:
                raise UploadAborted()
            encrypted_pieces = []
            length = 0
            while data:
                encrypted_piece = data.pop(0)
                length += len(encrypted_piece)
                crypttext_segment_hasher.update(encrypted_piece)
                self._crypttext_hasher.update(encrypted_piece)
                encrypted_pieces.append(encrypted_piece)

            if allow_short:
                if length < input_chunk_size:
                    # padding
                    pad_size = input_chunk_size - length
                    encrypted_pieces.append('\x00' * pad_size)
            else:
                # non-tail segments should be the full segment size
                assert length == input_chunk_size

            encrypted_piece = "".join(encrypted_pieces)
            return previous_chunks + [encrypted_piece]

        d.addCallback(_got)
        d.addCallback(lambda chunks:
                      self._gather_data(num_chunks-1, input_chunk_size,
                                        crypttext_segment_hasher,
                                        allow_short, chunks))
        return d

    def _send_segment(self, (shares, shareids), segnum):
        # To generate the URI, we must generate the roothash, so we must
        # generate all shares, even if we aren't actually giving them to
        # anybody. This means that the set of shares we create will be equal
        # to or larger than the set of landlords. If we have any landlord who
        # *doesn't* have a share, that's an error.
        _assert(set(self.landlords.keys()).issubset(set(shareids)),
                shareids=shareids, landlords=self.landlords)
        dl = []
        lognum = self.log("send_segment(%d)" % segnum)
        for i in range(len(shares)):
            subshare = shares[i]
            shareid = shareids[i]
            d = self.send_subshare(shareid, segnum, subshare, lognum)
            dl.append(d)
            subshare_hash = hashutil.block_hash(subshare)
            #from allmydata.util import idlib
            #log.msg("creating block (shareid=%d, blocknum=%d) "
            #        "len=%d %r .. %r: %s" %
            #        (shareid, segnum, len(subshare),
            #         subshare[:50], subshare[-50:], idlib.b2a(subshare_hash)))
            self.subshare_hashes[shareid].append(subshare_hash)

        dl = self._gather_responses(dl)
        def _logit(res):
            self.log("%s uploaded %s / %s bytes (%d%%) of your file." %
                     (self,
                      self.segment_size*(segnum+1),
                      self.segment_size*self.num_segments,
                      100 * (segnum+1) / self.num_segments,
                      ))
            return res
        dl.addCallback(_logit)
        return dl

    def send_subshare(self, shareid, segment_num, subshare, lognum):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        lognum2 = self.log("put_block to %s" % self.landlords[shareid],
                           parent=lognum, level=log.NOISY)
        d = sh.put_block(segment_num, subshare)
        def _done(res):
            self.log("put_block done", parent=lognum2, level=log.NOISY)
            return res
        d.addCallback(_done)
        d.addErrback(self._remove_shareholder, shareid,
                     "segnum=%d" % segment_num)
        return d

    def _remove_shareholder(self, why, shareid, where):
        ln = self.log("UNUSUAL: error while sending %s to shareholder=%d: %s" %
                      (where, shareid, why))
        if shareid in self.landlords:
            del self.landlords[shareid]
        else:
            # even more UNUSUAL
            self.log("WEIRD: they weren't in our list of landlords", parent=ln)
        if len(self.landlords) < self.shares_of_happiness:
            msg = "lost too many shareholders during upload: %s" % why
            raise NotEnoughPeersError(msg)
        self.log("but we can still continue with %s shares, we'll be happy "
                 "with at least %s" % (len(self.landlords),
                                       self.shares_of_happiness),
                 parent=ln)

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

    def finish_hashing(self):
        crypttext_hash = self._crypttext_hasher.digest()
        self.uri_extension_data["crypttext_hash"] = crypttext_hash
        u = self._uploadable
        d = u.get_plaintext_hash()
        def _got(plaintext_hash):
            self.uri_extension_data["plaintext_hash"] = plaintext_hash
            return u.get_plaintext_hashtree_leaves(0, self.num_segments,
                                                   self.num_segments)
        d.addCallback(_got)
        def _got_hashtree_leaves(leaves):
            ht = list(HashTree(list(leaves)))
            self.uri_extension_data["plaintext_root_hash"] = ht[0]
            self._plaintext_hashtree_nodes = ht
        d.addCallback(_got_hashtree_leaves)
        return d

    def send_plaintext_hash_tree_to_all_shareholders(self):
        self.log("sending plaintext hash tree")
        dl = []
        for shareid in self.landlords.keys():
            d = self.send_plaintext_hash_tree(shareid,
                                              self._plaintext_hashtree_nodes)
            dl.append(d)
        return self._gather_responses(dl)

    def send_plaintext_hash_tree(self, shareid, all_hashes):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.put_plaintext_hashes(all_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_plaintext_hashes")
        return d

    def send_crypttext_hash_tree_to_all_shareholders(self):
        self.log("sending crypttext hash tree")
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
        d = sh.put_crypttext_hashes(all_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_crypttext_hashes")
        return d

    def send_all_subshare_hash_trees(self):
        self.log("sending subshare hash trees")
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
        d = sh.put_block_hashes(all_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_block_hashes")
        return d

    def send_all_share_hash_trees(self):
        # each bucket gets a set of share hash tree nodes that are needed to
        # validate their share. This includes the share hash itself, but does
        # not include the top-level hash root (which is stored securely in
        # the URI instead).
        self.log("sending all share hash trees")
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
        d = sh.put_share_hashes(needed_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_share_hashes")
        return d

    def send_uri_extension_to_all_shareholders(self):
        self.log("sending uri_extension")
        for k in ('crypttext_root_hash', 'crypttext_hash',
                  'plaintext_root_hash', 'plaintext_hash',
                  ):
            assert k in self.uri_extension_data
        uri_extension = uri.pack_extension(self.uri_extension_data)
        self.uri_extension_hash = hashutil.uri_extension_hash(uri_extension)
        dl = []
        for shareid in self.landlords.keys():
            dl.append(self.send_uri_extension(shareid, uri_extension))
        return self._gather_responses(dl)

    def send_uri_extension(self, shareid, uri_extension):
        sh = self.landlords[shareid]
        d = sh.put_uri_extension(uri_extension)
        d.addErrback(self._remove_shareholder, shareid, "put_uri_extension")
        return d

    def close_all_shareholders(self):
        self.log("closing shareholders")
        dl = []
        for shareid in self.landlords:
            d = self.landlords[shareid].close()
            d.addErrback(self._remove_shareholder, shareid, "close")
            dl.append(d)
        return self._gather_responses(dl)

    def done(self):
        self.log("upload done")
        return (self.uri_extension_hash, self.required_shares,
                self.num_shares, self.file_size)

    def err(self, f):
        self.log("UNUSUAL: %s: upload failed: %s" % (self, f))
        # we need to abort any remaining shareholders, so they'll delete the
        # partial share, allowing someone else to upload it again.
        self.log("aborting shareholders")
        dl = []
        for shareid in list(self.landlords.keys()):
            d = self.landlords[shareid].abort()
            d.addErrback(self._remove_shareholder, shareid, "abort")
            dl.append(d)
        d = self._gather_responses(dl)
        def _done(res):
            self.log("shareholders aborted")
            if f.check(defer.FirstError):
                return f.value.subFailure
            return f
        d.addCallback(_done)
        return d
