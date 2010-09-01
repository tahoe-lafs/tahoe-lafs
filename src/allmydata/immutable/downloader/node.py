
import time
now = time.time
from twisted.python.failure import Failure
from twisted.internet import defer
from foolscap.api import eventually
from allmydata import uri
from allmydata.codec import CRSDecoder
from allmydata.util import base32, log, hashutil, mathutil, observer
from allmydata.interfaces import DEFAULT_MAX_SEGMENT_SIZE
from allmydata.hashtree import IncompleteHashTree, BadHashError, \
     NotEnoughHashesError

# local imports
from finder import ShareFinder
from fetcher import SegmentFetcher
from segmentation import Segmentation
from common import BadCiphertextHashError

class Cancel:
    def __init__(self, f):
        self._f = f
        self.active = True
    def cancel(self):
        if self.active:
            self.active = False
            self._f(self)

class DownloadNode:
    """Internal class which manages downloads and holds state. External
    callers use CiphertextFileNode instead."""

    # Share._node points to me
    def __init__(self, verifycap, storage_broker, secret_holder,
                 terminator, history, download_status):
        assert isinstance(verifycap, uri.CHKFileVerifierURI)
        self._verifycap = verifycap
        self._storage_broker = storage_broker
        self._si_prefix = base32.b2a_l(verifycap.storage_index[:8], 60)
        self.running = True
        if terminator:
            terminator.register(self) # calls self.stop() at stopService()
        # the rules are:
        # 1: Only send network requests if you're active (self.running is True)
        # 2: Use TimerService, not reactor.callLater
        # 3: You can do eventual-sends any time.
        # These rules should mean that once
        # stopService()+flushEventualQueue() fires, everything will be done.
        self._secret_holder = secret_holder
        self._history = history
        self._download_status = download_status

        k, N = self._verifycap.needed_shares, self._verifycap.total_shares
        self.share_hash_tree = IncompleteHashTree(N)

        # we guess the segment size, so Segmentation can pull non-initial
        # segments in a single roundtrip. This populates
        # .guessed_segment_size, .guessed_num_segments, and
        # .ciphertext_hash_tree (with a dummy, to let us guess which hashes
        # we'll need)
        self._build_guessed_tables(DEFAULT_MAX_SEGMENT_SIZE)

        # filled in when we parse a valid UEB
        self.have_UEB = False
        self.segment_size = None
        self.tail_segment_size = None
        self.tail_segment_padded = None
        self.num_segments = None
        self.block_size = None
        self.tail_block_size = None

        # things to track callers that want data

        # _segment_requests can have duplicates
        self._segment_requests = [] # (segnum, d, cancel_handle, logparent)
        self._active_segment = None # a SegmentFetcher, with .segnum

        self._segsize_observers = observer.OneShotObserverList()

        # we create one top-level logparent for this _Node, and another one
        # for each read() call. Segmentation and get_segment() messages are
        # associated with the read() call, everything else is tied to the
        # _Node's log entry.
        lp = log.msg(format="Immutable.DownloadNode(%(si)s) created:"
                     " size=%(size)d,"
                     " guessed_segsize=%(guessed_segsize)d,"
                     " guessed_numsegs=%(guessed_numsegs)d",
                     si=self._si_prefix, size=verifycap.size,
                     guessed_segsize=self.guessed_segment_size,
                     guessed_numsegs=self.guessed_num_segments,
                     level=log.OPERATIONAL, umid="uJ0zAQ")
        self._lp = lp

        self._sharefinder = ShareFinder(storage_broker, verifycap, self,
                                        self._download_status, lp)
        self._shares = set()

    def _build_guessed_tables(self, max_segment_size):
        size = min(self._verifycap.size, max_segment_size)
        s = mathutil.next_multiple(size, self._verifycap.needed_shares)
        self.guessed_segment_size = s
        r = self._calculate_sizes(self.guessed_segment_size)
        self.guessed_num_segments = r["num_segments"]
        # as with CommonShare, our ciphertext_hash_tree is a stub until we
        # get the real num_segments
        self.ciphertext_hash_tree = IncompleteHashTree(self.guessed_num_segments)
        self.ciphertext_hash_tree_leaves = self.guessed_num_segments

    def __repr__(self):
        return "ImmutableDownloadNode(%s)" % (self._si_prefix,)

    def stop(self):
        # called by the Terminator at shutdown, mostly for tests
        if self._active_segment:
            self._active_segment.stop()
            self._active_segment = None
        self._sharefinder.stop()

    # things called by outside callers, via CiphertextFileNode. get_segment()
    # may also be called by Segmentation.

    def read(self, consumer, offset=0, size=None, read_ev=None):
        """I am the main entry point, from which FileNode.read() can get
        data. I feed the consumer with the desired range of ciphertext. I
        return a Deferred that fires (with the consumer) when the read is
        finished.

        Note that there is no notion of a 'file pointer': each call to read()
        uses an independent offset= value."""
        # for concurrent operations: each gets its own Segmentation manager
        if size is None:
            size = self._verifycap.size
        # clip size so offset+size does not go past EOF
        size = min(size, self._verifycap.size-offset)
        if read_ev is None:
            read_ev = self._download_status.add_read_event(offset, size, now())

        lp = log.msg(format="imm Node(%(si)s).read(%(offset)d, %(size)d)",
                     si=base32.b2a(self._verifycap.storage_index)[:8],
                     offset=offset, size=size,
                     level=log.OPERATIONAL, parent=self._lp, umid="l3j3Ww")
        if self._history:
            sp = self._history.stats_provider
            sp.count("downloader.files_downloaded", 1) # really read() calls
            sp.count("downloader.bytes_downloaded", size)
        s = Segmentation(self, offset, size, consumer, read_ev, lp)
        # this raises an interesting question: what segments to fetch? if
        # offset=0, always fetch the first segment, and then allow
        # Segmentation to be responsible for pulling the subsequent ones if
        # the first wasn't large enough. If offset>0, we're going to need an
        # extra roundtrip to get the UEB (and therefore the segment size)
        # before we can figure out which segment to get. TODO: allow the
        # offset-table-guessing code (which starts by guessing the segsize)
        # to assist the offset>0 process.
        d = s.start()
        def _done(res):
            read_ev.finished(now())
            return res
        d.addBoth(_done)
        return d

    def get_segment(self, segnum, logparent=None):
        """Begin downloading a segment. I return a tuple (d, c): 'd' is a
        Deferred that fires with (offset,data) when the desired segment is
        available, and c is an object on which c.cancel() can be called to
        disavow interest in the segment (after which 'd' will never fire).

        You probably need to know the segment size before calling this,
        unless you want the first few bytes of the file. If you ask for a
        segment number which turns out to be too large, the Deferred will
        errback with BadSegmentNumberError.

        The Deferred fires with the offset of the first byte of the data
        segment, so that you can call get_segment() before knowing the
        segment size, and still know which data you received.

        The Deferred can also errback with other fatal problems, such as
        NotEnoughSharesError, NoSharesError, or BadCiphertextHashError.
        """
        lp = log.msg(format="imm Node(%(si)s).get_segment(%(segnum)d)",
                     si=base32.b2a(self._verifycap.storage_index)[:8],
                     segnum=segnum,
                     level=log.OPERATIONAL, parent=logparent, umid="UKFjDQ")
        self._download_status.add_segment_request(segnum, now())
        d = defer.Deferred()
        c = Cancel(self._cancel_request)
        self._segment_requests.append( (segnum, d, c, lp) )
        self._start_new_segment()
        return (d, c)

    def get_segsize(self):
        """Return a Deferred that fires when we know the real segment size."""
        if self.segment_size:
            return defer.succeed(self.segment_size)
        # TODO: this downloads (and discards) the first segment of the file.
        # We could make this more efficient by writing
        # fetcher.SegmentSizeFetcher, with the job of finding a single valid
        # share and extracting the UEB. We'd add Share.get_UEB() to request
        # just the UEB.
        (d,c) = self.get_segment(0)
        # this ensures that an error during get_segment() will errback the
        # caller, so Repair won't wait forever on completely missing files
        d.addCallback(lambda ign: self._segsize_observers.when_fired())
        return d

    # things called by the Segmentation object used to transform
    # arbitrary-sized read() calls into quantized segment fetches

    def _start_new_segment(self):
        if self._active_segment is None and self._segment_requests:
            segnum = self._segment_requests[0][0]
            k = self._verifycap.needed_shares
            lp = self._segment_requests[0][3]
            log.msg(format="%(node)s._start_new_segment: segnum=%(segnum)d",
                    node=repr(self), segnum=segnum,
                    level=log.NOISY, parent=lp, umid="wAlnHQ")
            self._active_segment = fetcher = SegmentFetcher(self, segnum, k, lp)
            active_shares = [s for s in self._shares if s.is_alive()]
            fetcher.add_shares(active_shares) # this triggers the loop


    # called by our child ShareFinder
    def got_shares(self, shares):
        self._shares.update(shares)
        if self._active_segment:
            self._active_segment.add_shares(shares)
    def no_more_shares(self):
        self._no_more_shares = True
        if self._active_segment:
            self._active_segment.no_more_shares()

    # things called by our Share instances

    def validate_and_store_UEB(self, UEB_s):
        log.msg("validate_and_store_UEB",
                level=log.OPERATIONAL, parent=self._lp, umid="7sTrPw")
        h = hashutil.uri_extension_hash(UEB_s)
        if h != self._verifycap.uri_extension_hash:
            raise BadHashError
        self._parse_and_store_UEB(UEB_s) # sets self._stuff
        # TODO: a malformed (but authentic) UEB could throw an assertion in
        # _parse_and_store_UEB, and we should abandon the download.
        self.have_UEB = True

        # inform the ShareFinder about our correct number of segments. This
        # will update the block-hash-trees in all existing CommonShare
        # instances, and will populate new ones with the correct value.
        self._sharefinder.update_num_segments()

    def _parse_and_store_UEB(self, UEB_s):
        # Note: the UEB contains needed_shares and total_shares. These are
        # redundant and inferior (the filecap contains the authoritative
        # values). However, because it is possible to encode the same file in
        # multiple ways, and the encoders might choose (poorly) to use the
        # same key for both (therefore getting the same SI), we might
        # encounter shares for both types. The UEB hashes will be different,
        # however, and we'll disregard the "other" encoding's shares as
        # corrupted.

        # therefore, we ignore d['total_shares'] and d['needed_shares'].

        d = uri.unpack_extension(UEB_s)

        log.msg(format="UEB=%(ueb)s, vcap=%(vcap)s",
                ueb=repr(uri.unpack_extension_readable(UEB_s)),
                vcap=self._verifycap.to_string(),
                level=log.NOISY, parent=self._lp, umid="cVqZnA")

        k, N = self._verifycap.needed_shares, self._verifycap.total_shares

        self.segment_size = d['segment_size']
        self._segsize_observers.fire(self.segment_size)

        r = self._calculate_sizes(self.segment_size)
        self.tail_segment_size = r["tail_segment_size"]
        self.tail_segment_padded = r["tail_segment_padded"]
        self.num_segments = r["num_segments"]
        self.block_size = r["block_size"]
        self.tail_block_size = r["tail_block_size"]
        log.msg("actual sizes: %s" % (r,),
                level=log.NOISY, parent=self._lp, umid="PY6P5Q")
        if (self.segment_size == self.guessed_segment_size
            and self.num_segments == self.guessed_num_segments):
            log.msg("my guess was right!",
                    level=log.NOISY, parent=self._lp, umid="x340Ow")
        else:
            log.msg("my guess was wrong! Extra round trips for me.",
                    level=log.NOISY, parent=self._lp, umid="tb7RJw")

        # zfec.Decode() instantiation is fast, but still, let's use the same
        # codec instance for all but the last segment. 3-of-10 takes 15us on
        # my laptop, 25-of-100 is 900us, 3-of-255 is 97us, 25-of-255 is
        # 2.5ms, worst-case 254-of-255 is 9.3ms
        self._codec = CRSDecoder()
        self._codec.set_params(self.segment_size, k, N)


        # Ciphertext hash tree root is mandatory, so that there is at most
        # one ciphertext that matches this read-cap or verify-cap. The
        # integrity check on the shares is not sufficient to prevent the
        # original encoder from creating some shares of file A and other
        # shares of file B. self.ciphertext_hash_tree was a guess before:
        # this is where we create it for real.
        self.ciphertext_hash_tree = IncompleteHashTree(self.num_segments)
        self.ciphertext_hash_tree_leaves = self.num_segments
        self.ciphertext_hash_tree.set_hashes({0: d['crypttext_root_hash']})

        self.share_hash_tree.set_hashes({0: d['share_root_hash']})

        # Our job is a fast download, not verification, so we ignore any
        # redundant fields. The Verifier uses a different code path which
        # does not ignore them.

    def _calculate_sizes(self, segment_size):
        # segments of ciphertext
        size = self._verifycap.size
        k = self._verifycap.needed_shares

        # this assert matches the one in encode.py:127 inside
        # Encoded._got_all_encoding_parameters, where the UEB is constructed
        assert segment_size % k == 0

        # the last segment is usually short. We don't store a whole segsize,
        # but we do pad the segment up to a multiple of k, because the
        # encoder requires that.
        tail_segment_size = size % segment_size
        if tail_segment_size == 0:
            tail_segment_size = segment_size
        padded = mathutil.next_multiple(tail_segment_size, k)
        tail_segment_padded = padded

        num_segments = mathutil.div_ceil(size, segment_size)

        # each segment is turned into N blocks. All but the last are of size
        # block_size, and the last is of size tail_block_size
        block_size = segment_size / k
        tail_block_size = tail_segment_padded / k

        return { "tail_segment_size": tail_segment_size,
                 "tail_segment_padded": tail_segment_padded,
                 "num_segments": num_segments,
                 "block_size": block_size,
                 "tail_block_size": tail_block_size,
                 }


    def process_share_hashes(self, share_hashes):
        for hashnum in share_hashes:
            if hashnum >= len(self.share_hash_tree):
                # "BadHashError" is normally for e.g. a corrupt block. We
                # sort of abuse it here to mean a badly numbered hash (which
                # indicates corruption in the number bytes, rather than in
                # the data bytes).
                raise BadHashError("hashnum %d doesn't fit in hashtree(%d)"
                                   % (hashnum, len(self.share_hash_tree)))
        self.share_hash_tree.set_hashes(share_hashes)

    def get_desired_ciphertext_hashes(self, segnum):
        if segnum < self.ciphertext_hash_tree_leaves:
            return self.ciphertext_hash_tree.needed_hashes(segnum,
                                                           include_leaf=True)
        return []
    def get_needed_ciphertext_hashes(self, segnum):
        cht = self.ciphertext_hash_tree
        return cht.needed_hashes(segnum, include_leaf=True)

    def process_ciphertext_hashes(self, hashes):
        assert self.num_segments is not None
        # this may raise BadHashError or NotEnoughHashesError
        self.ciphertext_hash_tree.set_hashes(hashes)


    # called by our child SegmentFetcher

    def want_more_shares(self):
        self._sharefinder.hungry()

    def fetch_failed(self, sf, f):
        assert sf is self._active_segment
        # deliver error upwards
        for (d,c) in self._extract_requests(sf.segnum):
            eventually(self._deliver, d, c, f)
        self._active_segment = None
        self._start_new_segment()

    def process_blocks(self, segnum, blocks):
        d = defer.maybeDeferred(self._decode_blocks, segnum, blocks)
        d.addCallback(self._check_ciphertext_hash, segnum)
        def _deliver(result):
            ds = self._download_status
            if isinstance(result, Failure):
                ds.add_segment_error(segnum, now())
            else:
                (offset, segment, decodetime) = result
                ds.add_segment_delivery(segnum, now(),
                                        offset, len(segment), decodetime)
            log.msg(format="delivering segment(%(segnum)d)",
                    segnum=segnum,
                    level=log.OPERATIONAL, parent=self._lp,
                    umid="j60Ojg")
            for (d,c) in self._extract_requests(segnum):
                eventually(self._deliver, d, c, result)
            self._active_segment = None
            self._start_new_segment()
        d.addBoth(_deliver)
        d.addErrback(lambda f:
                     log.err("unhandled error during process_blocks",
                             failure=f, level=log.WEIRD,
                             parent=self._lp, umid="MkEsCg"))

    def _decode_blocks(self, segnum, blocks):
        tail = (segnum == self.num_segments-1)
        codec = self._codec
        block_size = self.block_size
        decoded_size = self.segment_size
        if tail:
            # account for the padding in the last segment
            codec = CRSDecoder()
            k, N = self._verifycap.needed_shares, self._verifycap.total_shares
            codec.set_params(self.tail_segment_padded, k, N)
            block_size = self.tail_block_size
            decoded_size = self.tail_segment_padded

        shares = []
        shareids = []
        for (shareid, share) in blocks.iteritems():
            assert len(share) == block_size
            shareids.append(shareid)
            shares.append(share)
        del blocks

        start = now()
        d = codec.decode(shares, shareids)   # segment
        del shares
        def _process(buffers):
            decodetime = now() - start
            segment = "".join(buffers)
            assert len(segment) == decoded_size
            del buffers
            if tail:
                segment = segment[:self.tail_segment_size]
            return (segment, decodetime)
        d.addCallback(_process)
        return d

    def _check_ciphertext_hash(self, (segment, decodetime), segnum):
        assert self._active_segment.segnum == segnum
        assert self.segment_size is not None
        offset = segnum * self.segment_size

        h = hashutil.crypttext_segment_hash(segment)
        try:
            self.ciphertext_hash_tree.set_hashes(leaves={segnum: h})
            return (offset, segment, decodetime)
        except (BadHashError, NotEnoughHashesError):
            format = ("hash failure in ciphertext_hash_tree:"
                      " segnum=%(segnum)d, SI=%(si)s")
            log.msg(format=format, segnum=segnum, si=self._si_prefix,
                    failure=Failure(),
                    level=log.WEIRD, parent=self._lp, umid="MTwNnw")
            # this is especially weird, because we made it past the share
            # hash tree. It implies that we're using the wrong encoding, or
            # that the uploader deliberately constructed a bad UEB.
            msg = format % {"segnum": segnum, "si": self._si_prefix}
            raise BadCiphertextHashError(msg)

    def _deliver(self, d, c, result):
        # this method exists to handle cancel() that occurs between
        # _got_segment and _deliver
        if c.active:
            c.active = False # it is now too late to cancel
            d.callback(result) # might actually be an errback

    def _extract_requests(self, segnum):
        """Remove matching requests and return their (d,c) tuples so that the
        caller can retire them."""
        retire = [(d,c) for (segnum0, d, c, lp) in self._segment_requests
                  if segnum0 == segnum]
        self._segment_requests = [t for t in self._segment_requests
                                  if t[0] != segnum]
        return retire

    def _cancel_request(self, c):
        self._segment_requests = [t for t in self._segment_requests
                                  if t[2] != c]
        segnums = [segnum for (segnum,d,c,lp) in self._segment_requests]
        # self._active_segment might be None in rare circumstances, so make
        # sure we tolerate it
        if self._active_segment and self._active_segment.segnum not in segnums:
            self._active_segment.stop()
            self._active_segment = None
            self._start_new_segment()

    # called by ShareFinder to choose hashtree sizes in CommonShares, and by
    # SegmentFetcher to tell if it is still fetching a valid segnum.
    def get_num_segments(self):
        # returns (best_num_segments, authoritative)
        if self.num_segments is None:
            return (self.guessed_num_segments, False)
        return (self.num_segments, True)
