
import time
from twisted.internet import defer
from nevow import rend, tags as T
from allmydata.util import base32, idlib
from allmydata.web.common import IClient, getxmlfile
from allmydata.interfaces import IUploadStatus, IDownloadStatus, \
     IPublishStatus, IRetrieveStatus

def plural(sequence_or_length):
    if isinstance(sequence_or_length, int):
        length = sequence_or_length
    else:
        length = len(sequence_or_length)
    if length == 1:
        return ""
    return "s"

class RateAndTimeMixin:

    def render_time(self, ctx, data):
        # 1.23s, 790ms, 132us
        if data is None:
            return ""
        s = float(data)
        if s >= 1.0:
            return "%.2fs" % s
        if s >= 0.01:
            return "%dms" % (1000*s)
        if s >= 0.001:
            return "%.1fms" % (1000*s)
        return "%dus" % (1000000*s)

    def render_rate(self, ctx, data):
        # 21.8kBps, 554.4kBps 4.37MBps
        if data is None:
            return ""
        r = float(data)
        if r > 1000000:
            return "%1.2fMBps" % (r/1000000)
        if r > 1000:
            return "%.1fkBps" % (r/1000)
        return "%dBps" % r

class UploadResultsRendererMixin(RateAndTimeMixin):
    # this requires a method named 'upload_results'

    def render_pushed_shares(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.pushed_shares)
        return d

    def render_preexisting_shares(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.preexisting_shares)
        return d

    def render_sharemap(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.sharemap)
        def _render(sharemap):
            if sharemap is None:
                return "None"
            l = T.ul()
            for shnum in sorted(sharemap.keys()):
                l[T.li["%d -> %s" % (shnum, sharemap[shnum])]]
            return l
        d.addCallback(_render)
        return d

    def render_servermap(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.servermap)
        def _render(servermap):
            if servermap is None:
                return "None"
            l = T.ul()
            for peerid in sorted(servermap.keys()):
                peerid_s = idlib.shortnodeid_b2a(peerid)
                shares_s = ",".join(["#%d" % shnum
                                     for shnum in servermap[peerid]])
                l[T.li["[%s] got share%s: %s" % (peerid_s,
                                                 plural(servermap[peerid]),
                                                 shares_s)]]
            return l
        d.addCallback(_render)
        return d

    def data_file_size(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.file_size)
        return d

    def _get_time(self, name):
        d = self.upload_results()
        d.addCallback(lambda res: res.timings.get(name))
        return d

    def data_time_total(self, ctx, data):
        return self._get_time("total")

    def data_time_storage_index(self, ctx, data):
        return self._get_time("storage_index")

    def data_time_contacting_helper(self, ctx, data):
        return self._get_time("contacting_helper")

    def data_time_existence_check(self, ctx, data):
        return self._get_time("existence_check")

    def data_time_cumulative_fetch(self, ctx, data):
        return self._get_time("cumulative_fetch")

    def data_time_helper_total(self, ctx, data):
        return self._get_time("helper_total")

    def data_time_peer_selection(self, ctx, data):
        return self._get_time("peer_selection")

    def data_time_total_encode_and_push(self, ctx, data):
        return self._get_time("total_encode_and_push")

    def data_time_cumulative_encoding(self, ctx, data):
        return self._get_time("cumulative_encoding")

    def data_time_cumulative_sending(self, ctx, data):
        return self._get_time("cumulative_sending")

    def data_time_hashes_and_close(self, ctx, data):
        return self._get_time("hashes_and_close")

    def _get_rate(self, name):
        d = self.upload_results()
        def _convert(r):
            file_size = r.file_size
            time = r.timings.get(name)
            if time is None:
                return None
            try:
                return 1.0 * file_size / time
            except ZeroDivisionError:
                return None
        d.addCallback(_convert)
        return d

    def data_rate_total(self, ctx, data):
        return self._get_rate("total")

    def data_rate_storage_index(self, ctx, data):
        return self._get_rate("storage_index")

    def data_rate_encode(self, ctx, data):
        return self._get_rate("cumulative_encoding")

    def data_rate_push(self, ctx, data):
        return self._get_rate("cumulative_sending")

    def data_rate_encode_and_push(self, ctx, data):
        d = self.upload_results()
        def _convert(r):
            file_size = r.file_size
            if file_size is None:
                return None
            time1 = r.timings.get("cumulative_encoding")
            if time1 is None:
                return None
            time2 = r.timings.get("cumulative_sending")
            if time2 is None:
                return None
            try:
                return 1.0 * file_size / (time1+time2)
            except ZeroDivisionError:
                return None
        d.addCallback(_convert)
        return d

    def data_rate_ciphertext_fetch(self, ctx, data):
        d = self.upload_results()
        def _convert(r):
            fetch_size = r.ciphertext_fetched
            if fetch_size is None:
                return None
            time = r.timings.get("cumulative_fetch")
            if time is None:
                return None
            try:
                return 1.0 * fetch_size / time
            except ZeroDivisionError:
                return None
        d.addCallback(_convert)
        return d

