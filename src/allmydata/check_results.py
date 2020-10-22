from past.builtins import unicode

from zope.interface import implementer
from allmydata.interfaces import ICheckResults, ICheckAndRepairResults, \
     IDeepCheckResults, IDeepCheckAndRepairResults, IURI, IDisplayableServer
from allmydata.util import base32

@implementer(ICheckResults)
class CheckResults(object):

    def __init__(self, uri, storage_index,
                 healthy, recoverable, count_happiness,
                 count_shares_needed, count_shares_expected,
                 count_shares_good, count_good_share_hosts,
                 count_recoverable_versions, count_unrecoverable_versions,
                 servers_responding, sharemap,
                 count_wrong_shares, list_corrupt_shares, count_corrupt_shares,
                 list_incompatible_shares, count_incompatible_shares,
                 summary, report, share_problems, servermap):
        assert IURI.providedBy(uri), uri
        self._uri = uri
        self._storage_index = storage_index
        self._summary = ""
        self._healthy = bool(healthy)
        if self._healthy:
            assert recoverable
            if not summary:
                summary = "healthy"
        else:
            if not summary:
                summary = "not healthy"
        self._recoverable = recoverable
        if not self._recoverable:
            assert not self._healthy

        self._count_happiness = count_happiness
        self._count_shares_needed = count_shares_needed
        self._count_shares_expected = count_shares_expected
        self._count_shares_good = count_shares_good
        self._count_good_share_hosts = count_good_share_hosts
        self._count_recoverable_versions = count_recoverable_versions
        self._count_unrecoverable_versions = count_unrecoverable_versions
        for server in servers_responding:
            assert IDisplayableServer.providedBy(server), server
        self._servers_responding = servers_responding
        for shnum, servers in sharemap.items():
            for server in servers:
                assert IDisplayableServer.providedBy(server), server
        self._sharemap = sharemap
        self._count_wrong_shares = count_wrong_shares
        for (server, SI, shnum) in list_corrupt_shares:
            assert IDisplayableServer.providedBy(server), server
        self._list_corrupt_shares = list_corrupt_shares
        self._count_corrupt_shares = count_corrupt_shares
        for (server, SI, shnum) in list_incompatible_shares:
            assert IDisplayableServer.providedBy(server), server
        self._list_incompatible_shares = list_incompatible_shares
        self._count_incompatible_shares = count_incompatible_shares

        # On Python 2, we can mix bytes and Unicode. On Python 3, we want
        # unicode.
        if isinstance(summary, bytes):
            summary = unicode(summary, "utf-8")
        assert isinstance(summary, unicode)  # should be a single string
        self._summary = summary
        assert not isinstance(report, str) # should be list of strings
        self._report = report
        if servermap:
            from allmydata.mutable.servermap import ServerMap
            assert isinstance(servermap, ServerMap), servermap
        self._servermap = servermap # mutable only
        self._share_problems = share_problems

    def get_storage_index(self):
        return self._storage_index
    def get_storage_index_string(self):
        return base32.b2a(self._storage_index)
    def get_uri(self):
        return self._uri

    def is_healthy(self):
        return self._healthy
    def is_recoverable(self):
        return self._recoverable

    def get_happiness(self):
        return self._count_happiness

    def get_encoding_needed(self):
        return self._count_shares_needed
    def get_encoding_expected(self):
        return self._count_shares_expected

    def get_share_counter_good(self):
        return self._count_shares_good
    def get_share_counter_wrong(self):
        return self._count_wrong_shares

    def get_corrupt_shares(self):
        return self._list_corrupt_shares

    def get_incompatible_shares(self):
        return self._list_incompatible_shares

    def get_servers_responding(self):
        return self._servers_responding

    def get_host_counter_good_shares(self):
        return self._count_good_share_hosts

    def get_version_counter_recoverable(self):
        return self._count_recoverable_versions
    def get_version_counter_unrecoverable(self):
        return self._count_unrecoverable_versions

    def get_sharemap(self):
        return self._sharemap

    def as_dict(self):
        sharemap = {}
        for shnum, servers in self._sharemap.items():
            sharemap[shnum] = sorted([s.get_serverid() for s in servers])
        responding = [s.get_serverid() for s in self._servers_responding]
        corrupt = [(s.get_serverid(), SI, shnum)
                   for (s, SI, shnum) in self._list_corrupt_shares]
        incompatible = [(s.get_serverid(), SI, shnum)
                        for (s, SI, shnum) in self._list_incompatible_shares]
        d = {"count-happiness": self._count_happiness,
             "count-shares-needed": self._count_shares_needed,
             "count-shares-expected": self._count_shares_expected,
             "count-shares-good": self._count_shares_good,
             "count-good-share-hosts": self._count_good_share_hosts,
             "count-recoverable-versions": self._count_recoverable_versions,
             "count-unrecoverable-versions": self._count_unrecoverable_versions,
             "servers-responding": responding,
             "sharemap": sharemap,
             "count-wrong-shares": self._count_wrong_shares,
             "list-corrupt-shares": corrupt,
             "count-corrupt-shares": self._count_corrupt_shares,
             "list-incompatible-shares": incompatible,
             "count-incompatible-shares": self._count_incompatible_shares,
             }
        return d

    def get_summary(self):
        return self._summary
    def get_report(self):
        return self._report
    def get_share_problems(self):
        return self._share_problems
    def get_servermap(self):
        return self._servermap

