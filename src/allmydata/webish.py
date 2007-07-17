
import os.path
from twisted.application import service, strports
from twisted.web import static, resource, server, html, http
from twisted.python import util, log
from twisted.internet import defer
from nevow import inevow, rend, loaders, appserver, url, tags as T
from nevow.static import File as nevow_File # TODO: merge with static.File?
from allmydata.util import idlib, fileutil
import simplejson
from allmydata.uri import unpack_uri, is_dirnode_uri
from allmydata.interfaces import IDownloadTarget, IDirectoryNode, IFileNode
from allmydata import upload, download, uri
from zope.interface import implements, Interface
import urllib
from formless import webform

def getxmlfile(name):
    return loaders.xmlfile(util.sibpath(__file__, "web/%s" % name))

class IClient(Interface):
    pass

class Directory(rend.Page):
    addSlash = True
    docFactory = getxmlfile("directory.xhtml")

    def __init__(self, rootname, dirnode, dirpath):
        self._rootname = rootname
        self._dirnode = dirnode
        self._dirpath = dirpath

    def dirpath_as_string(self):
        return "/" + "/".join(self._dirpath)

    def render_title(self, ctx, data):
        return ctx.tag["Directory '%s':" % self.dirpath_as_string()]

    def render_header(self, ctx, data):
        parent_directories = ("<%s>" % self._rootname,) + self._dirpath
        num_dirs = len(parent_directories)

        header = ["Directory '"]
        for i,d in enumerate(parent_directories):
            upness = num_dirs - i - 1
            if upness:
                link = "/".join( ("..",) * upness )
            else:
                link = "."
            header.append(T.a(href=link)[d])
            if upness != 0:
                header.append("/")
        header.append("'")

        if not self._dirnode.is_mutable():
            header.append(" (readonly)")
        header.append(":")
        return ctx.tag[header]

    def render_welcome(self, ctx, data):
        depth = len(self._dirpath) + 2
        link = "/".join([".."] * depth)
        return T.div[T.a(href=link)["Return to Welcome page"]]

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
            delete = T.form(action=url.here, method="post")[
                T.input(type='hidden', name='t', value='delete'),
                T.input(type='hidden', name='name', value=name),
                T.input(type='hidden', name='when_done', value=url.here),
                T.input(type='submit', value='del', name="del"),
                ]

            rename = T.form(action=url.here, method="get")[
                T.input(type='hidden', name='t', value='rename-form'),
                T.input(type='hidden', name='name', value=name),
                T.input(type='hidden', name='when_done', value=url.here),
                T.input(type='submit', value='rename', name="rename"),
                ]
        else:
            delete = "-"
            rename = "-"
        ctx.fillSlots("delete", delete)
        ctx.fillSlots("rename", rename)

        # build the base of the uri_link link url
        uri_link = urllib.quote(target.get_uri().replace("/", "!"))

        if IFileNode.providedBy(target):
            # file
            dlurl = urllib.quote(name)
            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "FILE")


            #uri = target.uri
            #dl_uri_url = url.root.child("download_uri").child(uri)
            ## add a filename= query argument to give it a Content-Type
            #dl_uri_url = dl_uri_url.add("filename", name)
            #ctx.fillSlots("uri", T.a(href=dl_uri_url)[html.escape(uri)])

            #extract and display file size
            try:
                size = uri.get_filenode_size(target.get_uri())
            except AssertionError:
                size = "?"
            ctx.fillSlots("size", size)

            text_plain_link = "/uri/%s?filename=foo.txt" % uri_link
            text_plain_tag = T.a(href=text_plain_link)["text/plain"]

            # if we're a file, add the filename to the uri_link url
            uri_link += '?%s' % (urllib.urlencode({'filename': name}),)

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
            text_plain_tag = None
        else:
            raise RuntimeError("unknown thing %s" % (target,))

        childdata = [T.a(href="%s?t=json" % name)["JSON"], ", ",
                     T.a(href="%s?t=uri" % name)["URI"], ", ",
                     T.a(href="%s?t=readonly-uri" % name)["readonly-URI"], ", ",
                     T.a(href="/uri/%s" % uri_link)["URI-link"],
                     ]
        if text_plain_tag:
            childdata.extend([", ", text_plain_tag])

        ctx.fillSlots("data", childdata)

        return ctx.tag

    def render_forms(self, ctx, data):
        if not self._dirnode.is_mutable():
            return T.div["No upload forms: directory is immutable"]
        mkdir = T.form(action=".", method="post",
                       enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="mkdir"),
            T.input(type="hidden", name="when_done", value=url.here),
            T.legend(class_="freeform-form-label")["Create a new directory"],
            "New directory name: ",
            T.input(type="text", name="name"), " ",
            T.input(type="submit", value="Create"),
            ]]
        upload = T.form(action=".", method="post",
                        enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="upload"),
            T.input(type="hidden", name="when_done", value=url.here),
            T.legend(class_="freeform-form-label")["Upload a file to this directory"],
            "Choose a file to upload: ",
            T.input(type="file", name="file", class_="freeform-input-file"),
            " ",
            T.input(type="submit", value="Upload"),
            ]]
        mount = T.form(action=".", method="post",
                        enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="uri"),
            T.input(type="hidden", name="when_done", value=url.here),
            T.legend(class_="freeform-form-label")["Attach a file or directory"
                                                   " (by URI) to this"
                                                   " directory"],
            "New child name: ",
            T.input(type="text", name="name"), " ",
            "URI of new child: ",
            T.input(type="text", name="uri"), " ",
            T.input(type="submit", value="Attach"),
            ]]
        return [T.div(class_="freeform-form")[mkdir],
                T.div(class_="freeform-form")[upload],
                T.div(class_="freeform-form")[mount],
                ]

    def render_results(self, ctx, data):
        req = inevow.IRequest(ctx)
        if "results" in req.args:
            return req.args["results"]
        else:
            return ""

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
    def __init__(self, filenode, name):
        IFileNode(filenode)
        self._filenode = filenode
        self._name = name

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

