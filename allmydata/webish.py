
from twisted.application import service, internet
from twisted.web import static, resource, server
from twisted.python import util
from nevow import inevow, rend, loaders, appserver, tags as T
from allmydata.util import idlib
from allmydata.download import IDownloadTarget#, IDownloader
from zope.interface import implements
import urllib

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
        dl = self.parent.getServiceNamed("downloader")
        self.root.putChild("vdrive", Directory(dirnode, "/", dl))
        #print "REMEMBERING", self.site, dl, IDownloader
        #self.site.remember(dl, IDownloader)


class Welcome(rend.Page):
    addSlash = True
    docFactory = getxmlfile("welcome.xhtml")

class Directory(rend.Page):
    addSlash = True
    docFactory = getxmlfile("directory.xhtml")

    def __init__(self, dirnode, dirname, downloader):
        self._dirnode = dirnode
        self._dirname = dirname
        self._downloader = downloader

    def childFactory(self, ctx, name):
        if name.startswith("freeform"): # ick
            return None
        if name == "_download":
            args = inevow.IRequest(ctx).args
            filename = args["filename"][0]
            verifierid = args["verifierid"][0]
            return Downloader(self._downloader,
                              self._dirname, filename, idlib.a2b(verifierid))
        if self._dirname == "/":
            dirname = "/" + name
        else:
            dirname = self._dirname + "/" + name
        d = self._dirnode.callRemote("get", name)
        d.addCallback(lambda newnode:
                      Directory(newnode, dirname, self._downloader))
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
        else:
            # directory
            ctx.fillSlots("filename", T.a(href=name)[name])
            ctx.fillSlots("type", "DIR")
            ctx.fillSlots("fileid", "-")
        return ctx.tag

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
