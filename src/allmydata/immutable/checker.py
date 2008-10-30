
"""
Given a StorageIndex, count how many shares we can find.

This does no verification of the shares whatsoever. If the peer claims to
have the share, we believe them.
"""

from twisted.internet import defer
from twisted.python import log
from allmydata import storage
from allmydata.checker_results import CheckerResults
from allmydata.immutable import download
from allmydata.util import hashutil

class SimpleCHKFileChecker:
    """Return a list of (needed, total, found, sharemap), where sharemap maps
    share number to a list of (binary) nodeids of the shareholders."""

    def __init__(self, client, uri, storage_index, needed_shares, total_shares):
        self.peer_getter = client.get_permuted_peers
        self.needed_shares = needed_shares
        self.total_shares = total_shares
        self.found_shares = set()
        self.uri = uri
        self.storage_index = storage_index
        self.sharemap = {}
        self.responded = set()

    '''
    def check_synchronously(self, si):
        # this is how we would write this class if we were using synchronous
        # messages (or if we used promises).
        found = set()
        for (pmpeerid, peerid, connection) in self.peer_getter(storage_index):
            buckets = connection.get_buckets(si)
            found.update(buckets.keys())
        return len(found)
    '''

    def start(self):
        d = self._get_all_shareholders(self.storage_index)
        d.addCallback(self._done)
        return d

    def _get_all_shareholders(self, storage_index):
        dl = []
        for (peerid, ss) in self.peer_getter("storage", storage_index):
            d = ss.callRemote("get_buckets", storage_index)
            d.addCallbacks(self._got_response, self._got_error,
                           callbackArgs=(peerid,))
            dl.append(d)
        return defer.DeferredList(dl)

    def _got_response(self, buckets, peerid):
        # buckets is a dict: maps shum to an rref of the server who holds it
        self.found_shares.update(buckets.keys())
        for k in buckets:
            if k not in self.sharemap:
                self.sharemap[k] = []
            self.sharemap[k].append(peerid)
        self.responded.add(peerid)

    def _got_error(self, f):
        if f.check(KeyError):
            pass
        log.err(f)
        pass

    def _done(self, res):
        r = CheckerResults(self.uri, self.storage_index)
        report = []
        healthy = bool(len(self.found_shares) >= self.total_shares)
        r.set_healthy(healthy)
        data = {"count-shares-good": len(self.found_shares),
                "count-shares-needed": self.needed_shares,
                "count-shares-expected": self.total_shares,
                "count-wrong-shares": 0,
                }
        if healthy:
            data["count-recoverable-versions"] = 1
            data["count-unrecoverable-versions"] = 0
        else:
            data["count-recoverable-versions"] = 0
            data["count-unrecoverable-versions"] = 1

        data["count-corrupt-shares"] = 0 # non-verifier doesn't see corruption
        data["list-corrupt-shares"] = []
        hosts = set()
        sharemap = {}
        for (shnum,nodeids) in self.sharemap.items():
            hosts.update(nodeids)
            sharemap[shnum] = nodeids
        data["count-good-share-hosts"] = len(hosts)
        data["servers-responding"] = list(self.responded)
        data["sharemap"] = sharemap

        r.set_data(data)
        r.set_needs_rebalancing(bool( len(self.found_shares) > len(hosts) ))

        #r.stuff = (self.needed_shares, self.total_shares,
        #            len(self.found_shares), self.sharemap)
        if len(self.found_shares) < self.total_shares:
            wanted = set(range(self.total_shares))
            missing = wanted - self.found_shares
            report.append("Missing shares: %s" %
                          ",".join(["sh%d" % shnum
                                    for shnum in sorted(missing)]))
        r.set_report(report)
        # TODO: r.set_summary(summary)
        return r