class NeedAbsolutePathError:
    implements(inevow.IResource)

    def locateChild(self, ctx, segments):
        return rend.NotFound

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        req.setResponseCode(http.FORBIDDEN)
        req.setHeader("content-type", "text/plain")
        return "localfile= or localdir= requires an absolute path"

        

class LocalFileDownloader(resource.Resource):
    def __init__(self, filenode, local_filename):
        self._local_filename = local_filename
        IFileNode(filenode)
        self._filenode = filenode

    def render(self, req):
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
        req = inevow.IRequest(ctx)
        req.setHeader("content-type", "text/plain")
        return self.renderNode(self._filenode)

    def renderNode(self, filenode):
        file_uri = filenode.get_uri()
        data = ("filenode",
                {'mutable': False,
                 'uri': file_uri,
                 'size': uri.get_filenode_size(file_uri),
                 })
        return simplejson.dumps(data, indent=1)

class FileURI(FileJSONMetadata):
    def renderNode(self, filenode):
        file_uri = filenode.get_uri()
        return file_uri

class DirnodeWalkerMixin:
    """Visit all nodes underneath (and including) the rootnode, one at a
    time. For each one, call the visitor. The visitor will see the
    IDirectoryNode before it sees any of the IFileNodes inside. If the
    visitor returns a Deferred, I do not call the visitor again until it has
    fired.
    """

##    def _walk_if_we_could_use_generators(self, rootnode, rootpath=()):
##        # this is what we'd be doing if we didn't have the Deferreds and
##        # thus could use generators
##        yield rootpath, rootnode
##        for childname, childnode in rootnode.list().items():
##            childpath = rootpath + (childname,)
##            if IFileNode.providedBy(childnode):
##                yield childpath, childnode
##            elif IDirectoryNode.providedBy(childnode):
##                for res in self._walk_if_we_could_use_generators(childnode,
##                                                                 childpath):
##                    yield res

    def walk(self, rootnode, visitor, rootpath=()):
        d = rootnode.list()
        def _listed(listing):
            return listing.items()
        d.addCallback(_listed)
        d.addCallback(self._handle_items, visitor, rootpath)
        return d

    def _handle_items(self, items, visitor, rootpath):
        if not items:
            return
        childname, childnode = items[0]
        childpath = rootpath + (childname,)
        d = defer.maybeDeferred(visitor, childpath, childnode)
        if IDirectoryNode.providedBy(childnode):
            d.addCallback(lambda res: self.walk(childnode, visitor, childpath))
        d.addCallback(lambda res:
                      self._handle_items(items[1:], visitor, rootpath))
        return d

