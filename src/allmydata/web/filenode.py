
import simplejson

from twisted.web import http, static
from twisted.internet import defer
from nevow import url, rend
from nevow.inevow import IRequest

from allmydata.interfaces import ExistingChildError
from allmydata.monitor import Monitor
from allmydata.immutable.upload import FileHandle
from allmydata.immutable.filenode import LiteralFileNode
from allmydata.util import log, base32

from allmydata.web.common import text_plain, WebError, IClient, RenderMixin, \
     boolean_of_arg, get_arg, should_create_intermediate_directories
from allmydata.web.checker_results import CheckerResults, \
     CheckAndRepairResults, LiteralCheckerResults
from allmydata.web.info import MoreInfo

class ReplaceMeMixin:

    def replace_me_with_a_child(self, ctx, replace):
        # a new file is being uploaded in our place.
        req = IRequest(ctx)
        client = IClient(ctx)
        mutable = boolean_of_arg(get_arg(req, "mutable", "false"))
        if mutable:
            req.content.seek(0)
            data = req.content.read()
            d = client.create_mutable_file(data)
            def _uploaded(newnode):
                d2 = self.parentnode.set_node(self.name, newnode,
                                              overwrite=replace)
                d2.addCallback(lambda res: newnode)
                return d2
            d.addCallback(_uploaded)
        else:
            uploadable = FileHandle(req.content, convergence=client.convergence)
            d = self.parentnode.add_file(self.name, uploadable,
                                         overwrite=replace)
        def _done(filenode):
            log.msg("webish upload complete",
                    facility="tahoe.webish", level=log.NOISY)
            if self.node:
                # we've replaced an existing file (or modified a mutable
                # file), so the response code is 200
                req.setResponseCode(http.OK)
            else:
                # we've created a new file, so the code is 201
                req.setResponseCode(http.CREATED)
            return filenode.get_uri()
        d.addCallback(_done)
        return d

    def replace_me_with_a_childcap(self, ctx, replace):
        req = IRequest(ctx)
        req.content.seek(0)
        childcap = req.content.read()
        client = IClient(ctx)
        childnode = client.create_node_from_uri(childcap)
        d = self.parentnode.set_node(self.name, childnode, overwrite=replace)
        d.addCallback(lambda res: childnode.get_uri())
        return d

    def _read_data_from_formpost(self, req):
        # SDMF: files are small, and we can only upload data, so we read
        # the whole file into memory before uploading.
        contents = req.fields["file"]
        contents.file.seek(0)
        data = contents.file.read()
        return data

    def replace_me_with_a_formpost(self, ctx, replace):
        # create a new file, maybe mutable, maybe immutable
        req = IRequest(ctx)
        client = IClient(ctx)
        mutable = boolean_of_arg(get_arg(req, "mutable", "false"))

        if mutable:
            data = self._read_data_from_formpost(req)
            d = client.create_mutable_file(data)
            def _uploaded(newnode):
                d2 = self.parentnode.set_node(self.name, newnode,
                                              overwrite=replace)
                d2.addCallback(lambda res: newnode.get_uri())
                return d2
            d.addCallback(_uploaded)
            return d
        # create an immutable file
        contents = req.fields["file"]
        uploadable = FileHandle(contents.file, convergence=client.convergence)
        d = self.parentnode.add_file(self.name, uploadable, overwrite=replace)
        d.addCallback(lambda newnode: newnode.get_uri())
        return d

