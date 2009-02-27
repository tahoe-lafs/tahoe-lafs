
from nevow import rend, tags as T
from allmydata.web.common import getxmlfile, abbreviate_time
from allmydata.util.abbreviate import abbreviate_space

def remove_prefix(s, prefix):
    if not s.startswith(prefix):
        return None
    return s[len(prefix):]

class StorageStatus(rend.Page):
    docFactory = getxmlfile("storage_status.xhtml")
    # the default 'data' argument is the StorageServer instance

    def __init__(self, storage):
        rend.Page.__init__(self, storage)
        self.storage = storage

    def render_storage_running(self, ctx, storage):
        if storage:
            return ctx.tag
        else:
            return T.h1["No Storage Server Running"]

    def render_bool(self, ctx, data):
        return {True: "Yes", False: "No"}[bool(data)]

    def render_space(self, ctx, size):
        if size is None:
            return "?"
        return "%s (%d)" % (abbreviate_space(size), size)

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
        s = self.storage.bucket_counter.get_state()
        count = s.get("last-complete-bucket-count")
        if count is None:
            return "Not computed yet"
        return count

    def render_count_crawler_status(self, ctx, storage):
        s = self.storage.bucket_counter.get_progress()

        cycletime = s["estimated-time-per-cycle"]
        cycletime_s = ""
        if cycletime is not None:
            cycletime_s = " (estimated cycle time %ds)" % cycletime

        if s["cycle-in-progress"]:
            pct = s["cycle-complete-percentage"]
            soon = s["remaining-sleep-time"]

            eta = s["estimated-cycle-complete-time-left"]
            eta_s = ""
            if eta is not None:
                eta_s = " (ETA %ds)" % eta

            return ctx.tag["Current crawl %.1f%% complete" % pct,
                           eta_s,
                           " (next work in %s)" % abbreviate_time(soon),
                           cycletime_s,
                           ]
        else:
            soon = s["remaining-wait-time"]
            return ctx.tag["Next crawl in %s" % abbreviate_time(soon),
                           cycletime_s]
