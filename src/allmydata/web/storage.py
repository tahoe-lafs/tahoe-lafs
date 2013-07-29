
import time, simplejson
from nevow import rend, tags as T, inevow
from allmydata.web.common import getxmlfile, abbreviate_time, get_arg
from allmydata.util.abbreviate import abbreviate_space
from allmydata.util import idlib

def remove_prefix(s, prefix):
    if not s.startswith(prefix):
        return None
    return s[len(prefix):]

class StorageStatus(rend.Page):
    docFactory = getxmlfile("storage_status.xhtml")
    # the default 'data' argument is the StorageServer instance

    def __init__(self, storage, nickname=""):
        rend.Page.__init__(self, storage)
        self.storage = storage
        self.nickname = nickname

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        t = get_arg(req, "t")
        if t == "json":
            return self.render_JSON(req)
        return rend.Page.renderHTTP(self, ctx)

    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        accounting_crawler = self.storage.get_accounting_crawler()
        d = {"stats": self.storage.get_stats(),
             "bucket-counter": None,
             "lease-checker": accounting_crawler.get_state(),
             "lease-checker-progress": accounting_crawler.get_progress(),
             }
        return simplejson.dumps(d, indent=1) + "\n"

    def data_nickname(self, ctx, storage):
        return self.nickname
    def data_nodeid(self, ctx, storage):
        return idlib.nodeid_b2a(self.storage.get_serverid())

    def render_storage_running(self, ctx, storage):
        if storage:
            return ctx.tag
        else:
            return T.h1["No Storage Server Running"]

    def render_bool(self, ctx, data):
        return {True: "Yes", False: "No"}[bool(data)]

    def render_abbrev_space(self, ctx, size):
        if size is None:
            return "?"
        return abbreviate_space(size)

    def render_space(self, ctx, size):
        if size is None:
            return "?"
        return "%d" % size

    def data_stats(self, ctx, data):
        # FYI: 'data' appears to be self, rather than the StorageServer
        # object in self.original that gets passed to render_* methods. I
        # still don't understand Nevow.

        # Nevow has nevow.accessors.DictionaryContainer: Any data= directive
        # that appears in a context in which the current data is a dictionary
        # will be looked up as keys in that dictionary. So if data_stats()
        # returns a dictionary, then we can use something like this:
        #
        #  <ul n:data="stats">
        #   <li>disk_total: <span n:render="abbrev" n:data="disk_total" /></li>
        #  </ul>

        # to use get_stats()["storage_server.disk_total"] . However,
        # DictionaryContainer does a raw d[] instead of d.get(), so any
        # missing keys will cause an error, even if the renderer can tolerate
        # None values. To overcome this, we either need a dict-like object
        # that always returns None for unknown keys, or we must pre-populate
        # our dict with those missing keys, or we should get rid of data_
        # methods that return dicts (or find some way to override Nevow's
        # handling of dictionaries).

        d = dict([ (remove_prefix(k, "storage_server."), v)
                   for k,v in self.storage.get_stats().items() ])
        d.setdefault("disk_total", None)
        d.setdefault("disk_used", None)
        d.setdefault("disk_free_for_root", None)
        d.setdefault("disk_free_for_nonroot", None)
        d.setdefault("reserved_space", None)
        d.setdefault("disk_avail", None)
        return d

    def data_last_complete_bucket_count(self, ctx, data):
        s = self.storage.get_stats()
        if "storage_server.total_bucket_count" not in s:
            return "Not computed yet"
        return s['storage_server.total_bucket_count']

    def render_count_crawler_status(self, ctx, storage):
        return ctx.tag

    def format_crawler_progress(self, p):
        cycletime = p["estimated-time-per-cycle"]
        cycletime_s = ""
        if cycletime is not None:
            cycletime_s = " (estimated cycle time %s)" % abbreviate_time(cycletime)

        if p["cycle-in-progress"]:
            pct = p["cycle-complete-percentage"]
            soon = p["remaining-sleep-time"]

            eta = p["estimated-cycle-complete-time-left"]
            eta_s = ""
            if eta is not None:
                eta_s = " (ETA %ds)" % eta

            return ["Current crawl %.1f%% complete" % pct,
                    eta_s,
                    " (next work in %s)" % abbreviate_time(soon),
                    cycletime_s,
                    ]
        else:
            soon = p["remaining-wait-time"]
            return ["Next crawl in %s" % abbreviate_time(soon),
                    cycletime_s]

    def render_lease_expiration_enabled(self, ctx, data):
        ep = self.storage.get_expiration_policy()
        return ctx.tag[ep.describe_enabled()]

    def render_lease_expiration_mode(self, ctx, data):
        ep = self.storage.get_expiration_policy()
        ctx.tag[ep.describe_expiration()]
        return ctx.tag

    def format_recovered(self, sr, a):
        def maybe(d):
            if d is None:
                return "?"
            return "%d" % d
        return "%s shares, %s sharesets (%s mutable / %s immutable), %s (%s / %s)" % \
               (maybe(sr["%s-shares" % a]),
                maybe(sr["%s-buckets" % a]),
                maybe(sr["%s-buckets-mutable" % a]),
                maybe(sr["%s-buckets-immutable" % a]),
                abbreviate_space(sr["%s-diskbytes" % a]),
                abbreviate_space(sr["%s-diskbytes-mutable" % a]),
                abbreviate_space(sr["%s-diskbytes-immutable" % a]),
                )

    def render_lease_current_cycle_progress(self, ctx, data):
        ac = self.storage.get_accounting_crawler()
        p = ac.get_progress()
        return ctx.tag[self.format_crawler_progress(p)]

    def render_lease_current_cycle_results(self, ctx, data):
        ac = self.storage.get_accounting_crawler()
        p = ac.get_progress()
        if not p["cycle-in-progress"]:
            return ""
        s = ac.get_state()
        so_far = s["cycle-to-date"]
        sr = so_far["space-recovered"]
        er = s["estimated-remaining-cycle"]
        esr = er["space-recovered"]
        ec = s["estimated-current-cycle"]
        ecr = ec["space-recovered"]

        p = T.ul()
        def add(*pieces):
            p[T.li[pieces]]

        def maybe(d):
            if d is None:
                return "?"
            return "%d" % d
        add("So far, this cycle has examined %d shares in %d sharesets"
            % (sr["examined-shares"], sr["examined-buckets"]),
            " (%d mutable / %d immutable)"
            % (sr["examined-buckets-mutable"], sr["examined-buckets-immutable"]),
            " (%s / %s)" % (abbreviate_space(sr["examined-diskbytes-mutable"]),
                            abbreviate_space(sr["examined-diskbytes-immutable"])),
            )
        add("and has recovered: ", self.format_recovered(sr, "actual"))
        if so_far["expiration-enabled"]:
            add("The remainder of this cycle is expected to recover: ",
                self.format_recovered(esr, "actual"))
            add("The whole cycle is expected to examine %s shares in %s sharesets"
                % (maybe(ecr["examined-shares"]), maybe(ecr["examined-buckets"])))
            add("and to recover: ", self.format_recovered(ecr, "actual"))
        else:
            add("Expiration was not enabled.")

        if so_far["corrupt-shares"]:
            add("Corrupt shares:",
                T.ul[ [T.li[ ["SI %s shnum %d" % corrupt_share
                              for corrupt_share in so_far["corrupt-shares"] ]
                             ]]])

        return ctx.tag["Current cycle:", p]

    def render_lease_last_cycle_results(self, ctx, data):
        ac = self.storage.get_accounting_crawler()
        h = ac.get_state()["history"]
        if not h:
            return ""
        last = h[max(h.keys())]

        start, end = last["cycle-start-finish-times"]
        ctx.tag["Last complete cycle (which took %s and finished %s ago)"
                " recovered: " % (abbreviate_time(end-start),
                                  abbreviate_time(time.time() - end)),
                self.format_recovered(last["space-recovered"], "actual")
                ]

        p = T.ul()
        def add(*pieces):
            p[T.li[pieces]]

        saw = self.format_recovered(last["space-recovered"], "examined")
        add("and saw a total of ", saw)

        if not last["expiration-enabled"]:
            add("but expiration was not enabled.")

        if last["corrupt-shares"]:
            add("Corrupt shares:",
                T.ul[ [T.li[ ["SI %s shnum %d" % corrupt_share
                              for corrupt_share in last["corrupt-shares"] ]
                             ]]])

        return ctx.tag[p]

