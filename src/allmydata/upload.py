from zope.interface import implements
from twisted.python import log
from twisted.internet import defer
from twisted.application import service
from foolscap import Referenceable

from allmydata.util import idlib
from allmydata import encode
from allmydata.uri import pack_uri
from allmydata.interfaces import IUploadable, IUploader

from cStringIO import StringIO
import collections, random, sha

class NotEnoughPeersError(Exception):
    pass

class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

# this wants to live in storage, not here
class TooFullError(Exception):
    pass

class PeerTracker:
    def __init__(self, peerid, permutedid, connection, sharesize, blocksize, verifierid):
        self.peerid = peerid
        self.permutedid = permutedid
        self.connection = connection # to an RIClient
        self.buckets = {} # k: shareid, v: IRemoteBucketWriter
        self.sharesize = sharesize
        self.blocksize = blocksize
        self.verifierid = verifierid
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
        d = self._storageserver.callRemote("allocate_buckets", self.verifierid,
                                           sharenums, self.sharesize,
                                           self.blocksize, canary=Referenceable())
        d.addCallback(self._got_reply)
        return d
        
    def _got_reply(self, (alreadygot, buckets)):
        log.msg("%s._got_reply(%s)" % (self, (alreadygot, buckets)))
        self.buckets.update(buckets)
        return (alreadygot, set(buckets.keys()))

class FileUploader:

    def __init__(self, client, options={}):
        self._client = client
        self._options = options

    def set_params(self, needed_shares, shares_of_happiness, total_shares):
        self.needed_shares = needed_shares
        self.shares_of_happiness = shares_of_happiness
        self.total_shares = total_shares

    def set_filehandle(self, filehandle):
        self._filehandle = filehandle
        filehandle.seek(0, 2)
        self._size = filehandle.tell()
        filehandle.seek(0)

    def set_verifierid(self, vid):
        assert isinstance(vid, str)
        assert len(vid) == 20
        self._verifierid = vid

    def start(self):
        """Start uploading the file.

        The source of the data to be uploaded must have been set before this
        point by calling set_filehandle().

        This method returns a Deferred that will fire with the URI (a
        string)."""

        log.msg("starting upload [%s]" % (idlib.b2a(self._verifierid),))
        assert self.needed_shares

        # create the encoder, so we can know how large the shares will be
        self._encoder = encode.Encoder(self._options)
        self._encoder.setup(self._filehandle)
        share_size = self._encoder.get_share_size()
        block_size = self._encoder.get_block_size()

        # we are responsible for locating the shareholders. self._encoder is
        # responsible for handling the data and sending out the shares.
        peers = self._client.get_permuted_peers(self._verifierid)
        assert peers
        trackers = [ PeerTracker(peerid, permutedid, conn, share_size, block_size, self._verifierid)
                     for permutedid, peerid, conn in peers ]
        self.usable_peers = set(trackers) # this set shrinks over time
        self.used_peers = set() # while this set grows
        self.unallocated_sharenums = set(range(self.total_shares)) # this one shrinks

        d = self._locate_all_shareholders()
        d.addCallback(self._send_shares)
        d.addCallback(self._compute_uri)
        return d

    def _locate_all_shareholders(self):
        """
        @return: a set of PeerTracker instances that have agreed to hold some
            shares for us
        """
        return self._locate_more_shareholders()

    def _locate_more_shareholders(self):
        d = self._query_peers()
        d.addCallback(self._located_some_shareholders)
        return d

    def _located_some_shareholders(self, res):
        log.msg("_located_some_shareholders")
        log.msg(" still need homes for %d shares, still have %d usable peers" % (len(self.unallocated_sharenums), len(self.usable_peers)))
        if not self.unallocated_sharenums:
            # Finished allocating places for all shares.
            log.msg("%s._locate_all_shareholders() Finished allocating places for all shares." % self)
            log.msg("used_peers is %s" % (self.used_peers,))
            return self.used_peers
        if not self.usable_peers:
            # Ran out of peers who have space.
            log.msg("%s._locate_all_shareholders() Ran out of peers who have space." % self)
            if len(self.unallocated_sharenums) < (self.total_shares - self.shares_of_happiness):
                # But we allocated places for enough shares.
                log.msg("%s._locate_all_shareholders() But we allocated places for enough shares.")
                return self.used_peers
            raise NotEnoughPeersError
        # we need to keep trying
        return self._locate_more_shareholders()

    def _create_ring_of_things(self):
        PEER = 1 # must sort later than SHARE, for consistency with download
        SHARE = 0
        ring_of_things = [] # a list of (position_in_ring, whatami, x) where whatami is SHARE if x is a sharenum or else PEER if x is a PeerTracker instance
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
        log.msg("%s._got_response(%s, %s, %s): self.unallocated_sharenums: %s, unhandled: %s" % (self, (alreadygot, allocated), peer, shares_we_requested, self.unallocated_sharenums, shares_we_requested - alreadygot - allocated))
        self.unallocated_sharenums -= alreadygot
        self.unallocated_sharenums -= allocated

        if allocated:
            self.used_peers.add(peer)

        if shares_we_requested - alreadygot - allocated:
            log.msg("%s._got_response(%s, %s, %s): self.unallocated_sharenums: %s, unhandled: %s HE'S FULL" % (self, (alreadygot, allocated), peer, shares_we_requested, self.unallocated_sharenums, shares_we_requested - alreadygot - allocated))
            # Then he didn't accept some of the shares, so he's full.
            self.usable_peers.remove(peer)

    def _got_error(self, f, peer):
        log.msg("%s._got_error(%s, %s)" % (self, f, peer,))
        self.usable_peers.remove(peer)

    def _send_shares(self, used_peers):
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
        return self._encoder.start()

    def _compute_uri(self, roothash):
        codec_type = self._encoder._codec.get_encoder_type()
        codec_params = self._encoder._codec.get_serialized_params()
        tail_codec_params = self._encoder._tail_codec.get_serialized_params()
        return pack_uri(codec_type, codec_params, tail_codec_params,
                        self._verifierid,
                        roothash, self.needed_shares, self.total_shares,
                        self._size, self._encoder.segment_size)


