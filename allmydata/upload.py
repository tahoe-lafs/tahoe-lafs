
from twisted.python import failure
from twisted.internet import defer
from allmydata.util import idlib

class NotEnoughPeersError(Exception):
    pass

class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

# this wants to live in storage, not here
class TooFullError(Exception):
    pass

class Uploader:
    debug = False

    def __init__(self, peer):
        self._peer = peer

    def set_encoder(self, encoder):
        self._encoder = encoder

    def set_verifierid(self, vid):
        assert isinstance(vid, str)
        self._verifierid = vid

    def set_filesize(self, size):
        self._size = size

    def _calculate_parameters(self):
        self._shares = 100
        self._share_size = self._size / 25


    def start(self):
        # first step: who should we upload to?

        # maybe limit max_peers to 2*len(self.shares), to reduce memory
        # footprint
        max_peers = None

        self.permuted = self._peer.permute_peerids(self._verifierid, max_peers)
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
            raise NotEnoughPeersError
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
                                    leaser=self._peer.nodeid)
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
        return d

