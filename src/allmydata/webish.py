
from base64 import b32encode
import os.path
from twisted.application import service, strports, internet
from twisted.web import static, resource, server, html, http
from twisted.python import log
from twisted.internet import defer
from twisted.internet.interfaces import IConsumer
from nevow import inevow, rend, loaders, appserver, url, tags as T
from nevow.static import File as nevow_File # TODO: merge with static.File?
from allmydata.util import fileutil, sibpath
import simplejson
from allmydata.interfaces import IDownloadTarget, IDirectoryNode, IFileNode, \
     IMutableFileNode
from allmydata import download
from allmydata.upload import FileHandle, FileName
from allmydata import provisioning
from allmydata import get_package_versions_string
from zope.interface import implements, Interface
import urllib
from formless import webform

def getxmlfile(name):
    return loaders.xmlfile(sibpath.sibpath(__file__, "web/%s" % name))

class IClient(Interface):
    pass
class ILocalAccess(Interface):
    def local_access_is_allowed():
        """Return True if t=upload&localdir= is allowed, giving anyone who
        can talk to the webserver control over the local (disk) filesystem."""

def boolean_of_arg(arg):
    assert arg.lower() in ("true", "t", "1", "false", "f", "0")
    return arg.lower() in ("true", "t", "1")

def get_arg(req, argname, default=None, multiple=False):
    """Extract an argument from either the query args (req.args) or the form
    body fields (req.fields). If multiple=False, this returns a single value
    (or the default, which defaults to None), and the query args take
    precedence. If multiple=True, this returns a tuple of arguments (possibly
    empty), starting with all those in the query args.
    """
    results = []
    if argname in req.args:
        results.extend(req.args[argname])
    if req.fields and argname in req.fields:
        results.append(req.fields[argname].value)
    if multiple:
        return tuple(results)
    if results:
        return results[0]
    return default

# we must override twisted.web.http.Request.requestReceived with a version
# that doesn't use cgi.parse_multipart() . Since we actually use Nevow, we
# override the nevow-specific subclass, nevow.appserver.NevowRequest . This
# is an exact copy of twisted.web.http.Request (from SVN HEAD on 10-Aug-2007)
# that modifies the way form arguments are parsed. Note that this sort of
# surgery may induce a dependency upon a particular version of twisted.web

parse_qs = http.parse_qs
class MyRequest(appserver.NevowRequest):
    fields = None
    def requestReceived(self, command, path, version):
        """Called by channel when all data has been received.

        This method is not intended for users.
        """
        self.content.seek(0,0)
        self.args = {}
        self.stack = []

        self.method, self.uri = command, path
        self.clientproto = version
        x = self.uri.split('?', 1)

        if len(x) == 1:
            self.path = self.uri
        else:
            self.path, argstring = x
            self.args = parse_qs(argstring, 1)

        # cache the client and server information, we'll need this later to be
        # serialized and sent with the request so CGIs will work remotely
        self.client = self.channel.transport.getPeer()
        self.host = self.channel.transport.getHost()

        # Argument processing.

##      The original twisted.web.http.Request.requestReceived code parsed the
##      content and added the form fields it found there to self.args . It
##      did this with cgi.parse_multipart, which holds the arguments in RAM
##      and is thus unsuitable for large file uploads. The Nevow subclass
##      (nevow.appserver.NevowRequest) uses cgi.FieldStorage instead (putting
##      the results in self.fields), which is much more memory-efficient.
##      Since we know we're using Nevow, we can anticipate these arguments
##      appearing in self.fields instead of self.args, and thus skip the
##      parse-content-into-self.args step.

