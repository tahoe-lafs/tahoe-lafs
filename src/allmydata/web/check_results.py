from future.builtins import str

import time

from twisted.web import (
    http,
    html,
)
from twisted.python.filepath import FilePath
from twisted.web.template import (
    Element,
    XMLFile,
    renderer,
    renderElement,
    tags,
)
from allmydata.web.common import (
    exception_to_child,
    get_arg,
    get_root,
    render_exception,
    WebError,
    MultiFormatResource,
    SlotsSequenceElement,
)
from allmydata.web.operations import ReloadMixin
from allmydata.interfaces import (
    ICheckAndRepairResults,
    ICheckResults,
)
from allmydata.util import (
    base32,
    dictutil,
    jsonbytes as json,  # Supporting dumping bytes
)


def json_check_counts(r):
    d = {"count-happiness": r.get_happiness(),
         "count-shares-good": r.get_share_counter_good(),
         "count-shares-needed": r.get_encoding_needed(),
         "count-shares-expected": r.get_encoding_expected(),
         "count-good-share-hosts": r.get_host_counter_good_shares(),
         "count-corrupt-shares": len(r.get_corrupt_shares()),
         "list-corrupt-shares": [ (s.get_longname(), base32.b2a(si), shnum)
                                  for (s, si, shnum)
                                  in r.get_corrupt_shares() ],
         "servers-responding": [s.get_longname()
                                for s in r.get_servers_responding()],
         "sharemap": dict([(shareid,
                            sorted([s.get_longname() for s in servers]))
                           for (shareid, servers)
                           in r.get_sharemap().items()]),
         "count-wrong-shares": r.get_share_counter_wrong(),
         "count-recoverable-versions": r.get_version_counter_recoverable(),
         "count-unrecoverable-versions": r.get_version_counter_unrecoverable(),
         }
    return d

def json_check_results(r):
    if r is None:
        # LIT file
        data = {"storage-index": "",
                "results": {"healthy": True},
                }
        return data
    data = {}
    data["storage-index"] = r.get_storage_index_string()
    data["summary"] = r.get_summary()
    data["results"] = json_check_counts(r)
    data["results"]["healthy"] = r.is_healthy()
    data["results"]["recoverable"] = r.is_recoverable()
    return data

def json_check_and_repair_results(r):
    if r is None:
        # LIT file
        data = {"storage-index": "",
                "repair-attempted": False,
                }
        return data
    data = {}
    data["storage-index"] = r.get_storage_index_string()
    data["repair-attempted"] = r.get_repair_attempted()
    data["repair-successful"] = r.get_repair_successful()
    pre = r.get_pre_repair_results()
    data["pre-repair-results"] = json_check_results(pre)
    post = r.get_post_repair_results()
    data["post-repair-results"] = json_check_results(post)
    return data