class LocalDirectoryDownloader(resource.Resource, DirnodeWalkerMixin):
    def __init__(self, dirnode, localdir):
        self._dirnode = dirnode
        self._localdir = localdir

    def _handle(self, path, node):
        localfile = os.path.join(self._localdir, os.sep.join(path))
        if IDirectoryNode.providedBy(node):
            fileutil.make_dirs(localfile)
        elif IFileNode.providedBy(node):
            target = download.FileName(localfile)
            return node.download(target)

    def render(self, req):
        d = self.walk(self._dirnode, self._handle)
        def _done(res):
            req.setHeader("content-type", "text/plain")
            return "operation complete"
        d.addCallback(_done)
        return d

class DirectoryJSONMetadata(rend.Page):
    def __init__(self, dirnode):
        self._dirnode = dirnode

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        req.setHeader("content-type", "text/plain")
        return self.renderNode(self._dirnode)

    def renderNode(self, node):
        d = node.list()
        def _got(children):
            kids = {}
            for name, childnode in children.iteritems():
                if IFileNode.providedBy(childnode):
                    kiduri = childnode.get_uri()
                    kiddata = ("filenode",
                               {'mutable': False,
                                'uri': kiduri,
                                'size': uri.get_filenode_size(kiduri),
                                })
                else:
                    assert IDirectoryNode.providedBy(childnode)
                    kiduri = childnode.get_uri()
                    kiddata = ("dirnode",
                               {'mutable': childnode.is_mutable(),
                                'uri': kiduri,
                                })
                kids[name] = kiddata
            contents = { 'children': kids,
                         'mutable': node.is_mutable(),
                         'uri': node.get_uri(),
                         }
            data = ("dirnode", contents)
            return simplejson.dumps(data, indent=1)
        d.addCallback(_got)
        return d

class DirectoryURI(DirectoryJSONMetadata):
    def renderNode(self, node):
        return node.get_uri()

class DirectoryReadonlyURI(DirectoryJSONMetadata):
    def renderNode(self, node):
        return node.get_immutable_uri()

class RenameForm(rend.Page):
    addSlash = True
    docFactory = getxmlfile("rename-form.xhtml")

    def __init__(self, rootname, dirnode, dirpath):
        self._rootname = rootname
        self._dirnode = dirnode
        self._dirpath = dirpath

    def dirpath_as_string(self):
        return "/" + "/".join(self._dirpath)

    def render_title(self, ctx, data):
        return ctx.tag["Directory '%s':" % self.dirpath_as_string()]

    def render_header(self, ctx, data):
        parent_directories = ("<%s>" % self._rootname,) + self._dirpath
        num_dirs = len(parent_directories)

        header = [ "Rename in directory '",
                   "<%s>/" % self._rootname,
                   "/".join(self._dirpath),
                   "':", ]

        if not self._dirnode.is_mutable():
            header.append(" (readonly)")
        return ctx.tag[header]

    def render_when_done(self, ctx, data):
        return T.input(type="hidden", name="when_done", value=url.here)

    def render_get_name(self, ctx, data):
        req = inevow.IRequest(ctx)
        if 'name' in req.args:
            name = req.args['name'][0]
        else:
            name = ''
        ctx.tag.attributes['value'] = name
        return ctx.tag

