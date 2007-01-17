
from zope.interface import Interface, implements
from twisted.python import failure, log
from twisted.internet import defer
from twisted.application import service
from foolscap import Referenceable

from allmydata.util import idlib, bencode
from allmydata.util.idlib import peerid_to_short_string as shortid
from allmydata.util.deferredutil import DeferredListShouldSucceed
from allmydata import codec

from cStringIO import StringIO
import sha

class NotEnoughPeersError(Exception):
    pass

class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

# this wants to live in storage, not here
class TooFullError(Exception):
    pass


class FileUploader:
    debug = False

    def __init__(self, peer):
        self._peer = peer

    def set_params(self, min_shares, target_goodness, max_shares):
        self.min_shares = min_shares
        self.target_goodness = target_goodness
        self.max_shares = max_shares

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
        assert self.min_shares
        assert self.target_goodness

        # create the encoder, so we can know how large the shares will be
        total_shares = self.max_shares
        needed_shares = self.min_shares
        self._encoder = codec.ReplicatingEncoder()
        self._encoder.set_params(self._size, needed_shares, total_shares)
        self._share_size = self._encoder.get_share_size()

        # first step: who should we upload to?

        # We will talk to at most max_peers (which can be None to mean no
        # limit). Maybe limit max_peers to 2*len(self.shares), to reduce
        # memory footprint. For now, make it unlimited.
        max_peers = None

        self.permuted = self._peer.permute_peerids(self._verifierid, max_peers)
        self.peers_who_said_yes = []
        self.peers_who_said_no = []
        self.peers_who_had_errors = []

        self._total_peers = len(self.permuted)
        for p in self.permuted:
            assert isinstance(p, str)
        # we will shrink self.permuted as we give up on peers

        d = defer.maybeDeferred(self._find_peers)
        d.addCallback(self._got_enough_peers)
        d.addCallback(self._compute_uri)
        return d

    def _compute_uri(self, params):
        return "URI:%s" % bencode.bencode((self._verifierid, params))

    def _build_not_enough_peers_error(self):
        yes = ",".join([shortid(p) for p in self.peers_who_said_yes])
        no = ",".join([shortid(p) for p in self.peers_who_said_no])
        err = ",".join([shortid(p) for p in self.peers_who_had_errors])
        msg = ("%s goodness, want %s, have %d "
               "landlords, %d total peers, "
               "peers:yes=%s;no=%s;err=%s" %
               (self.goodness_points, self.target_goodness,
                len(self.landlords), self._total_peers,
                yes, no, err))
        return msg

    def _find_peers(self):
        # this returns a Deferred which fires (with a meaningless value) when
        # enough peers are found, or errbacks with a NotEnoughPeersError if
        # not.
        self.peer_index = 0
        self.goodness_points = 0
        self.landlords = [] # list of (peerid, bucket_num, remotebucket)
        return self._check_next_peer()

    def _check_next_peer(self):
        if self.debug:
            log.msg("FileUploader._check_next_peer: %d permuted, %d goodness"
                    " (want %d), have %d landlords, %d total peers" %
                    (len(self.permuted), self.goodness_points,
                     self.target_goodness, len(self.landlords),
                     self._total_peers))
        if (self.goodness_points >= self.target_goodness and
            len(self.landlords) >= self.min_shares):
            if self.debug: print " we're done!"
            return "done"
        if not self.permuted:
            # we've run out of peers to check without finding enough, which
            # means we won't be able to upload this file. Bummer.
            msg = self._build_not_enough_peers_error()
            log.msg("NotEnoughPeersError: %s" % msg)
            raise NotEnoughPeersError(msg)

        # otherwise we use self.peer_index to rotate through all the usable
        # peers. It gets inremented elsewhere, but wrapped here.
        if self.peer_index >= len(self.permuted):
            self.peer_index = 0

        peerid = self.permuted[self.peer_index]

        d = self._check_peer(peerid)
        d.addCallback(lambda res: self._check_next_peer())
        return d

    def _check_peer(self, peerid):
        # contact a single peer, and ask them to hold a share. If they say
        # yes, we update self.landlords and self.goodness_points, and
        # increment self.peer_index. If they say no, or are uncontactable, we
        # remove them from self.permuted. This returns a Deferred which never
        # errbacks.

        bucket_num = len(self.landlords)
        d = self._peer.get_remote_service(peerid, "storageserver")
        def _got_peer(service):
            if self.debug: print "asking %s" % shortid(peerid)
            d2 = service.callRemote("allocate_bucket",
                                    verifierid=self._verifierid,
                                    bucket_num=bucket_num,
                                    size=self._share_size,
                                    leaser=self._peer.nodeid,
                                    canary=Referenceable())
            return d2
        d.addCallback(_got_peer)

        def _allocate_response(bucket):
            if self.debug:
                print " peerid %s will grant us a lease" % shortid(peerid)
            self.peers_who_said_yes.append(peerid)
            self.landlords.append( (peerid, bucket_num, bucket) )
            self.goodness_points += 1
            self.peer_index += 1

        d.addCallback(_allocate_response)

        def _err(f):
            if self.debug: print "err from peer %s:" % idlib.b2a(peerid)
            assert isinstance(f, failure.Failure)
            if f.check(TooFullError):
                if self.debug: print " too full"
                self.peers_who_said_no.append(peerid)
            elif f.check(IndexError):
                if self.debug: print " no connection"
                self.peers_who_had_errors.append(peerid)
            else:
                if self.debug: print " other error:", res
                self.peers_who_had_errors.append(peerid)
                log.msg("FileUploader._check_peer(%s): err" % shortid(peerid))
                log.msg(f)
            self.permuted.remove(peerid) # this peer was unusable
            return None
        d.addErrback(_err)
        return d

    def _got_enough_peers(self, res):
        landlords = self.landlords
        if self.debug:
            log.msg("FileUploader._got_enough_peers")
            log.msg(" %d landlords" % len(landlords))
            if len(landlords) < 20:
                log.msg(" peerids: %s" % " ".join([idlib.b2a(l[0])
                                                   for l in landlords]))
                log.msg(" buckets: %s" % " ".join([str(l[1])
                                                   for l in landlords]))
        # assign shares to landlords
        self.sharemap = {}
        for peerid, bucket_num, bucket in landlords:
            self.sharemap[bucket_num] = bucket
        # the sharemap should have exactly len(landlords) shares, with
        # no holes
        assert sorted(self.sharemap.keys()) == range(len(landlords))
        # encode all the data at once: this class does not use segmentation
        data = self._filehandle.read()
        d = self._encoder.encode(data, len(landlords))
        d.addCallback(self._send_all_shares)
        d.addCallback(lambda res: self._encoder.get_serialized_params())
        return d

    def _send_one_share(self, bucket, sharedata, metadata):
        d = bucket.callRemote("write", sharedata)
        d.addCallback(lambda res:
                      bucket.callRemote("set_metadata", metadata))
        d.addCallback(lambda res:
                      bucket.callRemote("close"))
        return d

    def _send_all_shares(self, shares):
        dl = []
        for share in shares:
            (sharenum,sharedata) = share
            if self.debug:
                log.msg(" writing share %d" % sharenum)
            metadata = bencode.bencode(sharenum)
            assert len(sharedata) == self._share_size
            assert isinstance(sharedata, str)
            bucket = self.sharemap[sharenum]
            d = self._send_one_share(bucket, sharedata, metadata)
            dl.append(d)
        return DeferredListShouldSucceed(dl)

def netstring(s):
    return "%d:%s," % (len(s), s)

class IUploadable(Interface):
    def get_filehandle():
        pass
    def close_filehandle(f):
        pass

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
    name = "uploader"
    uploader_class = FileUploader
    debug = False

    def _compute_verifierid(self, f):
        hasher = sha.new(netstring("allmydata_v1_verifierid"))
        f.seek(0)
        data = f.read()
        hasher.update(data)#f.read())
        f.seek(0)
        # note: this is only of the plaintext data, no encryption yet
        return hasher.digest()

    def upload(self, f):
        # this returns (verifierid, encoding_params)
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
        u.set_params(2, 2, 4)
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
