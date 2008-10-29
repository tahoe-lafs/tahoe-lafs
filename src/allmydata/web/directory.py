
import simplejson
import urllib
import time

from twisted.internet import defer
from twisted.python.failure import Failure
from twisted.web import http, html
from nevow import url, rend, inevow, tags as T
from nevow.inevow import IRequest

from foolscap.eventual import fireEventually

from allmydata.util import base32
from allmydata.uri import from_string_dirnode
from allmydata.interfaces import IDirectoryNode, IFileNode, IMutableFileNode, \
     ExistingChildError, NoSuchChildError
from allmydata.monitor import Monitor
from allmydata.web.common import text_plain, WebError, \
     IClient, IOpHandleTable, NeedOperationHandleError, \
     boolean_of_arg, get_arg, get_root, \
     should_create_intermediate_directories, \
     getxmlfile, RenderMixin
from allmydata.web.filenode import ReplaceMeMixin, \
     FileNodeHandler, PlaceHolderNodeHandler
from allmydata.web.checker_results import CheckerResults, \
     CheckAndRepairResults, DeepCheckResults, DeepCheckAndRepairResults
from allmydata.web.info import MoreInfo
from allmydata.web.operations import ReloadMixin

class BlockingFileError(Exception):
    # TODO: catch and transform
    """We cannot auto-create a parent directory, because there is a file in
    the way"""

def make_handler_for(node, parentnode=None, name=None):
    if parentnode:
        assert IDirectoryNode.providedBy(parentnode)
    if IMutableFileNode.providedBy(node):
        return FileNodeHandler(node, parentnode, name)
    if IFileNode.providedBy(node):
        return FileNodeHandler(node, parentnode, name)
    if IDirectoryNode.providedBy(node):
        return DirectoryNodeHandler(node, parentnode, name)
    raise WebError("Cannot provide handler for '%s'" % node)