class UploadStatusPage(UploadResultsRendererMixin, rend.Page):
    docFactory = getxmlfile("upload-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.upload_status = data

    def upload_results(self):
        return defer.maybeDeferred(self.upload_status.get_results)

    def render_results(self, ctx, data):
        d = self.upload_results()
        def _got_results(results):
            if results:
                return ctx.tag
            return ""
        d.addCallback(_got_results)
        return d

    def render_started(self, ctx, data):
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        started_s = time.strftime(TIME_FORMAT,
                                  time.localtime(data.get_started()))
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_total_size(self, ctx, data):
        size = data.get_size()
        if size is None:
            size = "(unknown)"
        return size

    def render_progress_hash(self, ctx, data):
        progress = data.get_progress()[0]
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_progress_ciphertext(self, ctx, data):
        progress = data.get_progress()[1]
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_progress_encode_push(self, ctx, data):
        progress = data.get_progress()[2]
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

class DownloadResultsRendererMixin(RateAndTimeMixin):
    # this requires a method named 'download_results'

    def render_servermap(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.servermap)
        def _render(servermap):
            if servermap is None:
                return "None"
            l = T.ul()
            for peerid in sorted(servermap.keys()):
                peerid_s = idlib.shortnodeid_b2a(peerid)
                shares_s = ",".join(["#%d" % shnum
                                     for shnum in servermap[peerid]])
                l[T.li["[%s] has share%s: %s" % (peerid_s,
                                                 plural(servermap[peerid]),
                                                 shares_s)]]
            return l
        d.addCallback(_render)
        return d

    def render_servers_used(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.servers_used)
        def _got(servers_used):
            if not servers_used:
                return ""
            peerids_s = ", ".join(["[%s]" % idlib.shortnodeid_b2a(peerid)
                                   for peerid in servers_used])
            return T.li["Servers Used: ", peerids_s]
        d.addCallback(_got)
        return d

    def render_problems(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.server_problems)
        def _got(server_problems):
            if not server_problems:
                return ""
            l = T.ul()
            for peerid in sorted(server_problems.keys()):
                peerid_s = idlib.shortnodeid_b2a(peerid)
                l[T.li["[%s]: %s" % (peerid_s, server_problems[peerid])]]
            return T.li["Server Problems:", l]
        d.addCallback(_got)
        return d

    def data_file_size(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.file_size)
        return d

    def _get_time(self, name):
        d = self.download_results()
        d.addCallback(lambda res: res.timings.get(name))
        return d

    def data_time_total(self, ctx, data):
        return self._get_time("total")

    def data_time_peer_selection(self, ctx, data):
        return self._get_time("peer_selection")

    def data_time_uri_extension(self, ctx, data):
        return self._get_time("uri_extension")

    def data_time_hashtrees(self, ctx, data):
        return self._get_time("hashtrees")

    def data_time_segments(self, ctx, data):
        return self._get_time("segments")

    def data_time_cumulative_fetch(self, ctx, data):
        return self._get_time("cumulative_fetch")

    def data_time_cumulative_decode(self, ctx, data):
        return self._get_time("cumulative_decode")

    def data_time_cumulative_decrypt(self, ctx, data):
        return self._get_time("cumulative_decrypt")

    def _get_rate(self, name):
        d = self.download_results()
        def _convert(r):
            file_size = r.file_size
            time = r.timings.get(name)
            if time is None:
                return None
            try:
                return 1.0 * file_size / time
            except ZeroDivisionError:
                return None
        d.addCallback(_convert)
        return d

    def data_rate_total(self, ctx, data):
        return self._get_rate("total")

    def data_rate_segments(self, ctx, data):
        return self._get_rate("segments")

    def data_rate_fetch(self, ctx, data):
        return self._get_rate("cumulative_fetch")

    def data_rate_decode(self, ctx, data):
        return self._get_rate("cumulative_decode")

    def data_rate_decrypt(self, ctx, data):
        return self._get_rate("cumulative_decrypt")

    def render_server_timings(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.timings.get("fetch_per_server"))
        def _render(per_server):
            if per_server is None:
                return ""
            l = T.ul()
            for peerid in sorted(per_server.keys()):
                peerid_s = idlib.shortnodeid_b2a(peerid)
                times_s = ", ".join([self.render_time(None, t)
                                     for t in per_server[peerid]])
                l[T.li["[%s]: %s" % (peerid_s, times_s)]]
            return T.li["Per-Server Segment Fetch Response Times: ", l]
        d.addCallback(_render)
        return d

class DownloadStatusPage(DownloadResultsRendererMixin, rend.Page):
    docFactory = getxmlfile("download-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.download_status = data

    def download_results(self):
        return defer.maybeDeferred(self.download_status.get_results)

    def render_results(self, ctx, data):
        d = self.download_results()
        def _got_results(results):
            if results:
                return ctx.tag
            return ""
        d.addCallback(_got_results)
        return d

    def render_started(self, ctx, data):
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        started_s = time.strftime(TIME_FORMAT,
                                  time.localtime(data.get_started()))
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_total_size(self, ctx, data):
        size = data.get_size()
        if size is None:
            size = "(unknown)"
        return size

    def render_progress(self, ctx, data):
        progress = data.get_progress()
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

class RetrieveStatusPage(rend.Page, RateAndTimeMixin):
    docFactory = getxmlfile("retrieve-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.retrieve_status = data

    def render_started(self, ctx, data):
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        started_s = time.strftime(TIME_FORMAT,
                                  time.localtime(data.get_started()))
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_current_size(self, ctx, data):
        size = data.get_size()
        if size is None:
            size = "(unknown)"
        return size

    def render_progress(self, ctx, data):
        progress = data.get_progress()
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

    def render_encoding(self, ctx, data):
        k, n = data.get_encoding()
        return ctx.tag["Encoding: %s of %s" % (k, n)]

    def render_search_distance(self, ctx, data):
        d = data.get_search_distance()
        return ctx.tag["Search Distance: %s peer%s" % (d, plural(d))]

    def render_problems(self, ctx, data):
        problems = data.problems
        if not problems:
            return ""
        l = T.ul()
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            l[T.li["[%s]: %s" % (peerid_s, problems[peerid])]]
        return ctx.tag["Server Problems:", l]

    def _get_rate(self, data, name):
        file_size = self.retrieve_status.get_size()
        time = self.retrieve_status.timings.get(name)
        if time is None:
            return None
        try:
            return 1.0 * file_size / time
        except ZeroDivisionError:
            return None

    def data_time_total(self, ctx, data):
        return self.retrieve_status.timings.get("total")
    def data_rate_total(self, ctx, data):
        return self._get_rate(data, "total")

    def data_time_peer_selection(self, ctx, data):
        return self.retrieve_status.timings.get("peer_selection")

    def data_time_fetch(self, ctx, data):
        return self.retrieve_status.timings.get("fetch")
    def data_rate_fetch(self, ctx, data):
        return self._get_rate(data, "fetch")

    def data_time_cumulative_verify(self, ctx, data):
        return self.retrieve_status.timings.get("cumulative_verify")

    def data_time_decode(self, ctx, data):
        return self.retrieve_status.timings.get("decode")
    def data_rate_decode(self, ctx, data):
        return self._get_rate(data, "decode")

    def data_time_decrypt(self, ctx, data):
        return self.retrieve_status.timings.get("decrypt")
    def data_rate_decrypt(self, ctx, data):
        return self._get_rate(data, "decrypt")

    def render_server_timings(self, ctx, data):
        per_server = self.retrieve_status.timings.get("fetch_per_server")
        if not per_server:
            return ""
        l = T.ul()
        for peerid in sorted(per_server.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            times_s = ", ".join([self.render_time(None, t)
                                 for t in per_server[peerid]])
            l[T.li["[%s]: %s" % (peerid_s, times_s)]]
        return T.li["Per-Server Fetch Response Times: ", l]


class PublishStatusPage(rend.Page, RateAndTimeMixin):
    docFactory = getxmlfile("publish-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.publish_status = data

    def render_started(self, ctx, data):
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        started_s = time.strftime(TIME_FORMAT,
                                  time.localtime(data.get_started()))
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_current_size(self, ctx, data):
        size = data.get_size()
        if size is None:
            size = "(unknown)"
        return size

    def render_progress(self, ctx, data):
        progress = data.get_progress()
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

    def render_encoding(self, ctx, data):
        k, n = data.get_encoding()
        return ctx.tag["Encoding: %s of %s" % (k, n)]

    def render_peers_queried(self, ctx, data):
        return ctx.tag["Peers Queried: ", data.peers_queried]

    def render_sharemap(self, ctx, data):
        sharemap = data.sharemap
        if sharemap is None:
            return ctx.tag["None"]
        l = T.ul()
        for shnum in sorted(sharemap.keys()):
            l[T.li["%d -> Placed on " % shnum,
                   ", ".join(["[%s]" % idlib.shortnodeid_b2a(peerid)
                              for (peerid,seqnum,root_hash)
                              in sharemap[shnum]])]]
        return ctx.tag["Sharemap:", l]

    def render_problems(self, ctx, data):
        problems = data.problems
        if not problems:
            return ""
        l = T.ul()
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            l[T.li["[%s]: %s" % (peerid_s, problems[peerid])]]
        return ctx.tag["Server Problems:", l]

    def _get_rate(self, data, name):
        file_size = self.publish_status.get_size()
        time = self.publish_status.timings.get(name)
        if time is None:
            return None
        try:
            return 1.0 * file_size / time
        except ZeroDivisionError:
            return None

    def data_time_total(self, ctx, data):
        return self.publish_status.timings.get("total")
    def data_rate_total(self, ctx, data):
        return self._get_rate(data, "total")

    def data_time_setup(self, ctx, data):
        return self.publish_status.timings.get("setup")

    def data_time_query(self, ctx, data):
        return self.publish_status.timings.get("query")

    def data_time_privkey(self, ctx, data):
        return self.publish_status.timings.get("privkey")

    def data_time_privkey_fetch(self, ctx, data):
        return self.publish_status.timings.get("privkey_fetch")
    def render_privkey_from(self, ctx, data):
        peerid = data.privkey_from
        if peerid:
            return " (got from [%s])" % idlib.shortnodeid_b2a(peerid)
        else:
            return ""

    def data_time_encrypt(self, ctx, data):
        return self.publish_status.timings.get("encrypt")
    def data_rate_encrypt(self, ctx, data):
        return self._get_rate(data, "encrypt")

    def data_time_encode(self, ctx, data):
        return self.publish_status.timings.get("encode")
    def data_rate_encode(self, ctx, data):
        return self._get_rate(data, "encode")

    def data_time_pack(self, ctx, data):
        return self.publish_status.timings.get("pack")
    def data_rate_pack(self, ctx, data):
        return self._get_rate(data, "pack")
    def data_time_sign(self, ctx, data):
        return self.publish_status.timings.get("sign")

    def data_time_push(self, ctx, data):
        return self.publish_status.timings.get("push")
    def data_rate_push(self, ctx, data):
        return self._get_rate(data, "push")

    def data_initial_read_size(self, ctx, data):
        return self.publish_status.initial_read_size

    def render_server_timings(self, ctx, data):
        per_server = self.publish_status.timings.get("per_server")
        if not per_server:
            return ""
        l = T.ul()
        for peerid in sorted(per_server.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            times = []
            for op,t in per_server[peerid]:
                if op == "read":
                    times.append( "(" + self.render_time(None, t) + ")" )
                else:
                    times.append( self.render_time(None, t) )
            times_s = ", ".join(times)
            l[T.li["[%s]: %s" % (peerid_s, times_s)]]
        return T.li["Per-Server Response Times: ", l]


class Status(rend.Page):
    docFactory = getxmlfile("status.xhtml")
    addSlash = True

    def data_active_operations(self, ctx, data):
        active =  (IClient(ctx).list_active_uploads() +
                   IClient(ctx).list_active_downloads() +
                   IClient(ctx).list_active_publish() +
                   IClient(ctx).list_active_retrieve())
        return active

    def data_recent_operations(self, ctx, data):
        recent = [o for o in (IClient(ctx).list_recent_uploads() +
                              IClient(ctx).list_recent_downloads() +
                              IClient(ctx).list_recent_publish() +
                              IClient(ctx).list_recent_retrieve())
                  if not o.get_active()]
        recent.sort(lambda a,b: cmp(a.get_started(), b.get_started()))
        recent.reverse()
        return recent

    def render_row(self, ctx, data):
        s = data

        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        started_s = time.strftime(TIME_FORMAT,
                                  time.localtime(s.get_started()))
        ctx.fillSlots("started", started_s)

        si_s = base32.b2a_or_none(s.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        ctx.fillSlots("si", si_s)
        ctx.fillSlots("helper", {True: "Yes",
                                 False: "No"}[s.using_helper()])

        size = s.get_size()
        if size is None:
            size = "(unknown)"
        ctx.fillSlots("total_size", size)

        progress = data.get_progress()
        if IUploadStatus.providedBy(data):
            link = "up-%d" % data.get_counter()
            ctx.fillSlots("type", "upload")
            # TODO: make an ascii-art bar
            (chk, ciphertext, encandpush) = progress
            progress_s = ("hash: %.1f%%, ciphertext: %.1f%%, encode: %.1f%%" %
                          ( (100.0 * chk),
                            (100.0 * ciphertext),
                            (100.0 * encandpush) ))
            ctx.fillSlots("progress", progress_s)
        elif IDownloadStatus.providedBy(data):
            link = "down-%d" % data.get_counter()
            ctx.fillSlots("type", "download")
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        elif IPublishStatus.providedBy(data):
            link = "publish-%d" % data.get_counter()
            ctx.fillSlots("type", "publish")
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        else:
            assert IRetrieveStatus.providedBy(data)
            ctx.fillSlots("type", "retrieve")
            link = "retrieve-%d" % data.get_counter()
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        ctx.fillSlots("status", T.a(href=link)[s.get_status()])
        return ctx.tag

    def childFactory(self, ctx, name):
        client = IClient(ctx)
        stype,count_s = name.split("-")
        count = int(count_s)
        if stype == "up":
            for s in client.list_recent_uploads():
                if s.get_counter() == count:
                    return UploadStatusPage(s)
            for s in client.list_all_uploads():
                if s.get_counter() == count:
                    return UploadStatusPage(s)
        if stype == "down":
            for s in client.list_recent_downloads():
                if s.get_counter() == count:
                    return DownloadStatusPage(s)
            for s in client.list_all_downloads():
                if s.get_counter() == count:
                    return DownloadStatusPage(s)
        if stype == "publish":
            for s in client.list_recent_publish():
                if s.get_counter() == count:
                    return PublishStatusPage(s)
            for s in client.list_all_publish():
                if s.get_counter() == count:
                    return PublishStatusPage(s)
        if stype == "retrieve":
            for s in client.list_recent_retrieve():
                if s.get_counter() == count:
                    return RetrieveStatusPage(s)
            for s in client.list_all_retrieve():
                if s.get_counter() == count:
                    return RetrieveStatusPage(s)


