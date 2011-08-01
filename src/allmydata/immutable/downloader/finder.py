
import time
now = time.time
from foolscap.api import eventually
from allmydata.util import base32, log
from twisted.internet import reactor

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
    def __init__(self, server):
        self.server = server

class ShareFinder:
    OVERDUE_TIMEOUT = 10.0

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
        self.pending_requests = set()
        self.overdue_requests = set() # subset of pending_requests
        self.overdue_timers = {}

        self._storage_index = verifycap.storage_index
        self._si_prefix = base32.b2a_l(self._storage_index[:8], 60)
        self._node_logparent = logparent
        self._download_status = download_status
        self._lp = log.msg(format="ShareFinder[si=%(si)s] starting",
                           si=self._si_prefix,
                           level=log.NOISY, parent=logparent, umid="2xjj2A")

    def update_num_segments(self):
        (numsegs, authoritative) = self.node.get_num_segments()
        assert authoritative
        for cs in self._commonshares.values():
            cs.set_authoritative_num_segments(numsegs)

    def start_finding_servers(self):
        # don't get servers until somebody uses us: creating the
        # ImmutableFileNode should not cause work to happen yet. Test case is
        # test_dirnode, which creates us with storage_broker=None
        if not self._started:
            si = self.verifycap.storage_index
            servers = self._storage_broker.get_servers_for_psi(si)
            self._servers = iter(servers)
            self._started = True

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._lp
        return log.msg(*args, **kwargs)

    def stop(self):
        self.running = False
        while self.overdue_timers:
            req,t = self.overdue_timers.popitem()
            t.cancel()

    # called by our parent CiphertextDownloader
    def hungry(self):
        self.log(format="ShareFinder[si=%(si)s] hungry",
                 si=self._si_prefix, level=log.NOISY, umid="NywYaQ")
        self.start_finding_servers()
        self._hungry = True
        eventually(self.loop)

    # internal methods
    def loop(self):
        pending_s = ",".join([rt.server.get_name()
                              for rt in self.pending_requests]) # sort?
        self.log(format="ShareFinder loop: running=%(running)s"
                 " hungry=%(hungry)s, pending=%(pending)s",
                 running=self.running, hungry=self._hungry, pending=pending_s,
                 level=log.NOISY, umid="kRtS4Q")
        if not self.running:
            return
        if not self._hungry:
            return

        non_overdue = self.pending_requests - self.overdue_requests
        if len(non_overdue) >= self.max_outstanding_requests:
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
        eventually(self.share_consumer.no_more_shares)

    def send_request(self, server):
        req = RequestToken(server)
        self.pending_requests.add(req)
        lp = self.log(format="sending DYHB to [%(name)s]", name=server.get_name(),
                      level=log.NOISY, umid="Io7pyg")
        time_sent = now()
        d_ev = self._download_status.add_dyhb_request(server, time_sent)
        # TODO: get the timer from a Server object, it knows best
        self.overdue_timers[req] = reactor.callLater(self.OVERDUE_TIMEOUT,
                                                     self.overdue, req)
        d = server.get_rref().callRemote("get_buckets", self._storage_index)
        d.addBoth(incidentally, self._request_retired, req)
        d.addCallbacks(self._got_response, self._got_error,
                       callbackArgs=(server, req, d_ev, time_sent, lp),
                       errbackArgs=(server, req, d_ev, lp))
        d.addErrback(log.err, format="error in send_request",
                     level=log.WEIRD, parent=lp, umid="rpdV0w")
        d.addCallback(incidentally, eventually, self.loop)

    def _request_retired(self, req):
        self.pending_requests.discard(req)
        self.overdue_requests.discard(req)
        if req in self.overdue_timers:
            self.overdue_timers[req].cancel()
            del self.overdue_timers[req]

    def overdue(self, req):
        del self.overdue_timers[req]
        assert req in self.pending_requests # paranoia, should never be false
        self.overdue_requests.add(req)
        eventually(self.loop)

    def _got_response(self, buckets, server, req, d_ev, time_sent, lp):
        shnums = sorted([shnum for shnum in buckets])
        time_received = now()
        d_ev.finished(shnums, time_received)
        dyhb_rtt = time_received - time_sent
        if not buckets:
            self.log(format="no shares from [%(name)s]", name=server.get_name(),
                     level=log.NOISY, parent=lp, umid="U7d4JA")
            return
        shnums_s = ",".join([str(shnum) for shnum in shnums])
        self.log(format="got shnums [%(shnums)s] from [%(name)s]",
                 shnums=shnums_s, name=server.get_name(),
                 level=log.NOISY, parent=lp, umid="0fcEZw")
        shares = []
        for shnum, bucket in buckets.iteritems():
            s = self._create_share(shnum, bucket, server, dyhb_rtt)
            shares.append(s)
        self._deliver_shares(shares)

    def _create_share(self, shnum, bucket, server, dyhb_rtt):
        if shnum in self._commonshares:
            cs = self._commonshares[shnum]
        else:
            numsegs, authoritative = self.node.get_num_segments()
            cs = CommonShare(numsegs, self._si_prefix, shnum,
                             self._node_logparent)
            if authoritative:
                cs.set_authoritative_num_segments(numsegs)
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
        s = Share(bucket, server, self.verifycap, cs, self.node,
                  self._download_status, shnum, dyhb_rtt,
                  self._node_logparent)
        return s

    def _deliver_shares(self, shares):
        # they will call hungry() again if they want more
        self._hungry = False
        shares_s = ",".join([str(sh) for sh in shares])
        self.log(format="delivering shares: %s" % shares_s,
                 level=log.NOISY, umid="2n1qQw")
        eventually(self.share_consumer.got_shares, shares)

    def _got_error(self, f, server, req, d_ev, lp):
        d_ev.error(now())
        self.log(format="got error from [%(name)s]",
                 name=server.get_name(), failure=f,
                 level=log.UNUSUAL, parent=lp, umid="zUKdCw")


