
import os
from zope.interface import implements
from twisted.python import log
from twisted.internet import defer
from twisted.application import service
from foolscap import Referenceable

from allmydata.util import idlib, hashutil
from allmydata import encode, storage, hashtree
from allmydata.uri import pack_uri, pack_lit
from allmydata.interfaces import IUploadable, IUploader

from cStringIO import StringIO
import collections, random


class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

# this wants to live in storage, not here
class TooFullError(Exception):
    pass

# our current uri_extension is 846 bytes for small files, a few bytes
# more for larger ones (since the filesize is encoded in decimal in a
# few places). Ask for a little bit more just in case we need it. If
# the extension changes size, we can change EXTENSION_SIZE to
# allocate a more accurate amount of space.
EXTENSION_SIZE = 1000

class PeerTracker:
    def __init__(self, peerid, permutedid, connection,
                 sharesize, blocksize, num_segments, num_share_hashes,
                 crypttext_hash):
        self.peerid = peerid
        self.permutedid = permutedid
        self.connection = connection # to an RIClient
        self.buckets = {} # k: shareid, v: IRemoteBucketWriter
        self.sharesize = sharesize
        #print "PeerTracker", peerid, permutedid, sharesize
        as = storage.allocated_size(sharesize,
                                    num_segments,
                                    num_share_hashes,
                                    EXTENSION_SIZE)
        self.allocated_size = as
                                                           
        self.blocksize = blocksize
        self.num_segments = num_segments
        self.num_share_hashes = num_share_hashes
        self.crypttext_hash = crypttext_hash
        self._storageserver = None

    def query(self, sharenums):
        if not self._storageserver:
            d = self.connection.callRemote("get_service", "storageserver")
            d.addCallback(self._got_storageserver)
            d.addCallback(lambda res: self._query(sharenums))
            return d
        return self._query(sharenums)
    def _got_storageserver(self, storageserver):
        self._storageserver = storageserver
    def _query(self, sharenums):
        #print " query", self.peerid, len(sharenums)
        d = self._storageserver.callRemote("allocate_buckets",
                                           self.crypttext_hash,
                                           sharenums,
                                           self.allocated_size,
                                           canary=Referenceable())
        d.addCallback(self._got_reply)
        return d
        
    def _got_reply(self, (alreadygot, buckets)):
        #log.msg("%s._got_reply(%s)" % (self, (alreadygot, buckets)))
        b = {}
        for sharenum, rref in buckets.iteritems():
            bp = storage.WriteBucketProxy(rref, self.sharesize,
                                          self.blocksize,
                                          self.num_segments,
                                          self.num_share_hashes,
                                          EXTENSION_SIZE)
            b[sharenum] = bp
        self.buckets.update(b)
        return (alreadygot, set(b.keys()))

