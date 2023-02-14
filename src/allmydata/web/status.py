"""
Ported to Python 3.
"""

from __future__ import division
from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from past.builtins import long

import itertools
import hashlib
import re
from twisted.internet import defer
from twisted.python.filepath import FilePath
from twisted.web.resource import Resource
from twisted.web.template import (
    Element,
    XMLFile,
    renderer,
    renderElement,
    tags,
)
from allmydata.util import base32, idlib, jsonbytes as json
from allmydata.web.common import (
    abbreviate_time,
    abbreviate_rate,
    abbreviate_size,
    exception_to_child,
    plural,
    compute_rate,
    render_exception,
    render_time,
    MultiFormatResource,
    SlotsSequenceElement,
    WebError,
)

from allmydata.interfaces import (
    IUploadStatus,
    IDownloadStatus,
    IPublishStatus,
    IRetrieveStatus,
    IServermapUpdaterStatus,
)


class UploadResultsRendererMixin(Element):
    # this requires a method named 'upload_results'

    @renderer
    def pushed_shares(self, req, tag):
        d = self.upload_results()
        d.addCallback(lambda res: str(res.get_pushed_shares()))
        return d

    @renderer
    def preexisting_shares(self, req, tag):
        d = self.upload_results()
        d.addCallback(lambda res: str(res.get_preexisting_shares()))
        return d

    @renderer
    def sharemap(self, req, tag):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_sharemap())
        def _render(sharemap):
            if sharemap is None:
                return "None"
            ul = tags.ul()
            for shnum, servers in sorted(sharemap.items()):
                server_names = ', '.join([str(s.get_name(), "utf-8") for s in servers])
                ul(tags.li("%d -> placed on [%s]" % (shnum, server_names)))
            return ul
        d.addCallback(_render)
        return d

    @renderer
    def servermap(self, req, tag):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_servermap())
        def _render(servermap):
            if servermap is None:
                return "None"
            ul = tags.ul()
            for server, shnums in sorted(servermap.items(), key=id):
                shares_s = ",".join(["#%d" % shnum for shnum in shnums])
                ul(tags.li("[%s] got share%s: %s" % (str(server.get_name(), "utf-8"),
                                                     plural(shnums), shares_s)))
            return ul
        d.addCallback(_render)
        return d

    @renderer
    def file_size(self, req, tag):
        d = self.upload_results()
        d.addCallback(lambda res: str(res.get_file_size()))
        return d

    def _get_time(self, name):
        d = self.upload_results()
        d.addCallback(lambda res: abbreviate_time(res.get_timings().get(name)))
        return d

    @renderer
    def time_total(self, req, tag):
        return tag(self._get_time("total"))

    @renderer
    def time_storage_index(self, req, tag):
        return tag(self._get_time("storage_index"))

    @renderer
    def time_contacting_helper(self, req, tag):
        return tag(self._get_time("contacting_helper"))

    @renderer
    def time_cumulative_fetch(self, req, tag):
        return tag(self._get_time("cumulative_fetch"))

    @renderer
    def time_helper_total(self, req, tag):
        return tag(self._get_time("helper_total"))

    @renderer
    def time_peer_selection(self, req, tag):
        return tag(self._get_time("peer_selection"))

    @renderer
    def time_total_encode_and_push(self, req, tag):
        return tag(self._get_time("total_encode_and_push"))

    @renderer
    def time_cumulative_encoding(self, req, tag):
        return tag(self._get_time("cumulative_encoding"))

    @renderer
    def time_cumulative_sending(self, req, tag):
        return tag(self._get_time("cumulative_sending"))

    @renderer
    def time_hashes_and_close(self, req, tag):
        return tag(self._get_time("hashes_and_close"))

    def _get_rate(self, name):
        d = self.upload_results()
        def _convert(r):
            file_size = r.get_file_size()
            duration = r.get_timings().get(name)
            return abbreviate_rate(compute_rate(file_size, duration))
        d.addCallback(_convert)
        return d

    @renderer
    def rate_total(self, req, tag):
        return tag(self._get_rate("total"))

    @renderer
    def rate_storage_index(self, req, tag):
        return tag(self._get_rate("storage_index"))

    @renderer
    def rate_encode(self, req, tag):
        return tag(self._get_rate("cumulative_encoding"))

    @renderer
    def rate_push(self, req, tag):
        return self._get_rate("cumulative_sending")

    @renderer
    def rate_encode_and_push(self, req, tag):
        d = self.upload_results()
        def _convert(r):
            file_size = r.get_file_size()
            time1 = r.get_timings().get("cumulative_encoding")
            time2 = r.get_timings().get("cumulative_sending")
            if (time1 is None or time2 is None):
                return abbreviate_rate(None)
            else:
                return abbreviate_rate(compute_rate(file_size, time1+time2))
        d.addCallback(_convert)
        return d

    @renderer
    def rate_ciphertext_fetch(self, req, tag):
        d = self.upload_results()
        def _convert(r):
            fetch_size = r.get_ciphertext_fetched()
            duration = r.get_timings().get("cumulative_fetch")
            return abbreviate_rate(compute_rate(fetch_size, duration))
        d.addCallback(_convert)
        return d


class UploadStatusPage(Resource, object):
    """Renders /status/up-%d."""

    def __init__(self, upload_status):
        """
        :param IUploadStatus upload_status: stats provider.
        """
        super(UploadStatusPage, self).__init__()
        self._upload_status = upload_status

    @render_exception
    def render_GET(self, req):
        elem = UploadStatusElement(self._upload_status)
        return renderElement(req, elem)