class POSTHandler(rend.Page):
    def __init__(self, node):
        self._node = node

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)

        if "t" in req.args:
            t = req.args["t"][0]
        else:
            t = req.fields["t"].value

        name = None
        if "name" in req.args:
            name = req.args["name"][0]
        elif name in req.fields:
            name = req.fields["name"].value
        if name and "/" in name:
            req.setResponseCode(http.BAD_REQUEST)
            req.setHeader("content-type", "text/plain")
            return "name= may not contain a slash"

        when_done = None
        if "when_done" in req.args:
            when_done = req.args["when_done"][0]

        if t == "mkdir":
            if not name:
                raise RuntimeError("mkdir requires a name")
            d = self._node.create_empty_directory(name)
            def _done(res):
                return "directory created"
            d.addCallback(_done)
        elif t == "uri":
            if not name:
                raise RuntimeError("set-uri requires a name")
            if "uri" in req.args:
                newuri = req.args["uri"][0]
            else:
                newuri = req.fields["uri"].value
            # sanity checking
            assert(is_dirnode_uri(newuri) or unpack_uri(newuri))
            d = self._node.set_uri(name, newuri)
            def _done(res):
                return newuri
            d.addCallback(_done)
        elif t == "delete":
            if not name:
                raise RuntimeError("delete requires a name")
            d = self._node.delete(name)
            def _done(res):
                return "thing deleted"
            d.addCallback(_done)
        elif t == "rename":
            from_name = 'from_name' in req.fields and req.fields["from_name"].value
            to_name = 'to_name' in req.fields and req.fields["to_name"].value
            if not from_name or not to_name:
                raise RuntimeError("rename requires from_name and to_name")
            if not IDirectoryNode.providedBy(self._node):
                raise RuntimeError("rename must only be called on directories")
            for k,v in [ ('from_name', from_name), ('to_name', to_name) ]:
                if v and "/" in v:
                    req.setResponseCode(http.BAD_REQUEST)
                    req.setHeader("content-type", "text/plain")
                    return "%s= may not contain a slash" % (k,)
            d = self._node.get(from_name)
            def add_dest(child):
                uri = child.get_uri()
                # now actually do the rename
                return self._node.set_uri(to_name, uri)
            d.addCallback(add_dest)
            def rm_src(junk):
                return self._node.delete(from_name)
            d.addCallback(rm_src)
            def _done(res):
                return "thing renamed"
            d.addCallback(_done)
        elif t == "upload":
            contents = req.fields["file"]
            name = name or contents.filename
            uploadable = upload.FileHandle(contents.file)
            d = self._node.add_file(name, uploadable)
            def _done(newnode):
                return newnode.get_uri()
            d.addCallback(_done)
        else:
            print "BAD t=%s" % t
            return "BAD t=%s" % t
        if when_done:
            d.addCallback(lambda res: url.URL.fromString(when_done))
        return d

