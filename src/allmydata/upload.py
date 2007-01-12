
from zope.interface import Interface, implements
from twisted.python import failure, log
from twisted.internet import defer
from twisted.application import service
from foolscap import Referenceable

from allmydata.util import idlib
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

    def set_filehandle(self, filehandle):
        self._filehandle = filehandle
        filehandle.seek(0, 2)
        self._size = filehandle.tell()
        filehandle.seek(0)

    def make_encoder(self):
        self._needed_shares = 4
        self._shares = 4
        self._encoder = codec.Encoder(self._filehandle, self._shares)
        self._share_size = self._size

    def set_verifierid(self, vid):
        assert isinstance(vid, str)
        self._verifierid = vid


    def start(self):
        log.msg("starting upload")
        if self.debug:
            print "starting upload"
        # first step: who should we upload to?

        # maybe limit max_peers to 2*len(self.shares), to reduce memory
        # footprint
        max_peers = None

        self.permuted = self._peer.permute_peerids(self._verifierid, max_peers)
        self._total_peers = len(self.permuted)
        for p in self.permuted:
            assert isinstance(p, str)
        # we will shrink self.permuted as we give up on peers
        self.peer_index = 0
        self.goodness_points = 0
        self.target_goodness = self._shares
        self.landlords = [] # list of (peerid, bucket_num, remotebucket)

        d = defer.maybeDeferred(self._check_next_peer)
        d.addCallback(self._got_all_peers)
        return d

    def _check_next_peer(self):
        if len(self.permuted) == 0:
            # there are no more to check
            raise NotEnoughPeersError("%s goodness, want %s, have %d "
                                      "landlords, %d total peers" %
                                      (self.goodness_points,
                                       self.target_goodness,
                                       len(self.landlords),
                                       self._total_peers))
        if self.peer_index >= len(self.permuted):
            self.peer_index = 0

        peerid = self.permuted[self.peer_index]

        d = self._peer.get_remote_service(peerid, "storageserver")
        def _got_peer(service):
            bucket_num = len(self.landlords)
            if self.debug: print "asking %s" % idlib.b2a(peerid)
            d2 = service.callRemote("allocate_bucket",
                                    verifierid=self._verifierid,
                                    bucket_num=bucket_num,
                                    size=self._share_size,
                                    leaser=self._peer.nodeid,
                                    canary=Referenceable())
            def _allocate_response(bucket):
                if self.debug:
                    print " peerid %s will grant us a lease" % idlib.b2a(peerid)
                self.landlords.append( (peerid, bucket_num, bucket) )
                self.goodness_points += 1
                if self.goodness_points >= self.target_goodness:
                    if self.debug: print " we're done!"
                    raise HaveAllPeersError()
                # otherwise we fall through to allocate more peers
            d2.addCallback(_allocate_response)
            return d2
        d.addCallback(_got_peer)
        def _done_with_peer(res):
            if self.debug: print "done with peer %s:" % idlib.b2a(peerid)
            if isinstance(res, failure.Failure):
                if res.check(HaveAllPeersError):
                    if self.debug: print " all done"
                    # we're done!
                    return
                if res.check(TooFullError):
                    if self.debug: print " too full"
                elif res.check(IndexError):
                    if self.debug: print " no connection"
                else:
                    if self.debug: print " other error:", res
                self.permuted.remove(peerid) # this peer was unusable
            else:
                if self.debug: print " they gave us a lease"
                # we get here for either good peers (when we still need
                # more), or after checking a bad peer (and thus still need
                # more). So now we need to grab a new peer.
                self.peer_index += 1
            return self._check_next_peer()
        d.addBoth(_done_with_peer)
        return d

    def _got_all_peers(self, res):
        d = self._encoder.do_upload(self.landlords)
        d.addCallback(lambda res: self._verifierid)
        return d

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

    def _compute_verifierid(self, f):
        hasher = sha.new(netstring("allmydata_v1_verifierid"))
        f.seek(0)
        hasher.update(f.read())
        f.seek(0)
        # note: this is only of the plaintext data, no encryption yet
        return hasher.digest()

    def upload(self, f):
        assert self.parent
        assert self.running
        f = IUploadable(f)
        fh = f.get_filehandle()
        u = FileUploader(self.parent)
        u.set_filehandle(fh)
        u.set_verifierid(self._compute_verifierid(fh))
        u.make_encoder()
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
