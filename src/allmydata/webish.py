
from twisted.application import service, strports
from twisted.web import static, resource, server, html, http
from twisted.python import util, log
from twisted.internet import defer
from nevow import inevow, rend, loaders, appserver, url, tags as T
from nevow.static import File as nevow_File # TODO: merge with static.File?
from allmydata.util import idlib
from allmydata.uri import unpack_uri
from allmydata.interfaces import IDownloadTarget, IDirectoryNode, IFileNode
from allmydata.dirnode import FileNode
from allmydata import upload, download
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


class Directory(rend.Page):
    addSlash = True
    docFactory = getxmlfile("directory.xhtml")

    def __init__(self, dirnode, dirname):
        self._dirnode = dirnode
        self._dirname = dirname

    def childFactory(self, ctx, name):
        print "Directory.childFactory", name
        if name.startswith("freeform"): # ick
            return None
        if name == "@manifest": # ick, this time it's my fault
            return Manifest(self._dirnode, self._dirname)
        return rend.NotFound

    def render_title(self, ctx, data):
        print "DIRECTORY.render_title"
        return ctx.tag["Directory '%s':" % self._dirname]

    def render_header(self, ctx, data):
        parent_directories = self._dirname.split("/")
        num_dirs = len(parent_directories)

        header = ["Directory '"]
        for i,d in enumerate(parent_directories):
            if d == "":
                link = "/".join([".."] * (num_dirs - i))
                header.append(T.a(href=link)["/"])
            else:
                if i == num_dirs-1:
                    link = "."
                else:
                    link = "/".join([".."] * (num_dirs - i - 1))
                header.append(T.a(href=link)[d])
                if i < num_dirs - 1:
                    header.append("/")
        header.append("'")

        if not self._dirnode.is_mutable():
            header.append(" (readonly)")
        header.append(":")
        return ctx.tag[header]

    def data_share_uri(self, ctx, data):
        return self._dirnode.get_uri()
    def data_share_readonly_uri(self, ctx, data):
        return self._dirnode.get_immutable_uri()

    def data_children(self, ctx, data):
        d = self._dirnode.list()
        d.addCallback(lambda dict: sorted(dict.items()))
        return d

    def render_row(self, ctx, data):
        name, target = data

        if self._dirnode.is_mutable():
            # this creates a button which will cause our child__delete method
            # to be invoked, which deletes the file and then redirects the
            # browser back to this directory
            del_url = url.here.child("_delete")
            #del_url = del_url.add("uri", target.uri)
            del_url = del_url.add("name", name)
            delete = T.form(action=del_url, method="post")[
                T.input(type='submit', value='del', name="del"),
                ]
        else:
            delete = "-"
        ctx.fillSlots("delete", delete)

        if IFileNode.providedBy(target):
            # file
            dlurl = urllib.quote(name)
            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "FILE")
            uri = target.uri
            dl_uri_url = url.root.child("download_uri").child(uri)
            # add a filename= query argument to give it a Content-Type
            dl_uri_url = dl_uri_url.add("filename", name)
            ctx.fillSlots("uri", T.a(href=dl_uri_url)[html.escape(uri)])

            #extract and display file size
            ctx.fillSlots("size", unpack_uri(uri)['size'])

        elif IDirectoryNode.providedBy(target):
            # directory
            subdir_url = urllib.quote(name)
            ctx.fillSlots("filename",
                          T.a(href=subdir_url)[html.escape(name)])
            if target.is_mutable():
                dirtype = "DIR"
            else:
                dirtype = "DIR-RO"
            ctx.fillSlots("type", dirtype)
            ctx.fillSlots("size", "-")
            ctx.fillSlots("uri", "-")
        else:
            raise RuntimeError("unknown thing %s" % (target,))
        return ctx.tag

    def render_forms(self, ctx, data):
        if self._dirnode.is_mutable():
            return webform.renderForms()
        return T.div["No upload forms: directory is immutable"]

    def render_results(self, ctx, data):
        req = inevow.IRequest(ctx)
        if "results" in req.args:
            return req.args["results"]
        else:
            return ""

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

        privateUpload = annotate.Radio(label="Private?", choices=["Yes"])
        privatearg = annotate.Argument("privateupload", privateUpload)

        ctxarg = annotate.Argument("ctx", annotate.Context())
        meth = annotate.Method(arguments=[contentsarg, privatearg, ctxarg],
                               label="Upload File to this directory")
        return annotate.MethodBinding("upload", meth, action="Upload")

    def uploadprivate(self, filename, uri):
        message = "webish upload complete, filename %s %s" % (filename, uri)
        log.msg(message)
        return url.here.add("filename", filename).add("results", message)

    def upload(self, contents, privateupload, ctx):
        # contents is a cgi.FieldStorage instance
        log.msg("starting webish upload")

        uploader = get_uploader_service(ctx)
        uploadable = upload.FileHandle(contents.file)
        name = contents.filename
        if privateupload:
            d = uploader.upload(uploadable)
            d.addCallback(lambda uri: self.uploadprivate(name, uri))
        else:
            d = self._dirnode.add_file(name, uploadable)
        def _done(res):
            log.msg("webish upload complete")
            return res
        d.addCallback(_done)
        return d # TODO: huh?
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
        log.msg("making new webish directory: %s" % (name,))
        d = self._dirnode.create_empty_directory(name)
        def _done(res):
            log.msg("webish mkdir complete")
            return res
        d.addCallback(_done)
        return d

    def bind_mount(self, ctx):
        namearg = annotate.Argument("name",
                                    annotate.String("Name to place incoming directory: "))
        uriarg = annotate.Argument("uri",
                                   annotate.String("URI of Shared Directory"))
        meth = annotate.Method(arguments=[namearg, uriarg],
                               label="Add Shared Directory")
        return annotate.MethodBinding("mount", meth,
                                      action="Mount Shared Directory")

    def mount(self, name, uri):
        d = self._dirnode.set_uri(name, uri)
        #d.addCallback(lambda done: url.here.child(name))
        return d

    def child__delete(self, ctx):
        # perform the delete, then redirect back to the directory page
        args = inevow.IRequest(ctx).args
        name = args["name"][0]
        d = self._dirnode.delete(name)
        d.addCallback(lambda done: url.here.up())
        return d