class DirectoryNodeHandler(RenderMixin, rend.Page, ReplaceMeMixin):
    addSlash = True

    def __init__(self, node, parentnode=None, name=None):
        rend.Page.__init__(self)
        assert node
        self.node = node
        self.parentnode = parentnode
        self.name = name

    def childFactory(self, ctx, name):
        req = IRequest(ctx)
        name = name.decode("utf-8")
        d = self.node.get(name)
        d.addBoth(self.got_child, ctx, name)
        # got_child returns a handler resource: FileNodeHandler or
        # DirectoryNodeHandler
        return d

    def got_child(self, node_or_failure, ctx, name):
        DEBUG = False
        if DEBUG: print "GOT_CHILD", name, node_or_failure
        req = IRequest(ctx)
        method = req.method
        nonterminal = len(req.postpath) > 1
        t = get_arg(req, "t", "").strip()
        if isinstance(node_or_failure, Failure):
            f = node_or_failure
            f.trap(NoSuchChildError)
            # No child by this name. What should we do about it?
            if DEBUG: print "no child", name
            if DEBUG: print "postpath", req.postpath
            if nonterminal:
                if DEBUG: print " intermediate"
                if should_create_intermediate_directories(req):
                    # create intermediate directories
                    if DEBUG: print " making intermediate directory"
                    d = self.node.create_empty_directory(name)
                    d.addCallback(make_handler_for, self.node, name)
                    return d
            else:
                if DEBUG: print " terminal"
                # terminal node
                if (method,t) in [ ("POST","mkdir"), ("PUT","mkdir") ]:
                    if DEBUG: print " making final directory"
                    # final directory
                    d = self.node.create_empty_directory(name)
                    d.addCallback(make_handler_for, self.node, name)
                    return d
                if (method,t) in ( ("PUT",""), ("PUT","uri"), ):
                    if DEBUG: print " PUT, making leaf placeholder"
                    # we were trying to find the leaf filenode (to put a new
                    # file in its place), and it didn't exist. That's ok,
                    # since that's the leaf node that we're about to create.
                    # We make a dummy one, which will respond to the PUT
                    # request by replacing itself.
                    return PlaceHolderNodeHandler(self.node, name)
            if DEBUG: print " 404"
            # otherwise, we just return a no-such-child error
            return rend.FourOhFour()

        node = node_or_failure
        if nonterminal and should_create_intermediate_directories(req):
            if not IDirectoryNode.providedBy(node):
                # we would have put a new directory here, but there was a
                # file in the way.
                if DEBUG: print "blocking"
                raise WebError("Unable to create directory '%s': "
                               "a file was in the way" % name,
                               http.CONFLICT)
        if DEBUG: print "good child"
        return make_handler_for(node, self.node, name)

    def render_DELETE(self, ctx):
        assert self.parentnode and self.name
        d = self.parentnode.delete(self.name)
        d.addCallback(lambda res: self.node.get_uri())
        return d

    def render_GET(self, ctx):
        client = IClient(ctx)
        req = IRequest(ctx)
        # This is where all of the directory-related ?t=* code goes.
        t = get_arg(req, "t", "").strip()
        if not t:
            # render the directory as HTML, using the docFactory and Nevow's
            # whole templating thing.
            return DirectoryAsHTML(self.node)

        if t == "json":
            return DirectoryJSONMetadata(ctx, self.node)
        if t == "info":
            return MoreInfo(self.node)
        if t == "uri":
            return DirectoryURI(ctx, self.node)
        if t == "readonly-uri":
            return DirectoryReadonlyURI(ctx, self.node)
        if t == 'rename-form':
            return RenameForm(self.node)

        raise WebError("GET directory: bad t=%s" % t)

    def render_PUT(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        if t == "mkdir":
            # our job was done by the traversal/create-intermediate-directory
            # process that got us here.
            return text_plain(self.node.get_uri(), ctx) # TODO: urlencode
        if t == "uri":
            if not replace:
                # they're trying to set_uri and that name is already occupied
                # (by us).
                raise ExistingChildError()
            d = self.replace_me_with_a_childcap(ctx, replace)
            # TODO: results
            return d

        raise WebError("PUT to a directory")

    def render_POST(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()

        if t == "mkdir":
            d = self._POST_mkdir(req)
        elif t == "mkdir-p":
            # TODO: docs, tests
            d = self._POST_mkdir_p(req)
        elif t == "upload":
            d = self._POST_upload(ctx) # this one needs the context
        elif t == "uri":
            d = self._POST_uri(req)
        elif t == "delete":
            d = self._POST_delete(req)
        elif t == "rename":
            d = self._POST_rename(req)
        elif t == "check":
            d = self._POST_check(req)
        elif t == "start-deep-check":
            d = self._POST_start_deep_check(ctx)
        elif t == "start-manifest":
            d = self._POST_start_manifest(ctx)
        elif t == "start-deep-size":
            d = self._POST_start_deep_size(ctx)
        elif t == "start-deep-stats":
            d = self._POST_start_deep_stats(ctx)
        elif t == "set_children":
            # TODO: docs
            d = self._POST_set_children(req)
        else:
            raise WebError("POST to a directory with bad t=%s" % t)

        when_done = get_arg(req, "when_done", None)
        if when_done:
            d.addCallback(lambda res: url.URL.fromString(when_done))
        return d

    def _POST_mkdir(self, req):
        name = get_arg(req, "name", "")
        if not name:
            # our job is done, it was handled by the code in got_child
            # which created the final directory (i.e. us)
            return defer.succeed(self.node.get_uri()) # TODO: urlencode
        name = name.decode("utf-8")
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        d = self.node.create_empty_directory(name, overwrite=replace)
        d.addCallback(lambda child: child.get_uri()) # TODO: urlencode
        return d

    def _POST_mkdir_p(self, req):
        path = get_arg(req, "path")
        if not path:
            raise WebError("mkdir-p requires a path")
        path_ = tuple([seg.decode("utf-8") for seg in path.split('/') if seg ])
        # TODO: replace
        d = self._get_or_create_directories(self.node, path_)
        d.addCallback(lambda node: node.get_uri())
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
            f.trap(NoSuchChildError)
            return node.create_empty_directory(path[0])
        d.addErrback(_maybe_create)
        d.addCallback(self._get_or_create_directories, path[1:])
        return d

    def _POST_upload(self, ctx):
        req = IRequest(ctx)
        charset = get_arg(req, "_charset", "utf-8")
        contents = req.fields["file"]
        assert contents.filename is None or isinstance(contents.filename, str)
        name = get_arg(req, "name")
        name = name or contents.filename
        if name is not None:
            name = name.strip()
        if not name:
            # this prohibts empty, missing, and all-whitespace filenames
            raise WebError("upload requires a name")
        assert isinstance(name, str)
        name = name.decode(charset)
        if "/" in name:
            raise WebError("name= may not contain a slash", http.BAD_REQUEST)
        assert isinstance(name, unicode)

        # since POST /uri/path/file?t=upload is equivalent to
        # POST /uri/path/dir?t=upload&name=foo, just do the same thing that
        # childFactory would do. Things are cleaner if we only do a subset of
        # them, though, so we don't do: d = self.childFactory(ctx, name)

        d = self.node.get(name)
        def _maybe_got_node(node_or_failure):
            if isinstance(node_or_failure, Failure):
                f = node_or_failure
                f.trap(NoSuchChildError)
                # create a placeholder which will see POST t=upload
                return PlaceHolderNodeHandler(self.node, name)
            else:
                node = node_or_failure
                return make_handler_for(node, self.node, name)
        d.addBoth(_maybe_got_node)
        # now we have a placeholder or a filenodehandler, and we can just
        # delegate to it. We could return the resource back out of
        # DirectoryNodeHandler.renderHTTP, and nevow would recurse into it,
        # but the addCallback() that handles when_done= would break.
        d.addCallback(lambda child: child.renderHTTP(ctx))
        return d

    def _POST_uri(self, req):
        childcap = get_arg(req, "uri")
        if not childcap:
            raise WebError("set-uri requires a uri")
        name = get_arg(req, "name")
        if not name:
            raise WebError("set-uri requires a name")
        charset = get_arg(req, "_charset", "utf-8")
        name = name.decode(charset)
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        d = self.node.set_uri(name, childcap, overwrite=replace)
        d.addCallback(lambda res: childcap)
        return d

    def _POST_delete(self, req):
        name = get_arg(req, "name")
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
        charset = get_arg(req, "_charset", "utf-8")
        name = name.decode(charset)
        d = self.node.delete(name)
        d.addCallback(lambda res: "thing deleted")
        return d

    def _POST_rename(self, req):
        charset = get_arg(req, "_charset", "utf-8")
        from_name = get_arg(req, "from_name")
        if from_name is not None:
            from_name = from_name.strip()
            from_name = from_name.decode(charset)
            assert isinstance(from_name, unicode)
        to_name = get_arg(req, "to_name")
        if to_name is not None:
            to_name = to_name.strip()
            to_name = to_name.decode(charset)
            assert isinstance(to_name, unicode)
        if not from_name or not to_name:
            raise WebError("rename requires from_name and to_name")
        if from_name == to_name:
            return defer.succeed("redundant rename")

        # allow from_name to contain slashes, so they can fix names that were
        # accidentally created with them. But disallow them in to_name, to
        # discourage the practice.
        if "/" in to_name:
            raise WebError("to_name= may not contain a slash", http.BAD_REQUEST)

        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        d = self.node.move_child_to(from_name, self.node, to_name, replace)
        d.addCallback(lambda res: "thing renamed")
        return d

    def _POST_check(self, req):
        # check this directory
        verify = boolean_of_arg(get_arg(req, "verify", "false"))
        repair = boolean_of_arg(get_arg(req, "repair", "false"))
        if repair:
            d = self.node.check_and_repair(Monitor(), verify)
            d.addCallback(lambda res: CheckAndRepairResults(res))
        else:
            d = self.node.check(Monitor(), verify)
            d.addCallback(lambda res: CheckerResults(res))
        return d

    def _start_operation(self, monitor, renderer, ctx):
        table = IOpHandleTable(ctx)
        table.add_monitor(ctx, monitor, renderer)
        return table.redirect_to(ctx)

    def _POST_start_deep_check(self, ctx):
        # check this directory and everything reachable from it
        if not get_arg(ctx, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        verify = boolean_of_arg(get_arg(ctx, "verify", "false"))
        repair = boolean_of_arg(get_arg(ctx, "repair", "false"))
        if repair:
            monitor = self.node.start_deep_check_and_repair(verify)
            renderer = DeepCheckAndRepairResults(monitor)
        else:
            monitor = self.node.start_deep_check(verify)
            renderer = DeepCheckResults(monitor)
        return self._start_operation(monitor, renderer, ctx)

    def _POST_start_manifest(self, ctx):
        if not get_arg(ctx, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.build_manifest()
        renderer = ManifestResults(monitor)
        return self._start_operation(monitor, renderer, ctx)

    def _POST_start_deep_size(self, ctx):
        if not get_arg(ctx, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.start_deep_stats()
        renderer = DeepSizeResults(monitor)
        return self._start_operation(monitor, renderer, ctx)

    def _POST_start_deep_stats(self, ctx):
        if not get_arg(ctx, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.start_deep_stats()
        renderer = DeepStatsResults(monitor)
        return self._start_operation(monitor, renderer, ctx)

    def _POST_set_children(self, req):
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        req.content.seek(0)
        body = req.content.read()
        try:
            children = simplejson.loads(body)
        except ValueError, le:
            le.args = tuple(le.args + (body,))
            # TODO test handling of bad JSON
            raise
        cs = []
        for name, (file_or_dir, mddict) in children.iteritems():
            name = unicode(name) # simplejson-2.0.1 returns str *or* unicode
            cap = str(mddict.get('rw_uri') or mddict.get('ro_uri'))
            cs.append((name, cap, mddict.get('metadata')))
        d = self.node.set_children(cs, replace)
        d.addCallback(lambda res: "Okay so I did it.")
        # TODO: results
        return d

def abbreviated_dirnode(dirnode):
    u = from_string_dirnode(dirnode.get_uri())
    si = u.get_filenode_uri().storage_index
    si_s = base32.b2a(si)
    return si_s[:6]

class DirectoryAsHTML(rend.Page):
    # The remainder of this class is to render the directory into
    # human+browser -oriented HTML.
    docFactory = getxmlfile("directory.xhtml")
    addSlash = True

    def __init__(self, node):
        rend.Page.__init__(self)
        self.node = node

    def render_title(self, ctx, data):
        si_s = abbreviated_dirnode(self.node)
        header = ["Directory SI=%s" % si_s]
        return ctx.tag[header]

    def render_header(self, ctx, data):
        si_s = abbreviated_dirnode(self.node)
        header = ["Directory SI=%s" % si_s]
        if self.node.is_readonly():
            header.append(" (readonly)")
        return ctx.tag[header]

    def render_welcome(self, ctx, data):
        link = get_root(ctx)
        return T.div[T.a(href=link)["Return to Welcome page"]]

    def data_children(self, ctx, data):
        d = self.node.list()
        d.addCallback(lambda dict: sorted(dict.items()))
        def _stall_some(items):
            # Deferreds don't optimize out tail recursion, and the way
            # Nevow's flattener handles Deferreds doesn't take this into
            # account. As a result, large lists of Deferreds that fire in the
            # same turn (i.e. the output of defer.succeed) will cause a stack
            # overflow. To work around this, we insert a turn break after
            # every 100 items, using foolscap's fireEventually(). This gives
            # the stack a chance to be popped. It would also work to put
            # every item in its own turn, but that'd be a lot more
            # inefficient. This addresses ticket #237, for which I was never
            # able to create a failing unit test.
            output = []
            for i,item in enumerate(items):
                if i % 100 == 0:
                    output.append(fireEventually(item))
                else:
                    output.append(item)
            return output
        d.addCallback(_stall_some)
        return d

    def render_row(self, ctx, data):
        name, (target, metadata) = data
        name = name.encode("utf-8")
        assert not isinstance(name, unicode)
        nameurl = urllib.quote(name, safe="") # encode any slashes too

        root = get_root(ctx)
        here = "%s/uri/%s/" % (root, urllib.quote(self.node.get_uri()))
        if self.node.is_readonly():
            delete = "-"
            rename = "-"
        else:
            # this creates a button which will cause our child__delete method
            # to be invoked, which deletes the file and then redirects the
            # browser back to this directory
            delete = T.form(action=here, method="post")[
                T.input(type='hidden', name='t', value='delete'),
                T.input(type='hidden', name='name', value=name),
                T.input(type='hidden', name='when_done', value="."),
                T.input(type='submit', value='del', name="del"),
                ]

            rename = T.form(action=here, method="get")[
                T.input(type='hidden', name='t', value='rename-form'),
                T.input(type='hidden', name='name', value=name),
                T.input(type='hidden', name='when_done', value="."),
                T.input(type='submit', value='rename', name="rename"),
                ]

        ctx.fillSlots("delete", delete)
        ctx.fillSlots("rename", rename)

        times = []
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        if "ctime" in metadata:
            ctime = time.strftime(TIME_FORMAT,
                                  time.localtime(metadata["ctime"]))
            times.append("c: " + ctime)
        if "mtime" in metadata:
            mtime = time.strftime(TIME_FORMAT,
                                  time.localtime(metadata["mtime"]))
            if times:
                times.append(T.br())
                times.append("m: " + mtime)
        ctx.fillSlots("times", times)

        assert (IFileNode.providedBy(target)
                or IDirectoryNode.providedBy(target)
                or IMutableFileNode.providedBy(target)), target

        quoted_uri = urllib.quote(target.get_uri())

        if IMutableFileNode.providedBy(target):
            # to prevent javascript in displayed .html files from stealing a
            # secret directory URI from the URL, send the browser to a URI-based
            # page that doesn't know about the directory at all
            dlurl = "%s/file/%s/@@named=/%s" % (root, quoted_uri, nameurl)

            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "SSK")

            ctx.fillSlots("size", "?")

            text_plain_url = "%s/file/%s/@@named=/foo.txt" % (root, quoted_uri)
            info_link = "%s?t=info" % nameurl

        elif IFileNode.providedBy(target):
            dlurl = "%s/file/%s/@@named=/%s" % (root, quoted_uri, nameurl)

            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "FILE")

            ctx.fillSlots("size", target.get_size())

            text_plain_url = "%s/file/%s/@@named=/foo.txt" % (root, quoted_uri)
            info_link = "%s?t=info" % nameurl

        elif IDirectoryNode.providedBy(target):
            # directory
            uri_link = "%s/uri/%s/" % (root, urllib.quote(target.get_uri()))
            ctx.fillSlots("filename",
                          T.a(href=uri_link)[html.escape(name)])
            if target.is_readonly():
                dirtype = "DIR-RO"
            else:
                dirtype = "DIR"
            ctx.fillSlots("type", dirtype)
            ctx.fillSlots("size", "-")
            info_link = "%s/?t=info" % nameurl

        ctx.fillSlots("info", T.a(href=info_link)["More Info"])

        return ctx.tag

    def render_forms(self, ctx, data):
        forms = []

        if self.node.is_readonly():
            forms.append(T.div["No upload forms: directory is read-only"])
            return forms

        mkdir = T.form(action=".", method="post",
                       enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="mkdir"),
            T.input(type="hidden", name="when_done", value="."),
            T.legend(class_="freeform-form-label")["Create a new directory"],
            "New directory name: ",
            T.input(type="text", name="name"), " ",
            T.input(type="submit", value="Create"),
            ]]
        forms.append(T.div(class_="freeform-form")[mkdir])

        upload = T.form(action=".", method="post",
                        enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="upload"),
            T.input(type="hidden", name="when_done", value="."),
            T.legend(class_="freeform-form-label")["Upload a file to this directory"],
            "Choose a file to upload: ",
            T.input(type="file", name="file", class_="freeform-input-file"),
            " ",
            T.input(type="submit", value="Upload"),
            " Mutable?:",
            T.input(type="checkbox", name="mutable"),
            ]]
        forms.append(T.div(class_="freeform-form")[upload])

        mount = T.form(action=".", method="post",
                        enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="uri"),
            T.input(type="hidden", name="when_done", value="."),
            T.legend(class_="freeform-form-label")["Attach a file or directory"
                                                   " (by URI) to this"
                                                   " directory"],
            "New child name: ",
            T.input(type="text", name="name"), " ",
            "URI of new child: ",
            T.input(type="text", name="uri"), " ",
            T.input(type="submit", value="Attach"),
            ]]
        forms.append(T.div(class_="freeform-form")[mount])
        return forms

    def render_results(self, ctx, data):
        req = IRequest(ctx)
        return get_arg(req, "results", "")


