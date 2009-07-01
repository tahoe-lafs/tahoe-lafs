
from twisted.internet import defer
from twisted.python import failure
from allmydata import hashtree
from allmydata.uri import from_string
from allmydata.util import hashutil, base32, idlib, log
from allmydata.check_results import CheckAndRepairResults, CheckResults

from common import MODE_CHECK, CorruptShareError
from servermap import ServerMap, ServermapUpdater
from layout import unpack_share, SIGNED_PREFIX_LENGTH

class MutableChecker:

    def __init__(self, node, monitor):
        self._node = node
        self._monitor = monitor
        self.bad_shares = [] # list of (nodeid,shnum,failure)
        self._storage_index = self._node.get_storage_index()
        self.results = CheckResults(from_string(node.get_uri()), self._storage_index)
        self.need_repair = False
        self.responded = set() # set of (binary) nodeids

    def check(self, verify=False, add_lease=False):
        servermap = ServerMap()
        u = ServermapUpdater(self._node, self._monitor, servermap, MODE_CHECK,
                             add_lease=add_lease)
        history = self._node._client.get_history()
        if history:
            history.notify_mapupdate(u.get_status())
        d = u.update()
        d.addCallback(self._got_mapupdate_results)
        if verify:
            d.addCallback(self._verify_all_shares)
        d.addCallback(lambda res: servermap)
        d.addCallback(self._fill_checker_results, self.results)
        d.addCallback(lambda res: self.results)
        return d

    def _got_mapupdate_results(self, servermap):
        # the file is healthy if there is exactly one recoverable version, it
        # has at least N distinct shares, and there are no unrecoverable
        # versions: all existing shares will be for the same version.
        self._monitor.raise_if_cancelled()
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
            d.addCallback(self._got_answer, peerid, servermap)
            dl.append(d)
        return defer.DeferredList(dl, fireOnOneErrback=True, consumeErrors=True)

    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        # isolate the callRemote to a separate method, so tests can subclass
        # Publish and override it
        d = ss.callRemote("slot_readv", storage_index, shnums, readv)
        return d

    def _got_answer(self, datavs, peerid, servermap):
        for shnum,datav in datavs.items():
            data = datav[0]
            try:
                self._got_results_one_share(shnum, peerid, data)
            except CorruptShareError:
                f = failure.Failure()
                self.need_repair = True
                self.bad_shares.append( (peerid, shnum, f) )
                prefix = data[:SIGNED_PREFIX_LENGTH]
                servermap.mark_bad_share(peerid, shnum, prefix)
                ss = servermap.connections[peerid]
                self.notify_server_corruption(ss, shnum, str(f.value))

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

    def notify_server_corruption(self, ss, shnum, reason):
        ss.callRemoteOnly("advise_corrupt_share",
                          "mutable", self._storage_index, shnum, reason)

    def _count_shares(self, smap, version):
        available_shares = smap.shares_available()
        (num_distinct_shares, k, N) = available_shares[version]
        counters = {}
        counters["count-shares-good"] = num_distinct_shares
        counters["count-shares-needed"] = k
        counters["count-shares-expected"] = N
        good_hosts = smap.all_peers_for_version(version)
        counters["count-good-share-hosts"] = len(good_hosts)
        vmap = smap.make_versionmap()
        counters["count-wrong-shares"] = sum([len(shares)
                                          for verinfo,shares in vmap.items()
                                          if verinfo != version])

        return counters

    def _fill_checker_results(self, smap, r):
        self._monitor.raise_if_cancelled()
        r.set_servermap(smap.copy())
        healthy = True
        data = {}
        report = []
        summary = []
        vmap = smap.make_versionmap()
        recoverable = smap.recoverable_versions()
        unrecoverable = smap.unrecoverable_versions()
        data["count-recoverable-versions"] = len(recoverable)
        data["count-unrecoverable-versions"] = len(unrecoverable)

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
            healthy = False
            summary.append("some versions are unrecoverable")
            report.append("Unhealthy: some versions are unrecoverable")
        if len(recoverable) == 0:
            healthy = False
            summary.append("no versions are recoverable")
            report.append("Unhealthy: no versions are recoverable")
        if len(recoverable) > 1:
            healthy = False
            summary.append("multiple versions are recoverable")
            report.append("Unhealthy: there are multiple recoverable versions")

        needs_rebalancing = False
        if recoverable:
            best_version = smap.best_recoverable_version()
            report.append("Best Recoverable Version: " +
                          smap.summarize_version(best_version))
            counters = self._count_shares(smap, best_version)
            data.update(counters)
            s = counters["count-shares-good"]
            k = counters["count-shares-needed"]
            N = counters["count-shares-expected"]
            if s < N:
                healthy = False
                report.append("Unhealthy: best version has only %d shares "
                              "(encoding is %d-of-%d)" % (s, k, N))
                summary.append("%d shares (enc %d-of-%d)" % (s, k, N))
            hosts = smap.all_peers_for_version(best_version)
            needs_rebalancing = bool( len(hosts) < N )
        elif unrecoverable:
            healthy = False
            # find a k and N from somewhere
            first = list(unrecoverable)[0]
            # not exactly the best version, but that doesn't matter too much
            data.update(self._count_shares(smap, first))
            # leave needs_rebalancing=False: the file being unrecoverable is
            # the bigger problem
        else:
            # couldn't find anything at all
            data["count-shares-good"] = 0
            data["count-shares-needed"] = 3 # arbitrary defaults
            data["count-shares-expected"] = 10
            data["count-good-share-hosts"] = 0
            data["count-wrong-shares"] = 0

        if self.bad_shares:
            data["count-corrupt-shares"] = len(self.bad_shares)
            data["list-corrupt-shares"] = locators = []
            report.append("Corrupt Shares:")
            summary.append("Corrupt Shares:")
            for (peerid, shnum, f) in sorted(self.bad_shares):
                locators.append( (peerid, self._storage_index, shnum) )
                s = "%s-sh%d" % (idlib.shortnodeid_b2a(peerid), shnum)
                if f.check(CorruptShareError):
                    ft = f.value.reason
                else:
                    ft = str(f)
                report.append(" %s: %s" % (s, ft))
                summary.append(s)
                p = (peerid, self._storage_index, shnum, f)
                r.problems.append(p)
                msg = ("CorruptShareError during mutable verify, "
                       "peerid=%(peerid)s, si=%(si)s, shnum=%(shnum)d, "
                       "where=%(where)s")
                log.msg(format=msg, peerid=idlib.nodeid_b2a(peerid),
                        si=base32.b2a(self._storage_index),
                        shnum=shnum,
                        where=ft,
                        level=log.WEIRD, umid="EkK8QA")
        else:
            data["count-corrupt-shares"] = 0
            data["list-corrupt-shares"] = []

        sharemap = {}
        for verinfo in vmap:
            for (shnum, peerid, timestamp) in vmap[verinfo]:
                shareid = "%s-sh%d" % (smap.summarize_version(verinfo), shnum)
                if shareid not in sharemap:
                    sharemap[shareid] = []
                sharemap[shareid].append(peerid)
        data["sharemap"] = sharemap
        data["servers-responding"] = list(smap.reachable_peers)

        r.set_healthy(healthy)
        r.set_recoverable(bool(recoverable))
        r.set_needs_rebalancing(needs_rebalancing)
        r.set_data(data)
        if healthy:
            r.set_summary("Healthy")
        else:
            r.set_summary("Unhealthy: " + " ".join(summary))
        r.set_report(report)


