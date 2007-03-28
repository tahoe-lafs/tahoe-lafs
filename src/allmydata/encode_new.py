# -*- test-case-name: allmydata.test.test_encode -*-

from zope.interface import implements
from twisted.internet import defer
from allmydata.chunk import HashTree, roundup_pow2
from allmydata.Crypto.Cipher import AES
import sha
from allmydata.util import mathutil
from allmydata.codec import CRSEncoder
from allmydata.interfaces import IEncoder

def hash(data):
    return sha.new(data).digest()

"""

The goal of the encoder is to turn the original file into a series of
'shares'. Each share is going to a 'shareholder' (nominally each shareholder
is a different host, but for small meshes there may be overlap). The number
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
subshares. The 'share' (say, share #1) that makes it out to a host is a
collection of these subshares (subshare A1, B1, C1), plus some hash-tree
information necessary to validate the data upon retrieval. Only one segment
is handled at a time: all subshares for segment A are delivered before any
work is begun on segment B.

As subshares are created, we retain the hash of each one. The list of
subshare hashes for a single share (say, hash(A1), hash(B1), hash(C1)) is
used to form the base of a Merkle hash tree for that share (hashtrees[1]).
This hash tree has one terminal leaf per subshare. The complete subshare hash
tree is sent to the shareholder after all the data has been sent. At
retrieval time, the decoder will ask for specific pieces of this tree before
asking for subshares, whichever it needs to validate those subshares.

(Note: we don't really need to generate this whole subshare hash tree
ourselves. It would be sufficient to have the shareholder generate it and
just tell us the root. This gives us an extra level of validation on the
transfer, though, and it is relatively cheap to compute.)

Each of these subshare hash trees has a root hash. The collection of these
root hashes for all shares are collected into the 'share hash tree', which
has one terminal leaf per share. After sending the subshares and the complete
subshare hash tree to each shareholder, we send them the portion of the share
hash tree that is necessary to validate their share. The root of the share
hash tree is put into the URI.

"""

def pad(s, l, c='\x00'):
    """
    Return string s with enough chars c appended to it to make its length be
    an even multiple of l bytes.

    @param s the original string
    @param l the length of the resulting padded string in bytes
    @param c the pad char
    """
    return s + c * mathutil.pad_size(len(s), l)

KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

