
import time
from nevow import rend, inevow, tags as T
from twisted.web import html
from allmydata.web.common import getxmlfile, get_arg, IClient
from allmydata.interfaces import ICheckAndRepairResults, ICheckerResults, \
     IDeepCheckResults, IDeepCheckAndRepairResults
from allmydata.util import base32, idlib

class ResultsBase:
    def _render_results(self, cr):
        assert ICheckerResults(cr)
        return T.pre["\n".join(self._html(cr.get_report()))] # TODO: more
    def _html(self, s):
        if isinstance(s, (str, unicode)):
            return html.escape(s)
        assert isinstance(s, (list, tuple))
        return [html.escape(w) for w in s]

class CheckerResults(rend.Page, ResultsBase):
    docFactory = getxmlfile("checker-results.xhtml")

    def __init__(self, results):
        self.r = ICheckerResults(results)

    def render_storage_index(self, ctx, data):
        return self.r.get_storage_index_string()

    def render_healthy(self, ctx, data):
        if self.r.is_healthy():
            return ctx.tag["Healthy!"]
        return ctx.tag["Not Healthy!:", self._html(self.r.get_summary())]

    def render_results(self, ctx, data):
        cr = self._render_results(self.r)
        return ctx.tag[cr]

    def render_return(self, ctx, data):
        req = inevow.IRequest(ctx)
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return T.div[T.a(href=return_to)["Return to parent directory"]]
        return ""

class CheckAndRepairResults(rend.Page, ResultsBase):
    docFactory = getxmlfile("check-and-repair-results.xhtml")

    def __init__(self, results):
        self.r = ICheckAndRepairResults(results)

    def render_storage_index(self, ctx, data):
        return self.r.get_storage_index_string()

    def render_healthy(self, ctx, data):
        cr = self.r.get_post_repair_results()
        if cr.is_healthy():
            return ctx.tag["Healthy!"]
        return ctx.tag["Not Healthy!:", self._html(cr.get_summary())]

    def render_repair_results(self, ctx, data):
        if self.r.get_repair_attempted():
            if self.r.get_repair_successful():
                return ctx.tag["Repair successful"]
            else:
                return ctx.tag["Repair unsuccessful"]
        return ctx.tag["No repair necessary"]

    def render_post_repair_results(self, ctx, data):
        cr = self._render_results(self.r.get_post_repair_results())
        return ctx.tag[cr]

    def render_maybe_pre_repair_results(self, ctx, data):
        if self.r.get_repair_attempted():
            cr = self._render_results(self.r.get_pre_repair_results())
            return ctx.tag[T.div["Pre-Repair Checker Results:"], cr]
        return ""

    def render_return(self, ctx, data):
        req = inevow.IRequest(ctx)
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return T.div[T.a(href=return_to)["Return to parent directory"]]
        return ""

class DeepCheckResults(rend.Page, ResultsBase):
    docFactory = getxmlfile("deep-check-results.xhtml")

    def __init__(self, results):
        assert IDeepCheckResults(results)
        self.r = results

    def render_root_storage_index(self, ctx, data):
        return self.r.get_root_storage_index_string()

    def data_objects_checked(self, ctx, data):
        return self.r.get_counters()["count-objects-checked"]
    def data_objects_healthy(self, ctx, data):
        return self.r.get_counters()["count-objects-healthy"]
    def data_objects_unhealthy(self, ctx, data):
        return self.r.get_counters()["count-objects-unhealthy"]

    def data_count_corrupt_shares(self, ctx, data):
        return self.r.get_counters()["count-corrupt-shares"]

    def render_problems_p(self, ctx, data):
        c = self.r.get_counters()
        if c["count-objects-unhealthy"]:
            return ctx.tag
        return ""

    def data_problems(self, ctx, data):
        all_objects = self.r.get_all_results()
        for path in sorted(all_objects.keys()):
            cr = all_objects[path]
            assert ICheckerResults.providedBy(cr)
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
        if self.r.get_counters()["count-corrupt-shares"]:
            return ctx.tag
        return ""

    def data_servers_with_corrupt_shares(self, ctx, data):
        servers = [serverid
                   for (serverid, storage_index, sharenum)
                   in self.r.get_corrupt_shares()]
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
        if self.r.get_counters()["count-corrupt-shares"]:
            return ctx.tag
        return ""
    def data_corrupt_shares(self, ctx, data):
        return self.r.get_corrupt_shares()
    def render_share_problem(self, ctx, data):
        serverid, storage_index, sharenum = data
        nickname = IClient(ctx).get_nickname_for_peerid(serverid)
        ctx.fillSlots("serverid", idlib.shortnodeid_b2a(serverid))
        if nickname:
            ctx.fillSlots("nickname", self._html(nickname))
        ctx.fillSlots("si", base32.b2a(storage_index))
        ctx.fillSlots("shnum", str(sharenum))
        return ctx.tag

    def render_return(self, ctx, data):
        req = inevow.IRequest(ctx)
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return T.div[T.a(href=return_to)["Return to parent directory"]]
        return ""

    def data_all_objects(self, ctx, data):
        r = self.r.get_all_results()
        for path in sorted(r.keys()):
            yield (path, r[path])

    def render_object(self, ctx, data):
        path, r = data
        ctx.fillSlots("path", "/".join(self._html(path)))
        ctx.fillSlots("healthy", str(r.is_healthy()))
        ctx.fillSlots("summary", self._html(r.get_summary()))
        return ctx.tag

    def render_runtime(self, ctx, data):
        req = inevow.IRequest(ctx)
        runtime = time.time() - req.processing_started_timestamp
        return ctx.tag["runtime: %s seconds" % runtime]

