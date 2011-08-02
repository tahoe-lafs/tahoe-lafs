
from allmydata.uri import from_string
from allmydata.util import base32, idlib, log
from allmydata.check_results import CheckAndRepairResults, CheckResults

from allmydata.mutable.common import MODE_CHECK, CorruptShareError
from allmydata.mutable.servermap import ServerMap, ServermapUpdater
from allmydata.mutable.retrieve import Retrieve # for verifying

class MutableChecker:

    def __init__(self, node, storage_broker, history, monitor):
        self._node = node
        self._storage_broker = storage_broker
        self._history = history
        self._monitor = monitor
        self.bad_shares = [] # list of (nodeid,shnum,failure)
        self._storage_index = self._node.get_storage_index()
        self.results = CheckResults(from_string(node.get_uri()), self._storage_index)
        self.need_repair = False
        self.responded = set() # set of (binary) nodeids

    def check(self, verify=False, add_lease=False):
        servermap = ServerMap()
        # Updating the servermap in MODE_CHECK will stand a good chance
        # of finding all of the shares, and getting a good idea of
        # recoverability, etc, without verifying.
        u = ServermapUpdater(self._node, self._storage_broker, self._monitor,
                             servermap, MODE_CHECK, add_lease=add_lease)
        if self._history:
            self._history.notify_mapupdate(u.get_status())
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

        # The file is unhealthy and needs to be repaired if:
        # - There are unrecoverable versions.
        if servermap.unrecoverable_versions():
            self.need_repair = True
        # - There isn't a recoverable version.
        if num_recoverable != 1:
            self.need_repair = True
        # - The best recoverable version is missing some shares.
        if self.best_version:
            available_shares = servermap.shares_available()
            (num_distinct_shares, k, N) = available_shares[self.best_version]
            if num_distinct_shares < N:
                self.need_repair = True

        return servermap

    def _verify_all_shares(self, servermap):
        # read every byte of each share
        #
        # This logic is going to be very nearly the same as the
        # downloader. I bet we could pass the downloader a flag that
        # makes it do this, and piggyback onto that instead of
        # duplicating a bunch of code.
        # 
        # Like:
        #  r = Retrieve(blah, blah, blah, verify=True)
        #  d = r.download()
        #  (wait, wait, wait, d.callback)
        #  
        #  Then, when it has finished, we can check the servermap (which
        #  we provided to Retrieve) to figure out which shares are bad,
        #  since the Retrieve process will have updated the servermap as
        #  it went along.
        #
        #  By passing the verify=True flag to the constructor, we are
        #  telling the downloader a few things.
        # 
        #  1. It needs to download all N shares, not just K shares.
        #  2. It doesn't need to decrypt or decode the shares, only
        #     verify them.
        if not self.best_version:
            return

        r = Retrieve(self._node, servermap, self.best_version, verify=True)
        d = r.download()
        d.addCallback(self._process_bad_shares)
        return d


    def _process_bad_shares(self, bad_shares):
        if bad_shares:
            self.need_repair = True
        self.bad_shares = bad_shares


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
    def __init__(self, node, storage_broker, history, monitor):
        MutableChecker.__init__(self, node, storage_broker, history, monitor)
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
            self.cr_results.repair_successful = repair_results.get_successful()
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