class Encoder(object):
    implements(IEncoder)

    def setup(self, infile):
        self.infile = infile
        infile.seek(0, 2)
        self.file_size = infile.tell()
        infile.seek(0, 0)

        self.num_shares = 100
        self.required_shares = 25

        self.segment_size = min(2*MiB, self.file_size)
        self.num_segments = mathutil.div_ceil(self.file_size, self.segment_size)

    def setup_encoder(self):
        self.encoder = CRSEncoder()
        self.encoder.set_params(self.segment_size, self.required_shares,
                                self.num_shares)

    def get_reservation_size(self):
        self.num_shares = 100
        self.share_size = mathutil.div_ceil(self.file_size, self.required_shares)
        overhead = self.compute_overhead()
        return self.share_size + overhead

    def setup_encryption(self):
        self.key = "\x00"*16
        self.cryptor = AES.new(key=self.key, mode=AES.MODE_CTR,
                               counterstart="\x00"*16)
        self.segment_num = 0
        self.subshare_hashes = [[] for x in range(self.num_shares)]
        # subshare_hashes[i] is a list that will be accumulated and then send
        # to landlord[i]. This list contains a hash of each segment_share
        # that we sent to that landlord.
        self.share_root_hashes = [None] * self.num_shares

    def start(self):
        self.setup_encryption()
        self.setup_encoder()
        d = defer.succeed(None)
        for i in range(self.num_segments):
            d.addCallback(lambda res: self.do_segment(i))
        d.addCallback(lambda res: self.send_all_subshare_hash_trees())
        d.addCallback(lambda res: self.send_all_share_hash_trees())
        d.addCallback(lambda res: self.close_all_shareholders())
        d.addCallback(lambda res: self.done())
        return d

    def do_segment(self, segnum):
        chunks = []
        # the ICodecEncoder API wants to receive a total of self.segment_size
        # bytes on each encode() call, broken up into a number of
        # identically-sized pieces. Due to the way the codec algorithm works,
        # these pieces need to be the same size as the share which the codec
        # will generate. Therefore we must feed it with input_piece_size that
        # equals the output share size.
        input_piece_size = self.encoder.get_share_size()

        # as a result, the number of input pieces per encode() call will be
        # equal to the number of required shares with which the codec was
        # constructed. You can think of the codec as chopping up a
        # 'segment_size' of data into 'required_shares' shares (not doing any
        # fancy math at all, just doing a split), then creating some number
        # of additional shares which can be substituted if the primary ones
        # are unavailable

        for i in range(self.required_shares):
            input_piece = self.infile.read(input_piece_size)
            if len(input_piece) < input_piece_size:
                # padding
                input_piece += ('\x00' * (input_piece_size - len(input_piece)))
            encrypted_piece = self.cryptor.encrypt(input_piece)
            chunks.append(encrypted_piece)
        d = self.encoder.encode(chunks)
        d.addCallback(self._encoded_segment)
        return d

    def _encoded_segment(self, (shares, shareids)):
        dl = []
        for i in range(len(shares)):
            subshare = shares[i]
            shareid = shareids[i]
            d = self.send_subshare(shareid, self.segment_num, subshare)
            dl.append(d)
            self.subshare_hashes[shareid].append(hash(subshare))
        self.segment_num += 1
        return defer.DeferredList(dl)

    def send_subshare(self, shareid, segment_num, subshare):
        #if False:
        #    offset = hash_size + segment_num * segment_size
        #    return self.send(shareid, "write", subshare, offset)
        return self.send(shareid, "put_subshare", segment_num, subshare)

    def set_landlords(self, landlords):
        self.landlords = landlords.copy()

    def send(self, shareid, methname, *args, **kwargs):
        ll = self.landlords[shareid]
        return ll.callRemote(methname, *args, **kwargs)

    def send_all_subshare_hash_trees(self):
        dl = []
        for shareid,hashes in enumerate(self.subshare_hashes):
            # hashes is a list of the hashes of all subshares that were sent
            # to shareholder[shareid].
            dl.append(self.send_one_subshare_hash_tree(shareid, hashes))
        return defer.DeferredList(dl)

    def send_one_subshare_hash_tree(self, shareid, subshare_hashes):
        t = HashTree(subshare_hashes)
        all_hashes = list(t)
        # all_hashes[0] is the root hash, == hash(ah[1]+ah[2])
        # all_hashes[1] is the left child, == hash(ah[3]+ah[4])
        # all_hashes[n] == hash(all_hashes[2*n+1] + all_hashes[2*n+2])
        self.share_root_hashes[shareid] = t[0]
        if False:
            block = "".join(all_hashes)
            return self.send(shareid, "write", block, offset=0)
        return self.send(shareid, "put_subshare_hashes", all_hashes)

    def send_all_share_hash_trees(self):
        dl = []
        for h in self.share_root_hashes:
            assert h
        # create the share hash tree
        t = HashTree(self.share_root_hashes)
        # the root of this hash tree goes into our URI
        self.root_hash = t[0]
        # now send just the necessary pieces out to each shareholder
        for i in range(self.num_shares):
            # the HashTree is given a list of leaves: 0,1,2,3..n .
            # These become nodes A+0,A+1,A+2.. of the tree, where A=n-1
            tree_width = roundup_pow2(self.num_shares)
            base_index = i + tree_width - 1
            needed_hash_indices = t.needed_for(base_index)
            hashes = [(hi, t[hi]) for hi in needed_hash_indices]
            dl.append(self.send_one_share_hash_tree(i, hashes))
        return defer.DeferredList(dl)

    def send_one_share_hash_tree(self, shareid, needed_hashes):
        return self.send(shareid, "put_share_hashes", needed_hashes)

    def close_all_shareholders(self):
        dl = []
        for shareid in range(self.num_shares):
            dl.append(self.send(shareid, "close"))
        return defer.DeferredList(dl)

    def done(self):
        return self.root_hash


from foolscap import RemoteInterface
from foolscap.schema import ListOf, TupleOf


class RIStorageBucketWriter(RemoteInterface):
    def put_subshare(segment_number=int, subshare=str):
        return None
    def put_segment_hashes(all_hashes=ListOf(str)):
        return None
    def put_share_hashes(needed_hashes=ListOf(TupleOf(int,str))):
        return None
    #def write(data=str, offset=int):
    #    return None
class RIStorageBucketReader(RemoteInterface):
    def get_share_hashes():
        return ListOf(TupleOf(int,str))
    def get_segment_hashes(which=ListOf(int)):
        return ListOf(str)
    def get_subshare(segment_number=int):
        return str
    #def read(size=int, offset=int):
    #    return str
