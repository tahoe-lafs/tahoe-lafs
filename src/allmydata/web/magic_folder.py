import simplejson

from twisted.web.server import UnsupportedMethod

from nevow import rend
from nevow.inevow import IRequest

from allmydata.web.common import WebError, TokenOnlyWebApi


class MagicFolderWebApi(TokenOnlyWebApi):
    """
    I provide the web-based API for Magic Folder status etc.
    """

    def __init__(self, client):
        TokenOnlyWebApi.__init__(self, client)
        self.client = client

    def post_json(self, req):
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