class ResultsBase(object):
    # self._client must point to the Client, so we can get nicknames and
    # determine the permuted peer order

    def _join_pathstring(self, path):
        """
        :param tuple path: a path represented by a tuple, such as
            ``(u'some', u'dir', u'file')``.

        :return: a string joined by path separaters, such as
            ``u'some/dir/file'``.
        """
        if path:
            pathstring = "/".join(self._html(path))
        else:
            pathstring = "<root>"
        return pathstring

    def _render_results(self, req, cr):
        assert ICheckResults(cr)
        c = self._client
        sb = c.get_storage_broker()
        r = []
        def add(name, value):
            r.append(tags.li(name + ": ", value))

        add("Report", tags.pre("\n".join(self._html(cr.get_report()))))

        add("Share Counts",
            "need %d-of-%d, have %d" % (cr.get_encoding_needed(),
                                        cr.get_encoding_expected(),
                                        cr.get_share_counter_good()))
        add("Happiness Level", str(cr.get_happiness()))
        add("Hosts with good shares", str(cr.get_host_counter_good_shares()))

        if cr.get_corrupt_shares():
            badsharemap = []
            for (s, si, shnum) in cr.get_corrupt_shares():
                d = tags.tr(tags.td("sh#%d" % shnum),
                            tags.td(tags.div(s.get_nickname(), class_="nickname"),
                                    tags.div(tags.tt(s.get_name()), class_="nodeid")),)
                badsharemap.append(d)
            add("Corrupt shares",
                tags.table(
                    tags.tr(tags.th("Share ID"),
                            tags.th((tags.div("Nickname"), tags.div("Node ID", class_="nodeid")), class_="nickname-and-peerid")),
                    badsharemap))
        else:
            add("Corrupt shares", "none")

        add("Wrong Shares", str(cr.get_share_counter_wrong()))

        sharemap_data = []
        shares_on_server = dictutil.DictOfSets()

        # FIXME: The two tables below contain nickname-and-nodeid
        # table column markup which is duplicated with each other,
        # introducer.xhtml, and deep-check-results.xhtml. All of these
        # (and any other presentations of nickname-and-nodeid) should be combined.

        for shareid in sorted(cr.get_sharemap().keys()):
            servers = sorted(cr.get_sharemap()[shareid],
                             key=lambda s: s.get_longname())
            for i,s in enumerate(servers):
                shares_on_server.add(s, shareid)
                shareid_s = ""
                if i == 0:
                    shareid_s = str(shareid)
                d = tags.tr(tags.td(shareid_s),
                            tags.td(tags.div(s.get_nickname(), class_="nickname"),
                                    tags.div(tags.tt(s.get_name()), class_="nodeid")))
                sharemap_data.append(d)

        add("Good Shares (sorted in share order)",
            tags.table(tags.tr(tags.th("Share ID"),
                               tags.th(tags.div("Nickname"),
                                       tags.div("Node ID", class_="nodeid"), class_="nickname-and-peerid")),
                       sharemap_data))

        add("Recoverable Versions", str(cr.get_version_counter_recoverable()))
        add("Unrecoverable Versions", str(cr.get_version_counter_unrecoverable()))

        # this table is sorted by permuted order
        permuted_servers = [s
                            for s
                            in sb.get_servers_for_psi(cr.get_storage_index())]

        num_shares_left = sum([len(shareids)
                               for shareids in shares_on_server.values()])
        servermap = []
        for s in permuted_servers:
            shareids = list(shares_on_server.get(s, []))
            shareids.reverse()
            shareids_s = [tags.tt(str(shareid), " ") for shareid in sorted(shareids)]

            d = tags.tr(tags.td(tags.div(s.get_nickname(), class_="nickname"),
                             tags.div(tags.tt(s.get_name()), class_="nodeid")),
                        tags.td(shareids_s), )
            servermap.append(d)
            num_shares_left -= len(shareids)
            if not num_shares_left:
                break

        add("Share Balancing (servers in permuted order)",
            tags.table(tags.tr(tags.th(tags.div("Nickname"),
                                    tags.div("Node ID", class_="nodeid"), class_="nickname-and-peerid"),
                            tags.th("Share IDs")),
                       servermap))

        return tags.ul(r)

    def _html(self, s):
        if isinstance(s, (bytes, str)):
            return html.escape(s)
        assert isinstance(s, (list, tuple))
        return [html.escape(w) for w in s]

    def _render_si_link(self, req, storage_index):
        si_s = base32.b2a(storage_index)
        ophandle = req.prepath[-1]
        target = "%s/operations/%s/%s" % (get_root(req), ophandle, si_s)
        output = get_arg(req, "output")
        if output:
            target = target + "?output=%s" % output
        return tags.a(si_s, href=target)


class LiteralCheckResultsRenderer(MultiFormatResource, ResultsBase):

    formatArgument = "output"

    def __init__(self, client):
        """
        :param allmydata.interfaces.IStatsProducer client: stats provider.
        """
        super(LiteralCheckResultsRenderer, self).__init__()
        self._client = client

    @render_exception
    def render_HTML(self, req):
        return renderElement(req, LiteralCheckResultsRendererElement())

    @render_exception
    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        data = json_check_results(None)
        return json.dumps(data, indent=1) + "\n"


class LiteralCheckResultsRendererElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("literal-check-results.xhtml"))

    def __init__(self):
        super(LiteralCheckResultsRendererElement, self).__init__()

    @renderer
    def return_to(self, req, tag):
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return tags.div(tags.a("Return to file.", href=return_to))
        return ""