class PlaceHolderNodeHandler(RenderMixin, rend.Page, ReplaceMeMixin):
    def __init__(self, parentnode, name):
        rend.Page.__init__(self)
        assert parentnode
        self.parentnode = parentnode
        self.name = name
        self.node = None

    def render_PUT(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        assert self.parentnode and self.name
        if req.getHeader("content-range"):
            raise WebError("Content-Range in PUT not yet supported",
                           http.NOT_IMPLEMENTED)
        if not t:
            return self.replace_me_with_a_child(ctx, replace)
        if t == "uri":
            return self.replace_me_with_a_childcap(ctx, replace)

        raise WebError("PUT to a file: bad t=%s" % t)

    def render_POST(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        if t == "upload":
            # like PUT, but get the file data from an HTML form's input field.
            # We could get here from POST /uri/mutablefilecap?t=upload,
            # or POST /uri/path/file?t=upload, or
            # POST /uri/path/dir?t=upload&name=foo . All have the same
            # behavior, we just ignore any name= argument
            d = self.replace_me_with_a_formpost(ctx, replace)
        else:
            # t=mkdir is handled in DirectoryNodeHandler._POST_mkdir, so
            # there are no other t= values left to be handled by the
            # placeholder.
            raise WebError("POST to a file: bad t=%s" % t)

        when_done = get_arg(req, "when_done", None)
        if when_done:
            d.addCallback(lambda res: url.URL.fromString(when_done))
        return d


class FileNodeHandler(RenderMixin, rend.Page, ReplaceMeMixin):
    def __init__(self, node, parentnode=None, name=None):
        rend.Page.__init__(self)
        assert node
        self.node = node
        self.parentnode = parentnode
        self.name = name

    def childFactory(self, ctx, name):
        req = IRequest(ctx)
        if should_create_intermediate_directories(req):
            raise WebError("Cannot create directory '%s', because its "
                           "parent is a file, not a directory" % name)
        raise WebError("Files have no children, certainly not named '%s'"
                       % name)

    def render_GET(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        if not t:
            # just get the contents
            # the filename arrives as part of the URL or in a form input
            # element, and will be sent back in a Content-Disposition header.
            # Different browsers use various character sets for this name,
            # sometimes depending upon how language environment is
            # configured. Firefox sends the equivalent of
            # urllib.quote(name.encode("utf-8")), while IE7 sometimes does
            # latin-1. Browsers cannot agree on how to interpret the name
            # they see in the Content-Disposition header either, despite some
            # 11-year old standards (RFC2231) that explain how to do it
            # properly. So we assume that at least the browser will agree
            # with itself, and echo back the same bytes that we were given.
            filename = get_arg(req, "filename", self.name) or "unknown"
            if self.node.is_mutable():
                # some day: d = self.node.get_best_version()
                d = makeMutableDownloadable(self.node)
            else:
                d = defer.succeed(self.node)
            d.addCallback(lambda dn: FileDownloader(dn, filename))
            return d
        if t == "json":
            return FileJSONMetadata(ctx, self.node)
        if t == "info":
            return MoreInfo(self.node)
        if t == "uri":
            return FileURI(ctx, self.node)
        if t == "readonly-uri":
            return FileReadOnlyURI(ctx, self.node)
        raise WebError("GET file: bad t=%s" % t)

    def render_HEAD(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        if t:
            raise WebError("GET file: bad t=%s" % t)
        filename = get_arg(req, "filename", self.name) or "unknown"
        if self.node.is_mutable():
            # some day: d = self.node.get_best_version()
            d = makeMutableDownloadable(self.node)
        else:
            d = defer.succeed(self.node)
        d.addCallback(lambda dn: FileDownloader(dn, filename))
        return d

    def render_PUT(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        if not t:
            if self.node.is_mutable():
                return self.replace_my_contents(ctx)
            if not replace:
                # this is the early trap: if someone else modifies the
                # directory while we're uploading, the add_file(overwrite=)
                # call in replace_me_with_a_child will do the late trap.
                raise ExistingChildError()
            assert self.parentnode and self.name
            return self.replace_me_with_a_child(ctx, replace)
        if t == "uri":
            if not replace:
                raise ExistingChildError()
            assert self.parentnode and self.name
            return self.replace_me_with_a_childcap(ctx, replace)

        raise WebError("PUT to a file: bad t=%s" % t)

    def render_POST(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        if t == "check":
            d = self._POST_check(req)
        elif t == "upload":
            # like PUT, but get the file data from an HTML form's input field
            # We could get here from POST /uri/mutablefilecap?t=upload,
            # or POST /uri/path/file?t=upload, or
            # POST /uri/path/dir?t=upload&name=foo . All have the same
            # behavior, we just ignore any name= argument
            if self.node.is_mutable():
                d = self.replace_my_contents_with_a_formpost(ctx)
            else:
                if not replace:
                    raise ExistingChildError()
                assert self.parentnode and self.name
                d = self.replace_me_with_a_formpost(ctx, replace)
        else:
            raise WebError("POST to file: bad t=%s" % t)

        when_done = get_arg(req, "when_done", None)
        if when_done:
            d.addCallback(lambda res: url.URL.fromString(when_done))
        return d

    def _POST_check(self, req):
        verify = boolean_of_arg(get_arg(req, "verify", "false"))
        repair = boolean_of_arg(get_arg(req, "repair", "false"))
        if isinstance(self.node, LiteralFileNode):
            return defer.succeed(LiteralCheckerResults())
        if repair:
            d = self.node.check_and_repair(Monitor(), verify)
            d.addCallback(lambda res: CheckAndRepairResults(res))
        else:
            d = self.node.check(Monitor(), verify)
            d.addCallback(lambda res: CheckerResults(res))
        return d

    def render_DELETE(self, ctx):
        assert self.parentnode and self.name
        d = self.parentnode.delete(self.name)
        d.addCallback(lambda res: self.node.get_uri())
        return d

    def replace_my_contents(self, ctx):
        req = IRequest(ctx)
        req.content.seek(0)
        new_contents = req.content.read()
        d = self.node.overwrite(new_contents)
        d.addCallback(lambda res: self.node.get_uri())
        return d

    def replace_my_contents_with_a_formpost(self, ctx):
        # we have a mutable file. Get the data from the formpost, and replace
        # the mutable file's contents with it.
        req = IRequest(ctx)
        new_contents = self._read_data_from_formpost(req)
        d = self.node.overwrite(new_contents)
        d.addCallback(lambda res: self.node.get_uri())
        return d

class MutableDownloadable:
    #implements(IDownloadable)
    def __init__(self, size, node):
        self.size = size
        self.node = node
    def get_size(self):
        return self.size
    def is_mutable(self):
        return True
    def read(self, consumer, offset=0, size=None):
        d = self.node.download_best_version()
        d.addCallback(self._got_data, consumer, offset, size)
        return d
    def _got_data(self, contents, consumer, offset, size):
        start = offset
        if size is not None:
            end = offset+size
        else:
            end = self.size
        # SDMF: we can write the whole file in one big chunk
        consumer.write(contents[start:end])
        return consumer

def makeMutableDownloadable(n):
    d = defer.maybeDeferred(n.get_size_of_best_version)
    d.addCallback(MutableDownloadable, n)
    return d

class FileDownloader(rend.Page):
    # since we override the rendering process (to let the tahoe Downloader
    # drive things), we must inherit from regular old twisted.web.resource
    # instead of nevow.rend.Page . Nevow will use adapters to wrap a
    # nevow.appserver.OldResourceAdapter around any
    # twisted.web.resource.IResource that it is given. TODO: it looks like
    # that wrapper would allow us to return a Deferred from render(), which
    # might could simplify the implementation of WebDownloadTarget.

    def __init__(self, filenode, filename):
        rend.Page.__init__(self)
        self.filenode = filenode
        self.filename = filename

    def renderHTTP(self, ctx):
        req = IRequest(ctx)
        gte = static.getTypeAndEncoding
        ctype, encoding = gte(self.filename,
                              static.File.contentTypes,
                              static.File.contentEncodings,
                              defaultType="text/plain")
        req.setHeader("content-type", ctype)
        if encoding:
            req.setHeader("content-encoding", encoding)

        save_to_filename = None
        if boolean_of_arg(get_arg(req, "save", "False")):
            # tell the browser to save the file rather display it we don't
            # try to encode the filename, instead we echo back the exact same
            # bytes we were given in the URL. See the comment in
            # FileNodeHandler.render_GET for the sad details.
            req.setHeader("content-disposition",
                          'attachment; filename="%s"' % self.filename)

        filesize = self.filenode.get_size()
        assert isinstance(filesize, (int,long)), filesize
        offset, size = 0, None
        contentsize = filesize
        req.setHeader("accept-ranges", "bytes")
        if not self.filenode.is_mutable():
            # TODO: look more closely at Request.setETag and how it interacts
            # with a conditional "if-etag-equals" request, I think this may
            # need to occur after the setResponseCode below
            si = self.filenode.get_storage_index()
            if si:
                req.setETag(base32.b2a(si))
        # TODO: for mutable files, use the roothash. For LIT, hash the data.
        # or maybe just use the URI for CHK and LIT.
        rangeheader = req.getHeader('range')
        if rangeheader:
            # adapted from nevow.static.File
            bytesrange = rangeheader.split('=')
            if bytesrange[0] != 'bytes':
                raise WebError("Syntactically invalid http range header!")
            start, end = bytesrange[1].split('-')
            if start:
                offset = int(start)
            if end:
                size = int(end) - offset + 1
            req.setResponseCode(http.PARTIAL_CONTENT)
            req.setHeader('content-range',"bytes %s-%s/%s" %
                          (str(offset), str(offset+size-1), str(filesize)))
            contentsize = size
        req.setHeader("content-length", str(contentsize))
        if req.method == "HEAD":
            return ""
        d = self.filenode.read(req, offset, size)
        def _error(f):
            if req.startedWriting:
                # The content-type is already set, and the response code has
                # already been sent, so we can't provide a clean error
                # indication. We can emit text (which a browser might
                # interpret as something else), and if we sent a Size header,
                # they might notice that we've truncated the data. Keep the
                # error message small to improve the chances of having our
                # error response be shorter than the intended results.
                #
                # We don't have a lot of options, unfortunately.
                req.write("problem during download\n")
            else:
                # We haven't written anything yet, so we can provide a
                # sensible error message.
                msg = str(f.type)
                msg.replace("\n", "|")
                req.setResponseCode(http.GONE, msg)
                req.setHeader("content-type", "text/plain")
                req.responseHeaders.setRawHeaders("content-encoding", [])
                req.responseHeaders.setRawHeaders("content-disposition", [])
                # TODO: HTML-formatted exception?
                req.write(str(f))
        d.addErrback(_error)
        d.addBoth(lambda ign: req.finish())
        return req.deferred


def FileJSONMetadata(ctx, filenode):
    if filenode.is_readonly():
        rw_uri = None
        ro_uri = filenode.get_uri()
    else:
        rw_uri = filenode.get_uri()
        ro_uri = filenode.get_readonly_uri()
    data = ("filenode", {})
    data[1]['size'] = filenode.get_size()
    if ro_uri:
        data[1]['ro_uri'] = ro_uri
    if rw_uri:
        data[1]['rw_uri'] = rw_uri
    data[1]['mutable'] = filenode.is_mutable()
    return text_plain(simplejson.dumps(data, indent=1) + "\n", ctx)

def FileURI(ctx, filenode):
    return text_plain(filenode.get_uri(), ctx)

def FileReadOnlyURI(ctx, filenode):
    if filenode.is_readonly():
        return text_plain(filenode.get_uri(), ctx)
    return text_plain(filenode.get_readonly_uri(), ctx)

class FileNodeDownloadHandler(FileNodeHandler):
    def childFactory(self, ctx, name):
        return FileNodeDownloadHandler(self.node, name=name)