class WebDownloadTarget:
    implements(IDownloadTarget)
    def __init__(self, req, content_type, content_encoding):
        self._req = req
        self._content_type = content_type
        self._content_encoding = content_encoding
        self._opened = False

    def open(self, size):
        self._opened = True
        self._req.setHeader("content-type", self._content_type)
        if self._content_encoding:
            self._req.setHeader("content-encoding", self._content_encoding)
        self._req.setHeader("content-length", str(size))

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
            self._req.setResponseCode(http.INTERNAL_SERVER_ERROR, msg)
            self._req.setHeader("content-type", "text/plain")
            # TODO: HTML-formatted exception?
            self._req.write(str(why))
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

class FileDownloader(resource.Resource):
    def __init__(self, name, filenode):
        self._name = name
        IFileNode(filenode)
        self._filenode = filenode

    def render(self, req):
        gte = static.getTypeAndEncoding
        type, encoding = gte(self._name,
                             static.File.contentTypes,
                             static.File.contentEncodings,
                             defaultType="text/plain")

        d = self._filenode.download(WebDownloadTarget(req, type, encoding))
        # exceptions during download are handled by the WebDownloadTarget
        d.addErrback(lambda why: None)
        return server.NOT_DONE_YET

class BlockingFileError(Exception):
    """We cannot auto-create a parent directory, because there is a file in
    the way"""

LOCALHOST = "127.0.0.1"

class NeedLocalhostError:
    implements(inevow.IResource)

    def locateChild(self, ctx, segments):
        return rend.NotFound

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        req.setResponseCode(http.FORBIDDEN)
        req.setHeader("content-type", "text/plain")
        return "localfile= or localdir= requires a local connection"

        

class LocalFileDownloader(resource.Resource):
    def __init__(self, filenode, local_filename):
        self._local_filename = local_filename
        IFileNode(filenode)
        self._filenode = filenode

    def render(self, req):
        print "LOCALFILEDOWNLOADER", self._local_filename
        target = download.FileName(self._local_filename)
        d = self._filenode.download(target)
        def _done(res):
            req.write(self._filenode.get_uri())
            req.finish()
        d.addCallback(_done)
        return server.NOT_DONE_YET

class FileJSONMetadata(rend.Page):
    def __init__(self, filenode):
        self._filenode = filenode

    def renderHTTP(self, ctx):
        file_uri = self._filenode.get_uri()
        pieces = unpack_uri(file_uri)
        data = "filenode\n"
        data += "JSONny stuff here\n"
        data += "uri=%s, size=%s" % (file_uri, pieces['size'])
        return data

class FileXMLMetadata(FileJSONMetadata):
    def renderHTTP(self, ctx):
        file_uri = self._filenode.get_uri()
        pieces = unpack_uri(file_uri)
        data = "<xmlish>\n"
        data += "filenode\n"
        data += "stuff here\n"
        data += "uri=%s, size=%s" % (file_uri, pieces['size'])
        return data