class DELETEHandler(rend.Page):
    def __init__(self, node, name):
        self._node = node
        self._name = name

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        d = self._node.delete(self._name)
        def _done(res):
            # what should this return??
            return "%s deleted" % self._name
        d.addCallback(_done)
        def _trap_missing(f):
            f.trap(KeyError)
            req.setResponseCode(http.NOT_FOUND)
            req.setHeader("content-type", "text/plain")
            return "no such child %s" % self._name
        d.addErrback(_trap_missing)
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

        # we must traverse the path, creating new directories as necessary
        d = self._get_or_create_directories(self._node, self._path[:-1])
        name = self._path[-1]
        if t == "upload":
            if localfile:
                d.addCallback(self._upload_localfile, localfile, name)
            elif localdir:
                # take the last step
                d.addCallback(self._get_or_create_directories, self._path[-1:])
                d.addCallback(self._upload_localdir, localdir)
            else:
                raise RuntimeError("t=upload requires localfile= or localdir=")
        elif t == "uri":
            d.addCallback(self._attach_uri, req.content, name)
        elif t == "mkdir":
            d.addCallback(self._mkdir, name)
        else:
            d.addCallback(self._upload_file, req.content, name)
        def _check_blocking(f):
            f.trap(BlockingFileError)
            req.setResponseCode(http.BAD_REQUEST)
            req.setHeader("content-type", "text/plain")
            return str(f.value)
        d.addErrback(_check_blocking)
        return d

    def _get_or_create_directories(self, node, path):
        if not IDirectoryNode.providedBy(node):
            # unfortunately it is too late to provide the name of the
            # blocking directory in the error message.
            raise BlockingFileError("cannot create directory because there "
                                    "is a file in the way")
        if not path:
            return defer.succeed(node)
        d = node.get(path[0])
        def _maybe_create(f):
            f.trap(KeyError)
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
        d = node.add_file(name, uploadable)
        def _done(filenode):
            log.msg("webish upload complete")
            return filenode.get_uri()
        d.addCallback(_done)
        return d

    def _upload_localfile(self, node, localfile, name):
        uploadable = upload.FileName(localfile)
        d = node.add_file(name, uploadable)
        d.addCallback(lambda filenode: filenode.get_uri())
        return d

    def _attach_uri(self, parentnode, contents, name):
        newuri = contents.read().strip()
        d = parentnode.set_uri(name, newuri)
        def _done(res):
            return newuri
        d.addCallback(_done)
        return d

    def _upload_localdir(self, node, localdir):
        # build up a list of files to upload
        all_files = []
        all_dirs = []
        for root, dirs, files in os.walk(localdir):
            if root == localdir:
                path = ()
            else:
                relative_root = root[len(localdir)+1:]
                path = tuple(relative_root.split(os.sep))
            for d in dirs:
                all_dirs.append(path + (d,))
            for f in files:
                all_files.append(path + (f,))
        d = defer.succeed(None)
        for dir in all_dirs:
            if dir:
                d.addCallback(self._makedir, node, dir)
        for f in all_files:
            d.addCallback(self._upload_one_file, node, localdir, f)
        return d

    def _makedir(self, res, node, dir):
        d = defer.succeed(None)
        # get the parent. As long as os.walk gives us parents before
        # children, this ought to work
        d.addCallback(lambda res: node.get_child_at_path(dir[:-1]))
        # then create the child directory
        d.addCallback(lambda parent: parent.create_empty_directory(dir[-1]))
        return d

    def _upload_one_file(self, res, node, localdir, f):
        # get the parent. We can be sure this exists because we already
        # went through and created all the directories we require.
        localfile = os.path.join(localdir, *f)
        d = node.get_child_at_path(f[:-1])
        d.addCallback(self._upload_localfile, localfile, f[-1])
        return d