##      args = self.args
##      ctype = self.getHeader('content-type')
##      if self.method == "POST" and ctype:
##          mfd = 'multipart/form-data'
##          key, pdict = cgi.parse_header(ctype)
##          if key == 'application/x-www-form-urlencoded':
##              args.update(parse_qs(self.content.read(), 1))
##          elif key == mfd:
##              try:
##                  args.update(cgi.parse_multipart(self.content, pdict))
##              except KeyError, e:
##                  if e.args[0] == 'content-disposition':
##                      # Parse_multipart can't cope with missing
##                      # content-dispostion headers in multipart/form-data
##                      # parts, so we catch the exception and tell the client
##                      # it was a bad request.
##                      self.channel.transport.write(
##                              "HTTP/1.1 400 Bad Request\r\n\r\n")
##                      self.channel.transport.loseConnection()
##                      return
##                  raise

        self.process()

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

        if self._dirnode.is_readonly():
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
        name, (target, metadata) = data

        if self._dirnode.is_readonly():
            delete = "-"
            rename = "-"
        else:
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

        ctx.fillSlots("delete", delete)
        ctx.fillSlots("rename", rename)
        check = T.form(action=url.here, method="post")[
            T.input(type='hidden', name='t', value='check'),
            T.input(type='hidden', name='name', value=name),
            T.input(type='hidden', name='when_done', value=url.here),
            T.input(type='submit', value='check', name="check"),
            ]
        ctx.fillSlots("overwrite", self.build_overwrite(ctx, (name, target)))
        ctx.fillSlots("check", check)

        # build the base of the uri_link link url
        uri_link = "/uri/" + urllib.quote(target.get_uri())

        assert (IFileNode.providedBy(target)
                or IDirectoryNode.providedBy(target)
                or IMutableFileNode.providedBy(target)), target

        if IMutableFileNode.providedBy(target):
            # file

            # add the filename to the uri_link url
            uri_link += '?%s' % (urllib.urlencode({'filename': name}),)

            # to prevent javascript in displayed .html files from stealing a
            # secret directory URI from the URL, send the browser to a URI-based
            # page that doesn't know about the directory at all
            #dlurl = urllib.quote(name)
            dlurl = uri_link

            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "SSK")

            ctx.fillSlots("size", "?")

            text_plain_link = uri_link + "?filename=foo.txt"
            text_plain_tag = T.a(href=text_plain_link)["text/plain"]

        elif IFileNode.providedBy(target):
            # file

            # add the filename to the uri_link url
            uri_link += '?%s' % (urllib.urlencode({'filename': name}),)

            # to prevent javascript in displayed .html files from stealing a
            # secret directory URI from the URL, send the browser to a URI-based
            # page that doesn't know about the directory at all
            #dlurl = urllib.quote(name)
            dlurl = uri_link

            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "FILE")

            ctx.fillSlots("size", target.get_size())

            text_plain_link = uri_link + "?filename=foo.txt"
            text_plain_tag = T.a(href=text_plain_link)["text/plain"]

        elif IDirectoryNode.providedBy(target):
            # directory
            subdir_url = urllib.quote(name)
            ctx.fillSlots("filename",
                          T.a(href=subdir_url)[html.escape(name)])
            if target.is_readonly():
                dirtype = "DIR-RO"
            else:
                dirtype = "DIR"
            ctx.fillSlots("type", dirtype)
            ctx.fillSlots("size", "-")
            text_plain_tag = None

        childdata = [T.a(href="%s?t=json" % name)["JSON"], ", ",
                     T.a(href="%s?t=uri" % name)["URI"], ", ",
                     T.a(href="%s?t=readonly-uri" % name)["readonly-URI"], ", ",
                     T.a(href=uri_link)["URI-link"],
                     ]
        if text_plain_tag:
            childdata.extend([", ", text_plain_tag])

        ctx.fillSlots("data", childdata)

        try:
            checker = IClient(ctx).getServiceNamed("checker")
        except KeyError:
            checker = None
        if checker:
            d = defer.maybeDeferred(checker.checker_results_for,
                                    target.get_verifier())
            def _got(checker_results):
                recent_results = reversed(checker_results[-5:])
                if IFileNode.providedBy(target):
                    results = ("[" +
                               ", ".join(["%d/%d" % (found, needed)
                                          for (when,
                                               (needed, total, found, sharemap))
                                          in recent_results]) +
                               "]")
                elif IDirectoryNode.providedBy(target):
                    results = ("[" +
                               "".join([{True:"+",False:"-"}[res]
                                        for (when, res) in recent_results]) +
                               "]")
                else:
                    results = "%d results" % len(checker_results)
                return results
            d.addCallback(_got)
            results = d
        else:
            results = "--"
        # TODO: include a link to see more results, including timestamps
        # TODO: use a sparkline
        ctx.fillSlots("checker_results", results)

        return ctx.tag

    def render_forms(self, ctx, data):
        if self._dirnode.is_readonly():
            return T.div["No upload forms: directory is read-only"]
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
            " Mutable?:",
            T.input(type="checkbox", name="mutable"),
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

    def build_overwrite(self, ctx, data):
        name, target = data
        if IMutableFileNode.providedBy(target) and not target.is_readonly():
            action="/uri/" + urllib.quote(target.get_uri())
            overwrite = T.form(action=action, method="post",
                               enctype="multipart/form-data")[
                T.fieldset[
                T.input(type="hidden", name="t", value="overwrite"),
                T.input(type='hidden', name='name', value=name),
                T.input(type='hidden', name='when_done', value=url.here),
                T.legend(class_="freeform-form-label")["Overwrite"],
                "Choose new file: ",
                T.input(type="file", name="file", class_="freeform-input-file"),
                " ",
                T.input(type="submit", value="Overwrite")
                ]]
            return [T.div(class_="freeform-form")[overwrite],]
        else:
            return []

    def render_results(self, ctx, data):
        req = inevow.IRequest(ctx)
        return get_arg(req, "results", "")

