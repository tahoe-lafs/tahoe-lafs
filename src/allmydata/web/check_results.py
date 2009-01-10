
import time
import simplejson
from nevow import rend, inevow, tags as T
from twisted.web import http, html
from allmydata.web.common import getxmlfile, get_arg, get_root, \
     IClient, WebError
from allmydata.web.operations import ReloadMixin
from allmydata.interfaces import ICheckAndRepairResults, ICheckResults
from allmydata.util import base32, idlib

class ResultsBase:
    def _render_results(self, ctx, cr):
        assert ICheckResults(cr)
        c = IClient(ctx)
        data = cr.get_data()
        r = []
        def add(name, value):
            r.append(T.li[name + ": ", value])

        add("Report", T.pre["\n".join(self._html(cr.get_report()))])
        add("Share Counts",
            "need %d-of-%d, have %d" % (data["count-shares-needed"],
                                        data["count-shares-expected"],
                                        data["count-shares-good"]))
        add("Hosts with good shares", data["count-good-share-hosts"])

        if data["list-corrupt-shares"]:
            badsharemap = []
            for (serverid, si, shnum) in data["list-corrupt-shares"]:
                nickname = c.get_nickname_for_peerid(serverid)
                badsharemap.append(T.tr[T.td["sh#%d" % shnum],
                                        T.td[T.tt[base32.b2a(serverid)],
                                             " (", nickname, ")"],
                                        ])
            add("Corrupt shares", T.table(border="1")[badsharemap])
        else:
            add("Corrupt shares", "none")

        add("Wrong Shares", data["count-wrong-shares"])

        sharemap = []
        servers = {}

        for shareid in sorted(data["sharemap"].keys()):
            serverids = data["sharemap"][shareid]
            for i,serverid in enumerate(serverids):
                if serverid not in servers:
                    servers[serverid] = []
                servers[serverid].append(shareid)
                shareid_s = ""
                if i == 0:
                    shareid_s = shareid
                nickname = c.get_nickname_for_peerid(serverid)
                sharemap.append(T.tr[T.td[shareid_s],
                                     T.td[T.tt[base32.b2a(serverid)],
                                          " (", nickname, ")"],
                                     ])
        add("Good Shares (sorted in share order)",
            T.table(border="1")[sharemap])


        add("Recoverable Versions", data["count-recoverable-versions"])
        add("Unrecoverable Versions", data["count-unrecoverable-versions"])

        # this table is sorted by permuted order
        permuted_peer_ids = [peerid
                             for (peerid, rref)
                             in c.get_permuted_peers("storage",
                                                     cr.get_storage_index())]

        num_shares_left = sum([len(shares) for shares in servers.values()])
        servermap = []
        for serverid in permuted_peer_ids:
            nickname = c.get_nickname_for_peerid(serverid)
            shareids = servers.get(serverid, [])
            shareids.reverse()
            shareids_s = [ T.tt[shareid, " "] for shareid in shareids ]
            servermap.append(T.tr[T.td[T.tt[base32.b2a(serverid)],
                                       " (", nickname, ")"],
                                  T.td[shareids_s] ])
            num_shares_left -= len(shareids)
            if not num_shares_left:
                break
        add("Share Balancing (servers in permuted order)",
            T.table(border="1")[servermap])

        return T.ul[r]

    def _json_check_and_repair_results(self, r):
        data = {}
        data["storage-index"] = r.get_storage_index_string()
        data["repair-attempted"] = r.get_repair_attempted()
        data["repair-successful"] = r.get_repair_successful()
        pre = r.get_pre_repair_results()
        data["pre-repair-results"] = self._json_check_results(pre)
        post = r.get_post_repair_results()
        data["post-repair-results"] = self._json_check_results(post)
        return data

    def _json_check_results(self, r):
        data = {}
        data["storage-index"] = r.get_storage_index_string()
        data["summary"] = r.get_summary()
        data["results"] = self._json_check_counts(r.get_data())
        data["results"]["needs-rebalancing"] = r.needs_rebalancing()
        data["results"]["healthy"] = r.is_healthy()
        data["results"]["recoverable"] = r.is_recoverable()
        return data

    def _json_check_counts(self, d):
        r = {}
        r["count-shares-good"] = d["count-shares-good"]
        r["count-shares-needed"] = d["count-shares-needed"]
        r["count-shares-expected"] = d["count-shares-expected"]
        r["count-good-share-hosts"] = d["count-good-share-hosts"]
        r["count-corrupt-shares"] = d["count-corrupt-shares"]
        r["list-corrupt-shares"] = [ (idlib.nodeid_b2a(serverid),
                                      base32.b2a(si), shnum)
                                     for (serverid, si, shnum)
                                     in d["list-corrupt-shares"] ]
        r["servers-responding"] = [idlib.nodeid_b2a(serverid)
                                   for serverid in d["servers-responding"]]
        sharemap = {}
        for (shareid, serverids) in d["sharemap"].items():
            sharemap[shareid] = [idlib.nodeid_b2a(serverid)
                                 for serverid in serverids]
        r["sharemap"] = sharemap

        r["count-wrong-shares"] = d["count-wrong-shares"]
        r["count-recoverable-versions"] = d["count-recoverable-versions"]
        r["count-unrecoverable-versions"] = d["count-unrecoverable-versions"]

        return r

    def _html(self, s):
        if isinstance(s, (str, unicode)):
            return html.escape(s)
        assert isinstance(s, (list, tuple))
        return [html.escape(w) for w in s]

    def want_json(self, ctx):
        output = get_arg(inevow.IRequest(ctx), "output", "").lower()
        if output.lower() == "json":
            return True
        return False

    def _render_si_link(self, ctx, storage_index):
        si_s = base32.b2a(storage_index)
        root = get_root(ctx)
        req = inevow.IRequest(ctx)
        ophandle = req.prepath[-1]
        target = "%s/operations/%s/%s" % (get_root(ctx), ophandle, si_s)
        output = get_arg(ctx, "output")
        if output:
            target = target + "?output=%s" % output
        return T.a(href=target)[si_s]

