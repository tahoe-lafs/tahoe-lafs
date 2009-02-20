
from nevow import rend, tags as T
from allmydata.web.common import getxmlfile, abbreviate_size

def abbreviate_if_known(size):
    if size is None:
        return "?"
    return abbreviate_size(size)

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

    def render_space(self, ctx, data):
        return abbreviate_if_known(data)

    def data_stats(self, ctx, data):
        # FYI: 'data' appears to be self, rather than the StorageServer
        # object in self.original that gets passed to render_* methods. I
        # still don't understand Nevow.

        # all xhtml tags that are children of a tag with n:render="stats"
        # will be processed with this dictionary, so something like:
        #  <ul n:data="stats">
        #   <li>disk_total: <span n:data="disk_total" /></li>
        #  </ul>
        # will use get_stats()["storage_server.disk_total"]
        return dict([ (remove_prefix(k, "storage_server."), v)
                      for k,v in self.storage.get_stats().items() ])
