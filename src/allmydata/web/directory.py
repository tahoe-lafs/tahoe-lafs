
import simplejson
import urllib

from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IPushProducer
from twisted.python.failure import Failure
from twisted.web import http, html
from nevow import url, rend, inevow, tags as T
from nevow.inevow import IRequest

from foolscap.api import fireEventually

from allmydata.util import base32, time_format
from allmydata.uri import from_string_dirnode
from allmydata.interfaces import IDirectoryNode, IFileNode, IFilesystemNode, \
     IImmutableFileNode, IMutableFileNode, ExistingChildError, \
     NoSuchChildError, EmptyPathnameComponentError
from allmydata.monitor import Monitor, OperationCancelledError
from allmydata import dirnode
from allmydata.web.common import text_plain, WebError, \
     IOpHandleTable, NeedOperationHandleError, \
     boolean_of_arg, get_arg, get_root, parse_replace_arg, \
     should_create_intermediate_directories, \
     getxmlfile, RenderMixin, humanize_failure, convert_children_json
from allmydata.web.filenode import ReplaceMeMixin, \
     FileNodeHandler, PlaceHolderNodeHandler
from allmydata.web.check_results import CheckResults, \
     CheckAndRepairResults, DeepCheckResults, DeepCheckAndRepairResults, \
     LiteralCheckResults
from allmydata.web.info import MoreInfo
from allmydata.web.operations import ReloadMixin
from allmydata.web.check_results import json_check_results, \
     json_check_and_repair_results

class BlockingFileError(Exception):
    # TODO: catch and transform
    """We cannot auto-create a parent directory, because there is a file in
    the way"""

def make_handler_for(node, client, parentnode=None, name=None):
    if parentnode:
        assert IDirectoryNode.providedBy(parentnode)
    if IFileNode.providedBy(node):
        return FileNodeHandler(client, node, parentnode, name)
    if IDirectoryNode.providedBy(node):
        return DirectoryNodeHandler(client, node, parentnode, name)
    return UnknownNodeHandler(client, node, parentnode, name)

