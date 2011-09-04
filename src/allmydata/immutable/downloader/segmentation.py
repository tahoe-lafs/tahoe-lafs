
import time
now = time.time
from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IPushProducer
from foolscap.api import eventually
from allmydata.util import log
from allmydata.util.spans import overlap
from allmydata.interfaces import DownloadStopped

from common import BadSegmentNumberError, WrongSegmentError

class Segmentation:
    """I am responsible for a single offset+size read of the file. I handle
    segmentation: I figure out which segments are necessary, request them
    (from my CiphertextDownloader) in order, and trim the segments down to
    match the offset+size span. I use the Producer/Consumer interface to only
    request one segment at a time.
    """
    implements(IPushProducer)
    def __init__(self, node, offset, size, consumer, read_ev, logparent=None):
        self._node = node
        self._hungry = True
        self._active_segnum = None
        self._cancel_segment_request = None
        # these are updated as we deliver data. At any given time, we still
        # want to download file[offset:offset+size]
        self._offset = offset
        self._size = size
        assert offset+size <= node._verifycap.size
        self._consumer = consumer
        self._read_ev = read_ev
        self._start_pause = None
        self._lp = logparent

    def start(self):
        self._alive = True
        self._deferred = defer.Deferred()
        self._deferred.addBoth(self._done)
        self._consumer.registerProducer(self, True)
        self._maybe_fetch_next()
        return self._deferred

    def _done(self, res):
        self._consumer.unregisterProducer()
        return res

    def _maybe_fetch_next(self):
        if not self._alive or not self._hungry:
            return
        if self._active_segnum is not None:
            return
        self._fetch_next()

    def _fetch_next(self):
        if self._size == 0:
            # done!
            self._alive = False
            self._hungry = False
            self._deferred.callback(self._consumer)
            return
        n = self._node
        have_actual_segment_size = n.segment_size is not None
        guess_s = ""
        if not have_actual_segment_size:
            guess_s = "probably "
        segment_size = n.segment_size or n.guessed_segment_size
        if self._offset == 0:
            # great! we want segment0 for sure
            wanted_segnum = 0
        else:
            # this might be a guess
            wanted_segnum = self._offset // segment_size
        log.msg(format="_fetch_next(offset=%(offset)d) %(guess)swants segnum=%(segnum)d",
                offset=self._offset, guess=guess_s, segnum=wanted_segnum,
                level=log.NOISY, parent=self._lp, umid="5WfN0w")
        self._active_segnum = wanted_segnum
        d,c = n.get_segment(wanted_segnum, self._lp)
        self._cancel_segment_request = c
        d.addBoth(self._request_retired)
        d.addCallback(self._got_segment, wanted_segnum)
        if not have_actual_segment_size:
            # we can retry once
            d.addErrback(self._retry_bad_segment)
        d.addErrback(self._error)

    def _request_retired(self, res):
        self._active_segnum = None
        self._cancel_segment_request = None
        return res

    def _got_segment(self, (segment_start,segment,decodetime), wanted_segnum):
        self._cancel_segment_request = None
        # we got file[segment_start:segment_start+len(segment)]
        # we want file[self._offset:self._offset+self._size]
        log.msg(format="Segmentation got data:"
                " want [%(wantstart)d-%(wantend)d),"
                " given [%(segstart)d-%(segend)d), for segnum=%(segnum)d",
                wantstart=self._offset, wantend=self._offset+self._size,
                segstart=segment_start, segend=segment_start+len(segment),
                segnum=wanted_segnum,
                level=log.OPERATIONAL, parent=self._lp, umid="32dHcg")

        o = overlap(segment_start, len(segment),  self._offset, self._size)
        # the overlap is file[o[0]:o[0]+o[1]]
        if not o or o[0] != self._offset:
            # we didn't get the first byte, so we can't use this segment
            log.msg("Segmentation handed wrong data:"
                    " want [%d-%d), given [%d-%d), for segnum=%d,"
                    " for si=%s"
                    % (self._offset, self._offset+self._size,
                       segment_start, segment_start+len(segment),
                       wanted_segnum, self._node._si_prefix),
                    level=log.UNUSUAL, parent=self._lp, umid="STlIiA")
            # we may retry if the segnum we asked was based on a guess
            raise WrongSegmentError("I was given the wrong data.")
        offset_in_segment = self._offset - segment_start
        desired_data = segment[offset_in_segment:offset_in_segment+o[1]]

        self._offset += len(desired_data)
        self._size -= len(desired_data)
        self._consumer.write(desired_data)
        # the consumer might call our .pauseProducing() inside that write()
        # call, setting self._hungry=False
        self._read_ev.update(len(desired_data), 0, 0)
        # note: filenode.DecryptingConsumer is responsible for calling
        # _read_ev.update with how much decrypt_time was consumed
        self._maybe_fetch_next()

    def _retry_bad_segment(self, f):
        f.trap(WrongSegmentError, BadSegmentNumberError)
        # we guessed the segnum wrong: either one that doesn't overlap with
        # the start of our desired region, or one that's beyond the end of
        # the world. Now that we have the right information, we're allowed to
        # retry once.
        assert self._node.segment_size is not None
        return self._maybe_fetch_next()

    def _error(self, f):
        log.msg("Error in Segmentation", failure=f,
                level=log.WEIRD, parent=self._lp, umid="EYlXBg")
        self._alive = False
        self._hungry = False
        self._deferred.errback(f)

    def stopProducing(self):
        log.msg("asked to stopProducing",
                level=log.NOISY, parent=self._lp, umid="XIyL9w")
        self._hungry = False
        self._alive = False
        # cancel any outstanding segment request
        if self._cancel_segment_request:
            self._cancel_segment_request.cancel()
            self._cancel_segment_request = None
        e = DownloadStopped("our Consumer called stopProducing()")
        self._deferred.errback(e)

    def pauseProducing(self):
        self._hungry = False
        self._start_pause = now()
    def resumeProducing(self):
        self._hungry = True
        eventually(self._maybe_fetch_next)
        if self._start_pause is not None:
            paused = now() - self._start_pause
            self._read_ev.update(0, 0, paused)
            self._start_pause = None