class FileURI(FileJSONMetadata):
    def renderHTTP(self, ctx):
        file_uri = self._filenode.get_uri()
        return file_uri

class LocalDirectoryDownloader(resource.Resource):
    def __init__(self, dirnode):
        self._dirnode = dirnode

    def renderHTTP(self, ctx):
        dl = get_downloader_service(ctx)
        pass # TODO

class DirectoryJSONMetadata(rend.Page):
    def __init__(self, dirnode):
        self._dirnode = dirnode

    def renderHTTP(self, ctx):
        file_uri = self._dirnode.get_uri()
        data = "dirnode\n"
        data += "JSONny stuff here\n"
        d = self._dirnode.list()
        def _got(children, data):
            for name, childnode in children.iteritems():
                data += "name=%s, child_uri=%s" % (name, childnode.get_uri())
            return data
        d.addCallback(_got, data)
        def _done(data):
            data += "done\n"
            return data
        d.addCallback(_done)
        return d

class DirectoryXMLMetadata(DirectoryJSONMetadata):
    def renderHTTP(self, ctx):
        file_uri = self._dirnode.get_uri()
        pieces = unpack_uri(file_uri)
        data = "<xmlish>\n"
        data += "dirnode\n"
        data += "stuff here\n"
        d = self._dirnode.list()
        def _got(children, data):
            for name, childnode in children:
                data += "name=%s, child_uri=%s" % (name, childnode.get_uri())
            return data
        d.addCallback(_got)
        def _done(data):
            data += "</done>\n"
            return data
        d.addCallback(_done)
        return d

class DirectoryURI(DirectoryJSONMetadata):
    def renderHTTP(self, ctx):
        dir_uri = self._dirnode.get_uri()
        return dir_uri

class DirectoryReadonlyURI(DirectoryJSONMetadata):
    def renderHTTP(self, ctx):
        dir_uri = self._dirnode.get_immutable_uri()
        return dir_uri

class POSTHandler(rend.Page):
    def __init__(self, node):
        self._node = node

    # TODO: handler methods

class DELETEHandler(rend.Page):
    def __init__(self, node, name):
        self._node = node
        self._name = name

    def renderHTTP(self, ctx):
        d = self._node.delete(self._name)
        def _done(res):
            # what should this return??
            return "%s deleted" % self._name
        d.addCallback(_done)
        return d

class PUTHandler(rend.Page):
    def __init__(self, node, path, t, localfile, localdir):
        self._node = node
        self._path = path
        self._t = t
        self._localfile = localfile
        self._localdir = localdir

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        t = self._t
        localfile = self._localfile
        localdir = self._localdir
        self._uploader = get_uploader_service(ctx)

        # we must traverse the path, creating new directories as necessary
        d = self._get_or_create_directories(self._node, self._path[:-1])
        name = self._path[-1]
        if localfile:
            d.addCallback(self._upload_localfile, localfile, name)
        elif localdir:
            d.addCallback(self._upload_localdir, localdir)
        elif t == "uri":
            d.addCallback(self._attach_uri, req.content, name)
        elif t == "mkdir":
            d.addCallback(self._mkdir, name)
        else:
            d.addCallback(self._upload_file, req.content, name)
        def _check_blocking(f):
            f.trap(BlockingFileError)
            req.setResponseCode(http.FORBIDDEN)
            req.setHeader("content-type", "text/plain")
            return str(f)
        d.addErrback(_check_blocking)
        return d

    def _get_or_create_directories(self, node, path):
        if not IDirectoryNode.providedBy(node):
            raise BlockingFileError
        if not path:
            return node
        d = node.get(path[0])
        def _maybe_create(f):
            f.trap(KeyError)
            print "CREATING", path[0]
            return node.create_empty_directory(path[0])
        d.addErrback(_maybe_create)
        d.addCallback(self._get_or_create_directories, path[1:])
        return d

    def _mkdir(self, node, name):
        d = node.create_empty_directory(name)
        def _done(newnode):
            return newnode.get_uri()
        d.addCallback(_done)
        return d

    def _upload_file(self, node, contents, name):
        uploadable = upload.FileHandle(contents)
        d = self._uploader.upload(uploadable)
        def _uploaded(uri):
            d1 = node.set_uri(name, uri)
            d1.addCallback(lambda res: uri)
            return d1
        d.addCallback(_uploaded)
        def _done(uri):
            log.msg("webish upload complete")
            return uri
        d.addCallback(_done)
        return d

    def _upload_localfile(self, node, localfile, name):
        uploadable = upload.FileName(localfile)
        d = self._uploader.upload(uploadable)
        def _uploaded(uri):
            print "SETTING URI", name, uri
            d1 = node.set_uri(name, uri)
            d1.addCallback(lambda res: uri)
            return d1
        d.addCallback(_uploaded)
        def _done(uri):
            log.msg("webish upload complete")
            return uri
        d.addCallback(_done)
        return d

    def _attach_uri(self, parentnode, contents, name):
        newuri = contents.read().strip()
        d = parentnode.set_uri(name, newuri)
        def _done(res):
            return newuri
        d.addCallback(_done)
        return d

    def _upload_localdir(self, node, localdir):
        pass # TODO