def DirectoryJSONMetadata(ctx, dirnode):
    d = dirnode.list()
    def _got(children):
        kids = {}
        for name, (childnode, metadata) in children.iteritems():
            if childnode.is_readonly():
                rw_uri = None
                ro_uri = childnode.get_uri()
            else:
                rw_uri = childnode.get_uri()
                ro_uri = childnode.get_readonly_uri()
            if IFileNode.providedBy(childnode):
                kiddata = ("filenode", {'size': childnode.get_size(),
                                        'metadata': metadata,
                                        })
            else:
                assert IDirectoryNode.providedBy(childnode), (childnode,
                                                              children,)
                kiddata = ("dirnode", {'metadata': metadata})
            if ro_uri:
                kiddata[1]["ro_uri"] = ro_uri
            if rw_uri:
                kiddata[1]["rw_uri"] = rw_uri
            kiddata[1]['mutable'] = childnode.is_mutable()
            kids[name] = kiddata
        if dirnode.is_readonly():
            drw_uri = None
            dro_uri = dirnode.get_uri()
        else:
            drw_uri = dirnode.get_uri()
            dro_uri = dirnode.get_readonly_uri()
        contents = { 'children': kids }
        if dro_uri:
            contents['ro_uri'] = dro_uri
        if drw_uri:
            contents['rw_uri'] = drw_uri
        contents['mutable'] = dirnode.is_mutable()
        data = ("dirnode", contents)
        return simplejson.dumps(data, indent=1) + "\n"
    d.addCallback(_got)
    d.addCallback(text_plain, ctx)
    return d