class LiteralCheckResults(rend.Page, ResultsBase):
    docFactory = getxmlfile("literal-check-results.xhtml")

    def renderHTTP(self, ctx):
        if self.want_json(ctx):
            return self.json(ctx)
        return rend.Page.renderHTTP(self, ctx)

    def json(self, ctx):
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        data = {"storage-index": "",
                "results": {"healthy": True},
                }
        return simplejson.dumps(data, indent=1) + "\n"

class CheckerBase:

    def renderHTTP(self, ctx):
        if self.want_json(ctx):
            return self.json(ctx)
        return rend.Page.renderHTTP(self, ctx)

    def render_storage_index(self, ctx, data):
        return self.r.get_storage_index_string()

    def render_return(self, ctx, data):
        req = inevow.IRequest(ctx)
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return T.div[T.a(href=return_to)["Return to parent directory"]]
        return ""

class CheckResults(CheckerBase, rend.Page, ResultsBase):
    docFactory = getxmlfile("check-results.xhtml")

    def __init__(self, results):
        self.r = ICheckResults(results)

    def json(self, ctx):
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        data = self._json_check_results(self.r)
        return simplejson.dumps(data, indent=1) + "\n"

    def render_summary(self, ctx, data):
        results = []
        if self.r.is_healthy():
            results.append("Healthy")
        elif self.r.is_recoverable():
            results.append("Not Healthy!")
        else:
            results.append("Not Recoverable!")
        results.append(" : ")
        results.append(self._html(self.r.get_summary()))
        return ctx.tag[results]

    def render_repair(self, ctx, data):
        if self.r.is_healthy():
            return ""
        repair = T.form(action=".", method="post",
                        enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="check"),
            T.input(type="hidden", name="repair", value="true"),
            T.input(type="submit", value="Repair"),
            ]]
        return ctx.tag[repair]

    def render_rebalance(self, ctx, data):
        if self.r.needs_rebalancing():
            return ctx.tag["(needs rebalancing)"]
        return ctx.tag["(does not need rebalancing)"]

    def render_results(self, ctx, data):
        cr = self._render_results(ctx, self.r)
        return ctx.tag[cr]