class Manifest(rend.Page):
    docFactory = getxmlfile("manifest.xhtml")
    def __init__(self, dirnode, dirname):
        self._dirnode = dirnode
        self._dirname = dirname

    def render_title(self, ctx):
        return T.title["Manifest of %s" % self._dirname]

    def render_header(self, ctx):
        return T.p["Manifest of %s" % self._dirname]

    def data_items(self, ctx, data):
        return self._dirnode.build_manifest()

    def render_row(self, ctx, refresh_cap):
        ctx.fillSlots("refresh_capability", refresh_cap)
        return ctx.tag

class VDrive(rend.Page):

    def __init__(self, node, name):
        self.node = node
        self.name = name

    def get_child_at_path(self, path):
        if path:
            return self.node.get_child_at_path(path)
        return defer.succeed(self.node)

    def locateChild(self, ctx, segments):
        req = inevow.IRequest(ctx)
        method = req.method
        path = segments

        # when we're pointing at a directory (like /vdrive/public/my_pix),
        # Directory.addSlash causes a redirect to /vdrive/public/my_pix/,
        # which appears here as ['my_pix', '']. This is supposed to hit the
        # same Directory as ['my_pix'].
        if path and path[-1] == '':
            path = path[:-1]

        print "VDrive.locateChild", method, segments, req.args
        t = ""
        if "t" in req.args:
            t = req.args["t"][0]

        localfile = None
        if "localfile" in req.args:
            localfile = req.args["localfile"][0]
        localdir = None
        if "localdir" in req.args:
            localdir = req.args["localdir"][0]
        if (localfile or localdir) and req.getHost().host != LOCALHOST:
            return NeedLocalhostError(), ()
        # TODO: think about clobbering/revealing config files and node secrets

        if method == "GET":
            # the node must exist, and our operation will be performed on the
            # node itself.
            name = path[-1]
            d = self.get_child_at_path(path)
            def file_or_dir(node):
                if IFileNode.providedBy(node):
                    if localfile:
                        # write contents to a local file
                        return LocalFileDownloader(node, localfile), ()
                    elif t == "":
                        # send contents as the result
                        print "FileDownloader"
                        return FileDownloader(name, node), ()
                    elif t == "json":
                        print "Localfilejsonmetadata"
                        return FileJSONMetadata(node), ()
                    elif t == "xml":
                        return FileXMLMetadata(node), ()
                    elif t == "uri":
                        return FileURI(node), ()
                    else:
                        raise RuntimeError("bad t=%s" % t)
                elif IDirectoryNode.providedBy(node):
                    print "GOT DIR"
                    if localdir:
                        # recursive download to a local directory
                        return LocalDirectoryDownloader(node, localdir), ()
                    elif t == "":
                        # send an HTML representation of the directory
                        print "GOT HTML DIR"
                        return Directory(node, name), ()
                    elif t == "json":
                        return DirectoryJSONMetadata(node), ()
                    elif t == "xml":
                        return DirectoryXMLMetadata(node), ()
                    elif t == "uri":
                        return DirectoryURI(node), ()
                    elif t == "readonly-uri":
                        return DirectoryReadonlyURI(node), ()
                    else:
                        raise RuntimeError("bad t=%s" % t)
                else:
                    raise RuntimeError("unknown node type")
            d.addCallback(file_or_dir)
        elif method == "POST":
            # the node must exist, and our operation will be performed on the
            # node itself.
            d = self.get_child_at_path(path)
            d.addCallback(lambda node: POSTHandler(node), ())
        elif method == "DELETE":
            # the node must exist, and our operation will be performed on its
            # parent node.
            assert path # you can't delete the root
            d = self.get_child_at_path(path[:-1])
            d.addCallback(lambda node: DELETEHandler(node, path[-1]), )
        elif method in ("PUT",):
            # the node may or may not exist, and our operation may involve
            # all the ancestors of the node.
            return PUTHandler(self.node, path, t, localfile, localdir), ()
        else:
            return rend.NotFound
        def _trap_KeyError(f):
            f.trap(KeyError)
            return rend.FourOhFour(), ()
        d.addErrback(_trap_KeyError)
        return d