@implementer(ICheckAndRepairResults)
class CheckAndRepairResults(object):

    def __init__(self, storage_index):
        self.storage_index = storage_index
        self.repair_attempted = False

    def get_storage_index(self):
        return self.storage_index
    def get_storage_index_string(self):
        return base32.b2a(self.storage_index)
    def get_repair_attempted(self):
        return self.repair_attempted
    def get_repair_successful(self):
        if not self.repair_attempted:
            return False
        return self.repair_successful
    def get_pre_repair_results(self):
        return self.pre_repair_results
    def get_post_repair_results(self):
        return self.post_repair_results


class DeepResultsBase(object):

    def __init__(self, root_storage_index):
        self.root_storage_index = root_storage_index
        if root_storage_index is None:
            self.root_storage_index_s = "<none>"  # is this correct?
        else:
            self.root_storage_index_s = base32.b2a(root_storage_index)

        self.objects_checked = 0
        self.objects_healthy = 0
        self.objects_unhealthy = 0
        self.objects_unrecoverable = 0
        self.corrupt_shares = []
        self.all_results = {}
        self.all_results_by_storage_index = {}
        self.stats = {}

    def update_stats(self, new_stats):
        self.stats.update(new_stats)

    def get_root_storage_index_string(self):
        return self.root_storage_index_s

    def get_corrupt_shares(self):
        return self.corrupt_shares

    def get_all_results(self):
        return self.all_results

    def get_results_for_storage_index(self, storage_index):
        return self.all_results_by_storage_index[storage_index]

    def get_stats(self):
        return self.stats


@implementer(IDeepCheckResults)
class DeepCheckResults(DeepResultsBase):

    def add_check(self, r, path):
        if not r:
            return # non-distributed object, i.e. LIT file
        r = ICheckResults(r)
        assert isinstance(path, (list, tuple))
        self.objects_checked += 1
        if r.is_healthy():
            self.objects_healthy += 1
        else:
            self.objects_unhealthy += 1
        if not r.is_recoverable():
            self.objects_unrecoverable += 1
        self.all_results[tuple(path)] = r
        self.all_results_by_storage_index[r.get_storage_index()] = r
        self.corrupt_shares.extend(r.get_corrupt_shares())

    def get_counters(self):
        return {"count-objects-checked": self.objects_checked,
                "count-objects-healthy": self.objects_healthy,
                "count-objects-unhealthy": self.objects_unhealthy,
                "count-objects-unrecoverable": self.objects_unrecoverable,
                "count-corrupt-shares": len(self.corrupt_shares),
                }


@implementer(IDeepCheckAndRepairResults)
class DeepCheckAndRepairResults(DeepResultsBase):

    def __init__(self, root_storage_index):
        DeepResultsBase.__init__(self, root_storage_index)
        self.objects_healthy_post_repair = 0
        self.objects_unhealthy_post_repair = 0
        self.objects_unrecoverable_post_repair = 0
        self.repairs_attempted = 0
        self.repairs_successful = 0
        self.repairs_unsuccessful = 0
        self.corrupt_shares_post_repair = []

    def add_check_and_repair(self, r, path):
        if not r:
            return # non-distributed object, i.e. LIT file
        r = ICheckAndRepairResults(r)
        assert isinstance(path, (list, tuple))
        pre_repair = r.get_pre_repair_results()
        post_repair = r.get_post_repair_results()
        self.objects_checked += 1
        if pre_repair.is_healthy():
            self.objects_healthy += 1
        else:
            self.objects_unhealthy += 1
        if not pre_repair.is_recoverable():
            self.objects_unrecoverable += 1
        self.corrupt_shares.extend(pre_repair.get_corrupt_shares())
        if r.get_repair_attempted():
            self.repairs_attempted += 1
            if r.get_repair_successful():
                self.repairs_successful += 1
            else:
                self.repairs_unsuccessful += 1
        if post_repair.is_healthy():
            self.objects_healthy_post_repair += 1
        else:
            self.objects_unhealthy_post_repair += 1
        if not post_repair.is_recoverable():
            self.objects_unrecoverable_post_repair += 1
        self.all_results[tuple(path)] = r
        self.all_results_by_storage_index[r.get_storage_index()] = r
        self.corrupt_shares_post_repair.extend(post_repair.get_corrupt_shares())

    def get_counters(self):
        return {"count-objects-checked": self.objects_checked,
                "count-objects-healthy-pre-repair": self.objects_healthy,
                "count-objects-unhealthy-pre-repair": self.objects_unhealthy,
                "count-objects-unrecoverable-pre-repair": self.objects_unrecoverable,
                "count-objects-healthy-post-repair": self.objects_healthy_post_repair,
                "count-objects-unhealthy-post-repair": self.objects_unhealthy_post_repair,
                "count-objects-unrecoverable-post-repair": self.objects_unrecoverable_post_repair,
                "count-repairs-attempted": self.repairs_attempted,
                "count-repairs-successful": self.repairs_successful,
                "count-repairs-unsuccessful": self.repairs_unsuccessful,
                "count-corrupt-shares-pre-repair": len(self.corrupt_shares),
                "count-corrupt-shares-post-repair": len(self.corrupt_shares_post_repair),
                }

    def get_remaining_corrupt_shares(self):
        return self.corrupt_shares_post_repair