class WebDownloadTarget:
    implements(IDownloadTarget, IConsumer)
    def __init__(self, req, content_type, content_encoding, save_to_file):
        self._req = req
        self._content_type = content_type
        self._content_encoding = content_encoding
        self._opened = False
        self._producer = None
        self._save_to_file = save_to_file

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
        if self._save_to_file is not None:
            # tell the browser to save the file rather display it
            # TODO: quote save_to_file properly
            self._req.setHeader("content-disposition",
                                'attachment; filename="%s"'
                                % self._save_to_file)

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
        assert (IFileNode.providedBy(filenode)
                or IMutableFileNode.providedBy(filenode))
        self._filenode = filenode
        self._name = name

    def render(self, req):
        gte = static.getTypeAndEncoding
        type, encoding = gte(self._name,
                             static.File.contentTypes,
                             static.File.contentEncodings,
                             defaultType="text/plain")
        save_to_file = None
        if get_arg(req, "save", False):
            # TODO: make the API specification clear: should "save=" or
            # "save=false" count?
            save_to_file = self._name
        wdt = WebDownloadTarget(req, type, encoding, save_to_file)
        d = self._filenode.download(wdt)
        # exceptions during download are handled by the WebDownloadTarget
        d.addErrback(lambda why: None)
        return server.NOT_DONE_YET

class BlockingFileError(Exception):
    """We cannot auto-create a parent directory, because there is a file in
    the way"""
class NoReplacementError(Exception):
    """There was already a child by that name, and you asked me to not replace it"""

LOCALHOST = "127.0.0.1"

class NeedLocalhostError:
    implements(inevow.IResource)

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        req.setResponseCode(http.FORBIDDEN)
        req.setHeader("content-type", "text/plain")
        return "localfile= or localdir= requires a local connection"

class NeedAbsolutePathError:
    implements(inevow.IResource)

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        req.setResponseCode(http.FORBIDDEN)
        req.setHeader("content-type", "text/plain")
        return "localfile= or localdir= requires an absolute path"

class LocalAccessDisabledError:
    implements(inevow.IResource)

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        req.setResponseCode(http.FORBIDDEN)
        req.setHeader("content-type", "text/plain")
        return "local file access is disabled"


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
                {'ro_uri': file_uri,
                 'size': filenode.get_size(),
                 })
        return simplejson.dumps(data, indent=1)

