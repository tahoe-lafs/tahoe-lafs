
"""
Given a StorageIndex, count how many shares we can find.

This does no verification of the shares whatsoever. If the peer claims to
have the share, we believe them.
"""

from twisted.internet import defer
from twisted.application import service
from twisted.python import log
from allmydata.interfaces import IVerifierURI
from allmydata import uri, download
from allmydata.util import hashutil, idlib

class SimpleCHKFileChecker:
    """Return a list of (needed, total, found, sharemap), where sharemap maps
    share number to a list of (binary) nodeids of the shareholders."""

    def __init__(self, peer_getter, uri_to_check):
        self.peer_getter = peer_getter
        self.found_shares = set()
        self.uri_to_check = uri_to_check
        self.sharemap = {}

    '''
    def check_synchronously(self, si):
        # this is how we would write this class if we were using synchronous
        # messages (or if we used promises).
        found = set()
        for (pmpeerid, peerid, connection) in self.peer_getter(storage_index):
            buckets = connection.get_service("storageserver").get_buckets(si)
            found.update(buckets.keys())
        return len(found)
    '''

    def check(self):
        d = self._get_all_shareholders(self.uri_to_check.storage_index)
        d.addCallback(self._done)
        return d

    def _get_all_shareholders(self, storage_index):
        dl = []
        for (pmpeerid, peerid, connection) in self.peer_getter(storage_index):
            d = connection.callRemote("get_service", "storageserver")
            d.addCallback(lambda ss: ss.callRemote("get_buckets",
                                                   storage_index))
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
        return (u.needed_shares, u.total_shares, len(self.found_shares),
                self.sharemap)

class SimpleDirnodeChecker:

    def __init__(self, tub):
        self.tub = tub

    def check(self, node):
        si = node.storage_index
        d = self.tub.getReference(node.furl)
        d.addCallback(self._get_dirnode, node.storage_index)
        d.addCallbacks(self._success, self._failed)
        return d

    def _get_dirnode(self, rref, storage_index):
        d = rref.callRemote("list", storage_index)
        return d

    def _success(self, res):
        return True
    def _failed(self, f):
        if f.check(IndexError):
            return False
        log.err(f)
        return False

class Checker(service.MultiService):
    """I am a service that helps perform file checks.
    """
    name = "checker"

    def check(self, uri_to_check):
        uri_to_check = IVerifierURI(uri_to_check)
        if uri_to_check is None:
            return defer.succeed(True)
        elif isinstance(uri_to_check, uri.CHKFileVerifierURI):
            peer_getter = self.parent.get_permuted_peers
            c = SimpleCHKFileChecker(peer_getter, uri_to_check)
            return c.check()
        elif isinstance(uri_to_check, uri.DirnodeVerifierURI):
            tub = self.parent.tub
            c = SimpleDirnodeChecker(tub)
            return c.check(uri_to_check)
        else:
            raise ValueError("I don't know how to check '%s'" % (uri_to_check,))

    def verify(self, uri_to_verify):
        uri_to_verify = IVerifierURI(uri_to_verify)
        if uri_to_verify is None:
            return defer.succeed(True)
        elif isinstance(uri_to_verify, uri.CHKFileVerifierURI):
            v = SimpleCHKFileVerifier(self.parent, uri_to_verify)
            return v.start()
        elif isinstance(uri_to_verify, uri.DirnodeVerifierURI):
            # for dirnodes, checking and verifying are currently equivalent
            tub = self.parent.tub
            c = SimpleDirnodeChecker(tub)
            return c.check(uri_to_verify)
        else:
            raise ValueError("I don't know how to verify '%s'" %
                             (uri_to_verify,))

class VerifyingOutput:
    def __init__(self, total_length):
        self._crypttext_hasher = hashutil.crypttext_hasher()
        self.length = 0
        self.total_length = total_length
        self._segment_number = 0
        self._crypttext_hash_tree = None
        self._opened = False

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
        return True


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

        self._output = VerifyingOutput(self._size)
        self._paused = False
        self._stopped = False

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


    def start(self):
        log.msg("starting download [%s]" % idlib.b2a(self._storage_index)[:6])

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