class Root(rend.Page):

    addSlash = True
    docFactory = getxmlfile("welcome.xhtml")

    def locateChild(self, ctx, segments):
        client = IClient(ctx)
        vdrive = client.getServiceNamed("vdrive")
        print "Root.locateChild", segments

        if segments[0] == "vdrive":
            if len(segments) < 2:
                return rend.NotFound
            if segments[1] == "global":
                d = vdrive.get_public_root()
                name = "public vdrive"
            elif segments[1] == "private":
                d = vdrive.get_private_root()
                name = "private vdrive"
            else:
                return rend.NotFound
            d.addCallback(lambda dirnode: VDrive(dirnode, name))
            d.addCallback(lambda vd: vd.locateChild(ctx, segments[2:]))
            return d
        elif segments[0] == "uri":
            if len(segments) < 2:
                return rend.NotFound
            uri = segments[1]
            d = vdrive.get_node(uri)
            d.addCallback(lambda node: VDrive(node), uri)
            d.addCallback(lambda vd: vd.locateChild(ctx, segments[2:]))
            return d
        elif segments[0] == "xmlrpc":
            pass # TODO
        elif segments[0] == "download_uri":
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
            child = FileDownloader(filename, FileNode(uri, IClient(ctx)))
            return child, ()
        return rend.Page.locateChild(self, ctx, segments)

    child_webform_css = webform.defaultCSS
    child_tahoe_css = nevow_File(util.sibpath(__file__, "web/tahoe.css"))

    #child_welcome = Welcome()

    def child_global_vdrive(self, ctx):
        client = IClient(ctx)
        vdrive = client.getServiceNamed("vdrive")
        if vdrive.have_public_root():
            d = vdrive.get_public_root()
            d.addCallback(lambda dirnode: Directory(dirnode, "/"))
            return d
        else:
            return static.Data("sorry, still initializing", "text/plain")

    def child_private_vdrive(self, ctx):
        client = IClient(ctx)
        vdrive = client.getServiceNamed("vdrive")
        if vdrive.have_private_root():
            d = vdrive.get_private_root()
            d.addCallback(lambda dirnode: Directory(dirnode, "~"))
            return d
        else:
            return static.Data("sorry, still initializing", "text/plain")

    def data_version(self, ctx, data):
        v = IClient(ctx).get_versions()
        return "tahoe: %s, zfec: %s, foolscap: %s, twisted: %s" % \
               (v['allmydata'], v['zfec'], v['foolscap'], v['twisted'])

    def data_my_nodeid(self, ctx, data):
        return idlib.b2a(IClient(ctx).nodeid)
    def data_introducer_furl(self, ctx, data):
        return IClient(ctx).introducer_furl
    def data_connected_to_introducer(self, ctx, data):
        if IClient(ctx).connected_to_introducer():
            return "yes"
        return "no"
    def data_connected_to_vdrive(self, ctx, data):
        if IClient(ctx).getServiceNamed("vdrive").have_public_root():
            return "yes"
        return "no"
    def data_num_peers(self, ctx, data):
        #client = inevow.ISite(ctx)._client
        client = IClient(ctx)
        return len(list(client.get_all_peerids()))

    def data_peers(self, ctx, data):
        d = []
        client = IClient(ctx)
        for nodeid in sorted(client.get_all_peerids()):
            row = (idlib.b2a(nodeid),)
            d.append(row)
        return d

    def render_row(self, ctx, data):
        (nodeid_a,) = data
        ctx.fillSlots("peerid", nodeid_a)
        return ctx.tag

    def render_global_vdrive(self, ctx, data):
        if IClient(ctx).getServiceNamed("vdrive").have_public_root():
            return T.p["To view the global shared filestore, ",
                       T.a(href="../global_vdrive")["Click Here!"],
                       ]
        return T.p["vdrive.furl not specified (or vdrive server not "
                   "responding), no vdrive available."]

    def render_private_vdrive(self, ctx, data):
        if IClient(ctx).getServiceNamed("vdrive").have_private_root():
            return T.p["To view your personal private non-shared filestore, ",
                       T.a(href="../private_vdrive")["Click Here!"],
                       ]
        return T.p["personal vdrive not available."]

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


class WebishServer(service.MultiService):
    name = "webish"

    def __init__(self, webport):
        service.MultiService.__init__(self)
        self.root = Root()
        #self.root.putChild("", url.here.child("welcome"))#Welcome())
                           
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
