
from zope.interface import implements
from allmydata.interfaces import ICheckResults, ICheckAndRepairResults, \
     IDeepCheckResults, IDeepCheckAndRepairResults, IURI
from allmydata.util import base32

class CheckerResults:
    implements(ICheckResults)

    def __init__(self, uri, storage_index):
        assert IURI.providedBy(uri), uri
        self.uri = uri
        self.storage_index = storage_index
        self.problems = []
        self.data = {"count-corrupt-shares": 0,
                     "list-corrupt-shares": [],
                     }
        self.summary = ""
        self.report = []

    def set_healthy(self, healthy):
        self.healthy = bool(healthy)
        if self.healthy:
            assert (not hasattr(self, 'recoverable')) or self.recoverable, hasattr(self, 'recoverable') and self.recoverable
            self.recoverable = True
            self.summary = "healthy"
        else:
            self.summary = "not healthy"
    def set_recoverable(self, recoverable):
        self.recoverable = recoverable
        if not self.recoverable:
            assert (not hasattr(self, 'healthy')) or not self.healthy
            self.healthy = False
    def set_needs_rebalancing(self, needs_rebalancing):
        self.needs_rebalancing_p = bool(needs_rebalancing)
    def set_data(self, data):
        self.data.update(data)
    def set_summary(self, summary):
        assert isinstance(summary, str) # should be a single string
        self.summary = summary
    def set_report(self, report):
        assert not isinstance(report, str) # should be list of strings
        self.report = report

    def set_servermap(self, smap):
        # mutable only
        self.servermap = smap


    def get_storage_index(self):
        return self.storage_index
    def get_storage_index_string(self):
        return base32.b2a(self.storage_index)
    def get_uri(self):
        return self.uri

    def is_healthy(self):
        return self.healthy
    def is_recoverable(self):
        return self.recoverable

    def needs_rebalancing(self):
        return self.needs_rebalancing_p
    def get_data(self):
        return self.data

    def get_summary(self):
        return self.summary
    def get_report(self):
        return self.report
    def get_servermap(self):
        return self.servermap

class CheckAndRepairResults:
    implements(ICheckAndRepairResults)

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


class DeepResultsBase:

    def __init__(self, root_storage_index):
        self.root_storage_index = root_storage_index
        if root_storage_index is None:
            self.root_storage_index_s = "<none>"
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


class DeepCheckResults(DeepResultsBase):
    implements(IDeepCheckResults)

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
        self.corrupt_shares.extend(r.get_data()["list-corrupt-shares"])

    def get_counters(self):
        return {"count-objects-checked": self.objects_checked,
                "count-objects-healthy": self.objects_healthy,
                "count-objects-unhealthy": self.objects_unhealthy,
                "count-objects-unrecoverable": self.objects_unrecoverable,
                "count-corrupt-shares": len(self.corrupt_shares),
                }


class DeepCheckAndRepairResults(DeepResultsBase):
    implements(IDeepCheckAndRepairResults)

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
        self.corrupt_shares.extend(pre_repair.get_data()["list-corrupt-shares"])
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
        self.corrupt_shares_post_repair.extend(post_repair.get_data()["list-corrupt-shares"])

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