class CheckerBase(object):

    @renderer
    def storage_index(self, req, tag):
        return self._results.get_storage_index_string()

    @renderer
    def return_to(self, req, tag):
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return tags.div(tags.a("Return to file/directory.", href=return_to))
        return ""


class CheckResultsRenderer(MultiFormatResource):

    formatArgument = "output"

    def __init__(self, client, results):
        """
        :param allmydata.interfaces.IStatsProducer client: stats provider.
        :param allmydata.interfaces.ICheckResults results: results of check/vefify operation.
        """
        super(CheckResultsRenderer, self).__init__()
        self._client = client
        self._results = ICheckResults(results)

    @render_exception
    def render_HTML(self, req):
        return renderElement(req, CheckResultsRendererElement(self._client, self._results))

    @render_exception
    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        data = json_check_results(self._results)
        return json.dumps(data, indent=1) + "\n"


class CheckResultsRendererElement(Element, CheckerBase, ResultsBase):

    loader = XMLFile(FilePath(__file__).sibling("check-results.xhtml"))

    def __init__(self, client, results):
        super(CheckResultsRendererElement, self).__init__()
        self._client = client
        self._results = results

    @renderer
    def summary(self, req, tag):
        results = []
        if self._results.is_healthy():
            results.append("Healthy")
        elif self._results.is_recoverable():
            results.append("Not Healthy!")
        else:
            results.append("Not Recoverable!")
        results.append(" : ")
        results.append(self._html(self._results.get_summary()))
        return tag(results)

    @renderer
    def repair(self, req, tag):
        if self._results.is_healthy():
            return ""

        #repair = T.form(action=".", method="post",
        #                enctype="multipart/form-data")[
        #    T.fieldset[
        #    T.input(type="hidden", name="t", value="check"),
        #    T.input(type="hidden", name="repair", value="true"),
        #    T.input(type="submit", value="Repair"),
        #    ]]
        #return ctx.tag[repair]

        return "" # repair button disabled until we make it work correctly,
                  # see #622 for details

    @renderer
    def results(self, req, tag):
        cr = self._render_results(req, self._results)
        return tag(cr)

class CheckAndRepairResultsRenderer(MultiFormatResource):

    formatArgument = "output"

    def __init__(self, client, results):
        """
        :param allmydata.interfaces.IStatsProducer client: stats provider.
        :param allmydata.interfaces.ICheckResults results: check/verify results.
        """
        super(CheckAndRepairResultsRenderer, self).__init__()
        self._client = client
        self._results = None
        if results:
            self._results = ICheckAndRepairResults(results)

    @render_exception
    def render_HTML(self, req):
        elem = CheckAndRepairResultsRendererElement(self._client, self._results)
        return renderElement(req, elem)

    @render_exception
    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        data = json_check_and_repair_results(self._results)
        return json.dumps(data, indent=1) + "\n"


class CheckAndRepairResultsRendererElement(Element, CheckerBase, ResultsBase):

    loader = XMLFile(FilePath(__file__).sibling("check-and-repair-results.xhtml"))

    def __init__(self, client, results):
        super(CheckAndRepairResultsRendererElement, self).__init__()
        self._client = client
        self._results = results

    @renderer
    def summary(self, req, tag):
        cr = self._results.get_post_repair_results()
        results = []
        if cr.is_healthy():
            results.append("Healthy")
        elif cr.is_recoverable():
            results.append("Not Healthy!")
        else:
            results.append("Not Recoverable!")
        results.append(" : ")
        results.append(self._html(cr.get_summary()))
        return tag(results)

    @renderer
    def repair_results(self, req, tag):
        if self._results.get_repair_attempted():
            if self._results.get_repair_successful():
                return tag("Repair successful")
            else:
                return tag("Repair unsuccessful")
        return tag("No repair necessary")

    @renderer
    def post_repair_results(self, req, tag):
        cr = self._render_results(req, self._results.get_post_repair_results())
        return tag(tags.div("Post-Repair Checker Results:"), cr)

    @renderer
    def maybe_pre_repair_results(self, req, tag):
        if self._results.get_repair_attempted():
            cr = self._render_results(req, self._results.get_pre_repair_results())
            return tag(tags.div("Pre-Repair Checker Results:"), cr)
        return ""


