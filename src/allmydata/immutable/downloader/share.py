
import struct
import time
now = time.time

from twisted.python.failure import Failure
from foolscap.api import eventually
from allmydata.util import base32, log, hashutil, mathutil
from allmydata.util.spans import Spans, DataSpans
from allmydata.interfaces import HASH_SIZE
from allmydata.hashtree import IncompleteHashTree, BadHashError, \
     NotEnoughHashesError

from allmydata.immutable.layout import make_write_bucket_proxy
from allmydata.util.observer import EventStreamObserver
from common import COMPLETE, CORRUPT, DEAD, BADSEGNUM


class LayoutInvalid(Exception):
    pass
class DataUnavailable(Exception):
    pass

class Share:
    """I represent a single instance of a single share (e.g. I reference the
    shnum2 for share SI=abcde on server xy12t, not the one on server ab45q).
    I am associated with a CommonShare that remembers data that is held in
    common among e.g. SI=abcde/shnum2 across all servers. I am also
    associated with a CiphertextFileNode for e.g. SI=abcde (all shares, all
    servers).
    """
    # this is a specific implementation of IShare for tahoe's native storage
    # servers. A different backend would use a different class.

    def __init__(self, rref, server_version, verifycap, commonshare, node,
                 download_status, peerid, shnum, logparent):
        self._rref = rref
        self._server_version = server_version
        self._node = node # holds share_hash_tree and UEB
        self.actual_segment_size = node.segment_size # might still be None
        # XXX change node.guessed_segment_size to
        # node.best_guess_segment_size(), which should give us the real ones
        # if known, else its guess.
        self._guess_offsets(verifycap, node.guessed_segment_size)
        self.actual_offsets = None
        self._UEB_length = None
        self._commonshare = commonshare # holds block_hash_tree
        self._download_status = download_status
        self._peerid = peerid
        self._peerid_s = base32.b2a(peerid)[:5]
        self._storage_index = verifycap.storage_index
        self._si_prefix = base32.b2a(verifycap.storage_index)[:8]
        self._shnum = shnum
        # self._alive becomes False upon fatal corruption or server error
        self._alive = True
        self._lp = log.msg(format="%(share)s created", share=repr(self),
                           level=log.NOISY, parent=logparent, umid="P7hv2w")

        self._pending = Spans() # request sent but no response received yet
        self._received = DataSpans() # ACK response received, with data
        self._unavailable = Spans() # NAK response received, no data

        # any given byte of the share can be in one of four states:
        #  in: _wanted, _requested, _received
        #      FALSE    FALSE       FALSE : don't care about it at all
        #      TRUE     FALSE       FALSE : want it, haven't yet asked for it
        #      TRUE     TRUE        FALSE : request is in-flight
        #                                   or didn't get it
        #      FALSE    TRUE        TRUE  : got it, haven't used it yet
        #      FALSE    TRUE        FALSE : got it and used it
        #      FALSE    FALSE       FALSE : block consumed, ready to ask again
        #
        # when we request data and get a NAK, we leave it in _requested
        # to remind ourself to not ask for it again. We don't explicitly
        # remove it from anything (maybe this should change).
        #
        # We retain the hashtrees in the Node, so we leave those spans in
        # _requested (and never ask for them again, as long as the Node is
        # alive). But we don't retain data blocks (too big), so when we
        # consume a data block, we remove it from _requested, so a later
        # download can re-fetch it.

        self._requested_blocks = [] # (segnum, set(observer2..))
        ver = server_version["http://allmydata.org/tahoe/protocols/storage/v1"]
        self._overrun_ok = ver["tolerates-immutable-read-overrun"]
        # If _overrun_ok and we guess the offsets correctly, we can get
        # everything in one RTT. If _overrun_ok and we guess wrong, we might
        # need two RTT (but we could get lucky and do it in one). If overrun
        # is *not* ok (tahoe-1.3.0 or earlier), we need four RTT: 1=version,
        # 2=offset table, 3=UEB_length and everything else (hashes, block),
        # 4=UEB.

        self.had_corruption = False # for unit tests

    def __repr__(self):
        return "Share(sh%d-on-%s)" % (self._shnum, self._peerid_s)

    def is_alive(self):
        # XXX: reconsider. If the share sees a single error, should it remain
        # dead for all time? Or should the next segment try again? This DEAD
        # state is stored elsewhere too (SegmentFetcher per-share states?)
        # and needs to be consistent. We clear _alive in self._fail(), which
        # is called upon a network error, or layout failure, or hash failure
        # in the UEB or a hash tree. We do not _fail() for a hash failure in
        # a block, but of course we still tell our callers about
        # state=CORRUPT so they'll find a different share.
        return self._alive

    def _guess_offsets(self, verifycap, guessed_segment_size):
        self.guessed_segment_size = guessed_segment_size
        size = verifycap.size
        k = verifycap.needed_shares
        N = verifycap.total_shares
        r = self._node._calculate_sizes(guessed_segment_size)
        # num_segments, block_size/tail_block_size
        # guessed_segment_size/tail_segment_size/tail_segment_padded
        share_size = mathutil.div_ceil(size, k)
        # share_size is the amount of block data that will be put into each
        # share, summed over all segments. It does not include hashes, the
        # UEB, or other overhead.

        # use the upload-side code to get this as accurate as possible
        ht = IncompleteHashTree(N)
        num_share_hashes = len(ht.needed_hashes(0, include_leaf=True))
        wbp = make_write_bucket_proxy(None, share_size, r["block_size"],
                                      r["num_segments"], num_share_hashes, 0,
                                      None)
        self._fieldsize = wbp.fieldsize
        self._fieldstruct = wbp.fieldstruct
        self.guessed_offsets = wbp._offsets

    # called by our client, the SegmentFetcher
    def get_block(self, segnum):
        """Add a block number to the list of requests. This will eventually
        result in a fetch of the data necessary to validate the block, then
        the block itself. The fetch order is generally
        first-come-first-served, but requests may be answered out-of-order if
        data becomes available sooner.

        I return an EventStreamObserver, which has two uses. The first is to
        call o.subscribe(), which gives me a place to send state changes and
        eventually the data block. The second is o.cancel(), which removes
        the request (if it is still active).

        I will distribute the following events through my EventStreamObserver:
         - state=OVERDUE: ?? I believe I should have had an answer by now.
                          You may want to ask another share instead.
         - state=BADSEGNUM: the segnum you asked for is too large. I must
                            fetch a valid UEB before I can determine this,
                            so the notification is asynchronous
         - state=COMPLETE, block=data: here is a valid block
         - state=CORRUPT: this share contains corrupted data
         - state=DEAD, f=Failure: the server reported an error, this share
                                  is unusable
        """
        log.msg("%s.get_block(%d)" % (repr(self), segnum),
                level=log.NOISY, parent=self._lp, umid="RTo9MQ")
        assert segnum >= 0
        o = EventStreamObserver()
        o.set_canceler(self, "_cancel_block_request")
        for i,(segnum0,observers) in enumerate(self._requested_blocks):
            if segnum0 == segnum:
                observers.add(o)
                break
        else:
            self._requested_blocks.append( (segnum, set([o])) )
        eventually(self.loop)
        return o

    def _cancel_block_request(self, o):
        new_requests = []
        for e in self._requested_blocks:
            (segnum0, observers) = e
            observers.discard(o)
            if observers:
                new_requests.append(e)
        self._requested_blocks = new_requests

    # internal methods
    def _active_segnum_and_observers(self):
        if self._requested_blocks:
            # we only retrieve information for one segment at a time, to
            # minimize alacrity (first come, first served)
            return self._requested_blocks[0]
        return None, []

    def loop(self):
        try:
            # if any exceptions occur here, kill the download
            log.msg("%s.loop, reqs=[%s], pending=%s, received=%s,"
                    " unavailable=%s" %
                    (repr(self),
                     ",".join([str(req[0]) for req in self._requested_blocks]),
                     self._pending.dump(), self._received.dump(),
                     self._unavailable.dump() ),
                    level=log.NOISY, parent=self._lp, umid="BaL1zw")
            self._do_loop()
            # all exception cases call self._fail(), which clears self._alive
        except (BadHashError, NotEnoughHashesError, LayoutInvalid), e:
            # Abandon this share. We do this if we see corruption in the
            # offset table, the UEB, or a hash tree. We don't abandon the
            # whole share if we see corruption in a data block (we abandon
            # just the one block, and still try to get data from other blocks
            # on the same server). In theory, we could get good data from a
            # share with a corrupt UEB (by first getting the UEB from some
            # other share), or corrupt hash trees, but the logic to decide
            # when this is safe is non-trivial. So for now, give up at the
            # first sign of corruption.
            #
            # _satisfy_*() code which detects corruption should first call
            # self._signal_corruption(), and then raise the exception.
            log.msg(format="corruption detected in %(share)s",
                    share=repr(self),
                    level=log.UNUSUAL, parent=self._lp, umid="gWspVw")
            self._fail(Failure(e), log.UNUSUAL)
        except DataUnavailable, e:
            # Abandon this share.
            log.msg(format="need data that will never be available"
                    " from %s: pending=%s, received=%s, unavailable=%s" %
                    (repr(self),
                     self._pending.dump(), self._received.dump(),
                     self._unavailable.dump() ),
                    level=log.UNUSUAL, parent=self._lp, umid="F7yJnQ")
            self._fail(Failure(e), log.UNUSUAL)
        except BaseException:
            self._fail(Failure())
            raise
        log.msg("%s.loop done, reqs=[%s], pending=%s, received=%s,"
                " unavailable=%s" %
                (repr(self),
                 ",".join([str(req[0]) for req in self._requested_blocks]),
                 self._pending.dump(), self._received.dump(),
                 self._unavailable.dump() ),
                level=log.NOISY, parent=self._lp, umid="9lRaRA")

    def _do_loop(self):
        # we are (eventually) called after all state transitions:
        #  new segments added to self._requested_blocks
        #  new data received from servers (responses to our read() calls)
        #  impatience timer fires (server appears slow)
        if not self._alive:
            return

        # First, consume all of the information that we currently have, for
        # all the segments people currently want.
        while self._get_satisfaction():
            pass

        # When we get no satisfaction (from the data we've received so far),
        # we determine what data we desire (to satisfy more requests). The
        # number of segments is finite, so I can't get no satisfaction
        # forever.
        wanted, needed = self._desire()

        # Finally, send out requests for whatever we need (desire minus
        # have). You can't always get what you want, but if you try
        # sometimes, you just might find, you get what you need.
        self._send_requests(wanted + needed)

        # and sometimes you can't even get what you need
        disappointment = needed & self._unavailable
        if disappointment.len():
            self.had_corruption = True
            raise DataUnavailable("need %s but will never get it" %
                                  disappointment.dump())

    def _get_satisfaction(self):
        # return True if we retired a data block, and should therefore be
        # called again. Return False if we don't retire a data block (even if
        # we do retire some other data, like hash chains).

        if self.actual_offsets is None:
            if not self._satisfy_offsets():
                # can't even look at anything without the offset table
                return False

        if not self._node.have_UEB:
            if not self._satisfy_UEB():
                # can't check any hashes without the UEB
                return False
        self.actual_segment_size = self._node.segment_size # might be updated
        assert self.actual_segment_size is not None

        # knowing the UEB means knowing num_segments. Despite the redundancy,
        # this is the best place to set this. CommonShare.set_numsegs will
        # ignore duplicate calls.
        assert self._node.num_segments is not None
        cs = self._commonshare
        cs.set_numsegs(self._node.num_segments)

        segnum, observers = self._active_segnum_and_observers()
        # if segnum is None, we don't really need to do anything (we have no
        # outstanding readers right now), but we'll fill in the bits that
        # aren't tied to any particular segment.

        if segnum is not None and segnum >= self._node.num_segments:
            for o in observers:
                o.notify(state=BADSEGNUM)
            self._requested_blocks.pop(0)
            return True

        if self._node.share_hash_tree.needed_hashes(self._shnum):
            if not self._satisfy_share_hash_tree():
                # can't check block_hash_tree without a root
                return False

        if cs.need_block_hash_root():
            block_hash_root = self._node.share_hash_tree.get_leaf(self._shnum)
            cs.set_block_hash_root(block_hash_root)

        if segnum is None:
            return False # we don't want any particular segment right now

        # block_hash_tree
        needed_hashes = self._commonshare.get_needed_block_hashes(segnum)
        if needed_hashes:
            if not self._satisfy_block_hash_tree(needed_hashes):
                # can't check block without block_hash_tree
                return False

        # ciphertext_hash_tree
        needed_hashes = self._node.get_needed_ciphertext_hashes(segnum)
        if needed_hashes:
            if not self._satisfy_ciphertext_hash_tree(needed_hashes):
                # can't check decoded blocks without ciphertext_hash_tree
                return False

        # data blocks
        return self._satisfy_data_block(segnum, observers)

    def _satisfy_offsets(self):
        version_s = self._received.get(0, 4)
        if version_s is None:
            return False
        (version,) = struct.unpack(">L", version_s)
        if version == 1:
            table_start = 0x0c
            self._fieldsize = 0x4
            self._fieldstruct = "L"
        elif version == 2:
            table_start = 0x14
            self._fieldsize = 0x8
            self._fieldstruct = "Q"
        else:
            self.had_corruption = True
            raise LayoutInvalid("unknown version %d (I understand 1 and 2)"
                                % version)
        offset_table_size = 6 * self._fieldsize
        table_s = self._received.pop(table_start, offset_table_size)
        if table_s is None:
            return False
        fields = struct.unpack(">"+6*self._fieldstruct, table_s)
        offsets = {}
        for i,field in enumerate(['data',
                                  'plaintext_hash_tree', # UNUSED
                                  'crypttext_hash_tree',
                                  'block_hashes',
                                  'share_hashes',
                                  'uri_extension',
                                  ] ):
            offsets[field] = fields[i]
        self.actual_offsets = offsets
        log.msg("actual offsets: data=%d, plaintext_hash_tree=%d, crypttext_hash_tree=%d, block_hashes=%d, share_hashes=%d, uri_extension=%d" % tuple(fields))
        self._received.remove(0, 4) # don't need this anymore

        # validate the offsets a bit
        share_hashes_size = offsets["uri_extension"] - offsets["share_hashes"]
        if share_hashes_size < 0 or share_hashes_size % (2+HASH_SIZE) != 0:
            # the share hash chain is stored as (hashnum,hash) pairs
            self.had_corruption = True
            raise LayoutInvalid("share hashes malformed -- should be a"
                                " multiple of %d bytes -- not %d" %
                                (2+HASH_SIZE, share_hashes_size))
        block_hashes_size = offsets["share_hashes"] - offsets["block_hashes"]
        if block_hashes_size < 0 or block_hashes_size % (HASH_SIZE) != 0:
            # the block hash tree is stored as a list of hashes
            self.had_corruption = True
            raise LayoutInvalid("block hashes malformed -- should be a"
                                " multiple of %d bytes -- not %d" %
                                (HASH_SIZE, block_hashes_size))
        # we only look at 'crypttext_hash_tree' if the UEB says we're
        # actually using it. Same with 'plaintext_hash_tree'. This gives us
        # some wiggle room: a place to stash data for later extensions.

        return True

    def _satisfy_UEB(self):
        o = self.actual_offsets
        fsize = self._fieldsize
        UEB_length_s = self._received.get(o["uri_extension"], fsize)
        if not UEB_length_s:
            return False
        (UEB_length,) = struct.unpack(">"+self._fieldstruct, UEB_length_s)
        UEB_s = self._received.pop(o["uri_extension"]+fsize, UEB_length)
        if not UEB_s:
            return False
        self._received.remove(o["uri_extension"], fsize)
        try:
            self._node.validate_and_store_UEB(UEB_s)
            return True
        except (LayoutInvalid, BadHashError), e:
            # TODO: if this UEB was bad, we'll keep trying to validate it
            # over and over again. Only log.err on the first one, or better
            # yet skip all but the first
            f = Failure(e)
            self._signal_corruption(f, o["uri_extension"], fsize+UEB_length)
            self.had_corruption = True
            raise

    def _satisfy_share_hash_tree(self):
        # the share hash chain is stored as (hashnum,hash) tuples, so you
        # can't fetch just the pieces you need, because you don't know
        # exactly where they are. So fetch everything, and parse the results
        # later.
        o = self.actual_offsets
        hashlen = o["uri_extension"] - o["share_hashes"]
        assert hashlen % (2+HASH_SIZE) == 0
        hashdata = self._received.get(o["share_hashes"], hashlen)
        if not hashdata:
            return False
        share_hashes = {}
        for i in range(0, hashlen, 2+HASH_SIZE):
            (hashnum,) = struct.unpack(">H", hashdata[i:i+2])
            hashvalue = hashdata[i+2:i+2+HASH_SIZE]
            share_hashes[hashnum] = hashvalue
        # TODO: if they give us an empty set of hashes,
        # process_share_hashes() won't fail. We must ensure that this
        # situation doesn't allow unverified shares through. Manual testing
        # shows that set_block_hash_root() throws an assert because an
        # internal node is None instead of an actual hash, but we want
        # something better. It's probably best to add a method to
        # IncompleteHashTree which takes a leaf number and raises an
        # exception unless that leaf is present and fully validated.
        try:
            self._node.process_share_hashes(share_hashes)
            # adds to self._node.share_hash_tree
        except (BadHashError, NotEnoughHashesError), e:
            f = Failure(e)
            self._signal_corruption(f, o["share_hashes"], hashlen)
            self.had_corruption = True
            raise
        self._received.remove(o["share_hashes"], hashlen)
        return True

    def _signal_corruption(self, f, start, offset):
        # there was corruption somewhere in the given range
        reason = "corruption in share[%d-%d): %s" % (start, start+offset,
                                                     str(f.value))
        self._rref.callRemoteOnly("advise_corrupt_share", reason)

    def _satisfy_block_hash_tree(self, needed_hashes):
        o_bh = self.actual_offsets["block_hashes"]
        block_hashes = {}
        for hashnum in needed_hashes:
            hashdata = self._received.get(o_bh+hashnum*HASH_SIZE, HASH_SIZE)
            if hashdata:
                block_hashes[hashnum] = hashdata
            else:
                return False # missing some hashes
        # note that we don't submit any hashes to the block_hash_tree until
        # we've gotten them all, because the hash tree will throw an
        # exception if we only give it a partial set (which it therefore
        # cannot validate)
        try:
            self._commonshare.process_block_hashes(block_hashes)
        except (BadHashError, NotEnoughHashesError), e:
            f = Failure(e)
            hashnums = ",".join([str(n) for n in sorted(block_hashes.keys())])
            log.msg(format="hash failure in block_hashes=(%(hashnums)s),"
                    " from %(share)s",
                    hashnums=hashnums, shnum=self._shnum, share=repr(self),
                    failure=f, level=log.WEIRD, parent=self._lp, umid="yNyFdA")
            hsize = max(0, max(needed_hashes)) * HASH_SIZE
            self._signal_corruption(f, o_bh, hsize)
            self.had_corruption = True
            raise
        for hashnum in needed_hashes:
            self._received.remove(o_bh+hashnum*HASH_SIZE, HASH_SIZE)
        return True

    def _satisfy_ciphertext_hash_tree(self, needed_hashes):
        start = self.actual_offsets["crypttext_hash_tree"]
        hashes = {}
        for hashnum in needed_hashes:
            hashdata = self._received.get(start+hashnum*HASH_SIZE, HASH_SIZE)
            if hashdata:
                hashes[hashnum] = hashdata
            else:
                return False # missing some hashes
        # we don't submit any hashes to the ciphertext_hash_tree until we've
        # gotten them all
        try:
            self._node.process_ciphertext_hashes(hashes)
        except (BadHashError, NotEnoughHashesError), e:
            f = Failure(e)
            hashnums = ",".join([str(n) for n in sorted(hashes.keys())])
            log.msg(format="hash failure in ciphertext_hashes=(%(hashnums)s),"
                    " from %(share)s",
                    hashnums=hashnums, share=repr(self), failure=f,
                    level=log.WEIRD, parent=self._lp, umid="iZI0TA")
            hsize = max(0, max(needed_hashes))*HASH_SIZE
            self._signal_corruption(f, start, hsize)
            self.had_corruption = True
            raise
        for hashnum in needed_hashes:
            self._received.remove(start+hashnum*HASH_SIZE, HASH_SIZE)
        return True

    def _satisfy_data_block(self, segnum, observers):
        tail = (segnum == self._node.num_segments-1)
        datastart = self.actual_offsets["data"]
        blockstart = datastart + segnum * self._node.block_size
        blocklen = self._node.block_size
        if tail:
            blocklen = self._node.tail_block_size

        block = self._received.pop(blockstart, blocklen)
        if not block:
            log.msg("no data for block %s (want [%d:+%d])" % (repr(self),
                                                              blockstart, blocklen))
            return False
        log.msg(format="%(share)s._satisfy_data_block [%(start)d:+%(length)d]",
                share=repr(self), start=blockstart, length=blocklen,
                level=log.NOISY, parent=self._lp, umid="uTDNZg")
        # this block is being retired, either as COMPLETE or CORRUPT, since
        # no further data reads will help
        assert self._requested_blocks[0][0] == segnum
        try:
            self._commonshare.check_block(segnum, block)
            # hurrah, we have a valid block. Deliver it.
            for o in observers:
                # goes to SegmentFetcher._block_request_activity
                o.notify(state=COMPLETE, block=block)
        except (BadHashError, NotEnoughHashesError), e:
            # rats, we have a corrupt block. Notify our clients that they
            # need to look elsewhere, and advise the server. Unlike
            # corruption in other parts of the share, this doesn't cause us
            # to abandon the whole share.
            f = Failure(e)
            log.msg(format="hash failure in block %(segnum)d, from %(share)s",
                    segnum=segnum, share=repr(self), failure=f,
                    level=log.WEIRD, parent=self._lp, umid="mZjkqA")
            for o in observers:
                o.notify(state=CORRUPT)
            self._signal_corruption(f, blockstart, blocklen)
            self.had_corruption = True
        # in either case, we've retired this block
        self._requested_blocks.pop(0)
        # popping the request keeps us from turning around and wanting the
        # block again right away
        return True # got satisfaction

    def _desire(self):
        segnum, observers = self._active_segnum_and_observers() # maybe None

        # 'want_it' is for data we merely want: we know that we don't really
        # need it. This includes speculative reads, like the first 1KB of the
        # share (for the offset table) and the first 2KB of the UEB.
        #
        # 'need_it' is for data that, if we have the real offset table, we'll
        # need. If we are only guessing at the offset table, it's merely
        # wanted. (The share is abandoned if we can't get data that we really
        # need).
        #
        # 'gotta_gotta_have_it' is for data that we absolutely need,
        # independent of whether we're still guessing about the offset table:
        # the version number and the offset table itself.
        #
        # Mr. Popeil, I'm in trouble, need your assistance on the double. Aww..

        desire = Spans(), Spans(), Spans()
        (want_it, need_it, gotta_gotta_have_it) = desire

        self.actual_segment_size = self._node.segment_size # might be updated
        o = self.actual_offsets or self.guessed_offsets
        segsize = self.actual_segment_size or self.guessed_segment_size
        r = self._node._calculate_sizes(segsize)

        if not self.actual_offsets:
            # all _desire functions add bits to the three desire[] spans
            self._desire_offsets(desire)

        # we can use guessed offsets as long as this server tolerates
        # overrun. Otherwise, we must wait for the offsets to arrive before
        # we try to read anything else.
        if self.actual_offsets or self._overrun_ok:
            if not self._node.have_UEB:
                self._desire_UEB(desire, o)
            # They might ask for a segment that doesn't look right.
            # _satisfy() will catch+reject bad segnums once we know the UEB
            # (and therefore segsize and numsegs), so we'll only fail this
            # test if we're still guessing. We want to avoid asking the
            # hashtrees for needed_hashes() for bad segnums. So don't enter
            # _desire_hashes or _desire_data unless the segnum looks
            # reasonable.
            if segnum < r["num_segments"]:
                # XXX somehow we're getting here for sh5. we don't yet know
                # the actual_segment_size, we're still working off the guess.
                # the ciphertext_hash_tree has been corrected, but the
                # commonshare._block_hash_tree is still in the guessed state.
                self._desire_share_hashes(desire, o)
                if segnum is not None:
                    self._desire_block_hashes(desire, o, segnum)
                    self._desire_data(desire, o, r, segnum, segsize)
            else:
                log.msg("_desire: segnum(%d) looks wrong (numsegs=%d)"
                        % (segnum, r["num_segments"]),
                        level=log.UNUSUAL, parent=self._lp, umid="tuYRQQ")

        log.msg("end _desire: want_it=%s need_it=%s gotta=%s"
                % (want_it.dump(), need_it.dump(), gotta_gotta_have_it.dump()))
        if self.actual_offsets:
            return (want_it, need_it+gotta_gotta_have_it)
        else:
            return (want_it+need_it, gotta_gotta_have_it)

    def _desire_offsets(self, desire):
        (want_it, need_it, gotta_gotta_have_it) = desire
        if self._overrun_ok:
            # easy! this includes version number, sizes, and offsets
            want_it.add(0, 1024)
            return

        # v1 has an offset table that lives [0x0,0x24). v2 lives [0x0,0x44).
        # To be conservative, only request the data that we know lives there,
        # even if that means more roundtrips.

        gotta_gotta_have_it.add(0, 4)  # version number, always safe
        version_s = self._received.get(0, 4)
        if not version_s:
            return
        (version,) = struct.unpack(">L", version_s)
        # The code in _satisfy_offsets will have checked this version
        # already. There is no code path to get this far with version>2.
        assert 1 <= version <= 2, "can't get here, version=%d" % version
        if version == 1:
            table_start = 0x0c
            fieldsize = 0x4
        elif version == 2:
            table_start = 0x14
            fieldsize = 0x8
        offset_table_size = 6 * fieldsize
        gotta_gotta_have_it.add(table_start, offset_table_size)

    def _desire_UEB(self, desire, o):
        (want_it, need_it, gotta_gotta_have_it) = desire

        # UEB data is stored as (length,data).
        if self._overrun_ok:
            # We can pre-fetch 2kb, which should probably cover it. If it
            # turns out to be larger, we'll come back here later with a known
            # length and fetch the rest.
            want_it.add(o["uri_extension"], 2048)
            # now, while that is probably enough to fetch the whole UEB, it
            # might not be, so we need to do the next few steps as well. In
            # most cases, the following steps will not actually add anything
            # to need_it

        need_it.add(o["uri_extension"], self._fieldsize)
        # only use a length if we're sure it's correct, otherwise we'll
        # probably fetch a huge number
        if not self.actual_offsets:
            return
        UEB_length_s = self._received.get(o["uri_extension"], self._fieldsize)
        if UEB_length_s:
            (UEB_length,) = struct.unpack(">"+self._fieldstruct, UEB_length_s)
            # we know the length, so make sure we grab everything
            need_it.add(o["uri_extension"]+self._fieldsize, UEB_length)

    def _desire_share_hashes(self, desire, o):
        (want_it, need_it, gotta_gotta_have_it) = desire

        if self._node.share_hash_tree.needed_hashes(self._shnum):
            hashlen = o["uri_extension"] - o["share_hashes"]
            need_it.add(o["share_hashes"], hashlen)

    def _desire_block_hashes(self, desire, o, segnum):
        (want_it, need_it, gotta_gotta_have_it) = desire

        # block hash chain
        for hashnum in self._commonshare.get_needed_block_hashes(segnum):
            need_it.add(o["block_hashes"]+hashnum*HASH_SIZE, HASH_SIZE)

        # ciphertext hash chain
        for hashnum in self._node.get_needed_ciphertext_hashes(segnum):
            need_it.add(o["crypttext_hash_tree"]+hashnum*HASH_SIZE, HASH_SIZE)

    def _desire_data(self, desire, o, r, segnum, segsize):
        (want_it, need_it, gotta_gotta_have_it) = desire
        tail = (segnum == r["num_segments"]-1)
        datastart = o["data"]
        blockstart = datastart + segnum * r["block_size"]
        blocklen = r["block_size"]
        if tail:
            blocklen = r["tail_block_size"]
        need_it.add(blockstart, blocklen)

    def _send_requests(self, desired):
        ask = desired - self._pending - self._received.get_spans()
        log.msg("%s._send_requests, desired=%s, pending=%s, ask=%s" %
                (repr(self), desired.dump(), self._pending.dump(), ask.dump()),
                level=log.NOISY, parent=self._lp, umid="E94CVA")
        # XXX At one time, this code distinguished between data blocks and
        # hashes, and made sure to send (small) requests for hashes before
        # sending (big) requests for blocks. The idea was to make sure that
        # all hashes arrive before the blocks, so the blocks can be consumed
        # and released in a single turn. I removed this for simplicity.
        # Reconsider the removal: maybe bring it back.
        ds = self._download_status

        for (start, length) in ask:
            # TODO: quantize to reasonably-large blocks
            self._pending.add(start, length)
            lp = log.msg(format="%(share)s._send_request"
                         " [%(start)d:+%(length)d]",
                         share=repr(self),
                         start=start, length=length,
                         level=log.NOISY, parent=self._lp, umid="sgVAyA")
            req_ev = ds.add_request_sent(self._peerid, self._shnum,
                                         start, length, now())
            d = self._send_request(start, length)
            d.addCallback(self._got_data, start, length, req_ev, lp)
            d.addErrback(self._got_error, start, length, req_ev, lp)
            d.addCallback(self._trigger_loop)
            d.addErrback(lambda f:
                         log.err(format="unhandled error during send_request",
                                 failure=f, parent=self._lp,
                                 level=log.WEIRD, umid="qZu0wg"))

    def _send_request(self, start, length):
        return self._rref.callRemote("read", start, length)

    def _got_data(self, data, start, length, req_ev, lp):
        req_ev.finished(len(data), now())
        if not self._alive:
            return
        log.msg(format="%(share)s._got_data [%(start)d:+%(length)d] -> %(datalen)d",
                share=repr(self), start=start, length=length, datalen=len(data),
                level=log.NOISY, parent=lp, umid="5Qn6VQ")
        self._pending.remove(start, length)
        self._received.add(start, data)

        # if we ask for [a:c], and we get back [a:b] (b<c), that means we're
        # never going to get [b:c]. If we really need that data, this block
        # will never complete. The easiest way to get into this situation is
        # to hit a share with a corrupted offset table, or one that's somehow
        # been truncated. On the other hand, when overrun_ok is true, we ask
        # for data beyond the end of the share all the time (it saves some
        # RTT when we don't know the length of the share ahead of time). So
        # not every asked-for-but-not-received byte is fatal.
        if len(data) < length:
            self._unavailable.add(start+len(data), length-len(data))

        # XXX if table corruption causes our sections to overlap, then one
        # consumer (i.e. block hash tree) will pop/remove the data that
        # another consumer (i.e. block data) mistakenly thinks it needs. It
        # won't ask for that data again, because the span is in
        # self._requested. But that span won't be in self._unavailable
        # because we got it back from the server. TODO: handle this properly
        # (raise DataUnavailable). Then add sanity-checking
        # no-overlaps-allowed tests to the offset-table unpacking code to
        # catch this earlier. XXX

        # accumulate a wanted/needed span (not as self._x, but passed into
        # desire* functions). manage a pending/in-flight list. when the
        # requests are sent out, empty/discard the wanted/needed span and
        # populate/augment the pending list. when the responses come back,
        # augment either received+data or unavailable.

        # if a corrupt offset table results in double-usage, we'll send
        # double requests.

        # the wanted/needed span is only "wanted" for the first pass. Once
        # the offset table arrives, it's all "needed".

    def _got_error(self, f, start, length, req_ev, lp):
        req_ev.finished("error", now())
        log.msg(format="error requesting %(start)d+%(length)d"
                " from %(server)s for si %(si)s",
                start=start, length=length,
                server=self._peerid_s, si=self._si_prefix,
                failure=f, parent=lp, level=log.UNUSUAL, umid="BZgAJw")
        # retire our observers, assuming we won't be able to make any
        # further progress
        self._fail(f, log.UNUSUAL)

    def _trigger_loop(self, res):
        if self._alive:
            eventually(self.loop)
        return res

    def _fail(self, f, level=log.WEIRD):
        log.msg(format="abandoning %(share)s",
                share=repr(self), failure=f,
                level=level, parent=self._lp, umid="JKM2Og")
        self._alive = False
        for (segnum, observers) in self._requested_blocks:
            for o in observers:
                o.notify(state=DEAD, f=f)