def netstring(s):
    return "%d:%s," % (len(s), s)

class FileName:
    implements(IUploadable)
    def __init__(self, filename):
        self._filename = filename
    def get_filehandle(self):
        return open(self._filename, "rb")
    def close_filehandle(self, f):
        f.close()

class Data:
    implements(IUploadable)
    def __init__(self, data):
        self._data = data
    def get_filehandle(self):
        return StringIO(self._data)
    def close_filehandle(self, f):
        pass

class FileHandle:
    implements(IUploadable)
    def __init__(self, filehandle):
        self._filehandle = filehandle
    def get_filehandle(self):
        return self._filehandle
    def close_filehandle(self, f):
        # the originator of the filehandle reserves the right to close it
        pass

class Uploader(service.MultiService):
    """I am a service that allows file uploading.
    """
    implements(IUploader)
    name = "uploader"
    uploader_class = FileUploader

    needed_shares = 25 # Number of shares required to reconstruct a file.
    desired_shares = 75 # We will abort an upload unless we can allocate space for at least this many.
    total_shares = 100 # Total number of shares created by encoding.  If everybody has room then this is is how many we will upload.

    def _compute_verifierid(self, f):
        hasher = sha.new(netstring("allmydata_v1_verifierid"))
        f.seek(0)
        data = f.read()
        hasher.update(data)#f.read())
        f.seek(0)
        # note: this is only of the plaintext data, no encryption yet
        return hasher.digest()

    def upload(self, f, options={}):
        # this returns the URI
        assert self.parent
        assert self.running
        f = IUploadable(f)
        fh = f.get_filehandle()
        u = self.uploader_class(self.parent, options)
        u.set_filehandle(fh)
        u.set_params(self.needed_shares, self.desired_shares, self.total_shares)
        u.set_verifierid(self._compute_verifierid(fh))
        d = u.start()
        def _done(res):
            f.close_filehandle(fh)
            return res
        d.addBoth(_done)
        return d

    # utility functions
    def upload_data(self, data):
        return self.upload(Data(data))
    def upload_filename(self, filename):
        return self.upload(FileName(filename))
    def upload_filehandle(self, filehandle):
        return self.upload(FileHandle(filehandle))
