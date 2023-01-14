"""
Ported to Python 3.
"""
from __future__ import annotations

from urllib.parse import quote as urlquote

from twisted.web import http
from twisted.internet import defer
from twisted.python.filepath import FilePath
from twisted.web.resource import Resource
from twisted.web.template import (
    XMLFile,
    renderer,
    renderElement,
    tags,
)
from allmydata.immutable.upload import FileHandle
from allmydata.mutable.publish import MutableFileHandle
from allmydata.web.common import (
    get_keypair,
    get_arg,
    boolean_of_arg,
    convert_children_json,
    WebError,
    get_format,
    get_mutable_type,
    render_exception,
    url_for_string,
)
from allmydata.web import status

def PUTUnlinkedCHK(req, client):
    # "PUT /uri", to create an unlinked file.
    uploadable = FileHandle(req.content, client.convergence)
    d = client.upload(uploadable)
    d.addCallback(lambda results: results.get_uri())
    # that fires with the URI of the new file
    return d

def PUTUnlinkedSSK(req, client, version):
    # SDMF: files are small, and we can only upload data
    req.content.seek(0)
    data = MutableFileHandle(req.content)
    keypair = get_keypair(req)
    d = client.create_mutable_file(data, version=version, unique_keypair=keypair)
    d.addCallback(lambda n: n.get_uri())
    return d

def PUTUnlinkedCreateDirectory(req, client):
    # "PUT /uri?t=mkdir", to create an unlinked directory.
    file_format = get_format(req, None)
    if file_format == "CHK":
        raise WebError("format=CHK not accepted for PUT /uri?t=mkdir",
                       http.BAD_REQUEST)
    mt = None
    if file_format:
        mt = get_mutable_type(file_format)
    d = client.create_dirnode(version=mt)
    d.addCallback(lambda dirnode: dirnode.get_uri())
    # XXX add redirect_to_result
    return d


def POSTUnlinkedCHK(req, client):
    fileobj = req.fields["file"].file
    uploadable = FileHandle(fileobj, client.convergence)
    d = client.upload(uploadable)
    when_done = get_arg(req, "when_done", None)
    if when_done:
        # if when_done= is provided, return a redirect instead of our
        # usual upload-results page
        def _done(upload_results, redir_to):
            if b"%(uri)s" in redir_to:
                redir_to = redir_to.replace(b"%(uri)s", urlquote(upload_results.get_uri()).encode("utf-8"))
            return url_for_string(req, redir_to)
        d.addCallback(_done, when_done)
    else:
        # return the Upload Results page, which includes the URI
        d.addCallback(UploadResultsPage)
    return d


class UploadResultsPage(Resource, object):
    """'POST /uri', to create an unlinked file."""

    def __init__(self, upload_results):
        """
        :param IUploadResults upload_results: stats provider.
        """
        super(UploadResultsPage, self).__init__()
        self._upload_results = upload_results

    @render_exception
    def render_POST(self, req):
        elem = UploadResultsElement(self._upload_results)
        return renderElement(req, elem)


class UploadResultsElement(status.UploadResultsRendererMixin):

    loader = XMLFile(FilePath(__file__).sibling("upload-results.xhtml"))

    def __init__(self, upload_results):
        super(UploadResultsElement, self).__init__()
        self._upload_results = upload_results

    def upload_results(self):
        return defer.succeed(self._upload_results)

    @renderer
    def done(self, req, tag):
        d = self.upload_results()
        d.addCallback(lambda res: "done!")
        return d

    @renderer
    def uri(self, req, tag):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_uri())
        return d

    @renderer
    def download_link(self, req, tag):
        d = self.upload_results()
        d.addCallback(lambda res:
                      tags.a("/uri/" + str(res.get_uri(), "utf-8"),
                             href="/uri/" + urlquote(str(res.get_uri(), "utf-8"))))
        return d


def POSTUnlinkedSSK(req, client, version):
    # "POST /uri", to create an unlinked file.
    # SDMF: files are small, and we can only upload data
    contents = req.fields["file"].file
    data = MutableFileHandle(contents)
    d = client.create_mutable_file(data, version=version)
    d.addCallback(lambda n: n.get_uri())
    return d

def POSTUnlinkedCreateDirectory(req, client):
    # "POST /uri?t=mkdir", to create an unlinked directory.
    ct = req.getHeader("content-type") or ""
    if not ct.startswith("multipart/form-data"):
        # guard against accidental attempts to call t=mkdir as if it were
        # t=mkdir-with-children, but make sure we tolerate the usual HTML
        # create-directory form (in which the t=mkdir and redirect_to_result=
        # and other arguments can be passed encoded as multipath/form-data,
        # in the request body).
        req.content.seek(0)
        kids_json = req.content.read()
        if kids_json:
            raise WebError("t=mkdir does not accept children=, "
                           "try t=mkdir-with-children instead",
                           http.BAD_REQUEST)
    file_format = get_format(req, None)
    if file_format == "CHK":
        raise WebError("format=CHK not currently accepted for POST /uri?t=mkdir",
                       http.BAD_REQUEST)
    mt = None
    if file_format:
        mt = get_mutable_type(file_format)
    d = client.create_dirnode(version=mt)
    redirect = get_arg(req, "redirect_to_result", "false")
    if boolean_of_arg(redirect):
        def _then_redir(res):
            new_url = "uri/" + urlquote(res.get_uri())
            req.setResponseCode(http.SEE_OTHER) # 303
            req.setHeader('location', new_url)
            return ''
        d.addCallback(_then_redir)
    else:
        d.addCallback(lambda dirnode: dirnode.get_uri())
    return d

def POSTUnlinkedCreateDirectoryWithChildren(req, client):
    # "POST /uri?t=mkdir", to create an unlinked directory.
    req.content.seek(0)
    kids_json = req.content.read()
    kids = convert_children_json(client.nodemaker, kids_json)
    d = client.create_dirnode(initial_children=kids)
    redirect = get_arg(req, "redirect_to_result", "false")
    if boolean_of_arg(redirect):
        def _then_redir(res):
            new_url = "uri/" + urlquote(res.get_uri())
            req.setResponseCode(http.SEE_OTHER) # 303
            req.setHeader('location', new_url)
            return ''
        d.addCallback(_then_redir)
    else:
        d.addCallback(lambda dirnode: dirnode.get_uri())
    return d

def POSTUnlinkedCreateImmutableDirectory(req, client):
    # "POST /uri?t=mkdir", to create an unlinked directory.
    req.content.seek(0)
    kids_json = req.content.read()
    kids = convert_children_json(client.nodemaker, kids_json)
    d = client.create_immutable_dirnode(kids)
    redirect = get_arg(req, "redirect_to_result", "false")
    if boolean_of_arg(redirect):
        def _then_redir(res):
            new_url = "uri/" + urlquote(res.get_uri())
            req.setResponseCode(http.SEE_OTHER) # 303
            req.setHeader('location', new_url)
            return ''
        d.addCallback(_then_redir)
    else:
        d.addCallback(lambda dirnode: dirnode.get_uri())
    return d
