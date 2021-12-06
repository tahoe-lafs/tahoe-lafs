"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import time
from twisted.python.filepath import FilePath
from twisted.web.template import (
    Element,
    XMLFile,
    tags as T,
    renderer,
    renderElement
)
from allmydata.web.common import (
    abbreviate_time,
    MultiFormatResource
)
from allmydata.util.abbreviate import abbreviate_space
from allmydata.util import time_format, idlib, jsonbytes as json


def remove_prefix(s, prefix):
    if not s.startswith(prefix):
        return None
    return s[len(prefix):]


class StorageStatusElement(Element):
    """Class to render a storage status page."""

    loader = XMLFile(FilePath(__file__).sibling("storage_status.xhtml"))

    def __init__(self, storage, nickname=""):
        """
        :param _StorageServer storage: data about storage.
        :param string nickname: friendly name for storage.
        """
        super(StorageStatusElement, self).__init__()
        self._storage = storage
        self._nickname = nickname

    @renderer
    def nickname(self, req, tag):
        return tag(self._nickname)

    @renderer
    def nodeid(self, req, tag):
        return tag(idlib.nodeid_b2a(self._storage.my_nodeid))

    def _get_storage_stat(self, key):
        """Get storage server statistics.

        Storage Server keeps a dict that contains various usage and
        latency statistics.  The dict looks like this:

          {
            'storage_server.accepting_immutable_shares': 1,
            'storage_server.allocated': 0,
            'storage_server.disk_avail': 106539192320,
            'storage_server.disk_free_for_nonroot': 106539192320,
            'storage_server.disk_free_for_root': 154415284224,
            'storage_server.disk_total': 941088460800,
            'storage_server.disk_used': 786673176576,
            'storage_server.latencies.add-lease.01_0_percentile': None,
            'storage_server.latencies.add-lease.10_0_percentile': None,
            ...
          }

        ``StorageServer.get_stats()`` returns the above dict.  Storage
        status page uses a subset of the items in the dict, concerning
        disk usage.

        :param str key: storage server statistic we want to know.
        """
        return self._storage.get_stats().get(key)

    def render_abbrev_space(self, size):
        if size is None:
            return u"?"
        return abbreviate_space(size)

    def render_space(self, size):
        if size is None:
            return u"?"
        return u"%d" % size

    @renderer
    def storage_stats(self, req, tag):
        # Render storage status table that appears near the top of the page.
        total = self._get_storage_stat("storage_server.disk_total")
        used = self._get_storage_stat("storage_server.disk_used")
        free_root = self._get_storage_stat("storage_server.disk_free_for_root")
        free_nonroot = self._get_storage_stat("storage_server.disk_free_for_nonroot")
        reserved = self._get_storage_stat("storage_server.reserved_space")
        available = self._get_storage_stat("storage_server.disk_avail")

        tag.fillSlots(
            disk_total = self.render_space(total),
            disk_total_abbrev = self.render_abbrev_space(total),
            disk_used = self.render_space(used),
            disk_used_abbrev = self.render_abbrev_space(used),
            disk_free_for_root = self.render_space(free_root),
            disk_free_for_root_abbrev = self.render_abbrev_space(free_root),
            disk_free_for_nonroot = self.render_space(free_nonroot),
            disk_free_for_nonroot_abbrev = self.render_abbrev_space(free_nonroot),
            reserved_space = self.render_space(reserved),
            reserved_space_abbrev = self.render_abbrev_space(reserved),
            disk_avail = self.render_space(available),
            disk_avail_abbrev = self.render_abbrev_space(available)
        )
        return tag

    @renderer
    def accepting_immutable_shares(self, req, tag):
        accepting = self._get_storage_stat("storage_server.accepting_immutable_shares")
        return tag({True: "Yes", False: "No"}[bool(accepting)])

    @renderer
    def last_complete_bucket_count(self, req, tag):
        s = self._storage.bucket_counter.get_state()
        count = s.get("last-complete-bucket-count")
        if count is None:
            return tag("Not computed yet")
        return tag(str(count))

    @renderer
    def count_crawler_status(self, req, tag):
        p = self._storage.bucket_counter.get_progress()
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
    def storage_running(self, req, tag):
        if self._storage:
            return tag
        return T.h1("No Storage Server Running")

    @renderer
    def lease_expiration_enabled(self, req, tag):
        lc = self._storage.lease_checker
        if lc.expiration_enabled:
            return tag("Enabled: expired leases will be removed")
        else:
            return tag("Disabled: scan-only mode, no leases will be removed")

    @renderer
    def lease_expiration_mode(self, req, tag):
        lc = self._storage.lease_checker
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
                "will be considered expired."
                % (isoutcdate, localizedutcdate, ))
        if len(lc.mode) > 2:
            tag(" The following sharetypes will be expired: ",
                " ".join(sorted(lc.sharetypes_to_expire)), ".")
        return tag

    @renderer
    def lease_current_cycle_progress(self, req, tag):
        lc = self._storage.lease_checker
        p = lc.get_progress()
        return tag(self.format_crawler_progress(p))

    @renderer
    def lease_current_cycle_results(self, req, tag):
        lc = self._storage.lease_checker
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
                T.ul( (T.li( ["SI %s shnum %d" % (si, shnum)
                              for si, shnum in so_far["corrupt-shares"] ]
                             ))))
        return tag("Current cycle:", p)

    @renderer
    def lease_last_cycle_results(self, req, tag):
        lc = self._storage.lease_checker
        h = lc.get_state()["history"]
        if not h:
            return ""
        biggest = str(max(int(k) for k in h.keys()))
        last = h[biggest]

        start, end = last["cycle-start-finish-times"]
        tag("Last complete cycle (which took %s and finished %s ago)"
            " recovered: " % (abbreviate_time(end-start),
                              abbreviate_time(time.time() - end)),
            self.format_recovered(last["space-recovered"], "actual"))

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
                T.ul( (T.li( ["SI %s shnum %d" % (si, shnum)
                              for si, shnum in last["corrupt-shares"] ]
                             ))))

        return tag(p)

    @staticmethod
    def format_recovered(sr, a):
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

class StorageStatus(MultiFormatResource):
    def __init__(self, storage, nickname=""):
        super(StorageStatus, self).__init__()
        self._storage = storage
        self._nickname = nickname

    def render_HTML(self, req):
        return renderElement(req, StorageStatusElement(self._storage, self._nickname))

    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        d = {"stats": self._storage.get_stats(),
             "bucket-counter": self._storage.bucket_counter.get_state(),
             "lease-checker": self._storage.lease_checker.get_state(),
             "lease-checker-progress": self._storage.lease_checker.get_progress(),
             }
        return json.dumps(d, indent=1) + "\n"