class VerifyingOutput:
    def __init__(self, total_length, results):
        self._crypttext_hasher = hashutil.crypttext_hasher()
        self.length = 0
        self.total_length = total_length
        self._segment_number = 0
        self._crypttext_hash_tree = None
        self._opened = False
        self._results = results
        results.set_healthy(False)

    def setup_hashtrees(self, plaintext_hashtree, crypttext_hashtree):
        self._crypttext_hash_tree = crypttext_hashtree

    def write_segment(self, crypttext):
        self.length += len(crypttext)

        self._crypttext_hasher.update(crypttext)
        if self._crypttext_hash_tree:
            ch = hashutil.crypttext_segment_hasher()
            ch.update(crypttext)
            crypttext_leaves = {self._segment_number: ch.digest()}
            self._crypttext_hash_tree.set_hashes(leaves=crypttext_leaves)

        self._segment_number += 1

    def close(self):
        self.crypttext_hash = self._crypttext_hasher.digest()

    def finish(self):
        self._results.set_healthy(True)
        # the return value of finish() is passed out of FileDownloader._done,
        # but SimpleCHKFileVerifier overrides this with the CheckerResults
        # instance instead.


class SimpleCHKFileVerifier(download.FileDownloader):
    # this reconstructs the crypttext, which verifies that at least 'k' of
    # the shareholders are around and have valid data. It does not check the
    # remaining shareholders, and it cannot verify the plaintext.
    check_plaintext_hash = False

    def __init__(self, client, uri, storage_index, k, N, size, ueb_hash):
        self._client = client

        self._uri = uri
        self._storage_index = storage_index
        self._uri_extension_hash = ueb_hash
        self._total_shares = N
        self._size = size
        self._num_needed_shares = k

        self._si_s = storage.si_b2a(self._storage_index)
        self.init_logging()

        self._check_results = r = CheckerResults(self._uri, self._storage_index)
        r.set_data({"count-shares-needed": k,
                    "count-shares-expected": N,
                    })
        self._output = VerifyingOutput(self._size, r)
        self._paused = False
        self._stopped = False

        self._results = None
        self.active_buckets = {} # k: shnum, v: bucket
        self._share_buckets = [] # list of (sharenum, bucket) tuples
        self._share_vbuckets = {} # k: shnum, v: set of ValidatedBuckets
        self._uri_extension_sources = []

        self._uri_extension_data = None

        self._fetch_failures = {"uri_extension": 0,
                                "plaintext_hashroot": 0,
                                "plaintext_hashtree": 0,
                                "crypttext_hashroot": 0,
                                "crypttext_hashtree": 0,
                                }

    def init_logging(self):
        self._log_prefix = prefix = storage.si_b2a(self._storage_index)[:5]
        num = self._client.log("SimpleCHKFileVerifier(%s): starting" % prefix)
        self._log_number = num

    def log(self, *args, **kwargs):
        if not "parent" in kwargs:
            kwargs['parent'] = self._log_number
        # add a prefix to the message, regardless of how it is expressed
        prefix = "SimpleCHKFileVerifier(%s): " % self._log_prefix
        if "format" in kwargs:
            kwargs["format"] = prefix + kwargs["format"]
        elif "message" in kwargs:
            kwargs["message"] = prefix + kwargs["message"]
        elif args:
            m = prefix + args[0]
            args = (m,) + args[1:]
        return self._client.log(*args, **kwargs)


    def start(self):
        log.msg("starting download [%s]" % storage.si_b2a(self._storage_index)[:5])

        # first step: who should we download from?
        d = defer.maybeDeferred(self._get_all_shareholders)
        d.addCallback(self._got_all_shareholders)
        # now get the uri_extension block from somebody and validate it
        d.addCallback(self._obtain_uri_extension)
        d.addCallback(self._got_uri_extension)
        d.addCallback(self._get_hashtrees)
        d.addCallback(self._create_validated_buckets)
        # once we know that, we can download blocks from everybody
        d.addCallback(self._download_all_segments)
        d.addCallback(self._done)
        d.addCallback(self._verify_done)
        return d

    def _verify_done(self, ignored):
        # TODO: The following results are just stubs, and need to be replaced
        # with actual values. These exist to make things like deep-check not
        # fail.
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