class CheckAndRepairResults(CheckerBase, rend.Page, ResultsBase):
    docFactory = getxmlfile("check-and-repair-results.xhtml")

    def __init__(self, results):
        self.r = ICheckAndRepairResults(results)

    def json(self, ctx):
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        data = self._json_check_and_repair_results(self.r)
        return simplejson.dumps(data, indent=1) + "\n"

    def render_summary(self, ctx, data):
        cr = self.r.get_post_repair_results()
        results = []
        if cr.is_healthy():
            results.append("Healthy")
        elif cr.is_recoverable():
            results.append("Not Healthy!")
        else:
            results.append("Not Recoverable!")
        results.append(" : ")
        results.append(self._html(cr.get_summary()))
        return ctx.tag[results]

    def render_repair_results(self, ctx, data):
        if self.r.get_repair_attempted():
            if self.r.get_repair_successful():
                return ctx.tag["Repair successful"]
            else:
                return ctx.tag["Repair unsuccessful"]
        return ctx.tag["No repair necessary"]

    def render_post_repair_results(self, ctx, data):
        cr = self._render_results(ctx, self.r.get_post_repair_results())
        return ctx.tag[cr]

    def render_maybe_pre_repair_results(self, ctx, data):
        if self.r.get_repair_attempted():
            cr = self._render_results(ctx, self.r.get_pre_repair_results())
            return ctx.tag[T.div["Pre-Repair Checker Results:"], cr]
        return ""