class DeepCheckResultsRenderer(MultiFormatResource):

    formatArgument = "output"

    def __init__(self, client, monitor):
        """
        :param allmydata.interfaces.IStatsProducer client: stats provider.
        :param allmydata.monitor.IMonitor monitor: status, progress, and cancellation provider.
        """
        super(DeepCheckResultsRenderer, self).__init__()
        self._client = client
        self.monitor = monitor

    @exception_to_child
    def getChild(self, name, req):
        if not name:
            return self
        # /operation/$OPHANDLE/$STORAGEINDEX provides detailed information
        # about a specific file or directory that was checked
        si = base32.a2b(name)
        r = self.monitor.get_status()
        try:
            return CheckResultsRenderer(self._client,
                                        r.get_results_for_storage_index(si))
        except KeyError:
            raise WebError("No detailed results for SI %s" % html.escape(name),
                           http.NOT_FOUND)

    @render_exception
    def render_HTML(self, req):
        elem = DeepCheckResultsRendererElement(self.monitor)
        return renderElement(req, elem)

    @render_exception
    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        data = {}
        data["finished"] = self.monitor.is_finished()
        res = self.monitor.get_status()
        data["root-storage-index"] = res.get_root_storage_index_string()
        c = res.get_counters()
        data["count-objects-checked"] = c["count-objects-checked"]
        data["count-objects-healthy"] = c["count-objects-healthy"]
        data["count-objects-unhealthy"] = c["count-objects-unhealthy"]
        data["count-corrupt-shares"] = c["count-corrupt-shares"]
        data["list-corrupt-shares"] = [ (s.get_longname(),
                                         base32.b2a(storage_index),
                                         shnum)
                                        for (s, storage_index, shnum)
                                        in res.get_corrupt_shares() ]
        data["list-unhealthy-files"] = [ (path_t, json_check_results(r))
                                         for (path_t, r)
                                         in res.get_all_results().items()
                                         if not r.is_healthy() ]
        data["stats"] = res.get_stats()
        return json.dumps(data, indent=1) + "\n"


class DeepCheckResultsRendererElement(Element, ResultsBase, ReloadMixin):

    loader = XMLFile(FilePath(__file__).sibling("deep-check-results.xhtml"))

    def __init__(self, monitor):
        super(DeepCheckResultsRendererElement, self).__init__()
        self.monitor = monitor

    @renderer
    def root_storage_index(self, req, tag):
        if not self.monitor.get_status():
            return ""
        return self.monitor.get_status().get_root_storage_index_string()

    def _get_monitor_counter(self, name):
        if not self.monitor.get_status():
            return ""
        return str(self.monitor.get_status().get_counters().get(name))

    @renderer
    def objects_checked(self, req, tag):
        return self._get_monitor_counter("count-objects-checked")

    @renderer
    def objects_healthy(self, req, tag):
        return self._get_monitor_counter("count-objects-healthy")

    @renderer
    def objects_unhealthy(self, req, tag):
        return self._get_monitor_counter("count-objects-unhealthy")

    @renderer
    def objects_unrecoverable(self, req, tag):
        return self._get_monitor_counter("count-objects-unrecoverable")

    @renderer
    def count_corrupt_shares(self, req, tag):
        return self._get_monitor_counter("count-corrupt-shares")

    @renderer
    def problems_p(self, req, tag):
        if self._get_monitor_counter("count-objects-unhealthy"):
            return tag
        return ""

    @renderer
    def problems(self, req, tag):
        all_objects = self.monitor.get_status().get_all_results()
        problems = []

        for path in sorted(all_objects.keys()):
            cr = all_objects[path]
            assert ICheckResults.providedBy(cr)
            if not cr.is_healthy():
                summary_text = ""
                summary = cr.get_summary()
                if summary:
                    summary_text = ": " + summary
                summary_text += " [SI: %s]" % cr.get_storage_index_string().decode("ascii")
                problems.append({
                    # Not sure self._join_pathstring(path) is the
                    # right thing to use here.
                    "problem": self._join_pathstring(path) + self._html(summary_text),
                })

        return SlotsSequenceElement(tag, problems)

    @renderer
    def servers_with_corrupt_shares_p(self, req, tag):
        if self._get_monitor_counter("count-corrupt-shares"):
            return tag
        return ""

    @renderer
    def servers_with_corrupt_shares(self, req, tag):
        servers = [s
                   for (s, storage_index, sharenum)
                   in self.monitor.get_status().get_corrupt_shares()]
        servers.sort(key=lambda s: s.get_longname())

        problems = []

        for server in servers:
            name = [server.get_name()]
            nickname = server.get_nickname()
            if nickname:
                name.append(" (%s)" % self._html(nickname))
            problems.append({"problem": name})

        return SlotsSequenceElement(tag, problems)

    @renderer
    def corrupt_shares_p(self, req, tag):
        if self._get_monitor_counter("count-corrupt-shares"):
            return tag
        return ""

    @renderer
    def corrupt_shares(self, req, tag):
        shares = self.monitor.get_status().get_corrupt_shares()
        problems = []

        for share in shares:
            server, storage_index, sharenum = share
            nickname = server.get_nickname()
            problem = {
                "serverid": server.get_name(),
                "nickname": self._html(nickname),
                "si": self._render_si_link(req, storage_index),
                "shnum": str(sharenum),
            }
            problems.append(problem)

        return SlotsSequenceElement(tag, problems)

    @renderer
    def return_to(self, req, tag):
        return_to = get_arg(req, "return_to", None)
        if return_to:
            return tags.div(tags.a("Return to file/directory.", href=return_to))
        return ""

    @renderer
    def all_objects(self, req, tag):
        results = self.monitor.get_status().get_all_results()
        objects = []

        for path in sorted(results.keys()):
            result = results.get(path)
            storage_index = result.get_storage_index()
            object = {
                "path": self._join_pathstring(path),
                "healthy": str(result.is_healthy()),
                "recoverable": str(result.is_recoverable()),
                "storage_index": self._render_si_link(req, storage_index),
                "summary": self._html(result.get_summary()),
            }
            objects.append(object)

        return SlotsSequenceElement(tag, objects)

    @renderer
    def runtime(self, req, tag):
        runtime = 'unknown'
        if hasattr(req, 'processing_started_timestamp'):
            runtime = time.time() - req.processing_started_timestamp
        return tag("runtime: %s seconds" % runtime)


