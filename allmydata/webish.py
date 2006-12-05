
from twisted.application import service, internet
from twisted.web import static, resource, server
from twisted.python import util, log
from nevow import inevow, rend, loaders, appserver, url, tags as T
from allmydata.util import idlib
from allmydata.download import IDownloadTarget#, IDownloader
from allmydata import upload
from zope.interface import implements
import urllib
from formless import annotate, webform

def getxmlfile(name):
    return loaders.xmlfile(util.sibpath(__file__, "web/%s" % name))

class WebishServer(service.MultiService):
    name = "webish"
    WEBPORTFILE = "webport"

    def __init__(self, webport):
        service.MultiService.__init__(self)
        self.root = root = static.Data("root", "text/plain")
        w = Welcome()
        root.putChild("", w)
        root.putChild("vdrive",
                      static.Data("sorry, still initializing", "text/plain"))
        self.site = site = appserver.NevowSite(root)
        internet.TCPServer(webport, site).setServiceParent(self)

    def set_root_dirnode(self, dirnode):
        self.root.putChild("vdrive", Directory(dirnode, "/", self.parent))
        #print "REMEMBERING", self.site, dl, IDownloader
        #self.site.remember(dl, IDownloader)


class Welcome(rend.Page):
    addSlash = True
    docFactory = getxmlfile("welcome.xhtml")


class Directory(rend.Page):
    addSlash = True
    docFactory = getxmlfile("directory.xhtml")

    def __init__(self, dirnode, dirname, client):
        self._dirnode = dirnode
        self._dirname = dirname
        self._client = client

    def childFactory(self, ctx, name):
        if name.startswith("freeform"): # ick
            return None
        if name == "_download":
            args = inevow.IRequest(ctx).args
            filename = args["filename"][0]
            verifierid = args["verifierid"][0]
            return Downloader(self._client.getServiceNamed("downloader"),
                              self._dirname, filename, idlib.a2b(verifierid))
        if self._dirname == "/":
            dirname = "/" + name
        else:
            dirname = self._dirname + "/" + name
        d = self._dirnode.callRemote("get", name)
        d.addCallback(lambda newnode:
                      Directory(newnode, dirname, self._client))
        return d

    def render_title(self, ctx, data):
        return ctx.tag["Directory of '%s':" % self._dirname]

    def render_header(self, ctx, data):
        return "Directory of '%s':" % self._dirname

    def data_children(self, ctx, data):
        d = self._dirnode.callRemote("list")
        return d

    def render_row(self, ctx, data):
        name, target = data
        if isinstance(target, str):
            # file
            args = {'verifierid': idlib.b2a(target),
                    'filename': name,
                    }
            dlurl = "_download?%s" % urllib.urlencode(args)
            ctx.fillSlots("filename", T.a(href=dlurl)[name])
            ctx.fillSlots("type", "FILE")
            ctx.fillSlots("fileid", idlib.b2a(target))

            # this creates a button which will cause our child__delete method
            # to be invoked, which deletes the file and then redirects the
            # browser back to this directory
            del_url = url.here.child("_delete")
            #del_url = del_url.add("verifierid", idlib.b2a(target))
            del_url = del_url.add("name", name)
            delete = T.form(action=del_url, method="post")[
                T.input(type='submit', value='del', name="del"),
                ]
            ctx.fillSlots("delete", delete)
        else:
            # directory
            ctx.fillSlots("filename", T.a(href=name)[name])
            ctx.fillSlots("type", "DIR")
            ctx.fillSlots("fileid", "-")
            ctx.fillSlots("delete", "-")
        return ctx.tag

    child_webform_css = webform.defaultCSS
    def render_forms(self, ctx, data):
        return webform.renderForms()

    def bind_upload(self, ctx):
        """upload1"""
        # Note: this comment is no longer accurate, as it reflects the older
        # (apparently deprecated) formless.autocallable /
        # annotate.TypedInterface approach.

        # Each method gets a box. The string in the autocallable(action=)
        # argument is put on the border of the box, as well as in the submit
        # button. The top-most contents of the box are the method's
        # docstring, if any. Each row contains a string for the argument
        # followed by the argument's input box. If you do not provide an
        # action= argument to autocallable, the method name is capitalized
        # and used instead.
        up = annotate.FileUpload(label="Choose a file to upload: ",
                                 required=True,
                                 requiredFailMessage="Do iT!")
        contentsarg = annotate.Argument("contents", up)

        ctxarg = annotate.Argument("ctx", annotate.Context())
        meth = annotate.Method(arguments=[contentsarg, ctxarg])
        return annotate.MethodBinding("upload", meth, action="Upload File")

    def upload(self, contents, ctx):
        # contents is a cgi.FieldStorage instance
        log.msg("starting webish upload")

        uploader = self._client.getServiceNamed("uploader")
        d = uploader.upload(upload.Data(contents.value))
        name = contents.filename
        d.addCallback(lambda vid:
                      self._dirnode.callRemote("add_file", name, vid))
        def _done(res):
            log.msg("webish upload complete")
            return res
        d.addCallback(_done)
        return d
        return url.here.add("results",
                            "upload of '%s' complete!" % contents.filename)

    def bind_mkdir(self, ctx):
        """Make new directory 1"""
        namearg = annotate.Argument("name",
                                    annotate.String("New directory name: "))
        meth = annotate.Method(arguments=[namearg])
        return annotate.MethodBinding("mkdir", meth, action="Make Directory")

    def mkdir(self, name):
        """mkdir2"""
        log.msg("making new webish directory")
        d = self._dirnode.callRemote("add_directory", name)
        def _done(res):
            log.msg("webish mkdir complete")
            return res
        d.addCallback(_done)
        return d

    def child__delete(self, ctx):
        # perform the delete, then redirect back to the directory page
        args = inevow.IRequest(ctx).args
        vdrive = self._client.getServiceNamed("vdrive")
        d = vdrive.remove(self._dirnode, args["name"][0])
        def _deleted(res):
            return url.here.up()
        d.addCallback(_deleted)
        return d

class WebDownloadTarget:
    implements(IDownloadTarget)
    def __init__(self, req):
        self._req = req
    def open(self):
        pass
    def write(self, data):
        self._req.write(data)
    def close(self):
        self._req.finish()
    def fail(self):
        self._req.finish()
    def register_canceller(self, cb):
        pass
    def finish(self):
        pass

class TypedFile(static.File):
    # serve data from a named file, but using a Content-Type derived from a
    # different filename
    isLeaf = True
    def __init__(self, path, requested_filename):
        static.File.__init__(self, path)
        gte = static.getTypeAndEncoding
        self.type, self.encoding = gte(requested_filename,
                                       self.contentTypes,
                                       self.contentEncodings,
                                       self.defaultType)

class Downloader(resource.Resource):
    def __init__(self, downloader, dirname, name, verifierid):
        self._downloader = downloader
        self._dirname = dirname
        self._name = name
        self._verifierid = verifierid

    def render(self, ctx):
        req = inevow.IRequest(ctx)
        gte = static.getTypeAndEncoding
        type, encoding = gte(self._name,
                             static.File.contentTypes,
                             static.File.contentEncodings,
                             defaultType="text/plain")
        req.setHeader("content-type", type)
        if encoding:
            req.setHeader('content-encoding', encoding)

        t = WebDownloadTarget(req)
        #dl = IDownloader(ctx)
        dl = self._downloader
        dl.download(self._verifierid, t)
        return server.NOT_DONE_YET