class Tahoe3PeerSelector:

    def get_shareholders(self, client,
                         storage_index, share_size, block_size,
                         num_segments, total_shares, shares_of_happiness):
        """
        @return: a set of PeerTracker instances that have agreed to hold some
            shares for us
        """

        self.total_shares = total_shares
        self.shares_of_happiness = shares_of_happiness

        # we are responsible for locating the shareholders. self._encoder is
        # responsible for handling the data and sending out the shares.
        peers = client.get_permuted_peers(storage_index)
        assert peers

        # this needed_hashes computation should mirror
        # Encoder.send_all_share_hash_trees. We use an IncompleteHashTree
        # (instead of a HashTree) because we don't require actual hashing
        # just to count the levels.
        ht = hashtree.IncompleteHashTree(total_shares)
        num_share_hashes = len(ht.needed_hashes(0, include_leaf=True))

        trackers = [ PeerTracker(peerid, permutedid, conn,
                                 share_size, block_size,
                                 num_segments, num_share_hashes,
                                 storage_index)
                     for permutedid, peerid, conn in peers ]
        self.usable_peers = set(trackers) # this set shrinks over time
        self.used_peers = set() # while this set grows
        self.unallocated_sharenums = set(range(total_shares)) # this one shrinks

        return self._locate_more_shareholders()

    def _locate_more_shareholders(self):
        d = self._query_peers()
        d.addCallback(self._located_some_shareholders)
        return d

    def _located_some_shareholders(self, res):
        log.msg("_located_some_shareholders")
        log.msg(" still need homes for %d shares, still have %d usable peers"
                % (len(self.unallocated_sharenums), len(self.usable_peers)))
        if not self.unallocated_sharenums:
            # Finished allocating places for all shares.
            log.msg("%s._locate_all_shareholders() "
                    "Finished allocating places for all shares." % self)
            log.msg("used_peers is %s" % (self.used_peers,))
            return self.used_peers
        if not self.usable_peers:
            # Ran out of peers who have space.
            log.msg("%s._locate_all_shareholders() "
                    "Ran out of peers who have space." % self)
            margin = self.total_shares - self.shares_of_happiness
            if len(self.unallocated_sharenums) < margin:
                # But we allocated places for enough shares.
                log.msg("%s._locate_all_shareholders() "
                        "But we allocated places for enough shares.")
                return self.used_peers
            raise encode.NotEnoughPeersError
        # we need to keep trying
        return self._locate_more_shareholders()

    def _create_ring_of_things(self):
        PEER = 1 # must sort later than SHARE, for consistency with download
        SHARE = 0
        # ring_of_things is a list of (position_in_ring, whatami, x) where
        # whatami is SHARE if x is a sharenum or else PEER if x is a
        # PeerTracker instance
        ring_of_things = []
        ring_of_things.extend([ (peer.permutedid, PEER, peer,)
                                for peer in self.usable_peers ])
        shares = [ (i * 2**160 / self.total_shares, SHARE, i)
                   for i in self.unallocated_sharenums]
        ring_of_things.extend(shares)
        ring_of_things.sort()
        ring_of_things = collections.deque(ring_of_things)
        return ring_of_things
        
    def _query_peers(self):
        """
        @return: a deferred that fires when all queries have resolved
        """
        PEER = 1
        SHARE = 0
        ring = self._create_ring_of_things()

        # Choose a random starting point, talk to that peer.
        ring.rotate(random.randrange(0, len(ring)))

        # Walk backwards to find a peer.  We know that we'll eventually find
        # one because we earlier asserted that there was at least one.
        while ring[0][1] != PEER:
            ring.rotate(-1)
        peer = ring[0][2]
        assert isinstance(peer, PeerTracker), peer
        ring.rotate(-1)

        # loop invariant: at the top of the loop, we are always one step to
        # the left of a peer, which is stored in the peer variable.
        outstanding_queries = []
        sharenums_to_query = set()
        for i in range(len(ring)):
            if ring[0][1] == SHARE:
                sharenums_to_query.add(ring[0][2])
            else:
                if True or sharenums_to_query:
                    d = peer.query(sharenums_to_query)
                    d.addCallbacks(self._got_response, self._got_error, callbackArgs=(peer, sharenums_to_query), errbackArgs=(peer,))
                    outstanding_queries.append(d)
                    d.addErrback(log.err)
                peer = ring[0][2]
                sharenums_to_query = set()
            ring.rotate(-1)
        
        return defer.DeferredList(outstanding_queries)

    def _got_response(self, (alreadygot, allocated), peer, shares_we_requested):
        """
        @type alreadygot: a set of sharenums
        @type allocated: a set of sharenums
        """
        # TODO: some future version of Foolscap might not convert inbound
        # sets into sets.Set on us, even when we're using 2.4
        alreadygot = set(alreadygot)
        allocated = set(allocated)
        #log.msg("%s._got_response(%s, %s, %s): "
        #        "self.unallocated_sharenums: %s, unhandled: %s"
        #        % (self, (alreadygot, allocated), peer, shares_we_requested,
        #           self.unallocated_sharenums,
        #           shares_we_requested - alreadygot - allocated))
        self.unallocated_sharenums -= alreadygot
        self.unallocated_sharenums -= allocated

        if allocated:
            self.used_peers.add(peer)

        if shares_we_requested - alreadygot - allocated:
            # Then he didn't accept some of the shares, so he's full.

            #log.msg("%s._got_response(%s, %s, %s): "
            #        "self.unallocated_sharenums: %s, unhandled: %s HE'S FULL"
            #        % (self,
            #           (alreadygot, allocated), peer, shares_we_requested,
            #           self.unallocated_sharenums,
            #           shares_we_requested - alreadygot - allocated))
            self.usable_peers.remove(peer)

    def _got_error(self, f, peer):
        log.msg("%s._got_error(%s, %s)" % (self, f, peer,))
        self.usable_peers.remove(peer)