class DeepCheckAndRepairResultsRenderer(MultiFormatResource):

    formatArgument = "output"

    def __init__(self, client, monitor):
        """
        :param allmydata.interfaces.IStatsProducer client: stats provider.
        :param allmydata.monitor.IMonitor monitor: status, progress, and cancellation provider.
        """
        super(DeepCheckAndRepairResultsRenderer, self).__init__()
        self._client = client
        self.monitor = monitor

    @exception_to_child
    def getChild(self, name, req):
        if not name:
            return self
        # /operation/$OPHANDLE/$STORAGEINDEX provides detailed information
        # about a specific file or directory that was checked
        si = base32.a2b(name)
        s = self.monitor.get_status()
        try:
            results = s.get_results_for_storage_index(si)
            return CheckAndRepairResultsRenderer(self._client, results)
        except KeyError:
            raise WebError("No detailed results for SI %s" % html.escape(name),
                           http.NOT_FOUND)

    @render_exception
    def render_HTML(self, req):
        elem = DeepCheckAndRepairResultsRendererElement(self.monitor)
        return renderElement(req, elem)

    @render_exception
    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
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

        data["list-corrupt-shares"] = [ (s.get_longname(),
                                         base32.b2a(storage_index),
                                         shnum)
                                        for (s, storage_index, shnum)
                                        in res.get_corrupt_shares() ]

        remaining_corrupt = [ (s.get_longname(), base32.b2a(storage_index),
                               shnum)
                              for (s, storage_index, shnum)
                              in res.get_remaining_corrupt_shares() ]
        data["list-remaining-corrupt-shares"] = remaining_corrupt

        unhealthy = [ (path_t,
                       json_check_results(crr.get_pre_repair_results()))
                      for (path_t, crr)
                      in res.get_all_results().items()
                      if not crr.get_pre_repair_results().is_healthy() ]
        data["list-unhealthy-files"] = unhealthy
        data["stats"] = res.get_stats()
        return json.dumps(data, indent=1) + "\n"