class CommonShare:
    """I hold data that is common across all instances of a single share,
    like sh2 on both servers A and B. This is just the block hash tree.
    """
    def __init__(self, guessed_numsegs, si_prefix, shnum, logparent):
        self.si_prefix = si_prefix
        self.shnum = shnum
        # in the beginning, before we have the real UEB, we can only guess at
        # the number of segments. But we want to ask for block hashes early.
        # So if we're asked for which block hashes are needed before we know
        # numsegs for sure, we return a guess.
        self._block_hash_tree = IncompleteHashTree(guessed_numsegs)
        self._know_numsegs = False
        self._logparent = logparent

    def set_numsegs(self, numsegs):
        if self._know_numsegs:
            return
        self._block_hash_tree = IncompleteHashTree(numsegs)
        self._know_numsegs = True

    def need_block_hash_root(self):
        return bool(not self._block_hash_tree[0])

    def set_block_hash_root(self, roothash):
        assert self._know_numsegs
        self._block_hash_tree.set_hashes({0: roothash})

    def get_needed_block_hashes(self, segnum):
        # XXX: include_leaf=True needs thought: how did the old downloader do
        # it? I think it grabbed *all* block hashes and set them all at once.
        # Since we want to fetch less data, we either need to fetch the leaf
        # too, or wait to set the block hashes until we've also received the
        # block itself, so we can hash it too, and set the chain+leaf all at
        # the same time.
        return self._block_hash_tree.needed_hashes(segnum, include_leaf=True)

    def process_block_hashes(self, block_hashes):
        assert self._know_numsegs
        # this may raise BadHashError or NotEnoughHashesError
        self._block_hash_tree.set_hashes(block_hashes)

    def check_block(self, segnum, block):
        assert self._know_numsegs
        h = hashutil.block_hash(block)
        # this may raise BadHashError or NotEnoughHashesError
        self._block_hash_tree.set_hashes(leaves={segnum: h})