class DeepCheckAndRepairResults(rend.Page, ResultsBase):
    docFactory = getxmlfile("deep-check-and-repair-results.xhtml")

    def __init__(self, results):
        assert IDeepCheckAndRepairResults(results)
        self.r = results

    def render_root_storage_index(self, ctx, data):
        return self.r.get_root_storage_index_string()

    def data_objects_checked(self, ctx, data):
        return self.r.get_counters()["count-objects-checked"]

    def data_objects_healthy(self, ctx, data):
        return self.r.get_counters()["count-objects-healthy-pre-repair"]
    def data_objects_unhealthy(self, ctx, data):
        return self.r.get_counters()["count-objects-unhealthy-pre-repair"]
    def data_corrupt_shares(self, ctx, data):
        return self.r.get_counters()["count-corrupt-shares-pre-repair"]

    def data_repairs_attempted(self, ctx, data):
        return self.r.get_counters()["count-repairs-attempted"]
    def data_repairs_successful(self, ctx, data):
        return self.r.get_counters()["count-repairs-successful"]
    def data_repairs_unsuccessful(self, ctx, data):
        return self.r.get_counters()["count-repairs-unsuccessful"]

    def data_objects_healthy_post(self, ctx, data):
        return self.r.get_counters()["count-objects-healthy-post-repair"]
    def data_objects_unhealthy_post(self, ctx, data):
        return self.r.get_counters()["count-objects-unhealthy-post-repair"]
    def data_corrupt_shares_post(self, ctx, data):
        return self.r.get_counters()["count-corrupt-shares-post-repair"]

    def render_pre_repair_problems_p(self, ctx, data):
        c = self.r.get_counters()
        if c["count-objects-unhealthy-pre-repair"]:
            return ctx.tag
        return ""

    def data_pre_repair_problems(self, ctx, data):
        all_objects = self.r.get_all_results()
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
        c = self.r.get_counters()
        if (c["count-objects-unhealthy-post-repair"]
            or c["count-corrupt-shares-post-repair"]):
            return ctx.tag
        return ""

    def data_post_repair_problems(self, ctx, data):
        all_objects = self.r.get_all_results()
        for path in sorted(all_objects.keys()):
            r = all_objects[path]
            assert ICheckAndRepairResults.providedBy(r)
            cr = r.get_post_repair_results()
            if not cr.is_healthy():
                yield path, cr

    def render_servers_with_corrupt_shares_p(self, ctx, data):
        if self.r.get_counters()["count-corrupt-shares-pre-repair"]:
            return ctx.tag
        return ""
    def data_servers_with_corrupt_shares(self, ctx, data):
        return [] # TODO
    def render_server_problem(self, ctx, data):
        pass


    def render_remaining_corrupt_shares_p(self, ctx, data):
        if self.r.get_counters()["count-corrupt-shares-post-repair"]:
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
        r = self.r.get_all_results()
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
