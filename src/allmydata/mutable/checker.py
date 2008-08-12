
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from allmydata import hashtree
from allmydata.util import hashutil, base32, idlib
from allmydata.interfaces import ICheckerResults

from common import MODE_CHECK, CorruptShareError
from servermap import ServerMap, ServermapUpdater
from layout import unpack_share, SIGNED_PREFIX_LENGTH

class MutableChecker:

    def __init__(self, node):
        self._node = node
        self.bad_shares = [] # list of (nodeid,shnum,failure)
        self._storage_index = self._node.get_storage_index()
        self.results = Results(self._storage_index)
        self.need_repair = False

    def check(self, verify=False, repair=False):
        servermap = ServerMap()
        self.results.servermap = servermap
        u = ServermapUpdater(self._node, servermap, MODE_CHECK)
        d = u.update()
        d.addCallback(self._got_mapupdate_results)
        if verify:
            d.addCallback(self._verify_all_shares)
        if repair:
            d.addCallback(self._maybe_do_repair)
        d.addCallback(self._generate_results)
        d.addCallback(self._return_results)
        return d

    def _got_mapupdate_results(self, servermap):
        # the file is healthy if there is exactly one recoverable version, it
        # has at least N distinct shares, and there are no unrecoverable
        # versions: all existing shares will be for the same version.
        self.best_version = None
        num_recoverable = len(servermap.recoverable_versions())
        if num_recoverable:
            self.best_version = servermap.best_recoverable_version()

        if servermap.unrecoverable_versions():
            self.need_repair = True
        if num_recoverable != 1:
            self.need_repair = True
        if self.best_version:
            available_shares = servermap.shares_available()
            (num_distinct_shares, k, N) = available_shares[self.best_version]
            if num_distinct_shares < N:
                self.need_repair = True

        return servermap

    def _verify_all_shares(self, servermap):
        # read every byte of each share
        if not self.best_version:
            return
        versionmap = servermap.make_versionmap()
        shares = versionmap[self.best_version]
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.best_version
        offsets = dict(offsets_tuple)
        readv = [ (0, offsets["EOF"]) ]
        dl = []
        for (shnum, peerid, timestamp) in shares:
            ss = servermap.connections[peerid]
            d = self._do_read(ss, peerid, self._storage_index, [shnum], readv)
            d.addCallback(self._got_answer, peerid)
            dl.append(d)
        return defer.DeferredList(dl, fireOnOneErrback=True)

    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        # isolate the callRemote to a separate method, so tests can subclass
        # Publish and override it
        d = ss.callRemote("slot_readv", storage_index, shnums, readv)
        return d

    def _got_answer(self, datavs, peerid):
        for shnum,datav in datavs.items():
            data = datav[0]
            try:
                self._got_results_one_share(shnum, peerid, data)
            except CorruptShareError:
                f = failure.Failure()
                self.need_repair = True
                self.bad_shares.append( (peerid, shnum, f) )
                prefix = data[:SIGNED_PREFIX_LENGTH]
                self.results.servermap.mark_bad_share(peerid, shnum, prefix)

    def check_prefix(self, peerid, shnum, data):
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.best_version
        got_prefix = data[:SIGNED_PREFIX_LENGTH]
        if got_prefix != prefix:
            raise CorruptShareError(peerid, shnum,
                                    "prefix mismatch: share changed while we were reading it")

    def _got_results_one_share(self, shnum, peerid, data):
        self.check_prefix(peerid, shnum, data)

        # the [seqnum:signature] pieces are validated by _compare_prefix,
        # which checks their signature against the pubkey known to be
        # associated with this file.

        (seqnum, root_hash, IV, k, N, segsize, datalen, pubkey, signature,
         share_hash_chain, block_hash_tree, share_data,
         enc_privkey) = unpack_share(data)

        # validate [share_hash_chain,block_hash_tree,share_data]

        leaves = [hashutil.block_hash(share_data)]
        t = hashtree.HashTree(leaves)
        if list(t) != block_hash_tree:
            raise CorruptShareError(peerid, shnum, "block hash tree failure")
        share_hash_leaf = t[0]
        t2 = hashtree.IncompleteHashTree(N)
        # root_hash was checked by the signature
        t2.set_hashes({0: root_hash})
        try:
            t2.set_hashes(hashes=share_hash_chain,
                          leaves={shnum: share_hash_leaf})
        except (hashtree.BadHashError, hashtree.NotEnoughHashesError,
                IndexError), e:
            msg = "corrupt hashes: %s" % (e,)
            raise CorruptShareError(peerid, shnum, msg)

        # validate enc_privkey: only possible if we have a write-cap
        if not self._node.is_readonly():
            alleged_privkey_s = self._node._decrypt_privkey(enc_privkey)
            alleged_writekey = hashutil.ssk_writekey_hash(alleged_privkey_s)
            if alleged_writekey != self._node.get_writekey():
                raise CorruptShareError(peerid, shnum, "invalid privkey")

    def _maybe_do_repair(self, res):
        if not self.need_repair:
            return
        self.results.repair_attempted = True
        d = self._node.repair(self)
        def _repair_finished(repair_results):
            self.results.repair_succeeded = True
            self.results.repair_results = repair_results
        def _repair_error(f):
            # I'm not sure if I want to pass through a failure or not.
            self.results.repair_succeeded = False
            self.results.repair_failure = f
            return f
        d.addCallbacks(_repair_finished, _repair_error)
        return d

    def _generate_results(self, res):
        self.results.healthy = True
        smap = self.results.servermap
        report = []
        vmap = smap.make_versionmap()
        recoverable = smap.recoverable_versions()
        unrecoverable = smap.unrecoverable_versions()
        if recoverable:
            report.append("Recoverable Versions: " +
                          "/".join(["%d*%s" % (len(vmap[v]),
                                               smap.summarize_version(v))
                                    for v in recoverable]))
        if unrecoverable:
            report.append("Unrecoverable Versions: " +
                          "/".join(["%d*%s" % (len(vmap[v]),
                                               smap.summarize_version(v))
                                    for v in unrecoverable]))
        if smap.unrecoverable_versions():
            self.results.healthy = False
            report.append("Unhealthy: some versions are unrecoverable")
        if len(recoverable) == 0:
            self.results.healthy = False
            report.append("Unhealthy: no versions are recoverable")
        if len(recoverable) > 1:
            self.results.healthy = False
            report.append("Unhealthy: there are multiple recoverable versions")
        if self.best_version:
            report.append("Best Recoverable Version: " +
                          smap.summarize_version(self.best_version))
            available_shares = smap.shares_available()
            (num_distinct_shares, k, N) = available_shares[self.best_version]
            if num_distinct_shares < N:
                self.results.healthy = False
                report.append("Unhealthy: best recoverable version has only %d shares (encoding is %d-of-%d)"
                              % (num_distinct_shares, k, N))
        if self.bad_shares:
            report.append("Corrupt Shares:")
            for (peerid, shnum, f) in sorted(self.bad_shares):
                s = "%s-sh%d" % (idlib.shortnodeid_b2a(peerid), shnum)
                if f.check(CorruptShareError):
                    ft = f.value.reason
                else:
                    ft = str(f)
                report.append(" %s: %s" % (s, ft))
                p = (peerid, self._storage_index, shnum, f)
                self.results.problems.append(p)

        self.results.status_report = "\n".join(report) + "\n"

    def _return_results(self, res):
        return self.results


class Results:
    implements(ICheckerResults)

    def __init__(self, storage_index):
        self.storage_index = storage_index
        self.storage_index_s = base32.b2a(storage_index)[:6]
        self.repair_attempted = False
        self.status_report = "[not generated yet]" # string
        self.problems = [] # list of (peerid, storage_index, shnum, failure)

    def is_healthy(self):
        return self.healthy

    def get_storage_index(self):
        return self.storage_index
    def get_storage_index_string(self):
        return self.storage_index_s

    def get_mutability_string(self):
        return "mutable"

    def to_string(self):
        s = ""
        if self.healthy:
            s += "Healthy!\n"
        else:
            s += "Not Healthy!\n"
        s += "\n"
        s += self.status_report
        s += "\n"
        return s

