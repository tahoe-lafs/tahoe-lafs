
import itertools
from zope.interface import implements
from allmydata.interfaces import IDownloadStatus

class RequestEvent:
    def __init__(self, download_status, tag):
        self._download_status = download_status
        self._tag = tag
    def finished(self, received, when):
        self._download_status.add_request_finished(self._tag, received, when)

class DYHBEvent:
    def __init__(self, download_status, tag):
        self._download_status = download_status
        self._tag = tag
    def finished(self, shnums, when):
        self._download_status.add_dyhb_finished(self._tag, shnums, when)

class ReadEvent:
    def __init__(self, download_status, tag):
        self._download_status = download_status
        self._tag = tag
    def update(self, bytes, decrypttime, pausetime):
        self._download_status.update_read_event(self._tag, bytes,
                                                decrypttime, pausetime)
    def finished(self, finishtime):
        self._download_status.finish_read_event(self._tag, finishtime)

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
        self.started = None
        # self.dyhb_requests tracks "do you have a share" requests and
        # responses. It maps serverid to a tuple of:
        #  send time
        #  tuple of response shnums (None if response hasn't arrived, "error")
        #  response time (None if response hasn't arrived yet)
        self.dyhb_requests = {}

        # self.requests tracks share-data requests and responses. It maps
        # serverid to a tuple of:
        #  shnum,
        #  start,length,  (of data requested)
        #  send time
        #  response length (None if reponse hasn't arrived yet, or "error")
        #  response time (None if response hasn't arrived)
        self.requests = {}

        # self.segment_events tracks segment requests and delivery. It is a
        # list of:
        #  type ("request", "delivery", "error")
        #  segment number
        #  event time
        #  segment start (file offset of first byte, None except in "delivery")
        #  segment length (only in "delivery")
        #  time spent in decode (only in "delivery")
        self.segment_events = []

        # self.read_events tracks read() requests. It is a list of:
        #  start,length  (of data requested)
        #  request time
        #  finish time (None until finished)
        #  bytes returned (starts at 0, grows as segments are delivered)
        #  time spent in decrypt (None for ciphertext-only reads)
        #  time spent paused
        self.read_events = []

        self.known_shares = [] # (serverid, shnum)
        self.problems = []


    def add_dyhb_sent(self, serverid, when):
        r = (when, None, None)
        if serverid not in self.dyhb_requests:
            self.dyhb_requests[serverid] = []
        self.dyhb_requests[serverid].append(r)
        tag = (serverid, len(self.dyhb_requests[serverid])-1)
        return DYHBEvent(self, tag)

    def add_dyhb_finished(self, tag, shnums, when):
        # received="error" on error, else tuple(shnums)
        (serverid, index) = tag
        r = self.dyhb_requests[serverid][index]
        (sent, _, _) = r
        r = (sent, shnums, when)
        self.dyhb_requests[serverid][index] = r

    def add_request_sent(self, serverid, shnum, start, length, when):
        r = (shnum, start, length, when, None, None)
        if serverid not in self.requests:
            self.requests[serverid] = []
        self.requests[serverid].append(r)
        tag = (serverid, len(self.requests[serverid])-1)
        return RequestEvent(self, tag)

    def add_request_finished(self, tag, received, when):
        # received="error" on error, else len(data)
        (serverid, index) = tag
        r = self.requests[serverid][index]
        (shnum, start, length, sent, _, _) = r
        r = (shnum, start, length, sent, received, when)
        self.requests[serverid][index] = r

    def add_segment_request(self, segnum, when):
        if self.started is None:
            self.started = when
        r = ("request", segnum, when, None, None, None)
        self.segment_events.append(r)
    def add_segment_delivery(self, segnum, when, start, length, decodetime):
        r = ("delivery", segnum, when, start, length, decodetime)
        self.segment_events.append(r)
    def add_segment_error(self, segnum, when):
        r = ("error", segnum, when, None, None, None)
        self.segment_events.append(r)

    def add_read_event(self, start, length, when):
        if self.started is None:
            self.started = when
        r = (start, length, when, None, 0, 0, 0)
        self.read_events.append(r)
        tag = len(self.read_events)-1
        return ReadEvent(self, tag)
    def update_read_event(self, tag, bytes_d, decrypt_d, paused_d):
        r = self.read_events[tag]
        (start, length, requesttime, finishtime, bytes, decrypt, paused) = r
        bytes += bytes_d
        decrypt += decrypt_d
        paused += paused_d
        r = (start, length, requesttime, finishtime, bytes, decrypt, paused)
        self.read_events[tag] = r
    def finish_read_event(self, tag, finishtime):
        r = self.read_events[tag]
        (start, length, requesttime, _, bytes, decrypt, paused) = r
        r = (start, length, requesttime, finishtime, bytes, decrypt, paused)
        self.read_events[tag] = r

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
        for s_ev in self.segment_events:
            (etype, segnum, when, segstart, seglen, decodetime) = s_ev
            if etype == "request":
                outstanding.add(segnum)
            elif etype == "delivery":
                outstanding.remove(segnum)
            else: # "error"
                outstanding.remove(segnum)
                errorful.add(segnum)
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
            (start, length, ign1, finishtime, bytes, ign2, ign3) = r_ev
            if finishtime is None:
                total_outstanding += length
                total_received += bytes
            # else ignore completed requests
        if not total_outstanding:
            return 1.0
        return 1.0 * total_received / total_outstanding

    def using_helper(self):
        return False
    def get_active(self):
        return False # TODO
    def get_started(self):
        return self.started
    def get_results(self):
        return None # TODO