class UploadStatusElement(UploadResultsRendererMixin):

    loader = XMLFile(FilePath(__file__).sibling("upload-status.xhtml"))

    def __init__(self, upload_status):
        super(UploadStatusElement, self).__init__()
        self._upload_status = upload_status

    def upload_results(self):
        return defer.maybeDeferred(self._upload_status.get_results)

    @renderer
    def results(self, req, tag):
        d = self.upload_results()
        def _got_results(results):
            if results:
                return tag
            return ""
        d.addCallback(_got_results)
        return d

    @renderer
    def started(self, req, tag):
        started_s = render_time(self._upload_status.get_started())
        return tag(started_s)

    @renderer
    def si(self, req, tag):
        si_s = base32.b2a_or_none(self._upload_status.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        else:
            si_s = str(si_s, "utf-8")
        return tag(si_s)

    @renderer
    def helper(self, req, tag):
        return tag({True: "Yes",
                    False: "No"}[self._upload_status.using_helper()])

    @renderer
    def total_size(self, req, tag):
        size = self._upload_status.get_size()
        if size is None:
            return "(unknown)"
        return tag(str(size))

    @renderer
    def progress_hash(self, req, tag):
        progress = self._upload_status.get_progress()[0]
        # TODO: make an ascii-art bar
        return tag("%.1f%%" % (100.0 * progress))

    @renderer
    def progress_ciphertext(self, req, tag):
        progress = self._upload_status.get_progress()[1]
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    @renderer
    def progress_encode_push(self, req, tag):
        progress = self._upload_status.get_progress()[2]
        # TODO: make an ascii-art bar
        return tag("%.1f%%" % (100.0 * progress))

    @renderer
    def status(self, req, tag):
        return tag(self._upload_status.get_status())


def _find_overlap(events, start_key, end_key):
    """
    given a list of event dicts, return a new list in which each event
    has an extra "row" key (an int, starting at 0), and if appropriate
    a "serverid" key (ascii-encoded server id), replacing the "server"
    key. This is a hint to our JS frontend about how to overlap the
    parts of the graph it is drawing.

    we must always make a copy, since we're going to be adding keys
    and don't want to change the original objects. If we're
    stringifying serverids, we'll also be changing the serverid keys.
    """
    new_events = []
    rows = []
    for ev in events:
        ev = ev.copy()
        if 'server' in ev:
            ev["serverid"] = ev["server"].get_longname()
            del ev["server"]
        # find an empty slot in the rows
        free_slot = None
        for row,finished in enumerate(rows):
            if finished is not None:
                if ev[start_key] > finished:
                    free_slot = row
                    break
        if free_slot is None:
            free_slot = len(rows)
            rows.append(ev[end_key])
        else:
            rows[free_slot] = ev[end_key]
        ev["row"] = free_slot
        new_events.append(ev)
    return new_events

def _find_overlap_requests(events):
    """
    We compute a three-element 'row tuple' for each event: (serverid,
    shnum, row). All elements are ints. The first is a mapping from
    serverid to group number, the second is a mapping from shnum to
    subgroup number. The third is a row within the subgroup.

    We also return a list of lists of rowcounts, so renderers can decide
    how much vertical space to give to each row.
    """

    serverid_to_group = {}
    groupnum_to_rows = {} # maps groupnum to a table of rows. Each table
                          # is a list with an element for each row number
                          # (int starting from 0) that contains a
                          # finish_time, indicating that the row is empty
                          # beyond that time. If finish_time is None, it
                          # indicate a response that has not yet
                          # completed, so the row cannot be reused.
    new_events = []
    for ev in events:
        # DownloadStatus promises to give us events in temporal order
        ev = ev.copy()
        ev["serverid"] = ev["server"].get_longname()
        del ev["server"]
        if ev["serverid"] not in serverid_to_group:
            groupnum = len(serverid_to_group)
            serverid_to_group[ev["serverid"]] = groupnum
        groupnum = serverid_to_group[ev["serverid"]]
        if groupnum not in groupnum_to_rows:
            groupnum_to_rows[groupnum] = []
        rows = groupnum_to_rows[groupnum]
        # find an empty slot in the rows
        free_slot = None
        for row,finished in enumerate(rows):
            if finished is not None:
                if ev["start_time"] > finished:
                    free_slot = row
                    break
        if free_slot is None:
            free_slot = len(rows)
            rows.append(ev["finish_time"])
        else:
            rows[free_slot] = ev["finish_time"]
        ev["row"] = (groupnum, free_slot)
        new_events.append(ev)
    del groupnum
    # maybe also return serverid_to_group, groupnum_to_rows, and some
    # indication of the highest finish_time
    #
    # actually, return the highest rownum for each groupnum
    highest_rownums = [len(groupnum_to_rows[groupnum])
                       for groupnum in range(len(serverid_to_group))]
    return new_events, highest_rownums


def _color(server):
    h = hashlib.sha256(server.get_serverid()).digest()
    def m(c):
        return min(ord(c) // 2 + 0x80, 0xff)
    return "#%02x%02x%02x" % (m(h[0:1]), m(h[1:2]), m(h[2:3]))

class _EventJson(Resource, object):

    def __init__(self, download_status):
        self._download_status = download_status

    @render_exception
    def render(self, request):
        request.setHeader("content-type", "text/plain")
        data = { } # this will be returned to the GET
        ds = self._download_status

        data["misc"] = _find_overlap(
            ds.misc_events,
            "start_time", "finish_time",
        )
        data["read"] = _find_overlap(
            ds.read_events,
            "start_time", "finish_time",
        )
        data["segment"] = _find_overlap(
            ds.segment_events,
            "start_time", "finish_time",
        )
        # TODO: overlap on DYHB isn't very useful, and usually gets in the
        # way. So don't do it.
        data["dyhb"] = _find_overlap(
            ds.dyhb_requests,
            "start_time", "finish_time",
        )
        data["block"],data["block_rownums"] =_find_overlap_requests(ds.block_requests)

        server_info = {} # maps longname to {num,color,short}
        server_shortnames = {} # maps servernum to shortname
        for d_ev in ds.dyhb_requests:
            s = d_ev["server"]
            longname = s.get_longname()
            if longname not in server_info:
                num = len(server_info)
                server_info[longname] = {"num": num,
                                         "color": _color(s),
                                         "short": s.get_name() }
                server_shortnames[str(num)] = s.get_name()

        data["server_info"] = server_info
        data["num_serverids"] = len(server_info)
        # we'd prefer the keys of serverids[] to be ints, but this is JSON,
        # so they get converted to strings. Stupid javascript.
        data["serverids"] = server_shortnames
        data["bounds"] = {"min": ds.first_timestamp, "max": ds.last_timestamp}
        return json.dumps(data, indent=1) + "\n"


class DownloadStatusPage(Resource, object):
    """Renders /status/down-%d."""

    def __init__(self, download_status):
        """
        :param IDownloadStatus download_status: stats provider
        """
        super(DownloadStatusPage, self).__init__()
        self._download_status = download_status
        self.putChild(b"event_json", _EventJson(self._download_status))

    @render_exception
    def render_GET(self, req):
        elem = DownloadStatusElement(self._download_status)
        return renderElement(req, elem)


class DownloadStatusElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("download-status.xhtml"))

    def __init__(self, download_status):
        super(DownloadStatusElement, self).__init__()
        self._download_status = download_status

    # XXX: fun fact: the `get_results()` method which we wind up
    # invoking here (see immutable.downloader.status.DownloadStatus)
    # is unimplemented, and simply returns a `None`.  As a result,
    # `results()` renderer returns an empty tag, and does not invoke
    # any of the subsequent renderers.  Thus we end up not displaying
    # download results on the download status page.
    #
    # See #3310: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3310
    def download_results(self):
        return self._download_status.get_results()

    def _relative_time(self, t):
        if t is None:
            return t
        if self._download_status.first_timestamp is not None:
            return t - self._download_status.first_timestamp
        return t

    def _short_relative_time(self, t):
        t = self._relative_time(t)
        if t is None:
            return ""
        return "+%.6fs" % t

    def _rate_and_time(self, bytes_count, seconds):
        time_s = abbreviate_time(seconds)
        if seconds != 0:
            rate = abbreviate_rate(bytes_count / seconds)
            return tags.span(time_s, title=rate)
        return tags.span(time_s)

    # XXX: This method is a candidate for refactoring.  It renders
    # four tables from this function.  Layout part of those tables
    # could be moved to download-status.xhtml.
    #
    # See #3311: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3311
    @renderer
    def events(self, req, tag):
        if not self._download_status.get_storage_index():
            return tag

        srt = self._short_relative_time

        evtag = tags.div()

        # "DYHB Requests" table.
        dyhbtag = tags.table(align="left", class_="status-download-events")

        dyhbtag(tags.tr(tags.th("serverid"),
                        tags.th("sent"),
                        tags.th("received"),
                        tags.th("shnums"),
                        tags.th("RTT")))

        for d_ev in self._download_status.dyhb_requests:
            server = d_ev["server"]
            sent = d_ev["start_time"]
            shnums = d_ev["response_shnums"]
            received = d_ev["finish_time"]
            rtt = None
            if received is not None:
                rtt = received - sent
            if not shnums:
                shnums = ["-"]

            dyhbtag(tags.tr(style="background: %s" % _color(server))(
                (tags.td(server.get_name()),
                 tags.td(srt(sent)),
                 tags.td(srt(received)),
                 tags.td(",".join([str(shnum) for shnum in shnums])),
                 tags.td(abbreviate_time(rtt)),
                )))

        evtag(tags.h2("DYHB Requests:"), dyhbtag)
        evtag(tags.br(clear="all"))

        # "Read Events" table.
        readtag = tags.table(align="left",class_="status-download-events")

        readtag(tags.tr((
            tags.th("range"),
            tags.th("start"),
            tags.th("finish"),
            tags.th("got"),
            tags.th("time"),
            tags.th("decrypttime"),
            tags.th("pausedtime"),
            tags.th("speed"))))

        for r_ev in self._download_status.read_events:
            start = r_ev["start"]
            length = r_ev["length"]
            bytes_returned = r_ev["bytes_returned"]
            decrypt_time = ""
            if bytes_returned:
                decrypt_time = self._rate_and_time(bytes_returned, r_ev["decrypt_time"])
            speed, rtt = "",""
            if r_ev["finish_time"] is not None:
                rtt = r_ev["finish_time"] - r_ev["start_time"] - r_ev["paused_time"]
                speed = abbreviate_rate(compute_rate(bytes_returned, rtt))
                rtt = abbreviate_time(rtt)
            paused = abbreviate_time(r_ev["paused_time"])

            readtag(tags.tr(
                tags.td("[%d:+%d]" % (start, length)),
                tags.td(srt(r_ev["start_time"])),
                tags.td(srt(r_ev["finish_time"])),
                tags.td(str(bytes_returned)),
                tags.td(rtt),
                tags.td(decrypt_time),
                tags.td(paused),
                tags.td(speed),
            ))

        evtag(tags.h2("Read Events:"), readtag)
        evtag(tags.br(clear="all"))

        # "Segment Events" table.
        segtag = tags.table(align="left",class_="status-download-events")

        segtag(tags.tr(
            tags.th("segnum"),
            tags.th("start"),
            tags.th("active"),
            tags.th("finish"),
            tags.th("range"),
            tags.th("decodetime"),
            tags.th("segtime"),
            tags.th("speed")))

        for s_ev in self._download_status.segment_events:
            range_s = "-"
            segtime_s = "-"
            speed = "-"
            decode_time = "-"
            if s_ev["finish_time"] is not None:
                if s_ev["success"]:
                    segtime = s_ev["finish_time"] - s_ev["active_time"]
                    segtime_s = abbreviate_time(segtime)
                    seglen = s_ev["segment_length"]
                    range_s = "[%d:+%d]" % (s_ev["segment_start"], seglen)
                    speed = abbreviate_rate(compute_rate(seglen, segtime))
                    decode_time = self._rate_and_time(seglen, s_ev["decode_time"])
                else:
                    # error
                    range_s = "error"
            else:
                # not finished yet
                pass

            segtag(tags.tr(
                tags.td("seg%d" % s_ev["segment_number"]),
                tags.td(srt(s_ev["start_time"])),
                tags.td(srt(s_ev["active_time"])),
                tags.td(srt(s_ev["finish_time"])),
                tags.td(range_s),
                tags.td(decode_time),
                tags.td(segtime_s),
                tags.td(speed)))

        evtag(tags.h2("Segment Events:"), segtag)
        evtag(tags.br(clear="all"))

        # "Requests" table.
        reqtab = tags.table(align="left",class_="status-download-events")

        reqtab(tags.tr(
            tags.th("serverid"),
            tags.th("shnum"),
            tags.th("range"),
            tags.th("txtime"),
            tags.th("rxtime"),
            tags.th("received"),
            tags.th("RTT")))

        for r_ev in self._download_status.block_requests:
            server = r_ev["server"]
            rtt = None
            if r_ev["finish_time"] is not None:
                rtt = r_ev["finish_time"] - r_ev["start_time"]
            color = _color(server)
            reqtab(tags.tr(style="background: %s" % color)
                   (
                       tags.td(server.get_name()),
                       tags.td(str(r_ev["shnum"])),
                       tags.td("[%d:+%d]" % (r_ev["start"], r_ev["length"])),
                       tags.td(srt(r_ev["start_time"])),
                       tags.td(srt(r_ev["finish_time"])),
                       tags.td(str(r_ev["response_length"]) or ""),
                       tags.td(abbreviate_time(rtt)),
                   ))

        evtag(tags.h2("Requests:"), reqtab)
        evtag(tags.br(clear="all"))

        return evtag

    @renderer
    def results(self, req, tag):
        if self.download_results():
            return tag
        return ""

    @renderer
    def started(self, req, tag):
        started_s = render_time(self._download_status.get_started())
        return tag(started_s + " (%s)" % self._download_status.get_started())

    @renderer
    def si(self, req, tag):
        si_s = base32.b2a_or_none(self._download_status.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return tag(si_s)

    @renderer
    def helper(self, req, tag):
        return tag({True: "Yes",
                    False: "No"}[self._download_status.using_helper()])

    @renderer
    def total_size(self, req, tag):
        size = self._download_status.get_size()
        if size is None:
            return "(unknown)"
        return tag(str(size))

    @renderer
    def progress(self, req, tag):
        progress = self._download_status.get_progress()
        # TODO: make an ascii-art bar
        return tag("%.1f%%" % (100.0 * progress))

    @renderer
    def status(self, req, tag):
        return tag(self._download_status.get_status())

    @renderer
    def servers_used(self, req, tag):
        servers_used = self.download_results().servers_used
        if not servers_used:
            return ""
        peerids_s = ", ".join(["[%s]" % idlib.shortnodeid_b2a(peerid)
                               for peerid in servers_used])
        return tags.li("Servers Used: ", peerids_s)

    @renderer
    def servermap(self, req, tag):
        servermap = self.download_results().servermap
        if not servermap:
            return tag("None")
        ul = tags.ul()
        for peerid in sorted(servermap.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            shares_s = ",".join(["#%d" % shnum
                                 for shnum in servermap[peerid]])
            ul(tags.li("[%s] has share%s: %s" % (peerid_s,
                                                 plural(servermap[peerid]),
                                                 shares_s)))
        return ul

    @renderer
    def problems(self, req, tag):
        server_problems = self.download_results().server_problems
        if not server_problems:
            return ""
        ul = tags.ul()
        for peerid in sorted(server_problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            ul(tags.li("[%s]: %s" % (peerid_s, server_problems[peerid])))
        return tags.li("Server Problems:", ul)

    @renderer
    def file_size(self, req, tag):
        return tag(str(self.download_results().file_size))

    def _get_time(self, name):
        if self.download_results().timings:
            return self.download_results().timings.get(name)
        return None

    @renderer
    def time_total(self, req, tag):
        return tag(str(self._get_time("total")))

    @renderer
    def time_peer_selection(self, req, tag):
        return tag(str(self._get_time("peer_selection")))

    @renderer
    def time_uri_extension(self, req, tag):
        return tag(str(self._get_time("uri_extension")))

    @renderer
    def time_hashtrees(self, req, tag):
        return tag(str(self._get_time("hashtrees")))

    @renderer
    def time_segments(self, req, tag):
        return tag(str(self._get_time("segments")))

    @renderer
    def time_cumulative_fetch(self, req, tag):
        return tag(str(self._get_time("cumulative_fetch")))

    @renderer
    def time_cumulative_decode(self, req, tag):
        return tag(str(self._get_time("cumulative_decode")))

    @renderer
    def time_cumulative_decrypt(self, req, tag):
        return tag(str(self._get_time("cumulative_decrypt")))

    @renderer
    def time_paused(self, req, tag):
        return tag(str(self._get_time("paused")))

    def _get_rate(self, name):
        r = self.download_results()
        file_size = r.file_size
        duration = None
        if r.timings:
            duration = r.timings.get(name)
        return compute_rate(file_size, duration)

    @renderer
    def rate_total(self, req, tag):
        return tag(str(self._get_rate("total")))

    @renderer
    def rate_segments(self, req, tag):
        return tag(str(self._get_rate("segments")))

    @renderer
    def rate_fetch(self, req, tag):
        return tag(str(self._get_rate("cumulative_fetch")))

    @renderer
    def rate_decode(self, req, tag):
        return tag(str(self._get_rate("cumulative_decode")))

    @renderer
    def rate_decrypt(self, req, tag):
        return tag(str(self._get_rate("cumulative_decrypt")))

    @renderer
    def server_timings(self, req, tag):
        per_server = self._get_time("fetch_per_server")
        if per_server is None:
            return ""
        ul = tags.ul()
        for peerid in sorted(per_server.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            times_s = ", ".join([abbreviate_time(t)
                                 for t in per_server[peerid]])
            ul(tags.li("[%s]: %s" % (peerid_s, times_s)))
        return tags.li("Per-Server Segment Fetch Response Times: ", ul)


class RetrieveStatusPage(MultiFormatResource):
    """Renders /status/retrieve-%d."""

    def __init__(self, retrieve_status):
        """
        :param retrieve.RetrieveStatus retrieve_status: stats provider.
        """
        super(RetrieveStatusPage, self).__init__()
        self._retrieve_status = retrieve_status

    @render_exception
    def render_HTML(self, req):
        elem = RetrieveStatusElement(self._retrieve_status)
        return renderElement(req, elem)


class RetrieveStatusElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("retrieve-status.xhtml"))

    def __init__(self, retrieve_status):
        super(RetrieveStatusElement, self).__init__()
        self._retrieve_status = retrieve_status

    @renderer
    def started(self, req, tag):
        started_s = render_time(self._retrieve_status.get_started())
        return tag(started_s)

    @renderer
    def si(self, req, tag):
        si_s = base32.b2a_or_none(self._retrieve_status.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return tag(si_s)

    @renderer
    def helper(self, req, tag):
        return tag({True: "Yes",
                    False: "No"}[self._retrieve_status.using_helper()])

    @renderer
    def current_size(self, req, tag):
        size = str(self._retrieve_status.get_size())
        if size is None:
            size = "(unknown)"
        return tag(size)

    @renderer
    def progress(self, req, tag):
        progress = self._retrieve_status.get_progress()
        # TODO: make an ascii-art bar
        return tag("%.1f%%" % (100.0 * progress))

    @renderer
    def status(self, req, tag):
        return tag(self._retrieve_status.get_status())

    @renderer
    def encoding(self, req, tag):
        k, n = self._retrieve_status.get_encoding()
        return tag("Encoding: %s of %s" % (k, n))

    @renderer
    def problems(self, req, tag):
        problems = self._retrieve_status.get_problems()
        if not problems:
            return ""
        ul = tags.ul()
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            ul(tags.li("[%s]: %s" % (peerid_s, problems[peerid])))
        return tag("Server Problems:", ul)

    def _get_rate(self, name):
        file_size = self._retrieve_status.get_size()
        duration = self._retrieve_status.timings.get(name)
        return compute_rate(file_size, duration)

    @renderer
    def time_total(self, req, tag):
        return tag(str(self._retrieve_status.timings.get("total")))

    @renderer
    def rate_total(self, req, tag):
        return tag(str(self._get_rate("total")))

    @renderer
    def time_fetch(self, req, tag):
        return tag(str(self._retrieve_status.timings.get("fetch")))

    @renderer
    def rate_fetch(self, req, tag):
        return tag(str(self._get_rate("fetch")))

    @renderer
    def time_decode(self, req, tag):
        return tag(str(self._retrieve_status.timings.get("decode")))

    @renderer
    def rate_decode(self, req, tag):
        return tag(str(self._get_rate("decode")))

    @renderer
    def time_decrypt(self, req, tag):
        return tag(str(self._retrieve_status.timings.get("decrypt")))

    @renderer
    def rate_decrypt(self, req, tag):
        return tag(str(self._get_rate("decrypt")))

    @renderer
    def server_timings(self, req, tag):
        per_server = self._retrieve_status.timings.get("fetch_per_server")
        if not per_server:
            return tag("")
        l = tags.ul()
        for server in sorted(list(per_server.keys()), key=lambda s: s.get_name()):
            times_s = ", ".join([abbreviate_time(t)
                                 for t in per_server[server]])
            l(tags.li("[%s]: %s" % (str(server.get_name(), "utf-8"), times_s)))
        return tags.li("Per-Server Fetch Response Times: ", l)


class PublishStatusPage(MultiFormatResource):
    """Renders status/publish-%d."""

    def __init__(self, publish_status):
        """
        :param mutable.publish.PublishStatus publish_status: stats provider.
        """
        super(PublishStatusPage, self).__init__()
        self._publish_status = publish_status

    @render_exception
    def render_HTML(self, req):
        elem = PublishStatusElement(self._publish_status);
        return renderElement(req, elem)


class PublishStatusElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("publish-status.xhtml"))

    def __init__(self, publish_status):
        super(PublishStatusElement, self).__init__()
        self._publish_status = publish_status

    @renderer
    def started(self, req, tag):
        started_s = render_time(self._publish_status.get_started())
        return tag(started_s)

    @renderer
    def si(self, req, tag):
        si_s = base32.b2a_or_none(self._publish_status.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        else:
            si_s = str(si_s, "utf-8")
        return tag(si_s)

    @renderer
    def helper(self, req, tag):
        return tag({True: "Yes",
                    False: "No"}[self._publish_status.using_helper()])

    @renderer
    def current_size(self, req, tag):
        size = self._publish_status.get_size()
        if size is None:
            size = "(unknown)"
        return tag(str(size))

    @renderer
    def progress(self, req, tag):
        progress = self._publish_status.get_progress()
        # TODO: make an ascii-art bar
        return tag("%.1f%%" % (100.0 * progress))

    @renderer
    def status(self, req, tag):
        return tag(self._publish_status.get_status())

    @renderer
    def encoding(self, req, tag):
        k, n = self._publish_status.get_encoding()
        return tag("Encoding: %s of %s" % (k, n))

    @renderer
    def sharemap(self, req, tag):
        servermap = self._publish_status.get_servermap()
        if servermap is None:
            return tag("None")
        l = tags.ul()
        sharemap = servermap.make_sharemap()
        for shnum in sorted(sharemap.keys()):
            l(tags.li("%d -> Placed on " % shnum,
                      ", ".join(["[%s]" % str(server.get_name(), "utf-8")
                                 for server in sharemap[shnum]])))
        return tag("Sharemap:", l)

    @renderer
    def problems(self, req, tag):
        problems = self._publish_status.get_problems()
        if not problems:
            return tag()
        l = tags.ul()
        # XXX: is this exercised? I don't think PublishStatus.problems is
        # ever populated
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            l(tags.li("[%s]: %s" % (peerid_s, problems[peerid])))
        return tag(tags.li("Server Problems:", l))

    def _get_rate(self, name):
        file_size = self._publish_status.get_size()
        duration = self._publish_status.timings.get(name)
        return str(compute_rate(file_size, duration))

    def _get_time(self, name):
        return str(self._publish_status.timings.get(name))

    @renderer
    def time_total(self, req, tag):
        return tag(self._get_time("total"))

    @renderer
    def rate_total(self, req, tag):
        return tag(self._get_rate("total"))

    @renderer
    def time_setup(self, req, tag):
        return tag(self._get_time("setup"))

    @renderer
    def time_encrypt(self, req, tag):
        return tag(self._get_time("encrypt"))

    @renderer
    def rate_encrypt(self, req, tag):
        return tag(self._get_rate("encrypt"))

    @renderer
    def time_encode(self, req, tag):
        return tag(self._get_time("encode"))

    @renderer
    def rate_encode(self, req, tag):
        return tag(self._get_rate("encode"))

    @renderer
    def time_pack(self, req, tag):
        return tag(self._get_time("pack"))

    @renderer
    def rate_pack(self, req, tag):
        return tag(self._get_rate("pack"))

    @renderer
    def time_sign(self, req, tag):
        return tag(self._get_time("sign"))

    @renderer
    def time_push(self, req, tag):
        return tag(self._get_time("push"))

    @renderer
    def rate_push(self, req, tag):
        return self._get_rate("push")

    @renderer
    def server_timings(self, req, tag):
        per_server = self._publish_status.timings.get("send_per_server")
        if not per_server:
            return tag()
        l = tags.ul()
        for server in sorted(list(per_server.keys()), key=lambda s: s.get_name()):
            times_s = ", ".join([abbreviate_time(t)
                                 for t in per_server[server]])
            l(tags.li("[%s]: %s" % (str(server.get_name(), "utf-8"), times_s)))
        return tags.li("Per-Server Response Times: ", l)



class MapupdateStatusPage(MultiFormatResource):
    """Renders /status/mapupdate-%d."""

    def __init__(self, update_status):
        """
        :param update_status servermap.UpdateStatus: server map stats provider.
        """
        super(MapupdateStatusPage, self).__init__()
        self._update_status = update_status

    @render_exception
    def render_HTML(self, req):
        elem = MapupdateStatusElement(self._update_status);
        return renderElement(req, elem)


class MapupdateStatusElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("map-update-status.xhtml"))

    def __init__(self, update_status):
        super(MapupdateStatusElement, self).__init__()
        self._update_status = update_status

    @renderer
    def started(self, req, tag):
        started_s = render_time(self._update_status.get_started())
        return tag(started_s)

    @renderer
    def finished(self, req, tag):
        when = self._update_status.get_finished()
        if not when:
            return tag("not yet")
        started_s = render_time(self._update_status.get_finished())
        return tag(started_s)

    @renderer
    def si(self, req, tag):
        si_s = base32.b2a_or_none(self._update_status.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return tag(si_s)

    @renderer
    def helper(self, req, tag):
        return tag({True: "Yes",
                    False: "No"}[self._update_status.using_helper()])

    @renderer
    def progress(self, req, tag):
        progress = self._update_status.get_progress()
        # TODO: make an ascii-art bar
        return tag("%.1f%%" % (100.0 * progress))

    @renderer
    def status(self, req, tag):
        return tag(self._update_status.get_status())

    @renderer
    def problems(self, req, tag):
        problems = self._update_status.problems
        if not problems:
            return tag
        l = tags.ul()
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            l(tags.li("[%s]: %s" % (peerid_s, problems[peerid])))
        return tag("Server Problems:", l)

    @renderer
    def privkey_from(self, req, tag):
        server = self._update_status.get_privkey_from()
        if server:
            return tag(tags.li("Got privkey from: [%s]" % str(
                server.get_name(), "utf-8")))
        else:
            return tag

    # Helper to query update status timings.
    #
    # Querying `update_status.timings` can yield `None` or a numeric
    # value, but twisted.web has trouble flattening the element tree
    # when a node contains numeric values.  Stringifying them helps.
    def _get_update_status_timing(self, name, tag):
        res = self._update_status.timings.get(name)
        if not res:
            return tag("0")
        return tag(abbreviate_time(res))

    @renderer
    def time_total(self, req, tag):
        return self._get_update_status_timing("total", tag)

    @renderer
    def time_initial_queries(self, req, tag):
        return self._get_update_status_timing("initial_queries", tag)

    @renderer
    def time_cumulative_verify(self, req, tag):
        return self._get_update_status_timing("cumulative_verify", tag)

    @renderer
    def server_timings(self, req, tag):
        per_server = self._update_status.timings.get("per_server")
        if not per_server:
            return tag("")
        l = tags.ul()
        for server in sorted(per_server.keys(), key=lambda s: s.get_name()):
            times = []
            for op,started,t in per_server[server]:
                #times.append("%s/%.4fs/%s/%s" % (op,
                #                              started,
                #                              self.render_time(None, started - self.update_status.get_started()),
                #                              self.render_time(None,t)))
                if op == "query":
                    times.append(abbreviate_time(t))
                elif op == "late":
                    times.append("late(" + abbreviate_time(t) + ")")
                else:
                    times.append("privkey(" + abbreviate_time(t) + ")")
            times_s = ", ".join(times)
            l(tags.li("[%s]: %s" % (str(server.get_name(), "utf-8"), times_s)))
        return tags.li("Per-Server Response Times: ", l)


def marshal_json(s):
    # common item data
    item = {
        "storage-index-string": base32.b2a_or_none(s.get_storage_index()),
        "total-size": s.get_size(),
        "status": s.get_status(),
    }

    # type-specific item date
    if IUploadStatus.providedBy(s):
        h, c, e = s.get_progress()
        item["type"] = "upload"
        item["progress-hash"] = h
        item["progress-ciphertext"] = c
        item["progress-encode-push"] = e

    elif IDownloadStatus.providedBy(s):
        item["type"] = "download"
        item["progress"] = s.get_progress()

    elif IPublishStatus.providedBy(s):
        item["type"] = "publish"

    elif IRetrieveStatus.providedBy(s):
        item["type"] = "retrieve"

    elif IServermapUpdaterStatus.providedBy(s):
        item["type"] = "mapupdate"
        item["mode"] = s.get_mode()

    else:
        item["type"] = "unknown"
        item["class"] = s.__class__.__name__

    return item


class Status(MultiFormatResource):
    """Renders /status page."""

    def __init__(self, history):
        """
        :param allmydata.history.History history: provides operation statuses.
        """
        super(Status, self).__init__()
        self.history = history

    @render_exception
    def render_HTML(self, req):
        elem = StatusElement(self._get_active_operations(),
                             self._get_recent_operations())
        return renderElement(req, elem)

    @render_exception
    def render_JSON(self, req):
        # modern browsers now render this instead of forcing downloads
        req.setHeader("content-type", "application/json")
        data = {}
        data["active"] = active = []
        data["recent"] = recent = []

        for s in self._get_active_operations():
            active.append(marshal_json(s))

        for s in self._get_recent_operations():
            recent.append(marshal_json(s))

        return json.dumps(data, indent=1) + "\n"

    @exception_to_child
    def getChild(self, path, request):
        # The "if (path is empty) return self" line should handle
        # trailing slash in request path.
        #
        # Twisted Web's documentation says this: "If the URL ends in a
        # slash, for example ``http://example.com/foo/bar/`` , the
        # final URL segment will be an empty string. Resources can
        # thus know if they were requested with or without a final
        # slash."
        if not path and request.postpath != [b'']:
            return self

        h = self.history
        try:
            stype, count_s = path.split(b"-")
        except ValueError:
            raise WebError("no '-' in '{}'".format(str(path, "utf-8")))
        count = int(count_s)
        stype = str(stype, "ascii")
        if stype == "up":
            for s in itertools.chain(h.list_all_upload_statuses(),
                                     h.list_all_helper_statuses()):
                # immutable-upload helpers use the same status object as a
                # regular immutable-upload
                if s.get_counter() == count:
                    return UploadStatusPage(s)
        if stype == "down":
            for s in h.list_all_download_statuses():
                if s.get_counter() == count:
                    return DownloadStatusPage(s)
        if stype == "mapupdate":
            for s in h.list_all_mapupdate_statuses():
                if s.get_counter() == count:
                    return MapupdateStatusPage(s)
        if stype == "publish":
            for s in h.list_all_publish_statuses():
                if s.get_counter() == count:
                    return PublishStatusPage(s)
        if stype == "retrieve":
            for s in h.list_all_retrieve_statuses():
                if s.get_counter() == count:
                    return RetrieveStatusPage(s)

    def _get_all_statuses(self):
        h = self.history
        return itertools.chain(h.list_all_upload_statuses(),
                               h.list_all_download_statuses(),
                               h.list_all_mapupdate_statuses(),
                               h.list_all_publish_statuses(),
                               h.list_all_retrieve_statuses(),
                               h.list_all_helper_statuses(),
                               )

    def _get_active_operations(self):
        active = [s
                  for s in self._get_all_statuses()
                  if s.get_active()]
        active.sort(key=lambda a: a.get_started())
        active.reverse()
        return active

    def _get_recent_operations(self):
        recent = [s
                  for s in self._get_all_statuses()
                  if not s.get_active()]
        recent.sort(key=lambda a: a.get_started())
        recent.reverse()
        return recent


class StatusElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("status.xhtml"))

    def __init__(self, active, recent):
        super(StatusElement, self).__init__()
        self._active = active
        self._recent = recent

    @renderer
    def active_operations(self, req, tag):
        active = [self.get_op_state(op) for op in self._active]
        return SlotsSequenceElement(tag, active)

    @renderer
    def recent_operations(self, req, tag):
        recent = [self.get_op_state(op) for op in self._recent]
        return SlotsSequenceElement(tag, recent)

    @staticmethod
    def get_op_state(op):
        result = dict()

        started_s = render_time(op.get_started())
        result["started"] = started_s
        si_s = base32.b2a_or_none(op.get_storage_index())
        if si_s is None:
            si_s = "(None)"

        result["si"] = si_s
        result["helper"] = {True: "Yes", False: "No"}[op.using_helper()]

        size = op.get_size()
        if size is None:
            size = "(unknown)"
        elif isinstance(size, (int, long, float)):
            size = abbreviate_size(size)

        result["total_size"] = size

        progress = op.get_progress()
        if IUploadStatus.providedBy(op):
            link = "up-%d" % op.get_counter()
            result["type"] = "upload"
            # TODO: make an ascii-art bar
            (chk, ciphertext, encandpush) = progress
            progress_s = ("hash: %.1f%%, ciphertext: %.1f%%, encode: %.1f%%" %
                          ((100.0 * chk),
                           (100.0 * ciphertext),
                           (100.0 * encandpush)))
            result["progress"] = progress_s
        elif IDownloadStatus.providedBy(op):
            link = "down-%d" % op.get_counter()
            result["type"] = "download"
            result["progress"] = "%.1f%%" % (100.0 * progress)
        elif IPublishStatus.providedBy(op):
            link = "publish-%d" % op.get_counter()
            result["type"] = "publish"
            result["progress"] = "%.1f%%" % (100.0 * progress)
        elif IRetrieveStatus.providedBy(op):
            result["type"] = "retrieve"
            link = "retrieve-%d" % op.get_counter()
            result["progress"] = "%.1f%%" % (100.0 * progress)
        else:
            assert IServermapUpdaterStatus.providedBy(op)
            result["type"] = "mapupdate %s" % op.get_mode()
            link = "mapupdate-%d" % op.get_counter()
            result["progress"] = "%.1f%%" % (100.0 * progress)

        result["status"] = tags.a(op.get_status(),
                                  href="/status/{}".format(link))

        return result


# Render "/helper_status" page.
class HelperStatus(MultiFormatResource):

    def __init__(self, helper):
        super(HelperStatus, self).__init__()
        self._helper = helper

    @render_exception
    def render_HTML(self, req):
        return renderElement(req, HelperStatusElement(self._helper))

    @render_exception
    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        if self._helper:
            stats = self._helper.get_stats()
            return json.dumps(stats, indent=1) + "\n"
        return json.dumps({}) + "\n"

class HelperStatusElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("helper.xhtml"))

    def __init__(self, helper):
        """
        :param _allmydata.immutable.offloaded.Helper helper: upload helper.
        """
        super(HelperStatusElement, self).__init__()
        self._helper = helper

    @renderer
    def helper_running(self, req, tag):
        # helper.get_stats() returns a dict of this form:
        #
        #   {'chk_upload_helper.active_uploads': 0,
        #    'chk_upload_helper.encoded_bytes': 0,
        #    'chk_upload_helper.encoding_count': 0,
        #    'chk_upload_helper.encoding_size': 0,
        #    'chk_upload_helper.encoding_size_old': 0,
        #    'chk_upload_helper.fetched_bytes': 0,
        #    'chk_upload_helper.incoming_count': 0,
        #    'chk_upload_helper.incoming_size': 0,
        #    'chk_upload_helper.incoming_size_old': 0,
        #    'chk_upload_helper.resumes': 0,
        #    'chk_upload_helper.upload_already_present': 0,
        #    'chk_upload_helper.upload_need_upload': 0,
        #    'chk_upload_helper.upload_requests': 0}
        #
        # If helper is running, we render the above data on the page.
        if self._helper:
            self._data = self._helper.get_stats()
            return tag
        return tags.h1("No helper is running")

    @renderer
    def active_uploads(self, req, tag):
        return tag(str(self._data["chk_upload_helper.active_uploads"]))

    @renderer
    def incoming(self, req, tag):
        return tag("%d bytes in %d files" % (self._data["chk_upload_helper.incoming_size"],
                                             self._data["chk_upload_helper.incoming_count"]))

    @renderer
    def encoding(self, req, tag):
        return tag("%d bytes in %d files" % (self._data["chk_upload_helper.encoding_size"],
                                             self._data["chk_upload_helper.encoding_count"]))

    @renderer
    def upload_requests(self, req, tag):
        return tag(str(self._data["chk_upload_helper.upload_requests"]))

    @renderer
    def upload_already_present(self, req, tag):
        return tag(str(self._data["chk_upload_helper.upload_already_present"]))

    @renderer
    def upload_need_upload(self, req, tag):
        return tag(str(self._data["chk_upload_helper.upload_need_upload"]))

    @renderer
    def upload_bytes_fetched(self, req, tag):
        return tag(str(self._data["chk_upload_helper.fetched_bytes"]))

    @renderer
    def upload_bytes_encoded(self, req, tag):
        return tag(str(self._data["chk_upload_helper.encoded_bytes"]))


# Render "/statistics" page.
class Statistics(MultiFormatResource):
    """Class that renders "/statistics" page.

    :param _allmydata.stats.StatsProvider provider: node statistics
           provider.
    """

    def __init__(self, provider):
        super(Statistics, self).__init__()
        self._provider = provider

    @render_exception
    def render_HTML(self, req):
        return renderElement(req, StatisticsElement(self._provider))

    @render_exception
    def render_JSON(self, req):
        stats = self._provider.get_stats()
        req.setHeader("content-type", "text/plain")
        return json.dumps(stats, indent=1) + "\n"

    @render_exception
    def render_OPENMETRICS(self, req):
        """
        Render our stats in `OpenMetrics <https://openmetrics.io/>` format.
        For example Prometheus and Victoriametrics can parse this.
        Point the scraper to ``/statistics?t=openmetrics`` (instead of the
        default ``/metrics``).
        """
        req.setHeader("content-type", "application/openmetrics-text; version=1.0.0; charset=utf-8")
        stats = self._provider.get_stats()
        ret = []

        def mangle_name(name):
            return re.sub(
                u"_(\d\d)_(\d)_percentile",
                u'{quantile="0.\g<1>\g<2>"}',
                name.replace(u".", u"_")
            )

        def mangle_value(val):
            return str(val) if val is not None else u"NaN"

        for (k, v) in sorted(stats['counters'].items()):
            ret.append(u"tahoe_counters_%s %s" % (mangle_name(k), mangle_value(v)))
        for (k, v) in sorted(stats['stats'].items()):
            ret.append(u"tahoe_stats_%s %s" % (mangle_name(k), mangle_value(v)))

        ret.append(u"# EOF\n")

        return u"\n".join(ret)

class StatisticsElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("statistics.xhtml"))

    def __init__(self, provider):
        super(StatisticsElement, self).__init__()
        # provider.get_stats() returns a dict of the below form, for
        # example (there's often more data than this):
        #
        #  {
        #    'stats': {
        #      'storage_server.disk_used': 809601609728,
        #      'storage_server.accepting_immutable_shares': 1,
        #      'storage_server.disk_free_for_root': 131486851072,
        #      'storage_server.reserved_space': 1000000000,
        #      'node.uptime': 0.16520118713378906,
        #      'storage_server.disk_total': 941088460800,
        #      'cpu_monitor.total': 0.004513999999999907,
        #      'storage_server.disk_avail': 82610759168,
        #      'storage_server.allocated': 0,
        #      'storage_server.disk_free_for_nonroot': 83610759168 },
        #    'counters': {
        #      'uploader.files_uploaded': 0,
        #      'uploader.bytes_uploaded': 0,
        #       ... }
        #  }
        #
        # Note that `counters` can be empty.
        self._stats = provider.get_stats()

    @renderer
    def uploads(self, req, tag):
        files = self._stats["counters"].get("uploader.files_uploaded", 0)
        bytes_uploaded = self._stats["counters"].get("uploader.bytes_uploaded", 0)
        return tag(("%s files / %s bytes (%s)" %
                    (files, bytes_uploaded, abbreviate_size(bytes_uploaded))))

    @renderer
    def downloads(self, req, tag):
        files = self._stats["counters"].get("downloader.files_downloaded", 0)
        bytes_uploaded = self._stats["counters"].get("downloader.bytes_downloaded", 0)
        return tag("%s files / %s bytes (%s)" %
                   (files, bytes_uploaded, abbreviate_size(bytes_uploaded)))

    @renderer
    def publishes(self, req, tag):
        files = self._stats["counters"].get("mutable.files_published", 0)
        bytes_uploaded = self._stats["counters"].get("mutable.bytes_published", 0)
        return tag("%s files / %s bytes (%s)" % (files, bytes_uploaded,
                                                 abbreviate_size(bytes_uploaded)))

    @renderer
    def retrieves(self, req, tag):
        files = self._stats["counters"].get("mutable.files_retrieved", 0)
        bytes_uploaded = self._stats["counters"].get("mutable.bytes_retrieved", 0)
        return tag("%s files / %s bytes (%s)" % (files, bytes_uploaded,
                                                 abbreviate_size(bytes_uploaded)))

    @renderer
    def raw(self, req, tag):
        raw = json.dumps(self._stats, sort_keys=True, indent=4)
        return tag(raw)