class FileURI(FileJSONMetadata):
    def renderNode(self, filenode):
        file_uri = filenode.get_uri()
        return file_uri

class FileReadOnlyURI(FileJSONMetadata):
    def renderNode(self, filenode):
        if filenode.is_readonly():
            return filenode.get_uri()
        else:
            return filenode.get_readonly().get_uri()

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
        childname, (childnode, metadata) = items[0]
        childpath = rootpath + (childname,)
        d = defer.maybeDeferred(visitor, childpath, childnode, metadata)
        if IDirectoryNode.providedBy(childnode):
            d.addCallback(lambda res: self.walk(childnode, visitor, childpath))
        d.addCallback(lambda res:
                      self._handle_items(items[1:], visitor, rootpath))
        return d

class LocalDirectoryDownloader(resource.Resource, DirnodeWalkerMixin):
    def __init__(self, dirnode, localdir):
        self._dirnode = dirnode
        self._localdir = localdir

    def _handle(self, path, node, metadata):
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
            for name, (childnode, metadata) in children.iteritems():
                if IFileNode.providedBy(childnode):
                    kiduri = childnode.get_uri()
                    kiddata = ("filenode",
                               {'ro_uri': kiduri,
                                'size': childnode.get_size(),
                                })
                else:
                    assert IDirectoryNode.providedBy(childnode), (childnode, children,)
                    kiddata = ("dirnode",
                               {'ro_uri': childnode.get_readonly_uri(),
                                })
                    if not childnode.is_readonly():
                        kiddata[1]['rw_uri'] = childnode.get_uri()
                kids[name] = kiddata
            contents = { 'children': kids,
                         'ro_uri': node.get_readonly_uri(),
                         }
            if not node.is_readonly():
                contents['rw_uri'] = node.get_uri()
            data = ("dirnode", contents)
            return simplejson.dumps(data, indent=1)
        d.addCallback(_got)
        return d

class DirectoryURI(DirectoryJSONMetadata):
    def renderNode(self, node):
        return node.get_uri()

class DirectoryReadonlyURI(DirectoryJSONMetadata):
    def renderNode(self, node):
        return node.get_readonly_uri()

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

        if self._dirnode.is_readonly():
            header.append(" (readonly)")
        return ctx.tag[header]

    def render_when_done(self, ctx, data):
        return T.input(type="hidden", name="when_done", value=url.here)

    def render_get_name(self, ctx, data):
        req = inevow.IRequest(ctx)
        name = get_arg(req, "name", "")
        ctx.tag.attributes['value'] = name
        return ctx.tag

