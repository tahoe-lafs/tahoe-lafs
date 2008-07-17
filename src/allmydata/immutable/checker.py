
"""
Given a StorageIndex, count how many shares we can find.

This does no verification of the shares whatsoever. If the peer claims to
have the share, we believe them.
"""

from zope.interface import implements
from twisted.internet import defer
from twisted.python import log
from allmydata import storage
from allmydata.interfaces import IVerifierURI, \
     ICheckerResults, IDeepCheckResults
from allmydata.immutable import download
from allmydata.util import hashutil, base32

class Results:
    implements(ICheckerResults)

    def __init__(self, storage_index):
        # storage_index might be None for, say, LIT files
        self.storage_index = storage_index
        if storage_index is None:
            self.storage_index_s = "<none>"
        else:
            self.storage_index_s = base32.b2a(storage_index)[:6]

    def is_healthy(self):
        return self.healthy

    def get_storage_index_string(self):
        return self.storage_index_s

    def get_mutability_string(self):
        if self.storage_index:
            return "immutable"
        return "literal"

    def to_string(self):
        s = ""
        if self.healthy:
            s += "Healthy!\n"
        else:
            s += "Not Healthy!\n"
        return s

class DeepCheckResults:
    implements(IDeepCheckResults)

    def __init__(self):
        self.objects_checked = 0
        self.objects_healthy = 0
        self.repairs_attempted = 0
        self.repairs_successful = 0
        self.problems = []
        self.server_problems = {}

    def add_check(self, r):
        self.objects_checked += 1
        if r.is_healthy:
            self.objects_healthy += 1
        else:
            self.problems.append(r)

    def add_repair(self, is_successful):
        self.repairs_attempted += 1
        if is_successful:
            self.repairs_successful += 1

    def count_objects_checked(self):
        return self.objects_checked
    def count_objects_healthy(self):
        return self.objects_healthy
    def count_repairs_attempted(self):
        return self.repairs_attempted
    def count_repairs_successful(self):
        return self.repairs_successful
    def get_server_problems(self):
        return self.server_problems
    def get_problems(self):
        return self.problems


class SimpleCHKFileChecker:
    """Return a list of (needed, total, found, sharemap), where sharemap maps
    share number to a list of (binary) nodeids of the shareholders."""

    def __init__(self, peer_getter, uri_to_check):
        self.peer_getter = peer_getter
        self.found_shares = set()
        self.uri_to_check = IVerifierURI(uri_to_check)
        self.sharemap = {}

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

    def check(self):
        d = self._get_all_shareholders(self.uri_to_check.storage_index)
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

    def _got_error(self, f):
        if f.check(KeyError):
            pass
        log.err(f)
        pass

    def _done(self, res):
        u = self.uri_to_check
        r = Results(self.uri_to_check.storage_index)
        r.healthy = bool(len(self.found_shares) >= u.needed_shares)
        r.stuff = (u.needed_shares, u.total_shares, len(self.found_shares),
                   self.sharemap)
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
        results.healthy = False

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
        self._results.healthy = True
        return self._results


class SimpleCHKFileVerifier(download.FileDownloader):
    # this reconstructs the crypttext, which verifies that at least 'k' of
    # the shareholders are around and have valid data. It does not check the
    # remaining shareholders, and it cannot verify the plaintext.
    check_plaintext_hash = False

    def __init__(self, client, u):
        self._client = client

        u = IVerifierURI(u)
        self._storage_index = u.storage_index
        self._uri_extension_hash = u.uri_extension_hash
        self._total_shares = u.total_shares
        self._size = u.size
        self._num_needed_shares = u.needed_shares

        self._si_s = storage.si_b2a(self._storage_index)
        self.init_logging()

        r = Results(self._storage_index)
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

    def log(self, msg, parent=None):
        if parent is None:
            parent = self._log_number
        return self._client.log("SimpleCHKFileVerifier(%s): %s"
                                % (self._log_prefix, msg),
                                parent=parent)


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
        return d

