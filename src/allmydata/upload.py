from zope.interface import implements
from twisted.python import log
from twisted.internet import defer
from twisted.application import service
from foolscap import Referenceable

from allmydata.util import idlib, mathutil
from allmydata import encode_new
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
    def __init__(self, peerid, connection, sharesize, blocksize, verifierid):
        self.peerid = peerid
        self.connection = connection
        self.buckets = {} # k: shareid, v: IRemoteBucketWriter
        self.sharesize = sharesize
        self.blocksize = blocksize
        self.verifierid = verifierid

    def query(self, sharenums):
        d = self.connection.callRemote("allocate_buckets", self._verifierid,
                                       sharenums, self.sharesize,
                                       self.blocksize, canary=Referenceable())
        d.addCallback(self._got_reply)
        return d
        
    def _got_reply(self, (alreadygot, buckets)):
        self.buckets.update(buckets)
        return (alreadygot, set(buckets.keys()))

class FileUploader:
    debug = False

    def __init__(self, client):
        self._client = client

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
        if self.debug:
            print "starting upload"
        assert self.needed_shares

        # create the encoder, so we can know how large the shares will be
        self._encoder = encode_new.Encoder()
        self._encoder.setup(self._filehandle)
        share_size = self._encoder.get_share_size()
        block_size = self._encoder.get_block_size()

        # we are responsible for locating the shareholders. self._encoder is
        # responsible for handling the data and sending out the shares.
        peers = self._client.get_permuted_peers(self._verifierid)
        assert peers
        trackers = [ (permutedid, PeerTracker(peerid, conn, share_size, block_size, self._verifierid),)
                     for permutedid, peerid, conn in peers ]
        ring_things = [] # a list of (position_in_ring, whatami, x) where whatami is 0 if x is a sharenum or else 1 if x is a PeerTracker instance
        ring_things.extend([ (permutedpeerid, 1, peer,) for permutedpeerid, peer in trackers ])
        shares = [ (i * 2**160 / self.total_shares, 0, i) for i in range(self.total_shares) ]
        ring_things.extend(shares)
        ring_things.sort()
        self.ring_things = collections.deque(ring_things)
        self.usable_peers = set([peer for permutedid, peer in trackers])
        self.used_peers = set()
        self.unallocated_sharenums = set(shares)

        d = self._locate_all_shareholders()
        d.addCallback(self._send_shares)
        d.addCallback(self._compute_uri)
        return d

    def _locate_all_shareholders(self):
        """
        @return: a set of PeerTracker instances that have agreed to hold some
            shares for us
        """
        d = self._query_peers()
        def _done(res):
            if not self.unallocated_sharenums:
                return self._used_peers
            if not self.usable_peers:
                if len(self.unallocated_sharenums) < (self.total_shares - self.shares_of_happiness):
                    # close enough
                    return self._used_peers
                raise NotEnoughPeersError
            return self._query_peers()
        d.addCallback(_done)
        return d

    def _query_peers(self):
        """
        @return: a deferred that fires when all queries have resolved
        """
        # Choose a random starting point, talk to that peer.
        self.ring_things.rotate(random.randrange(0, len(self.ring_things)))

        # Walk backwards to find a peer.  We know that we'll eventually find
        # one because we earlier asserted that there was at least one.
        while self.ring_things[0][1] != 1:
            self.ring_things.rotate(-1)
        startingpoint = self.ring_things[0]
        peer = startingpoint[2]
        assert isinstance(peer, PeerTracker), peer
        self.ring_things.rotate(-1)

        # loop invariant: at the top of the loop, we are always one step to
        # the left of a peer, which is stored in the peer variable.
        outstanding_queries = []
        while self.ring_things[0] != startingpoint:
            # Walk backwards to find the previous peer (could be the same one).
            # Accumulate all shares that we find along the way.
            sharenums_to_query = set()
            while self.ring_things[0][1] != 1:
                sharenums_to_query.add(self.ring_things[0][2])
                self.ring_things.rotate(-1)

            d = peer.query(sharenums_to_query)
            d.addCallbacks(self._got_response, self._got_error, callbackArgs=(peer, sharenums_to_query), errbackArgs=(peer,))
            outstanding_queries.append(d)

            peer = self.ring_things[0][2]
            assert isinstance(peer, PeerTracker), peer
            self.ring_things.rotate(-1)

        return defer.DeferredList(outstanding_queries)

    def _got_response(self, (alreadygot, allocated), peer, shares_we_requested):
        """
        @type alreadygot: a set of sharenums
        @type allocated: a set of sharenums
        """
        self.unallocated_sharenums -= alreadygot
        self.unallocated_sharenums -= allocated

        if allocated:
            self.used_peers.add(peer)

        if shares_we_requested - alreadygot - allocated:
            # Then he didn't accept some of the shares, so he's full.
            self.usable_peers.remove(peer)

    def _got_error(self, f, peer):
        self.usable_peers -= peer

    def _send_shares(self, used_peers):
        buckets = {}
        for peer in used_peers:
            buckets.update(peer.buckets)
        assert len(buckets) == sum([len(peer.buckets) for peer in used_peers])
        self._encoder.set_shareholders(buckets)
        return self._encoder.start()

    def _compute_uri(self, roothash):
        codec_type = self._encoder._codec.get_encoder_type()
        codec_params = self._encoder._codec.get_serialized_params()
        return pack_uri(codec_type, codec_params, self._verifierid, roothash, self.needed_shares, self.total_shares, self._size, self._encoder.segment_size)


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
    debug = False

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

    def upload(self, f):
        # this returns the URI
        assert self.parent
        assert self.running
        f = IUploadable(f)
        fh = f.get_filehandle()
        u = self.uploader_class(self.parent)
        if self.debug:
            u.debug = True
        u.set_filehandle(fh)
        # push two shares, require that we get two back. TODO: this is
        # temporary, of course.
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