class POSTHandler(rend.Page):
    def __init__(self, node, replace):
        self._node = node
        self._replace = replace

    def _check_replacement(self, name):
        if self._replace:
            return defer.succeed(None)
        d = self._node.has_child(name)
        def _got(present):
            if present:
                raise NoReplacementError("There was already a child by that "
                                         "name, and you asked me to not "
                                         "replace it.")
            return None
        d.addCallback(_got)
        return d

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)

        t = get_arg(req, "t")
        assert t is not None

        name = get_arg(req, "name", None)
        if name and "/" in name:
            req.setResponseCode(http.BAD_REQUEST)
            req.setHeader("content-type", "text/plain")
            return "name= may not contain a slash"
        if name is not None:
            name = name.strip()
        # we allow the user to delete an empty-named file, but not to create
        # them, since that's an easy and confusing mistake to make

        when_done = get_arg(req, "when_done", None)
        if not boolean_of_arg(get_arg(req, "replace", "true")):
            self._replace = False

        if t == "mkdir":
            if not name:
                raise RuntimeError("mkdir requires a name")
            d = self._check_replacement(name)
            d.addCallback(lambda res: self._node.create_empty_directory(name))
            d.addCallback(lambda res: "directory created")
        elif t == "uri":
            if not name:
                raise RuntimeError("set-uri requires a name")
            newuri = get_arg(req, "uri")
            assert newuri is not None
            d = self._check_replacement(name)
            d.addCallback(lambda res: self._node.set_uri(name, newuri))
            d.addCallback(lambda res: newuri)
        elif t == "delete":
            if name is None:
                # apparently an <input type="hidden" name="name" value="">
                # won't show up in the resulting encoded form.. the 'name'
                # field is completely missing. So to allow deletion of an
                # empty file, we have to pretend that None means ''. The only
                # downide of this is a slightly confusing error message if
                # someone does a POST without a name= field. For our own HTML
                # thisn't a big deal, because we create the 'delete' POST
                # buttons ourselves.
                name = ''
            d = self._node.delete(name)
            d.addCallback(lambda res: "thing deleted")
        elif t == "rename":
            from_name = 'from_name' in req.fields and req.fields["from_name"].value
            if from_name is not None:
                from_name = from_name.strip()
            to_name = 'to_name' in req.fields and req.fields["to_name"].value
            if to_name is not None:
                to_name = to_name.strip()
            if not from_name or not to_name:
                raise RuntimeError("rename requires from_name and to_name")
            if not IDirectoryNode.providedBy(self._node):
                raise RuntimeError("rename must only be called on directories")
            for k,v in [ ('from_name', from_name), ('to_name', to_name) ]:
                if v and "/" in v:
                    req.setResponseCode(http.BAD_REQUEST)
                    req.setHeader("content-type", "text/plain")
                    return "%s= may not contain a slash" % (k,)
            d = self._check_replacement(to_name)
            d.addCallback(lambda res: self._node.get(from_name))
            def add_dest(child):
                uri = child.get_uri()
                # now actually do the rename
                return self._node.set_uri(to_name, uri)
            d.addCallback(add_dest)
            def rm_src(junk):
                return self._node.delete(from_name)
            d.addCallback(rm_src)
            d.addCallback(lambda res: "thing renamed")

        elif t == "upload":
            if "mutable" in req.fields:
                contents = req.fields["file"]
                name = name or contents.filename
                if name is not None:
                    name = name.strip()
                if not name:
                    raise RuntimeError("upload-mutable requires a name")
                # SDMF: files are small, and we can only upload data.
                contents.file.seek(0)
                data = contents.file.read()
                uploadable = FileHandle(contents.file)
                d = self._check_replacement(name)
                d.addCallback(lambda res: self._node.has_child(name))
                def _checked(present):
                    if present:
                        # modify the existing one instead of creating a new
                        # one
                        d2 = self._node.get(name)
                        def _got_newnode(newnode):
                            d3 = newnode.replace(data)
                            d3.addCallback(lambda res: newnode.get_uri())
                            return d3
                        d2.addCallback(_got_newnode)
                    else:
                        d2 = IClient(ctx).create_mutable_file(data)
                        def _uploaded(newnode):
                            d1 = self._node.set_node(name, newnode)
                            d1.addCallback(lambda res: newnode.get_uri())
                            return d1
                        d2.addCallback(_uploaded)
                    return d2
                d.addCallback(_checked)
            else:
                contents = req.fields["file"]
                name = name or contents.filename
                if name is not None:
                    name = name.strip()
                if not name:
                    raise RuntimeError("upload requires a name")
                uploadable = FileHandle(contents.file)
                d = self._check_replacement(name)
                d.addCallback(lambda res: self._node.add_file(name, uploadable))
                def _done(newnode):
                    return newnode.get_uri()
                d.addCallback(_done)

        elif t == "overwrite":
            contents = req.fields["file"]
            # SDMF: files are small, and we can only upload data.
            contents.file.seek(0)
            data = contents.file.read()
            # TODO: 'name' handling needs review
            d = defer.succeed(self._node)
            def _got_child_overwrite(child_node):
                child_node.replace(data)
                return child_node.get_uri()
            d.addCallback(_got_child_overwrite)

        elif t == "check":
            d = self._node.get(name)
            def _got_child_check(child_node):
                d2 = child_node.check()
                def _done(res):
                    log.msg("checked %s, results %s" % (child_node, res))
                    return str(res)
                d2.addCallback(_done)
                return d2
            d.addCallback(_got_child_check)
        else:
            print "BAD t=%s" % t
            return "BAD t=%s" % t
        if when_done:
            d.addCallback(lambda res: url.URL.fromString(when_done))
        def _check_replacement(f):
            # TODO: make this more human-friendly: maybe send them to the
            # when_done page but with an extra query-arg that will display
            # the error message in a big box at the top of the page. The
            # directory page that when_done= usually points to accepts a
            # result= argument.. use that.
            f.trap(NoReplacementError)
            req.setResponseCode(http.CONFLICT)
            req.setHeader("content-type", "text/plain")
            return str(f.value)
        d.addErrback(_check_replacement)
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
    def __init__(self, node, path, t, localfile, localdir, replace):
        self._node = node
        self._path = path
        self._t = t
        self._localfile = localfile
        self._localdir = localdir
        self._replace = replace

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        t = self._t
        localfile = self._localfile
        localdir = self._localdir

        if t == "upload" and not (localfile or localdir):
            req.setResponseCode(http.BAD_REQUEST)
            req.setHeader("content-type", "text/plain")
            return "t=upload requires localfile= or localdir="

        # we must traverse the path, creating new directories as necessary
        d = self._get_or_create_directories(self._node, self._path[:-1])
        name = self._path[-1]
        d.addCallback(self._check_replacement, name, self._replace)
        if t == "upload":
            if localfile:
                d.addCallback(self._upload_localfile, localfile, name)
            else:
                # localdir
                # take the last step
                d.addCallback(self._get_or_create_directories, self._path[-1:])
                d.addCallback(self._upload_localdir, localdir)
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
        def _check_replacement(f):
            f.trap(NoReplacementError)
            req.setResponseCode(http.CONFLICT)
            req.setHeader("content-type", "text/plain")
            return str(f.value)
        d.addErrback(_check_replacement)
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

    def _check_replacement(self, node, name, replace):
        if replace:
            return node
        d = node.has_child(name)
        def _got(present):
            if present:
                raise NoReplacementError("There was already a child by that "
                                         "name, and you asked me to not "
                                         "replace it.")
            return node
        d.addCallback(_got)
        return d

    def _mkdir(self, node, name):
        d = node.create_empty_directory(name)
        def _done(newnode):
            return newnode.get_uri()
        d.addCallback(_done)
        return d

    def _upload_file(self, node, contents, name):
        uploadable = FileHandle(contents)
        d = node.add_file(name, uploadable)
        def _done(filenode):
            log.msg("webish upload complete")
            return filenode.get_uri()
        d.addCallback(_done)
        return d

    def _upload_localfile(self, node, localfile, name):
        uploadable = FileName(localfile)
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
        msg = "No files to upload! %s is empty" % localdir
        if not os.path.exists(localdir):
            msg = "%s doesn't exist!" % localdir
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
        d = defer.succeed(msg)
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

