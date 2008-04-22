
import time, pprint, itertools
import simplejson
from twisted.internet import defer
from nevow import rend, inevow, tags as T
from allmydata.util import base32, idlib
from allmydata.web.common import IClient, getxmlfile, abbreviate_time, \
     abbreviate_rate, abbreviate_size, get_arg
from allmydata.interfaces import IUploadStatus, IDownloadStatus, \
     IPublishStatus, IRetrieveStatus, IServermapUpdaterStatus

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
        return abbreviate_time(data)

    def render_rate(self, ctx, data):
        return abbreviate_rate(data)

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
            time1 = r.timings.get("cumulative_encoding")
            time2 = r.timings.get("cumulative_sending")
            if (file_size is None or time1 is None or time2 is None):
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
            time = r.timings.get("cumulative_fetch")
            if (fetch_size is None or time is None):
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

    def data_time_paused(self, ctx, data):
        return self._get_time("paused")

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
        if time is None or file_size is None:
            return None
        try:
            return 1.0 * file_size / time
        except ZeroDivisionError:
            return None

    def data_time_total(self, ctx, data):
        return self.retrieve_status.timings.get("total")
    def data_rate_total(self, ctx, data):
        return self._get_rate(data, "total")

    def data_time_fetch(self, ctx, data):
        return self.retrieve_status.timings.get("fetch")
    def data_rate_fetch(self, ctx, data):
        return self._get_rate(data, "fetch")

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

    def render_sharemap(self, ctx, data):
        servermap = data.get_servermap()
        if servermap is None:
            return ctx.tag["None"]
        l = T.ul()
        sharemap = servermap.make_sharemap()
        for shnum in sorted(sharemap.keys()):
            l[T.li["%d -> Placed on " % shnum,
                   ", ".join(["[%s]" % idlib.shortnodeid_b2a(peerid)
                              for peerid in sharemap[shnum]])]]
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

    def render_server_timings(self, ctx, data):
        per_server = self.publish_status.timings.get("send_per_server")
        if not per_server:
            return ""
        l = T.ul()
        for peerid in sorted(per_server.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            times_s = ", ".join([self.render_time(None, t)
                                 for t in per_server[peerid]])
            l[T.li["[%s]: %s" % (peerid_s, times_s)]]
        return T.li["Per-Server Response Times: ", l]

class MapupdateStatusPage(rend.Page, RateAndTimeMixin):
    docFactory = getxmlfile("map-update-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.update_status = data

    def render_started(self, ctx, data):
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        started_s = time.strftime(TIME_FORMAT,
                                  time.localtime(data.get_started()))
        return started_s

    def render_finished(self, ctx, data):
        when = data.get_finished()
        if not when:
            return "not yet"
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        started_s = time.strftime(TIME_FORMAT,
                                  time.localtime(data.get_finished()))
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_progress(self, ctx, data):
        progress = data.get_progress()
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

    def render_problems(self, ctx, data):
        problems = data.problems
        if not problems:
            return ""
        l = T.ul()
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            l[T.li["[%s]: %s" % (peerid_s, problems[peerid])]]
        return ctx.tag["Server Problems:", l]

    def render_privkey_from(self, ctx, data):
        peerid = data.get_privkey_from()
        if peerid:
            return ctx.tag["Got privkey from: [%s]"
                           % idlib.shortnodeid_b2a(peerid)]
        else:
            return ""

    def data_time_total(self, ctx, data):
        return self.update_status.timings.get("total")

    def data_time_initial_queries(self, ctx, data):
        return self.update_status.timings.get("initial_queries")

    def data_time_cumulative_verify(self, ctx, data):
        return self.update_status.timings.get("cumulative_verify")

    def render_server_timings(self, ctx, data):
        per_server = self.update_status.timings.get("per_server")
        if not per_server:
            return ""
        l = T.ul()
        for peerid in sorted(per_server.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            times = []
            for op,started,t in per_server[peerid]:
                #times.append("%s/%.4fs/%s/%s" % (op,
                #                              started,
                #                              self.render_time(None, started - self.update_status.get_started()),
                #                              self.render_time(None,t)))
                if op == "query":
                    times.append( self.render_time(None, t) )
                elif op == "late":
                    times.append( "late(" + self.render_time(None, t) + ")" )
                else:
                    times.append( "privkey(" + self.render_time(None, t) + ")" )
            times_s = ", ".join(times)
            l[T.li["[%s]: %s" % (peerid_s, times_s)]]
        return T.li["Per-Server Response Times: ", l]

    def render_timing_chart(self, ctx, data):
        imageurl = self._timing_chart()
        return ctx.tag[imageurl]

    def _timing_chart(self):
        started = self.update_status.get_started()
        total = self.update_status.timings.get("total")
        per_server = self.update_status.timings.get("per_server")
        base = "http://chart.apis.google.com/chart?"
        pieces = ["cht=bhs", "chs=400x300"]
        pieces.append("chco=ffffff,4d89f9,c6d9fd") # colors
        data0 = []
        data1 = []
        data2 = []
        peerids_s = []
        top_abs = started
        # we sort the queries by the time at which we sent the first request
        sorttable = [ (times[0][1], peerid)
                      for peerid, times in per_server.items() ]
        sorttable.sort()
        peerids = [t[1] for t in sorttable]

        for peerid in peerids:
            times = per_server[peerid]
            peerid_s = idlib.shortnodeid_b2a(peerid)
            peerids_s.append(peerid_s)
            # for servermap updates, there are either one or two queries per
            # peer. The second (if present) is to get the privkey.
            op,q_started,q_elapsed = times[0]
            data0.append("%.3f" % (q_started-started))
            data1.append("%.3f" % q_elapsed)
            top_abs = max(top_abs, q_started+q_elapsed)
            if len(times) > 1:
                op,p_started,p_elapsed = times[0]
                data2.append("%.3f" % p_elapsed)
                top_abs = max(top_abs, p_started+p_elapsed)
            else:
                data2.append("0.0")
        finished = self.update_status.get_finished()
        if finished:
            top_abs = max(top_abs, finished)
        top_rel = top_abs - started
        chd = "chd=t:" + "|".join([",".join(data0),
                                   ",".join(data1),
                                   ",".join(data2)])
        pieces.append(chd)
        chds = "chds=0,%0.3f" % top_rel
        pieces.append(chds)
        pieces.append("chxt=x,y")
        pieces.append("chxr=0,0.0,%0.3f" % top_rel)
        pieces.append("chxl=1:|" + "|".join(reversed(peerids_s)))
        # use up to 10 grid lines, at decimal multiples.
        # mathutil.next_power_of_k doesn't handle numbers smaller than one,
        # unfortunately.
        #pieces.append("chg="

        if total is not None:
            finished_f = 1.0 * total / top_rel
            pieces.append("chm=r,FF0000,0,%0.3f,%0.3f" % (finished_f,
                                                          finished_f+0.01))
        url = base + "&".join(pieces)
        return T.img(src=url, align="right", float="right")


class Status(rend.Page):
    docFactory = getxmlfile("status.xhtml")
    addSlash = True

    def _get_all_statuses(self, client):
        return itertools.chain(client.list_all_upload_statuses(),
                               client.list_all_download_statuses(),
                               client.list_all_mapupdate_statuses(),
                               client.list_all_publish_statuses(),
                               client.list_all_retrieve_statuses(),
                               client.list_all_helper_statuses(),
                               )

    def data_active_operations(self, ctx, data):
        client = IClient(ctx)
        active = [s
                  for s in self._get_all_statuses(client)
                  if s.get_active()]
        return active

    def data_recent_operations(self, ctx, data):
        client = IClient(ctx)
        recent = [s
                  for s in self._get_all_statuses(client)
                  if not s.get_active()]
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
        elif IRetrieveStatus.providedBy(data):
            ctx.fillSlots("type", "retrieve")
            link = "retrieve-%d" % data.get_counter()
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        else:
            assert IServermapUpdaterStatus.providedBy(data)
            ctx.fillSlots("type", "mapupdate %s" % data.get_mode())
            link = "mapupdate-%d" % data.get_counter()
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        ctx.fillSlots("status", T.a(href=link)[s.get_status()])
        return ctx.tag

    def childFactory(self, ctx, name):
        client = IClient(ctx)
        stype,count_s = name.split("-")
        count = int(count_s)
        if stype == "up":
            for s in itertools.chain(client.list_all_upload_statuses(),
                                     client.list_all_helper_statuses()):
                # immutable-upload helpers use the same status object as a
                # regular immutable-upload
                if s.get_counter() == count:
                    return UploadStatusPage(s)
        if stype == "down":
            for s in client.list_all_download_statuses():
                if s.get_counter() == count:
                    return DownloadStatusPage(s)
        if stype == "mapupdate":
            for s in client.list_all_mapupdate_statuses():
                if s.get_counter() == count:
                    return MapupdateStatusPage(s)
        if stype == "publish":
            for s in client.list_all_publish_statuses():
                if s.get_counter() == count:
                    return PublishStatusPage(s)
        if stype == "retrieve":
            for s in client.list_all_retrieve_statuses():
                if s.get_counter() == count:
                    return RetrieveStatusPage(s)


class HelperStatus(rend.Page):
    docFactory = getxmlfile("helper.xhtml")

    def renderHTTP(self, ctx):
        t = get_arg(inevow.IRequest(ctx), "t")
        if t == "json":
            return self.render_JSON(ctx)
        # is there a better way to provide 'data' to all rendering methods?
        helper = IClient(ctx).getServiceNamed("helper")
        self.original = helper.get_stats()
        return rend.Page.renderHTTP(self, ctx)

    def render_JSON(self, ctx):
        try:
            h = IClient(ctx).getServiceNamed("helper")
        except KeyError:
            return simplejson.dumps({})

        stats = h.get_stats()
        return simplejson.dumps(stats, indent=1)

    def render_active_uploads(self, ctx, data):
        return data["chk_upload_helper.active_uploads"]

    def render_incoming(self, ctx, data):
        return "%d bytes in %d files" % (data["chk_upload_helper.incoming_size"],
                                         data["chk_upload_helper.incoming_count"])

    def render_encoding(self, ctx, data):
        return "%d bytes in %d files" % (data["chk_upload_helper.encoding_size"],
                                         data["chk_upload_helper.encoding_count"])

    def render_upload_requests(self, ctx, data):
        return str(data["chk_upload_helper.upload_requests"])

    def render_upload_already_present(self, ctx, data):
        return str(data["chk_upload_helper.upload_already_present"])

    def render_upload_need_upload(self, ctx, data):
        return str(data["chk_upload_helper.upload_need_upload"])

    def render_upload_bytes_fetched(self, ctx, data):
        return str(data["chk_upload_helper.fetched_bytes"])

    def render_upload_bytes_encoded(self, ctx, data):
        return str(data["chk_upload_helper.encoded_bytes"])


class Statistics(rend.Page):
    docFactory = getxmlfile("statistics.xhtml")

    def renderHTTP(self, ctx):
        provider = IClient(ctx).stats_provider
        stats = {'stats': {}, 'counters': {}}
        if provider:
            stats = provider.get_stats()
        t = get_arg(inevow.IRequest(ctx), "t")
        if t == "json":
            return simplejson.dumps(stats, indent=1)
        # is there a better way to provide 'data' to all rendering methods?
        self.original = stats
        return rend.Page.renderHTTP(self, ctx)

    def render_load_average(self, ctx, data):
        return str(data["stats"].get("load_monitor.avg_load"))

    def render_peak_load(self, ctx, data):
        return str(data["stats"].get("load_monitor.max_load"))

    def render_uploads(self, ctx, data):
        files = data["counters"].get("uploader.files_uploaded")
        bytes = data["counters"].get("uploader.bytes_uploaded")
        return ("%s files / %s bytes (%s)" %
                (files, bytes, abbreviate_size(bytes)))

    def render_downloads(self, ctx, data):
        files = data["counters"].get("downloader.files_downloaded")
        bytes = data["counters"].get("downloader.bytes_downloaded")
        return ("%s files / %s bytes (%s)" %
                (files, bytes, abbreviate_size(bytes)))

    def render_publishes(self, ctx, data):
        files = data["counters"].get("mutable.files_published")
        return "%s files" % (files,)

    def render_retrieves(self, ctx, data):
        files = data["counters"].get("mutable.files_retrieved")
        return "%s files" % (files,)

    def render_raw(self, ctx, data):
        raw = pprint.pformat(data)
        return ctx.tag[raw]
