
from twisted.application import service, strports
from twisted.web import static, resource, server, html
from twisted.python import util, log
from nevow import inevow, rend, loaders, appserver, url, tags as T
from allmydata.util import idlib
from allmydata.download import IDownloadTarget#, IDownloader
from allmydata import upload
from zope.interface import implements, Interface
import urllib
from formless import annotate, webform

def getxmlfile(name):
    return loaders.xmlfile(util.sibpath(__file__, "web/%s" % name))

class IClient(Interface):
    pass

def get_downloader_service(ctx):
    return IClient(ctx).getServiceNamed("downloader")
def get_uploader_service(ctx):
    return IClient(ctx).getServiceNamed("uploader")
def get_vdrive_service(ctx):
    return IClient(ctx).getServiceNamed("vdrive")

class Welcome(rend.Page):
    addSlash = True
    docFactory = getxmlfile("welcome.xhtml")

    def data_queen_pburl(self, ctx, data):
        return IClient(ctx).queen_pburl
    def data_connected_to_queen(self, ctx, data):
        if IClient(ctx).queen:
            return "yes"
        return "no"
    def data_num_peers(self, ctx, data):
        #client = inevow.ISite(ctx)._client
        client = IClient(ctx)
        return len(client.all_peers)
    def data_num_connected_peers(self, ctx, data):
        return len(IClient(ctx).connections)

    def data_peers(self, ctx, data):
        d = []
        client = IClient(ctx)
        for nodeid in sorted(client.all_peers):
            if nodeid in client.connections:
                connected = "yes"
            else:
                connected = "no"
            pburl = client.peer_pburls[nodeid]
            row = (idlib.b2a(nodeid), connected, pburl)
            d.append(row)
        return d

    def render_row(self, ctx, data):
        nodeid_a, connected, pburl = data
        ctx.fillSlots("peerid", nodeid_a)
        ctx.fillSlots("connected", connected)
        ctx.fillSlots("pburl", pburl)
        return ctx.tag

    # this is a form where users can download files by URI

    def bind_download(self, ctx):
        uriarg = annotate.Argument("uri",
                                   annotate.String("URI of file to download: "))
        namearg = annotate.Argument("filename",
                                    annotate.String("Filename to download as: "))
        ctxarg = annotate.Argument("ctx", annotate.Context())
        meth = annotate.Method(arguments=[uriarg, namearg, ctxarg],
                               label="Download File by URI")
        # buttons always use value=data.label
        # MethodBindingRenderer uses value=(data.action or data.label)
        return annotate.MethodBinding("download", meth, action="Download")

    def download(self, uri, filename, ctx):
        log.msg("webish downloading URI")
        target = url.here.sibling("download_uri").add("uri", uri)
        if filename:
            target = target.add("filename", filename)
        return target

    def render_forms(self, ctx, data):
        return webform.renderForms()


class Directory(rend.Page):
    addSlash = True
    docFactory = getxmlfile("directory.xhtml")

    def __init__(self, dirnode, dirname):
        self._dirnode = dirnode
        self._dirname = dirname

    def childFactory(self, ctx, name):
        if name.startswith("freeform"): # ick
            return None
        if self._dirname == "/":
            dirname = "/" + name
        else:
            dirname = self._dirname + "/" + name
        d = self._dirnode.callRemote("get", name)
        def _got_child(res):
            if isinstance(res, str):
                dl = get_downloader_service(ctx)
                return Downloader(dl, name, res)
            return Directory(res, dirname)
        d.addCallback(_got_child)
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
            dlurl = urllib.quote(name)
            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "FILE")
            uri = target
            dl_uri_url = url.root.child("download_uri").child(uri)
            # add a filename= query argument to give it a Content-Type
            dl_uri_url = dl_uri_url.add("filename", name)
            ctx.fillSlots("uri", T.a(href=dl_uri_url)[html.escape(uri)])

            # this creates a button which will cause our child__delete method
            # to be invoked, which deletes the file and then redirects the
            # browser back to this directory
            del_url = url.here.child("_delete")
            #del_url = del_url.add("uri", target)
            del_url = del_url.add("name", name)
            delete = T.form(action=del_url, method="post")[
                T.input(type='submit', value='del', name="del"),
                ]
            ctx.fillSlots("delete", delete)
        else:
            # directory
            subdir_url = urllib.quote(name)
            ctx.fillSlots("filename",
                          T.a(href=subdir_url)[html.escape(name)])
            ctx.fillSlots("type", "DIR")
            ctx.fillSlots("uri", "-")
            ctx.fillSlots("delete", "-")
        return ctx.tag

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
        meth = annotate.Method(arguments=[contentsarg, ctxarg],
                               label="Upload File to this directory")
        return annotate.MethodBinding("upload", meth, action="Upload")

    def upload(self, contents, ctx):
        # contents is a cgi.FieldStorage instance
        log.msg("starting webish upload")

        uploader = get_uploader_service(ctx)
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
        meth = annotate.Method(arguments=[namearg], label="Make New Subdirectory")
        return annotate.MethodBinding("mkdir", meth, action="Create Directory")

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
        vdrive = get_vdrive_service(ctx)
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
    def __init__(self, downloader, name, uri):
        self._downloader = downloader
        self._name = name
        self._uri = uri

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
        dl.download(self._uri, t)
        return server.NOT_DONE_YET



class Root(rend.Page):
    def locateChild(self, ctx, segments):
        if segments[0] == "download_uri":
            req = inevow.IRequest(ctx)
            dl = get_downloader_service(ctx)
            filename = "unknown_filename"
            if "filename" in req.args:
                filename = req.args["filename"][0]
            if len(segments) > 1:
                # http://host/download_uri/URIGOESHERE
                uri = segments[1]
            elif "uri" in req.args:
                # http://host/download_uri?uri=URIGOESHERE
                uri = req.args["uri"][0]
            else:
                return rend.NotFound
            child = Downloader(dl, filename, uri)
            return child, ()
        return rend.Page.locateChild(self, ctx, segments)

    child_webform_css = webform.defaultCSS

    child_welcome = Welcome()


class WebishServer(service.MultiService):
    name = "webish"

    def __init__(self, webport):
        service.MultiService.__init__(self)
        self.root = Root()
        placeholder = static.Data("sorry, still initializing", "text/plain")
        self.root.putChild("vdrive", placeholder)
        self.root.putChild("", url.here.child("welcome"))#Welcome())
                           
        self.site = site = appserver.NevowSite(self.root)
        s = strports.service(webport, site)
        s.setServiceParent(self)
        self.listener = s # stash it so the tests can query for the portnum

    def startService(self):
        service.MultiService.startService(self)
        # to make various services available to render_* methods, we stash a
        # reference to the client on the NevowSite. This will be available by
        # adapting the 'context' argument to a special marker interface named
        # IClient.
        self.site.remember(self.parent, IClient)
        # I thought you could do the same with an existing interface, but
        # apparently 'ISite' does not exist
        #self.site._client = self.parent

    def set_root_dirnode(self, dirnode):
        self.root.putChild("vdrive", Directory(dirnode, "/"))
        # I tried doing it this way and for some reason it didn't seem to work
        #print "REMEMBERING", self.site, dl, IDownloader
        #self.site.remember(dl, IDownloader)