class CHKUploader:
    peer_selector_class = Tahoe3PeerSelector

    def __init__(self, client, uploadable, options={}):
        self._client = client
        self._uploadable = IUploadable(uploadable)
        self._options = options

    def set_params(self, encoding_parameters):
        self._encoding_parameters = encoding_parameters

        needed_shares, shares_of_happiness, total_shares = encoding_parameters
        self.needed_shares = needed_shares
        self.shares_of_happiness = shares_of_happiness
        self.total_shares = total_shares

    def start(self):
        """Start uploading the file.

        This method returns a Deferred that will fire with the URI (a
        string)."""

        log.msg("starting upload of %s" % self._uploadable)

        d = self._uploadable.get_size()
        d.addCallback(self.setup_encoder)
        d.addCallback(self._uploadable.get_encryption_key)
        d.addCallback(self.setup_keys)
        d.addCallback(self.locate_all_shareholders)
        d.addCallback(self.set_shareholders)
        d.addCallback(lambda res: self._encoder.start())
        d.addCallback(self._compute_uri)
        return d

    def setup_encoder(self, size):
        self._size = size
        self._encoder = encode.Encoder(self._options)
        self._encoder.set_size(size)
        self._encoder.set_params(self._encoding_parameters)
        self._encoder.set_uploadable(self._uploadable)
        self._encoder.setup()
        return self._encoder.get_serialized_params()

    def setup_keys(self, key):
        assert isinstance(key, str)
        assert len(key) == 16  # AES-128
        self._encryption_key = key
        self._encoder.set_encryption_key(key)
        storage_index = hashutil.storage_index_chk_hash(key)
        assert isinstance(storage_index, str)
        # TODO: is there any point to having the SI be longer than the key?
        # There's certainly no extra entropy to be had..
        assert len(storage_index) == 32  # SHA-256
        self._storage_index = storage_index
        log.msg(" upload SI is [%s]" % (idlib.b2a(storage_index,)))


    def locate_all_shareholders(self, ignored=None):
        peer_selector = self.peer_selector_class()
        share_size = self._encoder.get_share_size()
        block_size = self._encoder.get_block_size()
        num_segments = self._encoder.get_num_segments()
        gs = peer_selector.get_shareholders
        d = gs(self._client,
               self._storage_index, share_size, block_size,
               num_segments, self.total_shares, self.shares_of_happiness)
        return d

    def set_shareholders(self, used_peers):
        """
        @param used_peers: a sequence of PeerTracker objects
        """
        log.msg("_send_shares, used_peers is %s" % (used_peers,))
        for peer in used_peers:
            assert isinstance(peer, PeerTracker)
        buckets = {}
        for peer in used_peers:
            buckets.update(peer.buckets)
        assert len(buckets) == sum([len(peer.buckets) for peer in used_peers])
        self._encoder.set_shareholders(buckets)

    def _compute_uri(self, uri_extension_hash):
        return pack_uri(storage_index=self._storage_index,
                        key=self._encryption_key,
                        uri_extension_hash=uri_extension_hash,
                        needed_shares=self.needed_shares,
                        total_shares=self.total_shares,
                        size=self._size,
                        )

