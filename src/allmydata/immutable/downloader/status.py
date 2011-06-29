
import itertools
from zope.interface import implements
from allmydata.interfaces import IDownloadStatus

class ReadEvent:
    def __init__(self, ev, ds):
        self._ev = ev
        self._ds = ds
    def update(self, bytes, decrypttime, pausetime):
        self._ev["bytes_returned"] += bytes
        self._ev["decrypt_time"] += decrypttime
        self._ev["paused_time"] += pausetime
    def finished(self, finishtime):
        self._ev["finish_time"] = finishtime
        self._ds.update_last_timestamp(finishtime)

class SegmentEvent:
    def __init__(self, ev, ds):
        self._ev = ev
        self._ds = ds
    def activate(self, when):
        if self._ev["active_time"] is None:
            self._ev["active_time"] = when
    def deliver(self, when, start, length, decodetime):
        assert self._ev["active_time"] is not None
        self._ev["finish_time"] = when
        self._ev["success"] = True
        self._ev["decode_time"] = decodetime
        self._ev["segment_start"] = start
        self._ev["segment_length"] = length
        self._ds.update_last_timestamp(when)
    def error(self, when):
        self._ev["finish_time"] = when
        self._ev["success"] = False
        self._ds.update_last_timestamp(when)

class DYHBEvent:
    def __init__(self, ev, ds):
        self._ev = ev
        self._ds = ds
    def error(self, when):
        self._ev["finish_time"] = when
        self._ev["success"] = False
        self._ds.update_last_timestamp(when)
    def finished(self, shnums, when):
        self._ev["finish_time"] = when
        self._ev["success"] = True
        self._ev["response_shnums"] = shnums
        self._ds.update_last_timestamp(when)

class BlockRequestEvent:
    def __init__(self, ev, ds):
        self._ev = ev
        self._ds = ds
    def finished(self, received, when):
        self._ev["finish_time"] = when
        self._ev["success"] = True
        self._ev["response_length"] = received
        self._ds.update_last_timestamp(when)
    def error(self, when):
        self._ev["finish_time"] = when
        self._ev["success"] = False
        self._ds.update_last_timestamp(when)


class DownloadStatus:
    # There is one DownloadStatus for each CiphertextFileNode. The status
    # object will keep track of all activity for that node.
    implements(IDownloadStatus)
    statusid_counter = itertools.count(0)

    def __init__(self, storage_index, size):
        self.storage_index = storage_index
        self.size = size
        self.counter = self.statusid_counter.next()
        self.helper = False

        self.first_timestamp = None
        self.last_timestamp = None

        # all four of these _events lists are sorted by start_time, because
        # they are strictly append-only (some elements are later mutated in
        # place, but none are removed or inserted in the middle).

        # self.read_events tracks read() requests. It is a list of dicts,
        # each with the following keys:
        #  start,length  (of data requested)
        #  start_time
        #  finish_time (None until finished)
        #  bytes_returned (starts at 0, grows as segments are delivered)
        #  decrypt_time (time spent in decrypt, None for ciphertext-only reads)
        #  paused_time (time spent paused by client via pauseProducing)
        self.read_events = []

        # self.segment_events tracks segment requests and their resolution.
        # It is a list of dicts:
        #  segment_number
        #  start_time
        #  active_time (None until work has begun)
        #  decode_time (time spent in decode, None until delievered)
        #  finish_time (None until resolved)
        #  success (None until resolved, then boolean)
        #  segment_start (file offset of first byte, None until delivered)
        #  segment_length (None until delivered)
        self.segment_events = []

        # self.dyhb_requests tracks "do you have a share" requests and
        # responses. It is a list of dicts:
        #  serverid (binary)
        #  start_time
        #  success (None until resolved, then boolean)
        #  response_shnums (tuple, None until successful)
        #  finish_time (None until resolved)
        self.dyhb_requests = []

        # self.block_requests tracks share-data requests and responses. It is
        # a list of dicts:
        #  serverid (binary),
        #  shnum,
        #  start,length,  (of data requested)
        #  start_time
        #  finish_time (None until resolved)
        #  success (None until resolved, then bool)
        #  response_length (None until success)
        self.block_requests = []

        self.known_shares = [] # (serverid, shnum)
        self.problems = []


    def add_read_event(self, start, length, when):
        if self.first_timestamp is None:
            self.first_timestamp = when
        r = { "start": start,
              "length": length,
              "start_time": when,
              "finish_time": None,
              "bytes_returned": 0,
              "decrypt_time": 0,
              "paused_time": 0,
              }
        self.read_events.append(r)
        return ReadEvent(r, self)

    def add_segment_request(self, segnum, when):
        if self.first_timestamp is None:
            self.first_timestamp = when
        r = { "segment_number": segnum,
              "start_time": when,
              "active_time": None,
              "finish_time": None,
              "success": None,
              "decode_time": None,
              "segment_start": None,
              "segment_length": None,
              }
        self.segment_events.append(r)
        return SegmentEvent(r, self)

    def add_dyhb_request(self, serverid, when):
        r = { "serverid": serverid,
              "start_time": when,
              "success": None,
              "response_shnums": None,
              "finish_time": None,
              }
        self.dyhb_requests.append(r)
        return DYHBEvent(r, self)

    def add_block_request(self, serverid, shnum, start, length, when):
        r = { "serverid": serverid,
              "shnum": shnum,
              "start": start,
              "length": length,
              "start_time": when,
              "finish_time": None,
              "success": None,
              "response_length": None,
              }
        self.block_requests.append(r)
        return BlockRequestEvent(r, self)

    def update_last_timestamp(self, when):
        if self.last_timestamp is None or when > self.last_timestamp:
            self.last_timestamp = when

    def add_known_share(self, serverid, shnum):
        self.known_shares.append( (serverid, shnum) )

    def add_problem(self, p):
        self.problems.append(p)

    # IDownloadStatus methods
    def get_counter(self):
        return self.counter
    def get_storage_index(self):
        return self.storage_index
    def get_size(self):
        return self.size
    def get_status(self):
        # mention all outstanding segment requests
        outstanding = set()
        errorful = set()
        outstanding = set([s_ev["segment_number"]
                           for s_ev in self.segment_events
                           if s_ev["finish_time"] is None])
        errorful = set([s_ev["segment_number"]
                        for s_ev in self.segment_events
                        if s_ev["success"] is False])
        def join(segnums):
            if len(segnums) == 1:
                return "segment %s" % list(segnums)[0]
            else:
                return "segments %s" % (",".join([str(i)
                                                  for i in sorted(segnums)]))
        error_s = ""
        if errorful:
            error_s = "; errors on %s" % join(errorful)
        if outstanding:
            s = "fetching %s" % join(outstanding)
        else:
            s = "idle"
        return s + error_s

    def get_progress(self):
        # measure all read events that aren't completely done, return the
        # total percentage complete for them
        if not self.read_events:
            return 0.0
        total_outstanding, total_received = 0, 0
        for r_ev in self.read_events:
            if r_ev["finish_time"] is None:
                total_outstanding += r_ev["length"]
                total_received += r_ev["bytes_returned"]
            # else ignore completed requests
        if not total_outstanding:
            return 1.0
        return 1.0 * total_received / total_outstanding

    def using_helper(self):
        return False

    def get_active(self):
        # a download is considered active if it has at least one outstanding
        # read() call
        for r_ev in self.read_events:
            (ign1, ign2, ign3, finishtime, ign4, ign5, ign6) = r_ev
            if finishtime is None:
                return True
        return False

    def get_started(self):
        return self.first_timestamp
    def get_results(self):
        return None # TODO
