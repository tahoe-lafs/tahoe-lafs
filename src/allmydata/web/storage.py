
import time, json
from twisted.python.filepath import FilePath
from twisted.web.template import Element, XMLFile, renderElement, renderer
from twisted.web.template import tags as T
from allmydata.web.common import (
    abbreviate_time,
    MultiFormatResource,
)
from allmydata.util.abbreviate import abbreviate_space
from allmydata.util import time_format, idlib


class StorageStatus(MultiFormatResource):
    # the default 'data' argument is the StorageServer instance

    def __init__(self, storage, nickname=""):
        super(StorageStatus, self).__init__()
        self.storage = storage
        self.nickname = nickname

    def _create_element(self):
        return StorageStatusElement(self.storage, self.nickname)

    def render_HTML(self, req):
        return renderElement(req, self._create_element())

    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        d = {"stats": self.storage.get_stats(),
             "bucket-counter": self.storage.bucket_counter.get_state(),
             "lease-checker": self.storage.lease_checker.get_state(),
             "lease-checker-progress": self.storage.lease_checker.get_progress(),
             }
        return json.dumps(d, indent=1) + "\n"


class StorageStatusElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("storage_status.xhtml"))

    def __init__(self, storage, nickname):
        super(StorageStatusElement, self).__init__()
        self.storage = storage
        self.nickname = nickname

    @renderer
    def storage_running(self, req, tag):
        if self.storage:
            return tag
        else:
            return T.h1("No Storage Server Running")

    @renderer
    def storage_stats(self, req, tag):
        stats = self.storage.get_stats()
        slots = {}
        for key in ["disk_total", "disk_used", "disk_free_for_root",
                    "disk_free_for_nonroot",  "reserved_space",  "disk_avail"]:
            size = stats.get('storage_server.' + key)
            if size is None:
                slots[key] = slots[key + '_abbrev'] = '?'
            else:
                slots[key] = format(size, '.0f')
                slots[key + '_abbrev'] = abbreviate_space(size)

        return tag.fillSlots(**slots)

    def _last_complete_bucket_count(self):
        s = self.storage.bucket_counter.get_state()
        count = s.get("last-complete-bucket-count")
        if count is None:
            return "Not computed yet"
        return str(count)

    @renderer
    def storage_info(self, req, tag):
        stats = self.storage.get_stats()
        info = {
            'nickname': self.nickname,
            'nodeid': idlib.nodeid_b2a(self.storage.my_nodeid),
            'accepting_immutable_shares':
                'Yes' if stats['storage_server.accepting_immutable_shares'] else 'No',
            'last_complete_bucket_count': self._last_complete_bucket_count(),
        }
        return tag.fillSlots(**info)

    @renderer
    def count_crawler_status(self, req, tag):
        p = self.storage.bucket_counter.get_progress()
        return tag(self.format_crawler_progress(p))

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

    @renderer
    def lease_expiration_enabled(self, req, tag):
        lc = self.storage.lease_checker
        if lc.expiration_enabled:
            return tag("Enabled: expired leases will be removed")
        else:
            return tag("Disabled: scan-only mode, no leases will be removed")

    @renderer
    def lease_expiration_mode(self, req, tag):
        lc = self.storage.lease_checker
        if lc.mode == "age":
            if lc.override_lease_duration is None:
                tag("Leases will expire naturally, probably 31 days after "
                        "creation or renewal.")
            else:
                tag("Leases created or last renewed more than %s ago "
                        "will be considered expired."
                        % abbreviate_time(lc.override_lease_duration))
        else:
            assert lc.mode == "cutoff-date"
            localizedutcdate = time.strftime("%d-%b-%Y", time.gmtime(lc.cutoff_date))
            isoutcdate = time_format.iso_utc_date(lc.cutoff_date)
            tag("Leases created or last renewed before %s (%s) UTC "
                    "will be considered expired." % (isoutcdate, localizedutcdate, ))
        if len(lc.mode) > 2:
            tag(" The following sharetypes will be expired: ",
                    " ".join(sorted(lc.sharetypes_to_expire)), ".")
        return tag

    def format_recovered(self, sr, a):
        def maybe(d):
            if d is None:
                return "?"
            return "%d" % d
        return "%s shares, %s buckets (%s mutable / %s immutable), %s (%s / %s)" % \
               (maybe(sr["%s-shares" % a]),
                maybe(sr["%s-buckets" % a]),
                maybe(sr["%s-buckets-mutable" % a]),
                maybe(sr["%s-buckets-immutable" % a]),
                abbreviate_space(sr["%s-diskbytes" % a]),
                abbreviate_space(sr["%s-diskbytes-mutable" % a]),
                abbreviate_space(sr["%s-diskbytes-immutable" % a]),
                )

    @renderer
    def lease_current_cycle_progress(self, req, tag):
        lc = self.storage.lease_checker
        p = lc.get_progress()
        return tag(self.format_crawler_progress(p))

    @renderer
    def lease_current_cycle_results(self, req, tag):
        lc = self.storage.lease_checker
        p = lc.get_progress()
        if not p["cycle-in-progress"]:
            return ""
        s = lc.get_state()
        so_far = s["cycle-to-date"]
        sr = so_far["space-recovered"]
        er = s["estimated-remaining-cycle"]
        esr = er["space-recovered"]
        ec = s["estimated-current-cycle"]
        ecr = ec["space-recovered"]

        p = T.ul()
        def add(*pieces):
            p(T.li(pieces))

        def maybe(d):
            if d is None:
                return "?"
            return "%d" % d
        add("So far, this cycle has examined %d shares in %d buckets"
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
            add("The whole cycle is expected to examine %s shares in %s buckets"
                % (maybe(ecr["examined-shares"]), maybe(ecr["examined-buckets"])))
            add("and to recover: ", self.format_recovered(ecr, "actual"))

        else:
            add("If expiration were enabled, we would have recovered: ",
                self.format_recovered(sr, "configured"), " by now")
            add("and the remainder of this cycle would probably recover: ",
                self.format_recovered(esr, "configured"))
            add("and the whole cycle would probably recover: ",
                self.format_recovered(ecr, "configured"))

        add("if we were strictly using each lease's default 31-day lease lifetime "
            "(instead of our configured behavior), "
            "this cycle would be expected to recover: ",
            self.format_recovered(ecr, "original"))

        if so_far["corrupt-shares"]:
            add("Corrupt shares:",
                T.ul(
                    T.li(
                        "SI %s shnum %d" % corrupt_share
                        for corrupt_share in so_far["corrupt-shares"]
                    )
                )
            )

        return tag("Current cycle:", p)

    @renderer
    def lease_last_cycle_results(self, req, tag):
        lc = self.storage.lease_checker
        h = lc.get_state()["history"]
        if not h:
            return ""
        last = h[max(h.keys())]

        start, end = last["cycle-start-finish-times"]
        tag("Last complete cycle (which took %s and finished %s ago)"
                " recovered: " % (abbreviate_time(end-start),
                                  abbreviate_time(time.time() - end)),
                self.format_recovered(last["space-recovered"], "actual")
                )

        p = T.ul()
        def add(*pieces):
            p(T.li(pieces))

        saw = self.format_recovered(last["space-recovered"], "examined")
        add("and saw a total of ", saw)

        if not last["expiration-enabled"]:
            rec = self.format_recovered(last["space-recovered"], "configured")
            add("but expiration was not enabled. If it had been, "
                "it would have recovered: ", rec)

        if last["corrupt-shares"]:
            add("Corrupt shares:",
                T.ul(
                    T.li(
                        "SI %s shnum %d" % corrupt_share
                        for corrupt_share in last["corrupt-shares"]
                    )
                )
            )

        return tag(p)