class DeepCheckAndRepairResultsRendererElement(DeepCheckResultsRendererElement):
    """
    The page generated here has several elements common to "deep check
    results" page; hence the code reuse.
    """

    loader = XMLFile(FilePath(__file__).sibling("deep-check-and-repair-results.xhtml"))

    def __init__(self, monitor):
        super(DeepCheckAndRepairResultsRendererElement, self).__init__(monitor)
        self.monitor = monitor

    @renderer
    def objects_healthy(self, req, tag):
        return self._get_monitor_counter("count-objects-healthy-pre-repair")

    @renderer
    def objects_unhealthy(self, req, tag):
        return self._get_monitor_counter("count-objects-unhealthy-pre-repair")

    @renderer
    def corrupt_shares(self, req, tag):
        return self._get_monitor_counter("count-corrupt-shares-pre-repair")

    @renderer
    def repairs_attempted(self, req, tag):
        return self._get_monitor_counter("count-repairs-attempted")

    @renderer
    def repairs_successful(self, req, tag):
        return self._get_monitor_counter("count-repairs-successful")

    @renderer
    def repairs_unsuccessful(self, req, tag):
        return self._get_monitor_counter("count-repairs-unsuccessful")

    @renderer
    def objects_healthy_post(self, req, tag):
        return self._get_monitor_counter("count-objects-healthy-post-repair")

    @renderer
    def objects_unhealthy_post(self, req, tag):
        return self._get_monitor_counter("count-objects-unhealthy-post-repair")

    @renderer
    def corrupt_shares_post(self, req, tag):
        return self._get_monitor_counter("count-corrupt-shares-post-repair")

    @renderer
    def pre_repair_problems_p(self, req, tag):
        if self._get_monitor_counter("count-objects-unhealthy-pre-repair"):
            return tag
        return ""

    @renderer
    def pre_repair_problems(self, req, tag):
        all_objects = self.monitor.get_status().get_all_results()
        problems = []

        for path in sorted(all_objects.keys()):
            r = all_objects[path]
            assert ICheckAndRepairResults.providedBy(r)
            cr = r.get_pre_repair_results()
            if not cr.is_healthy():
                problem = self._join_pathstring(path), ": ", self._html(cr.get_summary())
                problems.append({"problem": problem})

        return SlotsSequenceElement(tag, problems)

    @renderer
    def post_repair_problems_p(self, req, tag):
        if (self._get_monitor_counter("count-objects-unhealthy-post-repair")
            or self._get_monitor_counter("count-corrupt-shares-post-repair")):
            return tag
        return ""

    @renderer
    def post_repair_problems(self, req, tag):
        all_objects = self.monitor.get_status().get_all_results()
        problems = []

        for path in sorted(all_objects.keys()):
            r = all_objects[path]
            assert ICheckAndRepairResults.providedBy(r)
            cr = r.get_post_repair_results()
            if not cr.is_healthy():
                problem = self._join_pathstring(path), ": ", self._html(cr.get_summary())
                problems.append({"problem": problem})

        return SlotsSequenceElement(tag, problems)

    @renderer
    def remaining_corrupt_shares_p(self, req, tag):
        if self._get_monitor_counter("count-corrupt-shares-post-repair"):
            return tag
        return ""

    @renderer
    def post_repair_corrupt_shares(self, req, tag):
        # TODO: this was not implemented before porting to
        # twisted.web.template; leaving it as such.
        #
        # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3371
        corrupt = [{"share":"unimplemented"}]
        return SlotsSequenceElement(tag, corrupt)

    @renderer
    def all_objects(self, req, tag):
        results = {}
        if self.monitor.get_status():
            results = self.monitor.get_status().get_all_results()
        objects = []

        for path in sorted(results.keys()):
            result = results[path]
            storage_index = result.get_storage_index()
            obj = {
                "path": self._join_pathstring(path),
                "healthy_pre_repair": str(result.get_pre_repair_results().is_healthy()),
                "recoverable_pre_repair": str(result.get_pre_repair_results().is_recoverable()),
                "healthy_post_repair": str(result.get_post_repair_results().is_healthy()),
                "storage_index": self._render_si_link(req, storage_index),
                "summary": self._html(result.get_pre_repair_results().get_summary()),
            }
            objects.append(obj)

        return SlotsSequenceElement(tag, objects)