class ChildError:
    implements(inevow.IResource)
    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        req.setResponseCode(http.BAD_REQUEST)
        req.setHeader("content-type", "text/plain")
        return self.text

def child_error(text):
    ce = ChildError()
    ce.text = text
    return ce, ()

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

        t = get_arg(req, "t", "")
        localfile = get_arg(req, "localfile", None)
        if localfile is not None:
            if localfile != os.path.abspath(localfile):
                return NeedAbsolutePathError(), ()
        localdir = get_arg(req, "localdir", None)
        if localdir is not None:
            if localdir != os.path.abspath(localdir):
                return NeedAbsolutePathError(), ()
        if localfile or localdir:
            if not ILocalAccess(ctx).local_access_is_allowed():
                return LocalAccessDisabledError(), ()
            if req.getHost().host != LOCALHOST:
                return NeedLocalhostError(), ()
        # TODO: think about clobbering/revealing config files and node secrets

        replace = boolean_of_arg(get_arg(req, "replace", "true"))

        if method == "GET":
            # the node must exist, and our operation will be performed on the
            # node itself.
            d = self.get_child_at_path(path)
            def file_or_dir(node):
                if (IFileNode.providedBy(node)
                    or IMutableFileNode.providedBy(node)):
                    filename = "unknown"
                    if path:
                        filename = path[-1]
                    filename = get_arg(req, "filename", filename)
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
                        return FileReadOnlyURI(node), ()
                    else:
                        return child_error("bad t=%s" % t)
                elif IDirectoryNode.providedBy(node):
                    if t == "download":
                        if localdir:
                            # recursive download to a local directory
                            return LocalDirectoryDownloader(node, localdir), ()
                        return child_error("t=download requires localdir=")
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
                        return child_error("bad t=%s" % t)
                else:
                    return child_error("unknown node type")
            d.addCallback(file_or_dir)
        elif method == "POST":
            # the node must exist, and our operation will be performed on the
            # node itself.
            d = self.get_child_at_path(path)
            def _got_POST(node):
                return POSTHandler(node, replace), ()
            d.addCallback(_got_POST)
        elif method == "DELETE":
            # the node must exist, and our operation will be performed on its
            # parent node.
            assert path # you can't delete the root
            d = self.get_child_at_path(path[:-1])
            def _got_DELETE(node):
                return DELETEHandler(node, path[-1]), ()
            d.addCallback(_got_DELETE)
        elif method in ("PUT",):
            # the node may or may not exist, and our operation may involve
            # all the ancestors of the node.
            return PUTHandler(self.node, path, t, localfile, localdir, replace), ()
        else:
            return rend.NotFound
        return d