class DirectoryNodeHandler(RenderMixin, rend.Page, ReplaceMeMixin):
    addSlash = True

    def __init__(self, client, node, parentnode=None, name=None):
        rend.Page.__init__(self)
        self.client = client
        assert node
        self.node = node
        self.parentnode = parentnode
        self.name = name

    def childFactory(self, ctx, name):
        name = name.decode("utf-8")
        if not name:
            raise EmptyPathnameComponentError()
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
                    d = self.node.create_subdirectory(name)
                    d.addCallback(make_handler_for,
                                  self.client, self.node, name)
                    return d
            else:
                if DEBUG: print " terminal"
                # terminal node
                if (method,t) in [ ("POST","mkdir"), ("PUT","mkdir"),
                                   ("POST", "mkdir-with-children"),
                                   ("POST", "mkdir-immutable") ]:
                    if DEBUG: print " making final directory"
                    # final directory
                    kids = {}
                    if t in ("mkdir-with-children", "mkdir-immutable"):
                        req.content.seek(0)
                        kids_json = req.content.read()
                        kids = convert_children_json(self.client.nodemaker,
                                                     kids_json)
                    mutable = True
                    if t == "mkdir-immutable":
                        mutable = False
                    d = self.node.create_subdirectory(name, kids,
                                                      mutable=mutable)
                    d.addCallback(make_handler_for,
                                  self.client, self.node, name)
                    return d
                if (method,t) in ( ("PUT",""), ("PUT","uri"), ):
                    if DEBUG: print " PUT, making leaf placeholder"
                    # we were trying to find the leaf filenode (to put a new
                    # file in its place), and it didn't exist. That's ok,
                    # since that's the leaf node that we're about to create.
                    # We make a dummy one, which will respond to the PUT
                    # request by replacing itself.
                    return PlaceHolderNodeHandler(self.client, self.node, name)
            if DEBUG: print " 404"
            # otherwise, we just return a no-such-child error
            return f

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
        return make_handler_for(node, self.client, self.node, name)

    def render_DELETE(self, ctx):
        assert self.parentnode and self.name
        d = self.parentnode.delete(self.name)
        d.addCallback(lambda res: self.node.get_uri())
        return d

    def render_GET(self, ctx):
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
        replace = parse_replace_arg(get_arg(req, "replace", "true"))

        if t == "mkdir":
            # our job was done by the traversal/create-intermediate-directory
            # process that got us here.
            return text_plain(self.node.get_uri(), ctx) # TODO: urlencode
        if t == "uri":
            if not replace:
                # they're trying to set_uri and that name is already occupied
                # (by us).
                raise ExistingChildError()
            d = self.replace_me_with_a_childcap(req, self.client, replace)
            # TODO: results
            return d

        raise WebError("PUT to a directory")

    def render_POST(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()

        if t == "mkdir":
            d = self._POST_mkdir(req)
        elif t == "mkdir-with-children":
            d = self._POST_mkdir_with_children(req)
        elif t == "mkdir-immutable":
            d = self._POST_mkdir_immutable(req)
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
        elif t == "stream-deep-check":
            d = self._POST_stream_deep_check(ctx)
        elif t == "start-manifest":
            d = self._POST_start_manifest(ctx)
        elif t == "start-deep-size":
            d = self._POST_start_deep_size(ctx)
        elif t == "start-deep-stats":
            d = self._POST_start_deep_stats(ctx)
        elif t == "stream-manifest":
            d = self._POST_stream_manifest(ctx)
        elif t == "set_children":
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
        kids = {}
        d = self.node.create_subdirectory(name, kids, overwrite=replace)
        d.addCallback(lambda child: child.get_uri()) # TODO: urlencode
        return d

    def _POST_mkdir_with_children(self, req):
        name = get_arg(req, "name", "")
        if not name:
            # our job is done, it was handled by the code in got_child
            # which created the final directory (i.e. us)
            return defer.succeed(self.node.get_uri()) # TODO: urlencode
        name = name.decode("utf-8")
        # TODO: decide on replace= behavior, see #903
        #replace = boolean_of_arg(get_arg(req, "replace", "false"))
        req.content.seek(0)
        kids_json = req.content.read()
        kids = convert_children_json(self.client.nodemaker, kids_json)
        d = self.node.create_subdirectory(name, kids, overwrite=False)
        d.addCallback(lambda child: child.get_uri()) # TODO: urlencode
        return d

    def _POST_mkdir_immutable(self, req):
        name = get_arg(req, "name", "")
        if not name:
            # our job is done, it was handled by the code in got_child
            # which created the final directory (i.e. us)
            return defer.succeed(self.node.get_uri()) # TODO: urlencode
        name = name.decode("utf-8")
        # TODO: decide on replace= behavior, see #903
        #replace = boolean_of_arg(get_arg(req, "replace", "false"))
        req.content.seek(0)
        kids_json = req.content.read()
        kids = convert_children_json(self.client.nodemaker, kids_json)
        d = self.node.create_subdirectory(name, kids, overwrite=False, mutable=False)
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
            return node.create_subdirectory(path[0])
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
                return PlaceHolderNodeHandler(self.client, self.node, name)
            else:
                node = node_or_failure
                return make_handler_for(node, self.client, self.node, name)
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
        d = self.node.set_uri(name, childcap, childcap, overwrite=replace)
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

    def _maybe_literal(self, res, Results_Class):
        if res:
            return Results_Class(self.client, res)
        return LiteralCheckResults(self.client)

    def _POST_check(self, req):
        # check this directory
        verify = boolean_of_arg(get_arg(req, "verify", "false"))
        repair = boolean_of_arg(get_arg(req, "repair", "false"))
        add_lease = boolean_of_arg(get_arg(req, "add-lease", "false"))
        if repair:
            d = self.node.check_and_repair(Monitor(), verify, add_lease)
            d.addCallback(self._maybe_literal, CheckAndRepairResults)
        else:
            d = self.node.check(Monitor(), verify, add_lease)
            d.addCallback(self._maybe_literal, CheckResults)
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
        add_lease = boolean_of_arg(get_arg(ctx, "add-lease", "false"))
        if repair:
            monitor = self.node.start_deep_check_and_repair(verify, add_lease)
            renderer = DeepCheckAndRepairResults(self.client, monitor)
        else:
            monitor = self.node.start_deep_check(verify, add_lease)
            renderer = DeepCheckResults(self.client, monitor)
        return self._start_operation(monitor, renderer, ctx)

    def _POST_stream_deep_check(self, ctx):
        verify = boolean_of_arg(get_arg(ctx, "verify", "false"))
        repair = boolean_of_arg(get_arg(ctx, "repair", "false"))
        add_lease = boolean_of_arg(get_arg(ctx, "add-lease", "false"))
        walker = DeepCheckStreamer(ctx, self.node, verify, repair, add_lease)
        monitor = self.node.deep_traverse(walker)
        walker.setMonitor(monitor)
        # register to hear stopProducing. The walker ignores pauseProducing.
        IRequest(ctx).registerProducer(walker, True)
        d = monitor.when_done()
        def _done(res):
            IRequest(ctx).unregisterProducer()
            return res
        d.addBoth(_done)
        def _cancelled(f):
            f.trap(OperationCancelledError)
            return "Operation Cancelled"
        d.addErrback(_cancelled)
        def _error(f):
            # signal the error as a non-JSON "ERROR:" line, plus exception
            msg = "ERROR: %s(%s)\n" % (f.value.__class__.__name__,
                                       ", ".join([str(a) for a in f.value.args]))
            msg += str(f)
            return msg
        d.addErrback(_error)
        return d

    def _POST_start_manifest(self, ctx):
        if not get_arg(ctx, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.build_manifest()
        renderer = ManifestResults(self.client, monitor)
        return self._start_operation(monitor, renderer, ctx)

    def _POST_start_deep_size(self, ctx):
        if not get_arg(ctx, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.start_deep_stats()
        renderer = DeepSizeResults(self.client, monitor)
        return self._start_operation(monitor, renderer, ctx)

    def _POST_start_deep_stats(self, ctx):
        if not get_arg(ctx, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.start_deep_stats()
        renderer = DeepStatsResults(self.client, monitor)
        return self._start_operation(monitor, renderer, ctx)

    def _POST_stream_manifest(self, ctx):
        walker = ManifestStreamer(ctx, self.node)
        monitor = self.node.deep_traverse(walker)
        walker.setMonitor(monitor)
        # register to hear stopProducing. The walker ignores pauseProducing.
        IRequest(ctx).registerProducer(walker, True)
        d = monitor.when_done()
        def _done(res):
            IRequest(ctx).unregisterProducer()
            return res
        d.addBoth(_done)
        def _cancelled(f):
            f.trap(OperationCancelledError)
            return "Operation Cancelled"
        d.addErrback(_cancelled)
        def _error(f):
            # signal the error as a non-JSON "ERROR:" line, plus exception
            msg = "ERROR: %s(%s)\n" % (f.value.__class__.__name__,
                                       ", ".join([str(a) for a in f.value.args]))
            msg += str(f)
            return msg
        d.addErrback(_error)
        return d

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
        cs = {}
        for name, (file_or_dir, mddict) in children.iteritems():
            name = unicode(name) # simplejson-2.0.1 returns str *or* unicode
            writecap = mddict.get('rw_uri')
            if writecap is not None:
                writecap = str(writecap)
            readcap = mddict.get('ro_uri')
            if readcap is not None:
                readcap = str(readcap)
            cs[name] = (writecap, readcap, mddict.get('metadata'))
        d = self.node.set_children(cs, replace)
        d.addCallback(lambda res: "Okay so I did it.")
        # TODO: results
        return d

def abbreviated_dirnode(dirnode):
    u = from_string_dirnode(dirnode.get_uri())
    return u.abbrev_si()

class DirectoryAsHTML(rend.Page):
    # The remainder of this class is to render the directory into
    # human+browser -oriented HTML.
    docFactory = getxmlfile("directory.xhtml")
    addSlash = True

    def __init__(self, node):
        rend.Page.__init__(self)
        self.node = node

    def beforeRender(self, ctx):
        # attempt to get the dirnode's children, stashing them (or the
        # failure that results) for later use
        d = self.node.list()
        def _good(children):
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
            for i,item in enumerate(sorted(children.items())):
                if i % 100 == 0:
                    output.append(fireEventually(item))
                else:
                    output.append(item)
            self.dirnode_children = output
            return ctx
        def _bad(f):
            text, code = humanize_failure(f)
            self.dirnode_children = None
            self.dirnode_children_error = text
            return ctx
        d.addCallbacks(_good, _bad)
        return d

    def render_title(self, ctx, data):
        si_s = abbreviated_dirnode(self.node)
        header = ["Tahoe-LAFS - Directory SI=%s" % si_s]
        if self.node.is_readonly():
            header.append(" (read-only)")
        else:
            header.append(" (modifiable)")
        return ctx.tag[header]

    def render_header(self, ctx, data):
        si_s = abbreviated_dirnode(self.node)
        header = ["Tahoe-LAFS Directory SI=", T.span(class_="data-chars")[si_s]]
        if self.node.is_readonly():
            header.append(" (read-only)")
        return ctx.tag[header]

    def render_welcome(self, ctx, data):
        link = get_root(ctx)
        return T.div[T.a(href=link)["Return to Welcome page"]]

    def render_show_readonly(self, ctx, data):
        if self.node.is_readonly():
            return ""
        rocap = self.node.get_readonly_uri()
        root = get_root(ctx)
        uri_link = "%s/uri/%s/" % (root, urllib.quote(rocap))
        return ctx.tag[T.a(href=uri_link)["Read-Only Version"]]

    def render_try_children(self, ctx, data):
        # if the dirnode can be retrived, render a table of children.
        # Otherwise, render an apologetic error message.
        if self.dirnode_children is not None:
            return ctx.tag
        else:
            return T.div[T.p["Error reading directory:"],
                         T.p[self.dirnode_children_error]]

    def data_children(self, ctx, data):
        return self.dirnode_children

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
        linkcrtime = metadata.get('tahoe', {}).get("linkcrtime")
        if linkcrtime is not None:
            times.append("lcr: " + time_format.iso_local(linkcrtime))
        else:
            # For backwards-compatibility with links last modified by Tahoe < 1.4.0:
            if "ctime" in metadata:
                ctime = time_format.iso_local(metadata["ctime"])
                times.append("c: " + ctime)
        linkmotime = metadata.get('tahoe', {}).get("linkmotime")
        if linkmotime is not None:
            if times:
                times.append(T.br())
            times.append("lmo: " + time_format.iso_local(linkmotime))
        else:
            # For backwards-compatibility with links last modified by Tahoe < 1.4.0:
            if "mtime" in metadata:
                mtime = time_format.iso_local(metadata["mtime"])
                if times:
                    times.append(T.br())
                times.append("m: " + mtime)
        ctx.fillSlots("times", times)

        assert IFilesystemNode.providedBy(target), target
        writecap = target.get_uri() or ""
        quoted_uri = urllib.quote(writecap, safe="") # escape slashes too

        if IMutableFileNode.providedBy(target):
            # to prevent javascript in displayed .html files from stealing a
            # secret directory URI from the URL, send the browser to a URI-based
            # page that doesn't know about the directory at all
            dlurl = "%s/file/%s/@@named=/%s" % (root, quoted_uri, nameurl)

            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "SSK")

            ctx.fillSlots("size", "?")

            info_link = "%s/uri/%s?t=info" % (root, quoted_uri)

        elif IImmutableFileNode.providedBy(target):
            dlurl = "%s/file/%s/@@named=/%s" % (root, quoted_uri, nameurl)

            ctx.fillSlots("filename",
                          T.a(href=dlurl)[html.escape(name)])
            ctx.fillSlots("type", "FILE")

            ctx.fillSlots("size", target.get_size())

            info_link = "%s/uri/%s?t=info" % (root, quoted_uri)

        elif IDirectoryNode.providedBy(target):
            # directory
            uri_link = "%s/uri/%s/" % (root, urllib.quote(writecap))
            ctx.fillSlots("filename",
                          T.a(href=uri_link)[html.escape(name)])
            if not target.is_mutable():
                dirtype = "DIR-IMM"
            elif target.is_readonly():
                dirtype = "DIR-RO"
            else:
                dirtype = "DIR"
            ctx.fillSlots("type", dirtype)
            ctx.fillSlots("size", "-")
            info_link = "%s/uri/%s/?t=info" % (root, quoted_uri)

        else:
            # unknown
            ctx.fillSlots("filename", html.escape(name))
            ctx.fillSlots("type", "?")
            ctx.fillSlots("size", "-")
            # use a directory-relative info link, so we can extract both the
            # writecap and the readcap
            info_link = "%s?t=info" % urllib.quote(name)

        ctx.fillSlots("info", T.a(href=info_link)["More Info"])

        return ctx.tag

    def render_forms(self, ctx, data):
        forms = []

        if self.node.is_readonly():
            return T.div["No upload forms: directory is read-only"]
        if self.dirnode_children is None:
            return T.div["No upload forms: directory is unreadable"]

        mkdir = T.form(action=".", method="post",
                       enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="mkdir"),
            T.input(type="hidden", name="when_done", value="."),
            T.legend(class_="freeform-form-label")["Create a new directory in this directory"],
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
            T.legend(class_="freeform-form-label")["Add a link to a file or directory which is already in Tahoe-LAFS."],
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
            assert IFilesystemNode.providedBy(childnode), childnode
            rw_uri = childnode.get_uri()
            ro_uri = childnode.get_readonly_uri()
            if IFileNode.providedBy(childnode):
                if childnode.is_readonly():
                    rw_uri = None
                kiddata = ("filenode", {'size': childnode.get_size(),
                                        'mutable': childnode.is_mutable(),
                                        })
            elif IDirectoryNode.providedBy(childnode):
                if childnode.is_readonly():
                    rw_uri = None
                kiddata = ("dirnode", {'mutable': childnode.is_mutable()})
            else:
                kiddata = ("unknown", {})
            kiddata[1]["metadata"] = metadata
            if ro_uri:
                kiddata[1]["ro_uri"] = ro_uri
            if rw_uri:
                kiddata[1]["rw_uri"] = rw_uri
            verifycap = childnode.get_verify_cap()
            if verifycap:
                kiddata[1]['verify_uri'] = verifycap.to_string()
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
        verifycap = dirnode.get_verify_cap()
        if verifycap:
            contents['verify_uri'] = verifycap.to_string()
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

    def __init__(self, client, monitor):
        self.client = client
        self.monitor = monitor

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        output = get_arg(req, "output", "html").lower()
        if output == "text":
            return self.text(req)
        if output == "json":
            return self.json(req)
        return rend.Page.renderHTTP(self, ctx)

    def slashify_path(self, path):
        if not path:
            return ""
        return "/".join([p.encode("utf-8") for p in path])

    def text(self, req):
        req.setHeader("content-type", "text/plain")
        lines = []
        is_finished = self.monitor.is_finished()
        lines.append("finished: " + {True: "yes", False: "no"}[is_finished])
        for (path, cap) in self.monitor.get_status()["manifest"]:
            lines.append(self.slashify_path(path) + " " + cap)
        return "\n".join(lines) + "\n"

    def json(self, req):
        req.setHeader("content-type", "text/plain")
        m = self.monitor
        s = m.get_status()

        status = { "stats": s["stats"],
                   "finished": m.is_finished(),
                   "origin": base32.b2a(m.origin_si),
                   }
        if m.is_finished():
            # don't return manifest/verifycaps/SIs unless the operation is
            # done, to save on CPU/memory (both here and in the HTTP client
            # who has to unpack the JSON). Tests show that the ManifestWalker
            # needs about 1092 bytes per item, the JSON we generate here
            # requires about 503 bytes per item, and some internal overhead
            # (perhaps transport-layer buffers in twisted.web?) requires an
            # additional 1047 bytes per item.
            status.update({ "manifest": s["manifest"],
                            "verifycaps": [i for i in s["verifycaps"]],
                            "storage-index": [i for i in s["storage-index"]],
                            })
            # simplejson doesn't know how to serialize a set. We use a
            # generator that walks the set rather than list(setofthing) to
            # save a small amount of memory (4B*len) and a moderate amount of
            # CPU.
        return simplejson.dumps(status, indent=1)

    def _si_abbrev(self):
        return base32.b2a(self.monitor.origin_si)[:6]

    def render_title(self, ctx):
        return T.title["Manifest of SI=%s" % self._si_abbrev()]

    def render_header(self, ctx):
        return T.p["Manifest of SI=%s" % self._si_abbrev()]

    def data_items(self, ctx, data):
        return self.monitor.get_status()["manifest"]

    def render_row(self, ctx, (path, cap)):
        ctx.fillSlots("path", self.slashify_path(path))
        root = get_root(ctx)
        # TODO: we need a clean consistent way to get the type of a cap string
        if cap:
            if cap.startswith("URI:CHK") or cap.startswith("URI:SSK"):
                nameurl = urllib.quote(path[-1].encode("utf-8"))
                uri_link = "%s/file/%s/@@named=/%s" % (root, urllib.quote(cap),
                                                       nameurl)
            else:
                uri_link = "%s/uri/%s" % (root, urllib.quote(cap, safe=""))
            ctx.fillSlots("cap", T.a(href=uri_link)[cap])
        else:
            ctx.fillSlots("cap", "")
        return ctx.tag

class DeepSizeResults(rend.Page):
    def __init__(self, client, monitor):
        self.client = client
        self.monitor = monitor

    def renderHTTP(self, ctx):
        req = inevow.IRequest(ctx)
        output = get_arg(req, "output", "html").lower()
        req.setHeader("content-type", "text/plain")
        if output == "json":
            return self.json(req)
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

    def json(self, req):
        status = {"finished": self.monitor.is_finished(),
                  "size": self.monitor.get_status(),
                  }
        return simplejson.dumps(status)

class DeepStatsResults(rend.Page):
    def __init__(self, client, monitor):
        self.client = client
        self.monitor = monitor

    def renderHTTP(self, ctx):
        # JSON only
        inevow.IRequest(ctx).setHeader("content-type", "text/plain")
        s = self.monitor.get_status().copy()
        s["finished"] = self.monitor.is_finished()
        return simplejson.dumps(s, indent=1)

class ManifestStreamer(dirnode.DeepStats):
    implements(IPushProducer)

    def __init__(self, ctx, origin):
        dirnode.DeepStats.__init__(self, origin)
        self.req = IRequest(ctx)

    def setMonitor(self, monitor):
        self.monitor = monitor
    def pauseProducing(self):
        pass
    def resumeProducing(self):
        pass
    def stopProducing(self):
        self.monitor.cancel()

    def add_node(self, node, path):
        dirnode.DeepStats.add_node(self, node, path)
        d = {"path": path,
             "cap": node.get_uri()}

        if IDirectoryNode.providedBy(node):
            d["type"] = "directory"
        elif IFileNode.providedBy(node):
            d["type"] = "file"
        else:
            d["type"] = "unknown"

        v = node.get_verify_cap()
        if v:
            v = v.to_string()
        d["verifycap"] = v

        r = node.get_repair_cap()
        if r:
            r = r.to_string()
        d["repaircap"] = r

        si = node.get_storage_index()
        if si:
            si = base32.b2a(si)
        d["storage-index"] = si

        j = simplejson.dumps(d, ensure_ascii=True)
        assert "\n" not in j
        self.req.write(j+"\n")

    def finish(self):
        stats = dirnode.DeepStats.get_results(self)
        d = {"type": "stats",
             "stats": stats,
             }
        j = simplejson.dumps(d, ensure_ascii=True)
        assert "\n" not in j
        self.req.write(j+"\n")
        return ""

class DeepCheckStreamer(dirnode.DeepStats):
    implements(IPushProducer)

    def __init__(self, ctx, origin, verify, repair, add_lease):
        dirnode.DeepStats.__init__(self, origin)
        self.req = IRequest(ctx)
        self.verify = verify
        self.repair = repair
        self.add_lease = add_lease

    def setMonitor(self, monitor):
        self.monitor = monitor
    def pauseProducing(self):
        pass
    def resumeProducing(self):
        pass
    def stopProducing(self):
        self.monitor.cancel()

    def add_node(self, node, path):
        dirnode.DeepStats.add_node(self, node, path)
        data = {"path": path,
                "cap": node.get_uri()}

        if IDirectoryNode.providedBy(node):
            data["type"] = "directory"
        else:
            data["type"] = "file"

        v = node.get_verify_cap()
        if v:
            v = v.to_string()
        data["verifycap"] = v

        r = node.get_repair_cap()
        if r:
            r = r.to_string()
        data["repaircap"] = r

        si = node.get_storage_index()
        if si:
            si = base32.b2a(si)
        data["storage-index"] = si

        if self.repair:
            d = node.check_and_repair(self.monitor, self.verify, self.add_lease)
            d.addCallback(self.add_check_and_repair, data)
        else:
            d = node.check(self.monitor, self.verify, self.add_lease)
            d.addCallback(self.add_check, data)
        d.addCallback(self.write_line)
        return d

    def add_check_and_repair(self, crr, data):
        data["check-and-repair-results"] = json_check_and_repair_results(crr)
        return data

    def add_check(self, cr, data):
        data["check-results"] = json_check_results(cr)
        return data

    def write_line(self, data):
        j = simplejson.dumps(data, ensure_ascii=True)
        assert "\n" not in j
        self.req.write(j+"\n")

    def finish(self):
        stats = dirnode.DeepStats.get_results(self)
        d = {"type": "stats",
             "stats": stats,
             }
        j = simplejson.dumps(d, ensure_ascii=True)
        assert "\n" not in j
        self.req.write(j+"\n")
        return ""

class UnknownNodeHandler(RenderMixin, rend.Page):

    def __init__(self, client, node, parentnode=None, name=None):
        rend.Page.__init__(self)
        assert node
        self.node = node

    def render_GET(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        if t == "info":
            return MoreInfo(self.node)
        raise WebError("GET unknown URI type: can only do t=info, not t=%s" % t)


