
import simplejson

from zope.interface import implements
from twisted.internet.interfaces import IConsumer
from twisted.web import http, static, resource, server
from twisted.internet import defer
from nevow import url, rend
from nevow.inevow import IRequest

from allmydata.interfaces import IDownloadTarget, ExistingChildError
from allmydata.immutable.upload import FileHandle
from allmydata.immutable.filenode import LiteralFileNode
from allmydata.util import log

from allmydata.web.common import text_plain, WebError, IClient, RenderMixin, \
     boolean_of_arg, get_arg, should_create_intermediate_directories
from allmydata.web.checker_results import CheckerResults, \
     CheckAndRepairResults, LiteralCheckerResults

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
            save_to_file = boolean_of_arg(get_arg(req, "save", "False"))
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
            return FileDownloader(self.node, filename, save_to_file)
        if t == "json":
            return FileJSONMetadata(ctx, self.node)
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
        # if we have a filename, use it to get the content-type
        filename = get_arg(req, "filename", self.name) or "unknown"
        gte = static.getTypeAndEncoding
        ctype, encoding = gte(filename,
                              static.File.contentTypes,
                              static.File.contentEncodings,
                              defaultType="text/plain")
        req.setHeader("content-type", ctype)
        if encoding:
            req.setHeader("content-encoding", encoding)
        if self.node.is_mutable():
            d = self.node.get_size_of_best_version()
        # otherwise, we can get the size from the URI
        else:
            d = defer.succeed(self.node.get_size())
        def _got_length(length):
            req.setHeader("content-length", length)
            return ""
        d.addCallback(_got_length)
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
            d = self.node.check_and_repair(verify)
            d.addCallback(lambda res: CheckAndRepairResults(res))
        else:
            d = self.node.check(verify)
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


class WebDownloadTarget:
    implements(IDownloadTarget, IConsumer)
    def __init__(self, req, content_type, content_encoding, save_to_filename):
        self._req = req
        self._content_type = content_type
        self._content_encoding = content_encoding
        self._opened = False
        self._producer = None
        self._save_to_filename = save_to_filename

    def registerProducer(self, producer, streaming):
        self._req.registerProducer(producer, streaming)
    def unregisterProducer(self):
        self._req.unregisterProducer()

    def open(self, size):
        self._opened = True
        self._req.setHeader("content-type", self._content_type)
        if self._content_encoding:
            self._req.setHeader("content-encoding", self._content_encoding)
        self._req.setHeader("content-length", str(size))
        if self._save_to_filename is not None:
            # tell the browser to save the file rather display it we don't
            # try to encode the filename, instead we echo back the exact same
            # bytes we were given in the URL. See the comment in
            # FileNodeHandler.render_GET for the sad details.
            filename = self._save_to_filename
            self._req.setHeader("content-disposition",
                                'attachment; filename="%s"' % filename)

    def write(self, data):
        self._req.write(data)
    def close(self):
        self._req.finish()

    def fail(self, why):
        if self._opened:
            # The content-type is already set, and the response code
            # has already been sent, so we can't provide a clean error
            # indication. We can emit text (which a browser might interpret
            # as something else), and if we sent a Size header, they might
            # notice that we've truncated the data. Keep the error message
            # small to improve the chances of having our error response be
            # shorter than the intended results.
            #
            # We don't have a lot of options, unfortunately.
            self._req.write("problem during download\n")
        else:
            # We haven't written anything yet, so we can provide a sensible
            # error message.
            msg = str(why.type)
            msg.replace("\n", "|")
            self._req.setResponseCode(http.GONE, msg)
            self._req.setHeader("content-type", "text/plain")
            # TODO: HTML-formatted exception?
            self._req.write(str(why))
        self._req.finish()

    def register_canceller(self, cb):
        pass
    def finish(self):
        pass

class FileDownloader(resource.Resource):
    # since we override the rendering process (to let the tahoe Downloader
    # drive things), we must inherit from regular old twisted.web.resource
    # instead of nevow.rend.Page . Nevow will use adapters to wrap a
    # nevow.appserver.OldResourceAdapter around any
    # twisted.web.resource.IResource that it is given. TODO: it looks like
    # that wrapper would allow us to return a Deferred from render(), which
    # might could simplify the implementation of WebDownloadTarget.

    def __init__(self, filenode, filename, save_to_file):
        resource.Resource.__init__(self)
        self.filenode = filenode
        self.filename = filename
        self.save_to_file = save_to_file
    def render(self, req):
        gte = static.getTypeAndEncoding
        ctype, encoding = gte(self.filename,
                              static.File.contentTypes,
                              static.File.contentEncodings,
                              defaultType="text/plain")
        save_to_filename = None
        if self.save_to_file:
            save_to_filename = self.filename
        wdt = WebDownloadTarget(req, ctype, encoding, save_to_filename)
        d = self.filenode.download(wdt)
        # exceptions during download are handled by the WebDownloadTarget
        d.addErrback(lambda why: None)
        return server.NOT_DONE_YET

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
    return text_plain(simplejson.dumps(data, indent=1), ctx)

def FileURI(ctx, filenode):
    return text_plain(filenode.get_uri(), ctx)

def FileReadOnlyURI(ctx, filenode):
    if filenode.is_readonly():
        return text_plain(filenode.get_uri(), ctx)
    return text_plain(filenode.get_readonly_uri(), ctx)

class FileNodeDownloadHandler(FileNodeHandler):
    def childFactory(self, ctx, name):
        return FileNodeDownloadHandler(self.node, name=name)
