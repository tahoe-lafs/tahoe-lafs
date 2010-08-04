
from twisted.python.failure import Failure
from foolscap.api import eventually
from allmydata.interfaces import NotEnoughSharesError, NoSharesError
from allmydata.util import log
from allmydata.util.dictutil import DictOfSets
from common import AVAILABLE, PENDING, OVERDUE, COMPLETE, CORRUPT, DEAD, \
     BADSEGNUM, BadSegmentNumberError

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

    def __init__(self, node, segnum, k):
        self._node = node # _Node
        self.segnum = segnum
        self._k = k
        self._shares = {} # maps non-dead Share instance to a state, one of
                          # (AVAILABLE, PENDING, OVERDUE, COMPLETE, CORRUPT).
                          # State transition map is:
                          #  AVAILABLE -(send-read)-> PENDING
                          #  PENDING -(timer)-> OVERDUE
                          #  PENDING -(rx)-> COMPLETE, CORRUPT, DEAD, BADSEGNUM
                          #  OVERDUE -(rx)-> COMPLETE, CORRUPT, DEAD, BADSEGNUM
                          # If a share becomes DEAD, it is removed from the
                          # dict. If it becomes BADSEGNUM, the whole fetch is
                          # terminated.
        self._share_observers = {} # maps Share to EventStreamObserver for
                                   # active ones
        self._shnums = DictOfSets() # maps shnum to the shares that provide it
        self._blocks = {} # maps shnum to validated block data
        self._no_more_shares = False
        self._bad_segnum = False
        self._last_failure = None
        self._running = True

    def stop(self):
        log.msg("SegmentFetcher(%s).stop" % self._node._si_prefix,
                level=log.NOISY, umid="LWyqpg")
        self._cancel_all_requests()
        self._running = False
        self._shares.clear() # let GC work # ??? XXX


    # called by our parent _Node

    def add_shares(self, shares):
        # called when ShareFinder locates a new share, and when a non-initial
        # segment fetch is started and we already know about shares from the
        # previous segment
        for s in shares:
            self._shares[s] = AVAILABLE
            self._shnums.add(s._shnum, s)
        eventually(self.loop)

    def no_more_shares(self):
        # ShareFinder tells us it's reached the end of its list
        self._no_more_shares = True
        eventually(self.loop)

    # internal methods

    def _count_shnums(self, *states):
        """shnums for which at least one state is in the following list"""
        shnums = []
        for shnum,shares in self._shnums.iteritems():
            matches = [s for s in shares if self._shares.get(s) in states]
            if matches:
                shnums.append(shnum)
        return len(shnums)

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
        if self._bad_segnum:
            # oops, we were asking for a segment number beyond the end of the
            # file. This is an error.
            self.stop()
            e = BadSegmentNumberError("segnum=%d, numsegs=%d" %
                                      (self.segnum, self._node.num_segments))
            f = Failure(e)
            self._node.fetch_failed(self, f)
            return

        # are we done?
        if self._count_shnums(COMPLETE) >= k:
            # yay!
            self.stop()
            self._node.process_blocks(self.segnum, self._blocks)
            return

        # we may have exhausted everything
        if (self._no_more_shares and
            self._count_shnums(AVAILABLE, PENDING, OVERDUE, COMPLETE) < k):
            # no more new shares are coming, and the remaining hopeful shares
            # aren't going to be enough. boo!

            log.msg("share states: %r" % (self._shares,),
                    level=log.NOISY, umid="0ThykQ")
            if self._count_shnums(AVAILABLE, PENDING, OVERDUE, COMPLETE) == 0:
                format = ("no shares (need %(k)d)."
                          " Last failure: %(last_failure)s")
                args = { "k": k,
                         "last_failure": self._last_failure }
                error = NoSharesError
            else:
                format = ("ran out of shares: %(complete)d complete,"
                          " %(pending)d pending, %(overdue)d overdue,"
                          " %(unused)d unused, need %(k)d."
                          " Last failure: %(last_failure)s")
                args = {"complete": self._count_shnums(COMPLETE),
                        "pending": self._count_shnums(PENDING),
                        "overdue": self._count_shnums(OVERDUE),
                        # 'unused' should be zero
                        "unused": self._count_shnums(AVAILABLE),
                        "k": k,
                        "last_failure": self._last_failure,
                        }
                error = NotEnoughSharesError
            log.msg(format=format, level=log.UNUSUAL, umid="1DsnTg", **args)
            e = error(format % args)
            f = Failure(e)
            self.stop()
            self._node.fetch_failed(self, f)
            return

        # nope, not done. Are we "block-hungry" (i.e. do we want to send out
        # more read requests, or do we think we have enough in flight
        # already?)
        while self._count_shnums(PENDING, COMPLETE) < k:
            # we're hungry.. are there any unused shares?
            sent = self._send_new_request()
            if not sent:
                break

        # ok, now are we "share-hungry" (i.e. do we have enough known shares
        # to make us happy, or should we ask the ShareFinder to get us more?)
        if self._count_shnums(AVAILABLE, PENDING, COMPLETE) < k:
            # we're hungry for more shares
            self._node.want_more_shares()
            # that will trigger the ShareFinder to keep looking

    def _find_one(self, shares, state):
        # TODO could choose fastest, or avoid servers already in use
        for s in shares:
            if self._shares[s] == state:
                return s
        # can never get here, caller has assert in case of code bug

    def _send_new_request(self):
        # TODO: this is probably O(k^2), and we're called from a range(k)
        # loop, so O(k^3)

        # this first loop prefers sh0, then sh1, sh2, etc
        for shnum,shares in sorted(self._shnums.iteritems()):
            states = [self._shares[s] for s in shares]
            if COMPLETE in states or PENDING in states:
                # don't send redundant requests
                continue
            if AVAILABLE not in states:
                # no candidates for this shnum, move on
                continue
            # here's a candidate. Send a request.
            s = self._find_one(shares, AVAILABLE)
            assert s
            self._shares[s] = PENDING
            self._share_observers[s] = o = s.get_block(self.segnum)
            o.subscribe(self._block_request_activity, share=s, shnum=shnum)
            # TODO: build up a list of candidates, then walk through the
            # list, sending requests to the most desireable servers,
            # re-checking our block-hunger each time. For non-initial segment
            # fetches, this would let us stick with faster servers.
            return True
        # nothing was sent: don't call us again until you have more shares to
        # work with, or one of the existing shares has been declared OVERDUE
        return False

    def _cancel_all_requests(self):
        for o in self._share_observers.values():
            o.cancel()
        self._share_observers = {}

    def _block_request_activity(self, share, shnum, state, block=None, f=None):
        # called by Shares, in response to our s.send_request() calls.
        if not self._running:
            return
        log.msg("SegmentFetcher(%s)._block_request_activity:"
                " Share(sh%d-on-%s) -> %s" %
                (self._node._si_prefix, shnum, share._peerid_s, state),
                level=log.NOISY, umid="vilNWA")
        # COMPLETE, CORRUPT, DEAD, BADSEGNUM are terminal.
        if state in (COMPLETE, CORRUPT, DEAD, BADSEGNUM):
            self._share_observers.pop(share, None)
        if state is COMPLETE:
            # 'block' is fully validated
            self._shares[share] = COMPLETE
            self._blocks[shnum] = block
        elif state is OVERDUE:
            self._shares[share] = OVERDUE
            # OVERDUE is not terminal: it will eventually transition to
            # COMPLETE, CORRUPT, or DEAD.
        elif state is CORRUPT:
            self._shares[share] = CORRUPT
        elif state is DEAD:
            del self._shares[share]
            self._shnums[shnum].remove(share)
            self._last_failure = f
        elif state is BADSEGNUM:
            self._shares[share] = BADSEGNUM # ???
            self._bad_segnum = True
        eventually(self.loop)


