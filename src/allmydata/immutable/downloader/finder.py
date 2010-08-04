
import time
now = time.time
from foolscap.api import eventually
from allmydata.util import base32, log, idlib

from share import Share, CommonShare

def incidentally(res, f, *args, **kwargs):
    """Add me to a Deferred chain like this:
     d.addBoth(incidentally, func, arg)
    and I'll behave as if you'd added the following function:
     def _(res):
         func(arg)
         return res
    This is useful if you want to execute an expression when the Deferred
    fires, but don't care about its value.
    """
    f(*args, **kwargs)
    return res

class RequestToken:
    def __init__(self, peerid):
        self.peerid = peerid

class ShareFinder:
    def __init__(self, storage_broker, verifycap, node, download_status,
                 logparent=None, max_outstanding_requests=10):
        self.running = True # stopped by Share.stop, from Terminator
        self.verifycap = verifycap
        self._started = False
        self._storage_broker = storage_broker
        self.share_consumer = self.node = node
        self.max_outstanding_requests = max_outstanding_requests

        self._hungry = False

        self._commonshares = {} # shnum to CommonShare instance
        self.undelivered_shares = []
        self.pending_requests = set()

        self._storage_index = verifycap.storage_index
        self._si_prefix = base32.b2a_l(self._storage_index[:8], 60)
        self._node_logparent = logparent
        self._download_status = download_status
        self._lp = log.msg(format="ShareFinder[si=%(si)s] starting",
                           si=self._si_prefix,
                           level=log.NOISY, parent=logparent, umid="2xjj2A")

    def start_finding_servers(self):
        # don't get servers until somebody uses us: creating the
        # ImmutableFileNode should not cause work to happen yet. Test case is
        # test_dirnode, which creates us with storage_broker=None
        if not self._started:
            si = self.verifycap.storage_index
            s = self._storage_broker.get_servers_for_index(si)
            self._servers = iter(s)
            self._started = True

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._lp
        return log.msg(*args, **kwargs)

    def stop(self):
        self.running = False

    # called by our parent CiphertextDownloader
    def hungry(self):
        self.log(format="ShareFinder[si=%(si)s] hungry",
                 si=self._si_prefix, level=log.NOISY, umid="NywYaQ")
        self.start_finding_servers()
        self._hungry = True
        eventually(self.loop)

    # internal methods
    def loop(self):
        undelivered_s = ",".join(["sh%d@%s" %
                                  (s._shnum, idlib.shortnodeid_b2a(s._peerid))
                                  for s in self.undelivered_shares])
        pending_s = ",".join([idlib.shortnodeid_b2a(rt.peerid)
                              for rt in self.pending_requests]) # sort?
        self.log(format="ShareFinder loop: running=%(running)s"
                 " hungry=%(hungry)s, undelivered=%(undelivered)s,"
                 " pending=%(pending)s",
                 running=self.running, hungry=self._hungry,
                 undelivered=undelivered_s, pending=pending_s,
                 level=log.NOISY, umid="kRtS4Q")
        if not self.running:
            return
        if not self._hungry:
            return
        if self.undelivered_shares:
            sh = self.undelivered_shares.pop(0)
            # they will call hungry() again if they want more
            self._hungry = False
            self.log(format="delivering Share(shnum=%(shnum)d, server=%(peerid)s)",
                     shnum=sh._shnum, peerid=sh._peerid_s,
                     level=log.NOISY, umid="2n1qQw")
            eventually(self.share_consumer.got_shares, [sh])
            return

        if len(self.pending_requests) >= self.max_outstanding_requests:
            # cannot send more requests, must wait for some to retire
            return

        server = None
        try:
            if self._servers:
                server = self._servers.next()
        except StopIteration:
            self._servers = None

        if server:
            self.send_request(server)
            # we loop again to get parallel queries. The check above will
            # prevent us from looping forever.
            eventually(self.loop)
            return

        if self.pending_requests:
            # no server, but there are still requests in flight: maybe one of
            # them will make progress
            return

        self.log(format="ShareFinder.loop: no_more_shares, ever",
                 level=log.UNUSUAL, umid="XjQlzg")
        # we've run out of servers (so we can't send any more requests), and
        # we have nothing in flight. No further progress can be made. They
        # are destined to remain hungry.
        self.share_consumer.no_more_shares()

    def send_request(self, server):
        peerid, rref = server
        req = RequestToken(peerid)
        self.pending_requests.add(req)
        lp = self.log(format="sending DYHB to [%(peerid)s]",
                      peerid=idlib.shortnodeid_b2a(peerid),
                      level=log.NOISY, umid="Io7pyg")
        d_ev = self._download_status.add_dyhb_sent(peerid, now())
        d = rref.callRemote("get_buckets", self._storage_index)
        d.addBoth(incidentally, self.pending_requests.discard, req)
        d.addCallbacks(self._got_response, self._got_error,
                       callbackArgs=(rref.version, peerid, req, d_ev, lp),
                       errbackArgs=(peerid, req, d_ev, lp))
        d.addErrback(log.err, format="error in send_request",
                     level=log.WEIRD, parent=lp, umid="rpdV0w")
        d.addCallback(incidentally, eventually, self.loop)

    def _got_response(self, buckets, server_version, peerid, req, d_ev, lp):
        shnums = sorted([shnum for shnum in buckets])
        d_ev.finished(shnums, now())
        if buckets:
            shnums_s = ",".join([str(shnum) for shnum in shnums])
            self.log(format="got shnums [%(shnums)s] from [%(peerid)s]",
                     shnums=shnums_s, peerid=idlib.shortnodeid_b2a(peerid),
                     level=log.NOISY, parent=lp, umid="0fcEZw")
        else:
            self.log(format="no shares from [%(peerid)s]",
                     peerid=idlib.shortnodeid_b2a(peerid),
                     level=log.NOISY, parent=lp, umid="U7d4JA")
        if self.node.num_segments is None:
            best_numsegs = self.node.guessed_num_segments
        else:
            best_numsegs = self.node.num_segments
        for shnum, bucket in buckets.iteritems():
            self._create_share(best_numsegs, shnum, bucket, server_version,
                               peerid)

    def _create_share(self, best_numsegs, shnum, bucket, server_version,
                      peerid):
        if shnum in self._commonshares:
            cs = self._commonshares[shnum]
        else:
            cs = CommonShare(best_numsegs, self._si_prefix, shnum,
                             self._node_logparent)
            # Share._get_satisfaction is responsible for updating
            # CommonShare.set_numsegs after we know the UEB. Alternatives:
            #  1: d = self.node.get_num_segments()
            #     d.addCallback(cs.got_numsegs)
            #   the problem is that the OneShotObserverList I was using
            #   inserts an eventual-send between _get_satisfaction's
            #   _satisfy_UEB and _satisfy_block_hash_tree, and the
            #   CommonShare didn't get the num_segs message before
            #   being asked to set block hash values. To resolve this
            #   would require an immediate ObserverList instead of
            #   an eventual-send -based one
            #  2: break _get_satisfaction into Deferred-attached pieces.
            #     Yuck.
            self._commonshares[shnum] = cs
        s = Share(bucket, server_version, self.verifycap, cs, self.node,
                  self._download_status, peerid, shnum,
                  self._node_logparent)
        self.undelivered_shares.append(s)

    def _got_error(self, f, peerid, req, d_ev, lp):
        d_ev.finished("error", now())
        self.log(format="got error from [%(peerid)s]",
                 peerid=idlib.shortnodeid_b2a(peerid), failure=f,
                 level=log.UNUSUAL, parent=lp, umid="zUKdCw")


