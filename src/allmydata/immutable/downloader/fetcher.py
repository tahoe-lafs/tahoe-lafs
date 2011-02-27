
from twisted.python.failure import Failure
from foolscap.api import eventually
from allmydata.interfaces import NotEnoughSharesError, NoSharesError
from allmydata.util import log
from allmydata.util.dictutil import DictOfSets
from common import OVERDUE, COMPLETE, CORRUPT, DEAD, BADSEGNUM, \
     BadSegmentNumberError

class SegmentFetcher:
    """I am responsible for acquiring blocks for a single segment. I will use
    the Share instances passed to my add_shares() method to locate, retrieve,
    and validate those blocks. I expect my parent node to call my
    no_more_shares() method when there are no more shares available. I will
    call my parent's want_more_shares() method when I want more: I expect to
    see at least one call to add_shares or no_more_shares afterwards.

    When I have enough validated blocks, I will call my parent's
    process_blocks() method with a dictionary that maps shnum to blockdata.
    If I am unable to provide enough blocks, I will call my parent's
    fetch_failed() method with (self, f). After either of these events, I
    will shut down and do no further work. My parent can also call my stop()
    method to have me shut down early."""

    def __init__(self, node, segnum, k, logparent):
        self._node = node # _Node
        self.segnum = segnum
        self._k = k
        self._shares = [] # unused Share instances, sorted by "goodness"
                          # (RTT), then shnum. This is populated when DYHB
                          # responses arrive, or (for later segments) at
                          # startup. We remove shares from it when we call
                          # sh.get_block() on them.
        self._shares_from_server = DictOfSets() # maps serverid to set of
                                                # Shares on that server for
                                                # which we have outstanding
                                                # get_block() calls.
        self._max_shares_per_server = 1 # how many Shares we're allowed to
                                        # pull from each server. This starts
                                        # at 1 and grows if we don't have
                                        # sufficient diversity.
        self._active_share_map = {} # maps shnum to outstanding (and not
                                    # OVERDUE) Share that provides it.
        self._overdue_share_map = DictOfSets() # shares in the OVERDUE state
        self._lp = logparent
        self._share_observers = {} # maps Share to EventStreamObserver for
                                   # active ones
        self._blocks = {} # maps shnum to validated block data
        self._no_more_shares = False
        self._last_failure = None
        self._running = True

    def stop(self):
        log.msg("SegmentFetcher(%s).stop" % self._node._si_prefix,
                level=log.NOISY, parent=self._lp, umid="LWyqpg")
        self._cancel_all_requests()
        self._running = False
        # help GC ??? XXX
        del self._shares, self._shares_from_server, self._active_share_map
        del self._share_observers


    # called by our parent _Node

    def add_shares(self, shares):
        # called when ShareFinder locates a new share, and when a non-initial
        # segment fetch is started and we already know about shares from the
        # previous segment
        self._shares.extend(shares)
        self._shares.sort(key=lambda s: (s._dyhb_rtt, s._shnum) )
        eventually(self.loop)

    def no_more_shares(self):
        # ShareFinder tells us it's reached the end of its list
        self._no_more_shares = True
        eventually(self.loop)

    # internal methods

    def loop(self):
        try:
            # if any exception occurs here, kill the download
            self._do_loop()
        except BaseException:
            self._node.fetch_failed(self, Failure())
            raise

    def _do_loop(self):
        k = self._k
        if not self._running:
            return
        numsegs, authoritative = self._node.get_num_segments()
        if authoritative and self.segnum >= numsegs:
            # oops, we were asking for a segment number beyond the end of the
            # file. This is an error.
            self.stop()
            e = BadSegmentNumberError("segnum=%d, numsegs=%d" %
                                      (self.segnum, self._node.num_segments))
            f = Failure(e)
            self._node.fetch_failed(self, f)
            return

        #print "LOOP", self._blocks.keys(), "active:", self._active_share_map, "overdue:", self._overdue_share_map, "unused:", self._shares
        # Should we sent out more requests?
        while len(set(self._blocks.keys())
                  | set(self._active_share_map.keys())
                  ) < k:
            # we don't have data or active requests for enough shares. Are
            # there any unused shares we can start using?
            (sent_something, want_more_diversity) = self._find_and_use_share()
            if sent_something:
                # great. loop back around in case we need to send more.
                continue
            if want_more_diversity:
                # we could have sent something if we'd been allowed to pull
                # more shares per server. Increase the limit and try again.
                self._max_shares_per_server += 1
                log.msg("SegmentFetcher(%s) increasing diversity limit to %d"
                        % (self._node._si_prefix, self._max_shares_per_server),
                        level=log.NOISY, umid="xY2pBA")
                # Also ask for more shares, in the hopes of achieving better
                # diversity for the next segment.
                self._ask_for_more_shares()
                continue
            # we need more shares than the ones in self._shares to make
            # progress
            self._ask_for_more_shares()
            if self._no_more_shares:
                # But there are no more shares to be had. If we're going to
                # succeed, it will be with the shares we've already seen.
                # Will they be enough?
                if len(set(self._blocks.keys())
                       | set(self._active_share_map.keys())
                       | set(self._overdue_share_map.keys())
                       ) < k:
                    # nope. bail.
                    self._no_shares_error() # this calls self.stop()
                    return
                # our outstanding or overdue requests may yet work.
            # more shares may be coming. Wait until then.
            return

        # are we done?
        if len(set(self._blocks.keys())) >= k:
            # yay!
            self.stop()
            self._node.process_blocks(self.segnum, self._blocks)
            return

    def _no_shares_error(self):
        if not (self._shares or self._active_share_map or
                self._overdue_share_map or self._blocks):
            format = ("no shares (need %(k)d)."
                      " Last failure: %(last_failure)s")
            args = { "k": self._k,
                     "last_failure": self._last_failure }
            error = NoSharesError
        else:
            format = ("ran out of shares: complete=%(complete)s"
                      " pending=%(pending)s overdue=%(overdue)s"
                      " unused=%(unused)s need %(k)d."
                      " Last failure: %(last_failure)s")
            def join(shnums): return ",".join(["sh%d" % shnum
                                               for shnum in sorted(shnums)])
            pending_s = ",".join([str(sh)
                                  for sh in self._active_share_map.values()])
            overdue = set()
            for shares in self._overdue_share_map.values():
                overdue |= shares
            overdue_s = ",".join([str(sh) for sh in overdue])
            args = {"complete": join(self._blocks.keys()),
                    "pending": pending_s,
                    "overdue": overdue_s,
                    # 'unused' should be zero
                    "unused": ",".join([str(sh) for sh in self._shares]),
                    "k": self._k,
                    "last_failure": self._last_failure,
                    }
            error = NotEnoughSharesError
        log.msg(format=format,
                level=log.UNUSUAL, parent=self._lp, umid="1DsnTg",
                **args)
        e = error(format % args)
        f = Failure(e)
        self.stop()
        self._node.fetch_failed(self, f)

    def _find_and_use_share(self):
        sent_something = False
        want_more_diversity = False
        for sh in self._shares: # find one good share to fetch
            shnum = sh._shnum ; serverid = sh._server.get_serverid()
            if shnum in self._blocks:
                continue # don't request data we already have
            if shnum in self._active_share_map:
                # note: OVERDUE shares are removed from _active_share_map
                # and added to _overdue_share_map instead.
                continue # don't send redundant requests
            sfs = self._shares_from_server
            if len(sfs.get(serverid,set())) >= self._max_shares_per_server:
                # don't pull too much from a single server
                want_more_diversity = True
                continue
            # ok, we can use this share
            self._shares.remove(sh)
            self._active_share_map[shnum] = sh
            self._shares_from_server.add(serverid, sh)
            self._start_share(sh, shnum)
            sent_something = True
            break
        return (sent_something, want_more_diversity)

    def _start_share(self, share, shnum):
        self._share_observers[share] = o = share.get_block(self.segnum)
        o.subscribe(self._block_request_activity, share=share, shnum=shnum)

    def _ask_for_more_shares(self):
        if not self._no_more_shares:
            self._node.want_more_shares()
            # that will trigger the ShareFinder to keep looking, and call our
            # add_shares() or no_more_shares() later.

    def _cancel_all_requests(self):
        for o in self._share_observers.values():
            o.cancel()
        self._share_observers = {}

    def _block_request_activity(self, share, shnum, state, block=None, f=None):
        # called by Shares, in response to our s.send_request() calls.
        if not self._running:
            return
        log.msg("SegmentFetcher(%s)._block_request_activity: %s -> %s" %
                (self._node._si_prefix, repr(share), state),
                level=log.NOISY, parent=self._lp, umid="vilNWA")
        # COMPLETE, CORRUPT, DEAD, BADSEGNUM are terminal. Remove the share
        # from all our tracking lists.
        if state in (COMPLETE, CORRUPT, DEAD, BADSEGNUM):
            self._share_observers.pop(share, None)
            self._shares_from_server.discard(shnum, share)
            if self._active_share_map.get(shnum) is share:
                del self._active_share_map[shnum]
            self._overdue_share_map.discard(shnum, share)

        if state is COMPLETE:
            # 'block' is fully validated and complete
            self._blocks[shnum] = block

        if state is OVERDUE:
            # no longer active, but still might complete
            del self._active_share_map[shnum]
            self._overdue_share_map.add(shnum, share)
            # OVERDUE is not terminal: it will eventually transition to
            # COMPLETE, CORRUPT, or DEAD.

        if state is DEAD:
            self._last_failure = f
        if state is BADSEGNUM:
            # our main loop will ask the DownloadNode each time for the
            # number of segments, so we'll deal with this in the top of
            # _do_loop
            pass

        eventually(self.loop)