class Manifest(rend.Page):
    docFactory = getxmlfile("manifest.xhtml")
    def __init__(self, dirnode, dirpath):
        self._dirnode = dirnode
        self._dirpath = dirpath

    def dirpath_as_string(self):
        return "/" + "/".join(self._dirpath)

    def render_title(self, ctx):
        return T.title["Manifest of %s" % self.dirpath_as_string()]

    def render_header(self, ctx):
        return T.p["Manifest of %s" % self.dirpath_as_string()]

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

        t = ""
        if "t" in req.args:
            t = req.args["t"][0]

        localfile = None
        if "localfile" in req.args:
            localfile = req.args["localfile"][0]
            if localfile != os.path.abspath(localfile):
                return NeedAbsolutePathError(), ()
        localdir = None
        if "localdir" in req.args:
            localdir = req.args["localdir"][0]
            if localdir != os.path.abspath(localdir):
                return NeedAbsolutePathError(), ()
        if localfile or localdir:
            if req.getHost().host != LOCALHOST:
                return NeedLocalhostError(), ()
        # TODO: think about clobbering/revealing config files and node secrets

        if method == "GET":
            # the node must exist, and our operation will be performed on the
            # node itself.
            d = self.get_child_at_path(path)
            def file_or_dir(node):
                if IFileNode.providedBy(node):
                    filename = "unknown"
                    if path:
                        filename = path[-1]
                    if "filename" in req.args:
                        filename = req.args["filename"][0]
                    if t == "download":
                        if localfile:
                            # write contents to a local file
                            return LocalFileDownloader(node, localfile), ()
                        # send contents as the result
                        return FileDownloader(node, filename), ()
                    elif t == "":
                        # send contents as the result
                        return FileDownloader(node, filename), ()
                    elif t == "json":
                        return FileJSONMetadata(node), ()
                    elif t == "uri":
                        return FileURI(node), ()
                    elif t == "readonly-uri":
                        return FileURI(node), ()
                    else:
                        raise RuntimeError("bad t=%s" % t)
                elif IDirectoryNode.providedBy(node):
                    if t == "download":
                        if localdir:
                            # recursive download to a local directory
                            return LocalDirectoryDownloader(node, localdir), ()
                        raise RuntimeError("t=download requires localdir=")
                    elif t == "":
                        # send an HTML representation of the directory
                        return Directory(self.name, node, path), ()
                    elif t == "json":
                        return DirectoryJSONMetadata(node), ()
                    elif t == "uri":
                        return DirectoryURI(node), ()
                    elif t == "readonly-uri":
                        return DirectoryReadonlyURI(node), ()
                    elif t == "manifest":
                        return Manifest(node, path), ()
                    elif t == 'rename-form':
                        return RenameForm(self.name, node, path), ()
                    else:
                        raise RuntimeError("bad t=%s" % t)
                else:
                    raise RuntimeError("unknown node type")
            d.addCallback(file_or_dir)
        elif method == "POST":
            # the node must exist, and our operation will be performed on the
            # node itself.
            d = self.get_child_at_path(path)
            def _got(node):
                return POSTHandler(node), ()
            d.addCallback(_got)
        elif method == "DELETE":
            # the node must exist, and our operation will be performed on its
            # parent node.
            assert path # you can't delete the root
            d = self.get_child_at_path(path[:-1])
            def _got(node):
                return DELETEHandler(node, path[-1]), ()
            d.addCallback(_got)
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
        req = inevow.IRequest(ctx)
        vdrive = client.getServiceNamed("vdrive")

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
            if len(segments) == 1 or segments[1] == '':
                if "uri" in req.args:
                    uri = req.args["uri"][0].replace("/", "!")
                    there = url.URL.fromContext(ctx)
                    there = there.clear("uri")
                    there = there.child("uri").child(uri)
                    return there, ()
            if len(segments) < 2:
                return rend.NotFound
            uri = segments[1].replace("!", "/")
            d = vdrive.get_node(uri)
            d.addCallback(lambda node: VDrive(node, "from-uri"))
            d.addCallback(lambda vd: vd.locateChild(ctx, segments[2:]))
            def _trap_KeyError(f):
                f.trap(KeyError)
                return rend.FourOhFour(), ()
            d.addErrback(_trap_KeyError)
            return d
        elif segments[0] == "xmlrpc":
            pass # TODO
        return rend.Page.locateChild(self, ctx, segments)

    child_webform_css = webform.defaultCSS
    child_tahoe_css = nevow_File(util.sibpath(__file__, "web/tahoe.css"))

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
                       T.a(href="vdrive/global")["Click Here!"],
                       ]
        return T.p["vdrive.furl not specified (or vdrive server not "
                   "responding), no vdrive available."]

    def render_private_vdrive(self, ctx, data):
        if IClient(ctx).getServiceNamed("vdrive").have_private_root():
            return T.p["To view your personal private non-shared filestore, ",
                       T.a(href="vdrive/private")["Click Here!"],
                       ]
        return T.p["personal vdrive not available."]

    # this is a form where users can download files by URI

    def render_download_form(self, ctx, data):
        form = T.form(action="uri", method="get",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Download a file"],
            "URI of file to download: ",
            T.input(type="text", name="uri"), " ",
            "Filename to download as: ",
            T.input(type="text", name="filename"), " ",
            T.input(type="submit", value="Download"),
            ]]
        return T.div[form]


class WebishServer(service.MultiService):
    name = "webish"

    def __init__(self, webport):
        service.MultiService.__init__(self)
        self.root = Root()
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