def read_this_many_bytes(uploadable, size, prepend_data=[]):
    d = uploadable.read(size)
    def _got(data):
        assert isinstance(list)
        bytes = sum([len(piece) for piece in data])
        assert bytes > 0
        assert bytes <= size
        remaining = size - bytes
        if remaining:
            return read_this_many_bytes(uploadable, remaining,
                                        prepend_data + data)
        return prepend_data + data
    d.addCallback(_got)
    return d

class LiteralUploader:

    def __init__(self, client, uploadable, options={}):
        self._client = client
        self._uploadable = IUploadable(uploadable)
        self._options = options

    def set_params(self, encoding_parameters):
        pass

    def start(self):
        d = self._uploadable.get_size()
        d.addCallback(lambda size: read_this_many_bytes(self._uploadable, size))
        d.addCallback(lambda data: pack_lit("".join(data)))
        return d

    def close(self):
        pass


class ConvergentUploadMixin:
    # to use this, the class it is mixed in to must have a seekable
    # filehandle named self._filehandle

    def get_encryption_key(self, encoding_parameters):
        f = self._filehandle
        enckey_hasher = hashutil.key_hasher()
        #enckey_hasher.update(encoding_parameters) # TODO
        f.seek(0)
        BLOCKSIZE = 64*1024
        while True:
            data = f.read(BLOCKSIZE)
            if not data:
                break
            enckey_hasher.update(data)
        enckey = enckey_hasher.digest()[:16]
        f.seek(0)
        return defer.succeed(enckey)

class NonConvergentUploadMixin:
    def get_encryption_key(self, encoding_parameters):
        return defer.succeed(os.urandom(16))


class FileHandle(ConvergentUploadMixin):
    implements(IUploadable)

    def __init__(self, filehandle):
        self._filehandle = filehandle

    def get_size(self):
        self._filehandle.seek(0,2)
        size = self._filehandle.tell()
        self._filehandle.seek(0)
        return defer.succeed(size)

    def read(self, length):
        return defer.succeed([self._filehandle.read(length)])

    def close(self):
        # the originator of the filehandle reserves the right to close it
        pass

class FileName(FileHandle):
    def __init__(self, filename):
        FileHandle.__init__(self, open(filename, "rb"))
    def close(self):
        FileHandle.close(self)
        self._filehandle.close()

class Data(FileHandle):
    def __init__(self, data):
        FileHandle.__init__(self, StringIO(data))

class Uploader(service.MultiService):
    """I am a service that allows file uploading.
    """
    implements(IUploader)
    name = "uploader"
    uploader_class = CHKUploader
    URI_LIT_SIZE_THRESHOLD = 55

    DEFAULT_ENCODING_PARAMETERS = (25, 75, 100)
    # this is a tuple of (needed, desired, total). 'needed' is the number of
    # shares required to reconstruct a file. 'desired' means that we will
    # abort an upload unless we can allocate space for at least this many.
    # 'total' is the total number of shares created by encoding. If everybody
    # has room then this is is how many we will upload.

    def upload(self, uploadable, options={}):
        # this returns the URI
        assert self.parent
        assert self.running
        uploadable = IUploadable(uploadable)
        d = uploadable.get_size()
        def _got_size(size):
            uploader_class = self.uploader_class
            if size <= self.URI_LIT_SIZE_THRESHOLD:
                uploader_class = LiteralUploader
            uploader = self.uploader_class(self.parent, uploadable, options)
            uploader.set_params(self.parent.get_encoding_parameters()
                                or self.DEFAULT_ENCODING_PARAMETERS)
            return uploader.start()
        d.addCallback(_got_size)
        def _done(res):
            uploadable.close()
            return res
        d.addBoth(_done)
        return d

    # utility functions
    def upload_data(self, data, options={}):
        return self.upload(Data(data), options)
    def upload_filename(self, filename, options={}):
        return self.upload(FileName(filename), options)
    def upload_filehandle(self, filehandle, options={}):
        return self.upload(FileHandle(filehandle), options)