class DeepCheckResults(rend.Page, ResultsBase, ReloadMixin):
    docFactory = getxmlfile("deep-check-results.xhtml")

    def __init__(self, monitor):
        self.monitor = monitor

    def childFactory(self, ctx, name):
        if not name:
            return self
        # /operation/$OPHANDLE/$STORAGEINDEX provides detailed information
        # about a specific file or directory that was checked
        si = base32.a2b(name)
        r = self.monitor.get_status()
        try:
            return CheckResults(r.get_results_for_storage_index(si))
        except KeyError:
            raise WebError("No detailed results for SI %s" % html.escape(name),
                           http.NOT_FOUND)

    def renderHTTP(self, ctx):
        if self.want_json(ctx):
            return self.json(ctx)
        return rend.Page.renderHTTP(self, ctx)

    def json(self, ctx):
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        data = {}
        data["finished"] = self.monitor.is_finished()
        res = self.monitor.get_status()
        data["root-storage-index"] = res.get_root_storage_index_string()
        c = res.get_counters()
        data["count-objects-checked"] = c["count-objects-checked"]
        data["count-objects-healthy"] = c["count-objects-healthy"]
        data["count-objects-unhealthy"] = c["count-objects-unhealthy"]
        data["count-corrupt-shares"] = c["count-corrupt-shares"]
        data["list-corrupt-shares"] = [ (idlib.nodeid_b2a(serverid),
                                         base32.b2a(storage_index),
                                         shnum)
                                        for (serverid, storage_index, shnum)
                                        in res.get_corrupt_shares() ]
        data["list-unhealthy-files"] = [ (path_t, self._json_check_results(r))
                                         for (path_t, r)
                                         in res.get_all_results().items()
                                         if not r.is_healthy() ]
        data["stats"] = res.get_stats()
        return simplejson.dumps(data, indent=1) + "\n"

    def render_root_storage_index(self, ctx, data):
        return self.monitor.get_status().get_root_storage_index_string()

    def data_objects_checked(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-checked"]
    def data_objects_healthy(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-healthy"]
    def data_objects_unhealthy(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-unhealthy"]
    def data_objects_unrecoverable(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-unrecoverable"]

    def data_count_corrupt_shares(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-corrupt-shares"]

    def render_problems_p(self, ctx, data):
        c = self.monitor.get_status().get_counters()
        if c["count-objects-unhealthy"]:
            return ctx.tag
        return ""

    def data_problems(self, ctx, data):
        all_objects = self.monitor.get_status().get_all_results()
        for path in sorted(all_objects.keys()):
            cr = all_objects[path]
            assert ICheckResults.providedBy(cr)
            if not cr.is_healthy():
                yield path, cr

    def render_problem(self, ctx, data):
        path, cr = data
        summary_text = ""
        summary = cr.get_summary()
        if summary:
            summary_text = ": " + summary
        summary_text += " [SI: %s]" % cr.get_storage_index_string()
        return ctx.tag["/".join(self._html(path)), self._html(summary_text)]


    def render_servers_with_corrupt_shares_p(self, ctx, data):
        if self.monitor.get_status().get_counters()["count-corrupt-shares"]:
            return ctx.tag
        return ""

    def data_servers_with_corrupt_shares(self, ctx, data):
        servers = [serverid
                   for (serverid, storage_index, sharenum)
                   in self.monitor.get_status().get_corrupt_shares()]
        servers.sort()
        return servers

    def render_server_problem(self, ctx, data):
        serverid = data
        data = [idlib.shortnodeid_b2a(serverid)]
        c = IClient(ctx)
        nickname = c.get_nickname_for_peerid(serverid)
        if nickname:
            data.append(" (%s)" % self._html(nickname))
        return ctx.tag[data]


    def render_corrupt_shares_p(self, ctx, data):
        if self.monitor.get_status().get_counters()["count-corrupt-shares"]:
            return ctx.tag
        return ""
    def data_corrupt_shares(self, ctx, data):
        return self.monitor.get_status().get_corrupt_shares()
    def render_share_problem(self, ctx, data):
        serverid, storage_index, sharenum = data
        nickname = IClient(ctx).get_nickname_for_peerid(serverid)
        ctx.fillSlots("serverid", idlib.shortnodeid_b2a(serverid))
        if nickname:
            ctx.fillSlots("nickname", self._html(nickname))
        ctx.fillSlots("si", self._render_si_link(ctx, storage_index))
        ctx.fillSlots("shnum", str(sharenum))
        return ctx.tag

    def render_return(self, ctx, data):
        req = inevow.IRequest(ctx)
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return T.div[T.a(href=return_to)["Return to parent directory"]]
        return ""

    def data_all_objects(self, ctx, data):
        r = self.monitor.get_status().get_all_results()
        for path in sorted(r.keys()):
            yield (path, r[path])

    def render_object(self, ctx, data):
        path, r = data
        if path:
            pathstring = "/".join(self._html(path))
        else:
            pathstring = "<root>"
        ctx.fillSlots("path", pathstring)
        ctx.fillSlots("healthy", str(r.is_healthy()))
        ctx.fillSlots("recoverable", str(r.is_recoverable()))
        storage_index = r.get_storage_index()
        ctx.fillSlots("storage_index", self._render_si_link(ctx, storage_index))
        ctx.fillSlots("summary", self._html(r.get_summary()))
        return ctx.tag

    def render_runtime(self, ctx, data):
        req = inevow.IRequest(ctx)
        runtime = time.time() - req.processing_started_timestamp
        return ctx.tag["runtime: %s seconds" % runtime]

class DeepCheckAndRepairResults(rend.Page, ResultsBase, ReloadMixin):
    docFactory = getxmlfile("deep-check-and-repair-results.xhtml")

    def __init__(self, monitor):
        #assert IDeepCheckAndRepairResults(results)
        #self.r = results
        self.monitor = monitor

    def renderHTTP(self, ctx):
        if self.want_json(ctx):
            return self.json(ctx)
        return rend.Page.renderHTTP(self, ctx)

    def json(self, ctx):
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        res = self.monitor.get_status()
        data = {}
        data["finished"] = self.monitor.is_finished()
        data["root-storage-index"] = res.get_root_storage_index_string()
        c = res.get_counters()
        data["count-objects-checked"] = c["count-objects-checked"]

        data["count-objects-healthy-pre-repair"] = c["count-objects-healthy-pre-repair"]
        data["count-objects-unhealthy-pre-repair"] = c["count-objects-unhealthy-pre-repair"]
        data["count-objects-healthy-post-repair"] = c["count-objects-healthy-post-repair"]
        data["count-objects-unhealthy-post-repair"] = c["count-objects-unhealthy-post-repair"]

        data["count-repairs-attempted"] = c["count-repairs-attempted"]
        data["count-repairs-successful"] = c["count-repairs-successful"]
        data["count-repairs-unsuccessful"] = c["count-repairs-unsuccessful"]

        data["count-corrupt-shares-pre-repair"] = c["count-corrupt-shares-pre-repair"]
        data["count-corrupt-shares-post-repair"] = c["count-corrupt-shares-pre-repair"]

        data["list-corrupt-shares"] = [ (idlib.nodeid_b2a(serverid),
                                         base32.b2a(storage_index),
                                         shnum)
                                        for (serverid, storage_index, shnum)
                                        in res.get_corrupt_shares() ]

        remaining_corrupt = [ (idlib.nodeid_b2a(serverid),
                               base32.b2a(storage_index),
                               shnum)
                              for (serverid, storage_index, shnum)
                              in res.get_remaining_corrupt_shares() ]
        data["list-remaining-corrupt-shares"] = remaining_corrupt

        unhealthy = [ (path_t,
                       self._json_check_results(crr.get_pre_repair_results()))
                      for (path_t, crr)
                      in res.get_all_results().items()
                      if not crr.get_pre_repair_results().is_healthy() ]
        data["list-unhealthy-files"] = unhealthy
        data["stats"] = res.get_stats()
        return simplejson.dumps(data, indent=1) + "\n"

    def render_root_storage_index(self, ctx, data):
        return self.monitor.get_status().get_root_storage_index_string()

    def data_objects_checked(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-checked"]

    def data_objects_healthy(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-healthy-pre-repair"]
    def data_objects_unhealthy(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-unhealthy-pre-repair"]
    def data_corrupt_shares(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-corrupt-shares-pre-repair"]

    def data_repairs_attempted(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-repairs-attempted"]
    def data_repairs_successful(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-repairs-successful"]
    def data_repairs_unsuccessful(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-repairs-unsuccessful"]

    def data_objects_healthy_post(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-healthy-post-repair"]
    def data_objects_unhealthy_post(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-objects-unhealthy-post-repair"]
    def data_corrupt_shares_post(self, ctx, data):
        return self.monitor.get_status().get_counters()["count-corrupt-shares-post-repair"]

    def render_pre_repair_problems_p(self, ctx, data):
        c = self.monitor.get_status().get_counters()
        if c["count-objects-unhealthy-pre-repair"]:
            return ctx.tag
        return ""

    def data_pre_repair_problems(self, ctx, data):
        all_objects = self.monitor.get_status().get_all_results()
        for path in sorted(all_objects.keys()):
            r = all_objects[path]
            assert ICheckAndRepairResults.providedBy(r)
            cr = r.get_pre_repair_results()
            if not cr.is_healthy():
                yield path, cr

    def render_problem(self, ctx, data):
        path, cr = data
        return ["/".join(self._html(path)), ": ", self._html(cr.get_summary())]

    def render_post_repair_problems_p(self, ctx, data):
        c = self.monitor.get_status().get_counters()
        if (c["count-objects-unhealthy-post-repair"]
            or c["count-corrupt-shares-post-repair"]):
            return ctx.tag
        return ""

    def data_post_repair_problems(self, ctx, data):
        all_objects = self.monitor.get_status().get_all_results()
        for path in sorted(all_objects.keys()):
            r = all_objects[path]
            assert ICheckAndRepairResults.providedBy(r)
            cr = r.get_post_repair_results()
            if not cr.is_healthy():
                yield path, cr

    def render_servers_with_corrupt_shares_p(self, ctx, data):
        if self.monitor.get_status().get_counters()["count-corrupt-shares-pre-repair"]:
            return ctx.tag
        return ""
    def data_servers_with_corrupt_shares(self, ctx, data):
        return [] # TODO
    def render_server_problem(self, ctx, data):
        pass


    def render_remaining_corrupt_shares_p(self, ctx, data):
        if self.monitor.get_status().get_counters()["count-corrupt-shares-post-repair"]:
            return ctx.tag
        return ""
    def data_post_repair_corrupt_shares(self, ctx, data):
        return [] # TODO

    def render_share_problem(self, ctx, data):
        pass


    def render_return(self, ctx, data):
        req = inevow.IRequest(ctx)
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return T.div[T.a(href=return_to)["Return to parent directory"]]
        return ""

    def data_all_objects(self, ctx, data):
        r = self.monitor.get_status().get_all_results()
        for path in sorted(r.keys()):
            yield (path, r[path])

    def render_object(self, ctx, data):
        path, r = data
        ctx.fillSlots("path", "/".join(self._html(path)))
        ctx.fillSlots("healthy_pre_repair",
                      str(r.get_pre_repair_results().is_healthy()))
        ctx.fillSlots("healthy_post_repair",
                      str(r.get_post_repair_results().is_healthy()))
        ctx.fillSlots("summary",
                      self._html(r.get_pre_repair_results().get_summary()))
        return ctx.tag

    def render_runtime(self, ctx, data):
        req = inevow.IRequest(ctx)
        runtime = time.time() - req.processing_started_timestamp
        return ctx.tag["runtime: %s seconds" % runtime]