def DirectoryURI(ctx, dirnode):
    return text_plain(dirnode.get_uri(), ctx)

def DirectoryReadonlyURI(ctx, dirnode):
    return text_plain(dirnode.get_readonly_uri(), ctx)

class RenameForm(rend.Page):
    addSlash = True
    docFactory = getxmlfile("rename-form.xhtml")

    def render_title(self, ctx, data):
        return ctx.tag["Directory SI=%s" % abbreviated_dirnode(self.original)]

    def render_header(self, ctx, data):
        header = ["Rename "
                  "in directory SI=%s" % abbreviated_dirnode(self.original),
                  ]

        if self.original.is_readonly():
            header.append(" (readonly!)")
        header.append(":")
        return ctx.tag[header]

    def render_when_done(self, ctx, data):
        return T.input(type="hidden", name="when_done", value=".")

    def render_get_name(self, ctx, data):
        req = IRequest(ctx)
        name = get_arg(req, "name", "")
        ctx.tag.attributes['value'] = name
        return ctx.tag


class ManifestResults(rend.Page, ReloadMixin):
    docFactory = getxmlfile("manifest.xhtml")

    def __init__(self, monitor):
        self.monitor = monitor

    def renderHTTP(self, ctx):
        output = get_arg(inevow.IRequest(ctx), "output", "html").lower()
        if output == "text":
            return self.text(ctx)
        if output == "json":
            return self.json(ctx)
        return rend.Page.renderHTTP(self, ctx)

    def slashify_path(self, path):
        if not path:
            return ""
        return "/".join([p.encode("utf-8") for p in path])

    def text(self, ctx):
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        lines = []
        is_finished = self.monitor.is_finished()
        lines.append("finished: " + {True: "yes", False: "no"}[is_finished])
        for (path, cap) in self.monitor.get_status():
            lines.append(self.slashify_path(path) + " " + cap)
        return "\n".join(lines) + "\n"

    def json(self, ctx):
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        m = self.monitor
        status = {"manifest": m.get_status(),
                  "finished": m.is_finished(),
                  "origin": base32.b2a(m.origin_si),
                  }
        return simplejson.dumps(status, indent=1)

    def _si_abbrev(self):
        return base32.b2a(self.monitor.origin_si)[:6]

    def render_title(self, ctx):
        return T.title["Manifest of SI=%s" % self._si_abbrev()]

    def render_header(self, ctx):
        return T.p["Manifest of SI=%s" % self._si_abbrev()]

    def data_items(self, ctx, data):
        return self.monitor.get_status()

    def render_row(self, ctx, (path, cap)):
        ctx.fillSlots("path", self.slashify_path(path))
        root = get_root(ctx)
        # TODO: we need a clean consistent way to get the type of a cap string
        if cap.startswith("URI:CHK") or cap.startswith("URI:SSK"):
            nameurl = urllib.quote(path[-1].encode("utf-8"))
            uri_link = "%s/file/%s/@@named=/%s" % (root, urllib.quote(cap),
                                                   nameurl)
        else:
            uri_link = "%s/uri/%s" % (root, urllib.quote(cap))
        ctx.fillSlots("cap", T.a(href=uri_link)[cap])
        return ctx.tag

class DeepSizeResults(rend.Page):
    def __init__(self, monitor):
        self.monitor = monitor

    def renderHTTP(self, ctx):
        output = get_arg(inevow.IRequest(ctx), "output", "html").lower()
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        if output == "json":
            return self.json(ctx)
        # plain text
        is_finished = self.monitor.is_finished()
        output = "finished: " + {True: "yes", False: "no"}[is_finished] + "\n"
        if is_finished:
            stats = self.monitor.get_status()
            total = (stats.get("size-immutable-files", 0)
                     + stats.get("size-mutable-files", 0)
                     + stats.get("size-directories", 0))
            output += "size: %d\n" % total
        return output

    def json(self, ctx):
        status = {"finished": self.monitor.is_finished(),
                  "size": self.monitor.get_status(),
                  }
        return simplejson.dumps(status)

class DeepStatsResults(rend.Page):
    def __init__(self, monitor):
        self.monitor = monitor

    def renderHTTP(self, ctx):
        # JSON only
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        s = self.monitor.get_status().copy()
        s["finished"] = self.monitor.is_finished()
        return simplejson.dumps(s, indent=1)
