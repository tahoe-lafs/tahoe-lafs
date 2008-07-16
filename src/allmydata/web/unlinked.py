
import urllib
from twisted.web import http
from twisted.internet import defer
from nevow import rend, url, tags as T
from nevow.inevow import IRequest
from allmydata.immutable.upload import FileHandle
from allmydata.web.common import IClient, getxmlfile, get_arg, boolean_of_arg
from allmydata.web import status

def PUTUnlinkedCHK(ctx):
    req = IRequest(ctx)
    # "PUT /uri", to create an unlinked file.
    client = IClient(ctx)
    uploadable = FileHandle(req.content, client.convergence)
    d = client.upload(uploadable)
    d.addCallback(lambda results: results.uri)
    # that fires with the URI of the new file
    return d

def PUTUnlinkedSSK(ctx):
    req = IRequest(ctx)
    # SDMF: files are small, and we can only upload data
    req.content.seek(0)
    data = req.content.read()
    d = IClient(ctx).create_mutable_file(data)
    d.addCallback(lambda n: n.get_uri())
    return d

def PUTUnlinkedCreateDirectory(ctx):
    req = IRequest(ctx)
    # "PUT /uri?t=mkdir", to create an unlinked directory.
    d = IClient(ctx).create_empty_dirnode()
    d.addCallback(lambda dirnode: dirnode.get_uri())
    # XXX add redirect_to_result
    return d


def POSTUnlinkedCHK(ctx):
    req = IRequest(ctx)
    client = IClient(ctx)
    fileobj = req.fields["file"].file
    uploadable = FileHandle(fileobj, client.convergence)
    d = client.upload(uploadable)
    when_done = get_arg(req, "when_done", None)
    if when_done:
        # if when_done= is provided, return a redirect instead of our
        # usual upload-results page
        def _done(upload_results, redir_to):
            if "%(uri)s" in redir_to:
                redir_to = redir_to % {"uri": urllib.quote(upload_results.uri)
                                         }
            return url.URL.fromString(redir_to)
        d.addCallback(_done, when_done)
    else:
        # return the Upload Results page, which includes the URI
        d.addCallback(UploadResultsPage, ctx)
    return d


class UploadResultsPage(status.UploadResultsRendererMixin, rend.Page):
    """'POST /uri', to create an unlinked file."""
    docFactory = getxmlfile("upload-results.xhtml")

    def __init__(self, upload_results, ctx):
        rend.Page.__init__(self)
        self.results = upload_results

    def upload_results(self):
        return defer.succeed(self.results)

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

def POSTUnlinkedSSK(ctx):
    req = IRequest(ctx)
    # "POST /uri", to create an unlinked file.
    # SDMF: files are small, and we can only upload data
    contents = req.fields["file"]
    contents.file.seek(0)
    data = contents.file.read()
    d = IClient(ctx).create_mutable_file(data)
    d.addCallback(lambda n: n.get_uri())
    return d

def POSTUnlinkedCreateDirectory(ctx):
    req = IRequest(ctx)
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

