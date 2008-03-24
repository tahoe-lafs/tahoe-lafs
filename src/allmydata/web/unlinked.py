
import urllib
from twisted.web import http
from nevow import rend, inevow, url, tags as T
from allmydata.upload import FileHandle
from allmydata.web.common import IClient, getxmlfile, get_arg, boolean_of_arg
from allmydata.web import status
from allmydata.util import observer

class UnlinkedPUTCHKUploader(rend.Page):
    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        assert req.method == "PUT"
        # "PUT /uri", to create an unlinked file. This is like PUT but
        # without the associated set_uri.

        client = IClient(ctx)

        uploadable = FileHandle(req.content, client.convergence)
        d = client.upload(uploadable)
        d.addCallback(lambda results: results.uri)
        # that fires with the URI of the new file
        return d

class UnlinkedPUTSSKUploader(rend.Page):
    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        assert req.method == "PUT"
        # SDMF: files are small, and we can only upload data
        req.content.seek(0)
        data = req.content.read()
        d = IClient(ctx).create_mutable_file(data)
        d.addCallback(lambda n: n.get_uri())
        return d

class UnlinkedPUTCreateDirectory(rend.Page):
    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        assert req.method == "PUT"
        # "PUT /uri?t=mkdir", to create an unlinked directory.
        d = IClient(ctx).create_empty_dirnode()
        d.addCallback(lambda dirnode: dirnode.get_uri())
        # XXX add redirect_to_result
        return d

class UnlinkedPOSTCHKUploader(status.UploadResultsRendererMixin, rend.Page):
    """'POST /uri', to create an unlinked file."""
    docFactory = getxmlfile("upload-results.xhtml")

    def __init__(self, client, req):
        rend.Page.__init__(self)
        # we start the upload now, and distribute notification of its
        # completion to render_ methods with an ObserverList
        assert req.method == "POST"
        self._done = observer.OneShotObserverList()
        fileobj = req.fields["file"].file
        uploadable = FileHandle(fileobj, client.convergence)
        d = client.upload(uploadable)
        d.addBoth(self._done.fire)

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        when_done = get_arg(req, "when_done", None)
        if when_done:
            # if when_done= is provided, return a redirect instead of our
            # usual upload-results page
            d = self._done.when_fired()
            d.addCallback(lambda res: url.URL.fromString(when_done))
            return d
        return rend.Page.renderHTTP(self, ctx)

    def upload_results(self):
        return self._done.when_fired()

    def data_done(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: "done!")
        return d

    def data_uri(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.uri)
        return d

    def render_download_link(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: T.a(href="/uri/" + urllib.quote(res.uri))
                      ["/uri/" + res.uri])
        return d

class UnlinkedPOSTSSKUploader(rend.Page):
    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        assert req.method == "POST"

        # "POST /uri", to create an unlinked file.
        # SDMF: files are small, and we can only upload data
        contents = req.fields["file"]
        contents.file.seek(0)
        data = contents.file.read()
        d = IClient(ctx).create_mutable_file(data)
        d.addCallback(lambda n: n.get_uri())
        return d

class UnlinkedPOSTCreateDirectory(rend.Page):
    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        assert req.method == "POST"

        # "POST /uri?t=mkdir", to create an unlinked directory.
        d = IClient(ctx).create_empty_dirnode()
        redirect = get_arg(req, "redirect_to_result", "false")
        if boolean_of_arg(redirect):
            def _then_redir(res):
                new_url = "uri/" + urllib.quote(res.get_uri())
                req.setResponseCode(http.SEE_OTHER) # 303
                req.setHeader('location', new_url)
                req.finish()
                return ''
            d.addCallback(_then_redir)
        else:
            d.addCallback(lambda dirnode: dirnode.get_uri())
        return d