class MutableCheckAndRepairer(MutableChecker):
    def __init__(self, node, monitor):
        MutableChecker.__init__(self, node, monitor)
        self.cr_results = CheckAndRepairResults(self._storage_index)
        self.cr_results.pre_repair_results = self.results
        self.need_repair = False

    def check(self, verify=False, add_lease=False):
        d = MutableChecker.check(self, verify, add_lease)
        d.addCallback(self._maybe_repair)
        d.addCallback(lambda res: self.cr_results)
        return d

    def _maybe_repair(self, res):
        self._monitor.raise_if_cancelled()
        if not self.need_repair:
            self.cr_results.post_repair_results = self.results
            return
        if self._node.is_readonly():
            # ticket #625: we cannot yet repair read-only mutable files
            self.cr_results.post_repair_results = self.results
            self.cr_results.repair_attempted = False
            return
        self.cr_results.repair_attempted = True
        d = self._node.repair(self.results)
        def _repair_finished(repair_results):
            self.cr_results.repair_successful = True
            r = CheckResults(from_string(self._node.get_uri()), self._storage_index)
            self.cr_results.post_repair_results = r
            self._fill_checker_results(repair_results.servermap, r)
            self.cr_results.repair_results = repair_results # TODO?
        def _repair_error(f):
            # I'm not sure if I want to pass through a failure or not.
            self.cr_results.repair_successful = False
            self.cr_results.repair_failure = f # TODO?
            #self.cr_results.post_repair_results = ??
            return f
        d.addCallbacks(_repair_finished, _repair_error)
        return d