class URIPUTHandler(rend.Page):
    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        assert req.method == "PUT"

        t = get_arg(req, "t", "")

        if t == "":
            # "PUT /uri", to create an unlinked file. This is like PUT but
            # without the associated set_uri.
            uploadable = FileHandle(req.content)
            d = IClient(ctx).upload(uploadable)
            # that fires with the URI of the new file
            return d

        if t == "mkdir":
            # "PUT /uri?t=mkdir", to create an unlinked directory.
            d = IClient(ctx).create_empty_dirnode()
            d.addCallback(lambda dirnode: dirnode.get_uri())
            # XXX add redirect_to_result
            return d

        req.setResponseCode(http.BAD_REQUEST)
        req.setHeader("content-type", "text/plain")
        return "/uri only accepts PUT and PUT?t=mkdir"

class URIPOSTHandler(rend.Page):
    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        assert req.method == "POST"

        t = get_arg(req, "t", "").strip()

        if t in ("", "upload"):
            # "POST /uri", to create an unlinked file.
            fileobj = req.fields["file"].file
            uploadable = FileHandle(fileobj)
            d = IClient(ctx).upload(uploadable)
            # that fires with the URI of the new file
            return d

        if t == "mkdir":
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

        req.setResponseCode(http.BAD_REQUEST)
        req.setHeader("content-type", "text/plain")
        err = "/uri accepts only PUT, PUT?t=mkdir, POST?t=upload, and POST?t=mkdir"
        return err


