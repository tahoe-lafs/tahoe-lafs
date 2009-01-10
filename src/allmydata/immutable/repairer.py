from twisted.internet import defer
from allmydata import storage
from allmydata.check_results import CheckResults, CheckAndRepairResults
from allmydata.immutable import download
from allmydata.util import nummedobj
from allmydata.util.assertutil import precondition
from allmydata.uri import CHKFileVerifierURI

from allmydata.immutable import layout

import sha, time

def _permute_servers(servers, key):
    return sorted(servers, key=lambda x: sha.new(key+x[0]).digest())

class LogMixin(nummedobj.NummedObj):
    def __init__(self, client, verifycap):
        nummedobj.NummedObj.__init__(self)
        self._client = client
        self._verifycap = verifycap
        self._storageindex = self._verifycap.storage_index
        self._log_prefix = prefix = storage.si_b2a(self._storageindex)[:5]
        self._parentmsgid = self._client.log("%s(%s): starting" % (self.__repr__(), self._log_prefix))

    def log(self, msg, parent=None, *args, **kwargs):
        if parent is None:
            parent = self._parentmsgid
        return self._client.log("%s(%s): %s" % (self.__repr__(), self._log_prefix, msg), parent=parent, *args, **kwargs)

class Repairer(LogMixin):
    """ I generate any shares which were not available and upload them to servers.

    Which servers?  Well, I take the list of servers and if I used the Checker in verify mode
    then I exclude any servers which claimed to have a share but then either failed to serve it
    up or served up a corrupted one when I asked for it.  (If I didn't use verify mode, then I
    won't exclude any servers, not even servers which, when I subsequently attempt to download
    the file during repair, claim to have a share but then fail to produce it or then produce a
    corrupted share.)  Then I perform the normal server-selection process of permuting the order
    of the servers with the storage index, and choosing the next server which doesn't already
    have more shares than others.

    My process of uploading replacement shares proceeds in a segment-wise fashion -- first I ask
    servers if they can hold the new shares, and wait until enough have agreed then I download
    the first segment of the file and upload the first block of each replacement share, and only
    after all those blocks have been uploaded do I download the second segment of the file and
    upload the second block of each replacement share to its respective server.  (I do it this
    way in order to minimize the amount of downloading I have to do and the amount of memory I
    have to use at any one time.)

    If any of the servers to which I am uploading replacement shares fails to accept the blocks 
    during this process, then I just stop using that server, abandon any share-uploads that were 
    going to that server, and proceed to finish uploading the remaining shares to their 
    respective servers.  At the end of my work, I produce an object which satisfies the 
    ICheckAndRepairResults interface (by firing the deferred that I returned from start() and 
    passing that check-and-repair-results object).

    Before I send any new request to a server, I always ask the "monitor" object that was passed
    into my constructor whether this task has been cancelled (by invoking its
    raise_if_cancelled() method).
    """
    def __init__(self, client, verifycap, servers, monitor):
        assert precondition(isinstance(verifycap, CHKFileVerifierURI))
        assert precondition(isinstance(servers, (set, frozenset)))
        for (serverid, serverrref) in servers:
            assert precondition(isinstance(serverid, str))

        LogMixin.__init__(self, client, verifycap)

        self._monitor = monitor
        self._servers = servers

    def start(self):
        self.log("starting download")
        d = defer.succeed(_permute_servers(self._servers, self._storageindex))
        d.addCallback(self._check_phase)
        d.addCallback(self._repair_phase)
        return d

    def _check_phase(self, unused=None):
        return unused

    def _repair_phase(self, unused=None):
        bogusresults = CheckAndRepairResults(self._storageindex) # XXX THIS REPAIRER NOT HERE YET
        bogusresults.pre_repair_results = CheckResults(self._verifycap, self._storageindex)
        bogusresults.pre_repair_results.set_healthy(True)
        bogusresults.pre_repair_results.set_needs_rebalancing(False)
        bogusresults.post_repair_results = CheckResults(self._verifycap, self._storageindex)
        bogusresults.post_repair_results.set_healthy(True)
        bogusresults.post_repair_results.set_needs_rebalancing(False)
        bogusdata = {}
        bogusdata['count-shares-good'] = "this repairer not here yet"
        bogusdata['count-shares-needed'] = "this repairer not here yet"
        bogusdata['count-shares-expected'] = "this repairer not here yet"
        bogusdata['count-good-share-hosts'] = "this repairer not here yet"
        bogusdata['count-corrupt-shares'] = "this repairer not here yet"
        bogusdata['count-list-corrupt-shares'] = [] # XXX THIS REPAIRER NOT HERE YET
        bogusdata['servers-responding'] = [] # XXX THIS REPAIRER NOT HERE YET
        bogusdata['sharemap'] = {} # XXX THIS REPAIRER NOT HERE YET
        bogusdata['count-wrong-shares'] = "this repairer not here yet"
        bogusdata['count-recoverable-versions'] = "this repairer not here yet"
        bogusdata['count-unrecoverable-versions'] = "this repairer not here yet"
        bogusresults.pre_repair_results.data.update(bogusdata)
        bogusresults.post_repair_results.data.update(bogusdata)
        return bogusresults

    def _get_all_shareholders(self, ignored=None):
        dl = []
        for (peerid,ss) in self._client.get_permuted_peers("storage",
                                                           self._storageindex):
            d = ss.callRemote("get_buckets", self._storageindex)
            d.addCallbacks(self._got_response, self._got_error,
                           callbackArgs=(peerid,))
            dl.append(d)
        self._responses_received = 0
        self._queries_sent = len(dl)
        if self._status:
            self._status.set_status("Locating Shares (%d/%d)" %
                                    (self._responses_received,
                                     self._queries_sent))
        return defer.DeferredList(dl)

    def _got_response(self, buckets, peerid):
        self._responses_received += 1
        if self._results:
            elapsed = time.time() - self._started
            self._results.timings["servers_peer_selection"][peerid] = elapsed
        if self._status:
            self._status.set_status("Locating Shares (%d/%d)" %
                                    (self._responses_received,
                                     self._queries_sent))
        for sharenum, bucket in buckets.iteritems():
            b = layout.ReadBucketProxy(bucket, peerid, self._si_s)
            self.add_share_bucket(sharenum, b)
            self._uri_extension_sources.append(b)
            if self._results:
                if peerid not in self._results.servermap:
                    self._results.servermap[peerid] = set()
                self._results.servermap[peerid].add(sharenum)

    def _got_all_shareholders(self, res):
        if self._results:
            now = time.time()
            self._results.timings["peer_selection"] = now - self._started

        if len(self._share_buckets) < self._num_needed_shares:
            raise download.NotEnoughSharesError

    def _verify_done(self, ignored):
        # TODO: The following results are just stubs, and need to be replaced
        # with actual values. These exist to make things like deep-check not
        # fail. XXX
        self._check_results.set_needs_rebalancing(False)
        N = self._total_shares
        data = {
            "count-shares-good": N,
            "count-good-share-hosts": N,
            "count-corrupt-shares": 0,
            "list-corrupt-shares": [],
            "servers-responding": [],
            "sharemap": {},
            "count-wrong-shares": 0,
            "count-recoverable-versions": 1,
            "count-unrecoverable-versions": 0,
            }
        self._check_results.set_data(data)
        return self._check_results
