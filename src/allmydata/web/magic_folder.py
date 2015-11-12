import simplejson  # XXX why not built-in "json"

from nevow import rend, url, tags as T
from nevow.inevow import IRequest

from allmydata.web.common import getxmlfile, get_arg, WebError


class MagicFolderWebApi(rend.Page):
    """
    I provide the web-based API for Magic Folder status etc.
    """

    def __init__(self, client):
        ##rend.Page.__init__(self, storage)
        super(MagicFolderWebApi, self).__init__(client)
        self.client = client

    def _render_json(self, req):
        req.setHeader("content-type", "application/json")

        data = []
        for item in self.client._magic_folder.uploader.get_status():
            d = dict(
                path=item.relpath_u,
                status=item.status_history()[-1][0],
                kind='upload',
            )
            for (status, ts) in item.status_history():
                d[status + '_at'] = ts
            d['percent_done'] = item.progress.progress
            data.append(d)

        for item in self.client._magic_folder.downloader.get_status():
            d = dict(
                path=item.relpath_u,
                status=item.status_history()[-1][0],
                kind='download',
            )
            for (status, ts) in item.status_history():
                d[status + '_at'] = ts
            d['percent_done'] = item.progress.progress
            data.append(d)

        return simplejson.dumps(data)

    def renderHTTP(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", None)

        if t is None:
            return rend.Page.renderHTTP(self, ctx)

        t = t.strip()
        if t == 'json':
            return self._render_json(req)

        raise WebError("'%s' invalid type for 't' arg" % (t,), 400)