class Root(rend.Page):

    addSlash = True
    docFactory = getxmlfile("welcome.xhtml")

    def locateChild(self, ctx, segments):
        client = IClient(ctx)
        req = inevow.IRequest(ctx)

        segments = list(segments) # XXX HELP I AM YUCKY!
        while segments and not segments[-1]:
            segments.pop()
        if not segments:
            segments.append('')
        segments = tuple(segments)
        if segments:
            if segments[0] == "uri":
                if len(segments) == 1 or segments[1] == '':
                    uri = get_arg(req, "uri", None)
                    if uri is not None:
                        there = url.URL.fromContext(ctx)
                        there = there.clear("uri")
                        there = there.child("uri").child(uri)
                        return there, ()
                if len(segments) == 1:
                    # /uri
                    if req.method == "PUT":
                        # either "PUT /uri" to create an unlinked file, or
                        # "PUT /uri?t=mkdir" to create an unlinked directory
                        return URIPUTHandler(), ()
                    elif req.method == "POST":
                        # "POST /uri?t=upload&file=newfile" to upload an unlinked
                        # file or "POST /uri?t=mkdir" to create a new directory
                        return URIPOSTHandler(), ()
                if len(segments) < 2:
                    return rend.NotFound
                uri = segments[1]
                d = defer.maybeDeferred(client.create_node_from_uri, uri)
                d.addCallback(lambda node: VDrive(node, "from-uri"))
                d.addCallback(lambda vd: vd.locateChild(ctx, segments[2:]))
                def _trap_KeyError(f):
                    f.trap(KeyError)
                    return rend.FourOhFour(), ()
                d.addErrback(_trap_KeyError)
                return d
            elif segments[0] == "xmlrpc":
                raise NotImplementedError()
        return rend.Page.locateChild(self, ctx, segments)

    child_webform_css = webform.defaultCSS
    child_tahoe_css = nevow_File(sibpath.sibpath(__file__, "web/tahoe.css"))

    child_provisioning = provisioning.ProvisioningTool()

    def data_version(self, ctx, data):
        return get_package_versions_string()

    def data_my_nodeid(self, ctx, data):
        return b32encode(IClient(ctx).nodeid).lower()
    def data_introducer_furl(self, ctx, data):
        return IClient(ctx).introducer_furl
    def data_connected_to_introducer(self, ctx, data):
        if IClient(ctx).connected_to_introducer():
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
            row = (b32encode(nodeid).lower(),)
            d.append(row)
        return d

    def render_row(self, ctx, data):
        (nodeid_a,) = data
        ctx.fillSlots("peerid", nodeid_a)
        return ctx.tag

    # this is a form where users can download files by URI
    def render_download_form(self, ctx, data):
        form = T.form(action="uri", method="get",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["download a file"],
            "URI of file to download: ",
            T.input(type="text", name="uri"), " ",
            "Filename to download as: ",
            T.input(type="text", name="filename"), " ",
            T.input(type="submit", value="download"),
            ]]
        return T.div[form]

    # this is a form where users can create new directories
    def render_mkdir_form(self, ctx, data):
        form = T.form(action="uri", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["create a directory"],
            T.input(type="hidden", name="t", value="mkdir"),
            T.input(type="hidden", name="redirect_to_result", value="true"),
            T.input(type="submit", value="create"),
            ]]
        return T.div[form]


class LocalAccess:
    implements(ILocalAccess)
    def __init__(self):
        self.local_access = False
    def local_access_is_allowed(self):
        return self.local_access

class WebishServer(service.MultiService):
    name = "webish"

    def __init__(self, webport, nodeurl_path=None):
        service.MultiService.__init__(self)
        self.webport = webport
        self.root = Root()
        self.site = site = appserver.NevowSite(self.root)
        self.site.requestFactory = MyRequest
        self.allow_local = LocalAccess()
        self.site.remember(self.allow_local, ILocalAccess)
        s = strports.service(webport, site)
        s.setServiceParent(self)
        self.listener = s # stash it so the tests can query for the portnum
        self._started = defer.Deferred()
        if nodeurl_path:
            self._started.addCallback(self._write_nodeurl_file, nodeurl_path)

    def allow_local_access(self, enable=True):
        self.allow_local.local_access = enable

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
        self._started.callback(None)

    def _write_nodeurl_file(self, junk, nodeurl_path):
        # what is our webport?
        s = self.listener
        if isinstance(s, internet.TCPServer):
            base_url = "http://localhost:%d" % s._port.getHost().port
        elif isinstance(s, internet.SSLServer):
            base_url = "https://localhost:%d" % s._port.getHost().port
        else:
            base_url = None
        if base_url:
            f = open(nodeurl_path, 'wb')
            # this file is world-readable
            f.write(base_url + "\n")
            f.close()

