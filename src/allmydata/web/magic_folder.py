import simplejson  # XXX why not built-in "json"

from twisted.web.server import UnsupportedMethod

from nevow import rend, url, tags as T
from nevow.inevow import IRequest

from allmydata.web.common import getxmlfile, get_arg, WebError


class MagicFolderWebApi(rend.Page):
    """
    I provide the web-based API for Magic Folder status etc.
    """

    def __init__(self, client):
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
        if req.method != 'POST':
            raise UnsupportedMethod(('POST',))

        token = get_arg(req, "token", None)
        # XXX need constant-time comparison?
        if token is None or token != self.client.get_auth_token():
            raise WebError("Missing or invalid token.", 400)

        if t is None:
            return rend.Page.renderHTTP(self, ctx)

        t = t.strip()
        if t == 'json':
            return self._render_json(req)

        raise WebError("'%s' invalid type for 't' arg" % (t,), 400)


