"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, max, min  # noqa: F401
    # Don't use Future's str so that we don't get leaks into bad byte formatting
    from past.builtins import unicode as str

from urllib.parse import quote as url_quote
from datetime import timedelta

from zope.interface import implementer
from twisted.internet import defer
from twisted.internet.interfaces import IPushProducer
from twisted.python.failure import Failure
from twisted.web import http
from twisted.web.resource import ErrorPage
from twisted.web.resource import Resource
from twisted.web.template import (
    Element,
    XMLFile,
    renderElement,
    renderer,
    tags,
)
from hyperlink import URL
from twisted.python.filepath import FilePath

from allmydata.util import base32, jsonbytes as json
from allmydata.util.encodingutil import (
    to_bytes,
    quote_output,
)
from allmydata.uri import (
    from_string_dirnode,
    from_string,
    CHKFileURI,
    WriteableSSKFileURI,
    ReadonlySSKFileURI,
)
from allmydata.interfaces import IDirectoryNode, IFileNode, IFilesystemNode, \
     IImmutableFileNode, IMutableFileNode, ExistingChildError, \
     NoSuchChildError, EmptyPathnameComponentError, SDMF_VERSION, MDMF_VERSION
from allmydata.blacklist import ProhibitedNode
from allmydata.monitor import Monitor, OperationCancelledError
from allmydata import dirnode
from allmydata.web.common import (
    text_plain,
    WebError,
    NeedOperationHandleError,
    boolean_of_arg,
    get_arg,
    get_root,
    parse_replace_arg,
    should_create_intermediate_directories,
    humanize_failure,
    humanize_exception,
    convert_children_json,
    get_format,
    get_mutable_type,
    get_filenode_metadata,
    render_time,
    MultiFormatResource,
    SlotsSequenceElement,
    exception_to_child,
    render_exception,
    handle_when_done,
)
from allmydata.web.filenode import ReplaceMeMixin, \
     FileNodeHandler, PlaceHolderNodeHandler
from allmydata.web.check_results import CheckResultsRenderer, \
     CheckAndRepairResultsRenderer, DeepCheckResultsRenderer, \
     DeepCheckAndRepairResultsRenderer, LiteralCheckResultsRenderer
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


class DirectoryNodeHandler(ReplaceMeMixin, Resource, object):

    def __init__(self, client, node, parentnode=None, name=None):
        super(DirectoryNodeHandler, self).__init__()
        self.client = client
        assert node
        self.node = node
        self.parentnode = parentnode
        self.name = name
        self._operations = client.get_web_service().get_operations()

    @exception_to_child
    def getChild(self, name, req):
        """
        Dynamically create a child for the given request and name
        """
        # trying to replicate what I have observed as Nevow behavior
        # for these nodes, which is that a URI like
        # "/uri/URI%3ADIR2%3Aj...vq/" (that is, with a trailing slash
        # or no further children) renders "this" page.  We also need
        # to reject "/uri/URI:DIR2:..//", so we look at postpath.
        name = name.decode('utf8')
        if not name and req.postpath != [b'']:
            return self

        # Rejecting URIs that contain empty path pieces (for example:
        # "/uri/URI:DIR2:../foo//new.txt" or "/uri/URI:DIR2:..//") was
        # the old Nevow behavior and it is encoded in the test suite;
        # we will follow suit.
        for segment in req.prepath:
            if not segment:
                raise EmptyPathnameComponentError()

        d = self.node.get(name)
        d.addBoth(self._got_child, req, name)
        return d

    def _got_child(self, node_or_failure, req, name):
        """
        Callback when self.node.get has returned, meaning we have received
        whatever child was requested -- that is `node.get` has
        returned something (maybe an error). This method then performs
        the rest of the work of the Twisted API getChild(): returning
        a suitable child resource to Twisted Web.
        """
        terminal = (req.prepath + req.postpath)[-1].decode('utf8') == name
        nonterminal = not terminal  #len(req.postpath) > 0

        t = str(get_arg(req, b"t", b"").strip(), "ascii")
        if isinstance(node_or_failure, Failure):
            f = node_or_failure
            f.trap(NoSuchChildError)
            # No child by this name. What should we do about it?
            if nonterminal:
                if should_create_intermediate_directories(req):
                    # create intermediate directories
                    d = self.node.create_subdirectory(name)
                    d.addCallback(make_handler_for,
                                  self.client, self.node, name)
                    return d
            else:
                # terminal node
                terminal_requests = (
                    (b"POST", "mkdir"),
                    (b"PUT", "mkdir"),
                    (b"POST", "mkdir-with-children"),
                    (b"POST", "mkdir-immutable")
                )
                if (req.method, t) in terminal_requests:
                    # final directory
                    kids = {}
                    if t in ("mkdir-with-children", "mkdir-immutable"):
                        req.content.seek(0)
                        kids_json = req.content.read()
                        kids = convert_children_json(
                            self.client.nodemaker,
                            kids_json,
                        )
                    file_format = get_format(req, None)
                    mutable = True
                    mt = get_mutable_type(file_format)
                    if t == "mkdir-immutable":
                        mutable = False

                    d = self.node.create_subdirectory(
                        name, kids,
                        mutable=mutable,
                        mutable_version=mt,
                    )
                    d.addCallback(
                        make_handler_for,
                        self.client, self.node, name,
                    )
                    return d
                leaf_requests = (
                    (b"PUT",""),
                    (b"PUT","uri"),
                )
                if (req.method, t) in leaf_requests:
                    # we were trying to find the leaf filenode (to put a new
                    # file in its place), and it didn't exist. That's ok,
                    # since that's the leaf node that we're about to create.
                    # We make a dummy one, which will respond to the PUT
                    # request by replacing itself.
                    return PlaceHolderNodeHandler(self.client, self.node, name)
            # otherwise, we just return a no-such-child error
            return f

        node = node_or_failure
        if nonterminal and should_create_intermediate_directories(req):
            if not IDirectoryNode.providedBy(node):
                # we would have put a new directory here, but there was a
                # file in the way.
                return ErrorPage(
                    http.CONFLICT,
                    "Unable to create directory %s: a file was in the way" % quote_output(name),
                    "no details",
                )
        return make_handler_for(node, self.client, self.node, name)

    @render_exception
    def render_DELETE(self, req):
        assert self.parentnode and self.name
        d = self.parentnode.delete(self.name)
        d.addCallback(lambda res: self.node.get_uri())
        return d

    @render_exception
    def render_GET(self, req):
        # This is where all of the directory-related ?t=* code goes.
        t = str(get_arg(req, b"t", b"").strip(), "ascii")

        # t=info contains variable ophandles, t=rename-form contains the name
        # of the child being renamed. Neither is allowed an ETag.
        FIXED_OUTPUT_TYPES =  ["", "json", "uri", "readonly-uri"]
        if not self.node.is_mutable() and t in FIXED_OUTPUT_TYPES:
            si = self.node.get_storage_index()
            if si and req.setETag(b'DIR:%s-%s' % (base32.b2a(si), t.encode("ascii") or b"")):
                return b""

        if not t:
            # render the directory as HTML
            return renderElement(
                req,
                DirectoryAsHTML(
                    self.node,
                    self.client.mutable_file_default,
                )
            )

        if t == "json":
            return _directory_json_metadata(req, self.node)
        if t == "info":
            return MoreInfo(self.node)
        if t == "uri":
            return _directory_uri(req, self.node)
        if t == "readonly-uri":
            return _directory_readonly_uri(req, self.node)
        if t == 'rename-form':
            return renderElement(
                req,
                RenameForm(self.node),
            )

        raise WebError("GET directory: bad t=%s" % t)

    @render_exception
    def render_PUT(self, req):
        t = str(get_arg(req, b"t", b"").strip(), "ascii")
        replace = parse_replace_arg(get_arg(req, "replace", "true"))

        if t == "mkdir":
            # our job was done by the traversal/create-intermediate-directory
            # process that got us here.
            return text_plain(self.node.get_uri(), req) # TODO: urlencode
        if t == "uri":
            if not replace:
                # they're trying to set_uri and that name is already occupied
                # (by us).
                raise ExistingChildError()
            d = self.replace_me_with_a_childcap(req, self.client, replace)
            # TODO: results
            return d

        raise WebError("PUT to a directory")

    @render_exception
    def render_POST(self, req):
        t = str(get_arg(req, b"t", b"").strip(), "ascii")

        if t == "mkdir":
            d = self._POST_mkdir(req)
        elif t == "mkdir-with-children":
            d = self._POST_mkdir_with_children(req)
        elif t == "mkdir-immutable":
            d = self._POST_mkdir_immutable(req)
        elif t == "upload":
            d = self._POST_upload(req) # this one needs the context
        elif t == "uri":
            d = self._POST_uri(req)
        elif t == "delete" or t == "unlink":
            d = self._POST_unlink(req)
        elif t == "rename":
            d = self._POST_rename(req)
        elif t == "relink":
            d = self._POST_relink(req)
        elif t == "check":
            d = self._POST_check(req)
        elif t == "start-deep-check":
            d = self._POST_start_deep_check(req)
        elif t == "stream-deep-check":
            d = self._POST_stream_deep_check(req)
        elif t == "start-manifest":
            d = self._POST_start_manifest(req)
        elif t == "start-deep-size":
            d = self._POST_start_deep_size(req)
        elif t == "start-deep-stats":
            d = self._POST_start_deep_stats(req)
        elif t == "stream-manifest":
            d = self._POST_stream_manifest(req)
        elif t == "set_children" or t == "set-children":
            d = self._POST_set_children(req)
        else:
            raise WebError("POST to a directory with bad t=%s" % t)

        return handle_when_done(req, d)

    def _POST_mkdir(self, req):
        name = get_arg(req, "name", "")
        if not name:
            # our job is done, it was handled by the code in got_child
            # which created the final directory (i.e. us)
            return defer.succeed(self.node.get_uri()) # TODO: urlencode
        name = name.decode("utf-8")
        replace = boolean_of_arg(get_arg(req, "replace", "true"))
        kids = {}
        mt = get_mutable_type(get_format(req, None))
        d = self.node.create_subdirectory(name, kids, overwrite=replace,
                                          mutable_version=mt)
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
        mt = get_mutable_type(get_format(req, None))
        d = self.node.create_subdirectory(name, kids, overwrite=False,
                                          mutable_version=mt)
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

    def _POST_upload(self, req):
        charset = str(get_arg(req, "_charset", b"utf-8"), "utf-8")
        contents = req.fields["file"]

        # The filename embedded in the MIME file upload will be bytes on Python
        # 2, Unicode on Python 3, or missing (i.e. None). The "name" field in
        # the upload will be bytes on Python 2, Unicode on Python 3, or missing
        # (i.e. None). We go through all these variations until we have a name
        # that is Unicode.
        assert contents.filename is None or isinstance(contents.filename, (bytes, str))
        name = get_arg(req, "name")  # returns bytes or None
        name = name or contents.filename  # unicode, bytes or None
        if name is not None:
            name = name.strip()
        if not name:
            # this prohibts empty, missing, and all-whitespace filenames
            raise WebError("upload requires a name")
        if isinstance(name, bytes):
            name = name.decode(charset)
        assert isinstance(name, str)
        if "/" in name:
            raise WebError("name= may not contain a slash", http.BAD_REQUEST)

        # since POST /uri/path/file?t=upload is equivalent to
        # POST /uri/path/dir?t=upload&name=foo, just do the same thing that
        # childFactory would do. Things are cleaner if we only do a subset of
        # them, though, so we don't do: d = self.childFactory(req, name)

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
        # DirectoryNodeHandler.render_POST and it would get rendered but the
        # addCallback() that handles when_done= would break.
        def render_child(child):
            req.dont_apply_extra_processing = True
            return child.render(req)
        d.addCallback(render_child)
        return d

    def _POST_uri(self, req):
        childcap = get_arg(req, "uri")
        if not childcap:
            raise WebError("set-uri requires a uri")
        name = get_arg(req, "name")
        if not name:
            raise WebError("set-uri requires a name")
        charset = str(get_arg(req, "_charset", b"utf-8"), "ascii")
        name = name.decode(charset)
        replace = parse_replace_arg(get_arg(req, "replace", "true"))

        # We mustn't pass childcap for the readcap argument because we don't
        # know whether it is a read cap. Passing a read cap as the writecap
        # argument will work (it ends up calling NodeMaker.create_from_cap,
        # which derives a readcap if necessary and possible).
        d = self.node.set_uri(name, childcap, None, overwrite=replace)
        d.addCallback(lambda res: childcap)
        return d

    def _POST_unlink(self, req):
        name = get_arg(req, "name")
        if name is None:
            # apparently an <input type="hidden" name="name" value="">
            # won't show up in the resulting encoded form.. the 'name'
            # field is completely missing. So to allow unlinking of a
            # child with a name that is the empty string, we have to
            # pretend that None means ''. The only downside of this is
            # a slightly confusing error message if someone does a POST
            # without a name= field. For our own HTML this isn't a big
            # deal, because we create the 'unlink' POST buttons ourselves.
            name = b''
        charset = str(get_arg(req, "_charset", b"utf-8"), "ascii")
        name = name.decode(charset)
        d = self.node.delete(name)
        d.addCallback(lambda res: "thing unlinked")
        return d

    def _POST_rename(self, req):
        # rename is identical to relink, but to_dir is not allowed
        # and to_name is required.
        if get_arg(req, "to_dir") is not None:
            raise WebError("to_dir= is not valid for rename")
        if get_arg(req, "to_name") is None:
            raise WebError("to_name= is required for rename")
        return self._POST_relink(req)

    def _POST_relink(self, req):
        charset = str(get_arg(req, "_charset", b"utf-8"), "ascii")
        replace = parse_replace_arg(get_arg(req, "replace", "true"))

        from_name = get_arg(req, "from_name")
        if from_name is not None:
            from_name = from_name.strip()
            from_name = from_name.decode(charset)
            assert isinstance(from_name, str)
        else:
            raise WebError("from_name= is required")

        to_name = get_arg(req, "to_name")
        if to_name is not None:
            to_name = to_name.strip()
            to_name = to_name.decode(charset)
            assert isinstance(to_name, str)
        else:
            to_name = from_name

        # Disallow slashes in both from_name and to_name, that would only
        # cause confusion.
        if "/" in from_name:
            raise WebError("from_name= may not contain a slash",
                           http.BAD_REQUEST)
        if "/" in to_name:
            raise WebError("to_name= may not contain a slash",
                           http.BAD_REQUEST)

        to_dir = get_arg(req, "to_dir")
        if to_dir is not None and to_dir != self.node.get_write_uri():
            to_dir = to_dir.strip()
            to_dir = to_dir.decode(charset)
            assert isinstance(to_dir, str)
            to_path = to_dir.split(u"/")
            to_root = self.client.nodemaker.create_from_cap(to_bytes(to_path[0]))
            if not IDirectoryNode.providedBy(to_root):
                raise WebError("to_dir is not a directory", http.BAD_REQUEST)
            d = to_root.get_child_at_path(to_path[1:])
        else:
            d = defer.succeed(self.node)

        def _got_new_parent(new_parent):
            if not IDirectoryNode.providedBy(new_parent):
                raise WebError("to_dir is not a directory", http.BAD_REQUEST)

            return self.node.move_child_to(from_name, new_parent,
                                           to_name, replace)
        d.addCallback(_got_new_parent)
        d.addCallback(lambda res: "thing moved")
        return d

    def _maybe_literal(self, res, Results_Class):
        if res:
            return Results_Class(self.client, res)
        return LiteralCheckResultsRenderer(self.client)

    def _POST_check(self, req):
        # check this directory
        verify = boolean_of_arg(get_arg(req, "verify", "false"))
        repair = boolean_of_arg(get_arg(req, "repair", "false"))
        add_lease = boolean_of_arg(get_arg(req, "add-lease", "false"))
        if repair:
            d = self.node.check_and_repair(Monitor(), verify, add_lease)
            d.addCallback(self._maybe_literal, CheckAndRepairResultsRenderer)
        else:
            d = self.node.check(Monitor(), verify, add_lease)
            d.addCallback(self._maybe_literal, CheckResultsRenderer)
        return d

    def _start_operation(self, monitor, renderer, req):
        self._operations.add_monitor(req, monitor, renderer)
        return self._operations.redirect_to(req)

    def _POST_start_deep_check(self, req):
        # check this directory and everything reachable from it
        if not get_arg(req, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        verify = boolean_of_arg(get_arg(req, "verify", "false"))
        repair = boolean_of_arg(get_arg(req, "repair", "false"))
        add_lease = boolean_of_arg(get_arg(req, "add-lease", "false"))
        if repair:
            monitor = self.node.start_deep_check_and_repair(verify, add_lease)
            renderer = DeepCheckAndRepairResultsRenderer(self.client, monitor)
        else:
            monitor = self.node.start_deep_check(verify, add_lease)
            renderer = DeepCheckResultsRenderer(self.client, monitor)
        return self._start_operation(monitor, renderer, req)

    def _POST_stream_deep_check(self, req):
        verify = boolean_of_arg(get_arg(req, "verify", "false"))
        repair = boolean_of_arg(get_arg(req, "repair", "false"))
        add_lease = boolean_of_arg(get_arg(req, "add-lease", "false"))
        walker = DeepCheckStreamer(req, self.node, verify, repair, add_lease)
        monitor = self.node.deep_traverse(walker)
        walker.setMonitor(monitor)
        # register to hear stopProducing. The walker ignores pauseProducing.
        req.registerProducer(walker, True)
        d = monitor.when_done()
        def _done(res):
            req.unregisterProducer()
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

    def _POST_start_manifest(self, req):
        if not get_arg(req, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.build_manifest()
        renderer = ManifestResults(self.client, monitor)
        return self._start_operation(monitor, renderer, req)

    def _POST_start_deep_size(self, req):
        if not get_arg(req, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.start_deep_stats()
        renderer = DeepSizeResults(self.client, monitor)
        return self._start_operation(monitor, renderer, req)

    def _POST_start_deep_stats(self, req):
        if not get_arg(req, "ophandle"):
            raise NeedOperationHandleError("slow operation requires ophandle=")
        monitor = self.node.start_deep_stats()
        renderer = DeepStatsResults(self.client, monitor)
        return self._start_operation(monitor, renderer, req)

    def _POST_stream_manifest(self, req):
        walker = ManifestStreamer(req, self.node)
        monitor = self.node.deep_traverse(walker)
        walker.setMonitor(monitor)
        # register to hear stopProducing. The walker ignores pauseProducing.
        req.registerProducer(walker, True)
        d = monitor.when_done()
        def _done(res):
            req.unregisterProducer()
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
        replace = parse_replace_arg(get_arg(req, "replace", "true"))
        req.content.seek(0)
        body = req.content.read()
        try:
            children = json.loads(body)
        except ValueError as le:
            le.args = tuple(le.args + (body,))
            # TODO test handling of bad JSON
            raise
        cs = {}
        for name, (file_or_dir, mddict) in list(children.items()):
            name = str(name) # json returns str *or* unicode
            writecap = mddict.get('rw_uri')
            if writecap is not None:
                writecap = writecap.encode("utf-8")
            readcap = mddict.get('ro_uri')
            if readcap is not None:
                readcap = readcap.encode("utf-8")
            cs[name] = (writecap, readcap, mddict.get('metadata'))
        d = self.node.set_children(cs, replace)
        d.addCallback(lambda res: "Okay so I did it.")
        # TODO: results
        return d

def abbreviated_dirnode(dirnode):
    u = from_string_dirnode(dirnode.get_uri())
    return u.abbrev_si()

SPACE = u"\u00A0"*2

class DirectoryAsHTML(Element):
    # The remainder of this class is to render the directory into
    # human+browser -oriented HTML.
    loader = XMLFile(FilePath(__file__).sibling("directory.xhtml"))

    def __init__(self, node, default_mutable_format):
        super(DirectoryAsHTML, self).__init__()
        self.node = node
        if default_mutable_format not in (MDMF_VERSION, SDMF_VERSION):
            raise ValueError(
                "Uknown mutable format '{}'".format(default_mutable_format)
            )
        self.default_mutable_format = default_mutable_format

    @defer.inlineCallbacks
    def render(self, request):
        """
        Override Element.render .. we have async work to do before we flatten our template
        """
        # this could be improved; doing a more-straightforward port
        # here, which used to be in "beforeRender" .. so lots of the
        # renderers don't yield/wait properly, they expect the self.*
        # state that is set up here to "just be there" :/
        yield self._get_children(request)
        template = Element.render(self, request)
        defer.returnValue(template)

    @defer.inlineCallbacks
    def _get_children(self, req):
        # attempt to get the dirnode's children, stashing them (or the
        # failure that results) for later use
        try:
            children = yield self.node.list()
        except Exception as e:
            text, code = humanize_exception(e)
            children = None
            self.dirnode_children_error = text

        self.dirnode_children = children
        defer.returnValue(self.dirnode_children)

    @renderer
    def children(self, req, tag):
        return SlotsSequenceElement(
            tag,
            [
                self._child_slots(req, fname, data[0], data[1])
                for fname, data in self.dirnode_children.items()
            ]
        )

    @renderer
    def title(self, req, tag):
        si_s = str(abbreviated_dirnode(self.node), "utf-8")
        header = ["Tahoe-LAFS - Directory SI=%s" % si_s]
        if self.node.is_unknown():
            header.append(" (unknown)")
        elif not self.node.is_mutable():
            header.append(" (immutable)")
        elif self.node.is_readonly():
            header.append(" (read-only)")
        else:
            header.append(" (modifiable)")
        return tag(header)

    @renderer
    def header(self, req, tag):
        si_s = str(abbreviated_dirnode(self.node), "utf-8")
        header = ["Tahoe-LAFS Directory SI=", tags.span(si_s, class_="data-chars")]
        if self.node.is_unknown():
            header.append(" (unknown)")
        elif not self.node.is_mutable():
            header.append(" (immutable)")
        elif self.node.is_readonly():
            header.append(" (read-only)")
        return tag(header)

    @renderer
    def welcome(self, req, tag):
        link = get_root(req)
        return tag(tags.a("Return to Welcome page", href=link))

    @renderer
    def show_readonly(self, req, tag):
        if self.node.is_unknown() or self.node.is_readonly():
            return ""
        rocap = self.node.get_readonly_uri()
        root = get_root(req)
        uri_link = "%s/uri/%s/" % (root, url_quote(rocap))
        return tag(tags.a("Read-Only Version", href=uri_link))

    @renderer
    def try_children(self, req, tag):
        # if the dirnode can be retrived, render a table of children.
        # Otherwise, render an apologetic error message.
        if self.dirnode_children is not None:
            return tag
        else:
            return tags.div(
                tags.p("Error reading directory:"),
                tags.p(self.dirnode_children_error),
            )

    def _child_slots(self, req, name, target, metadata):
        """
        :returns: a dict of key/values to give to each item in the table
            of sub-items that directory.xhtml defines (this method is
            called by the 'children' renderer)
        """
        name = name.encode("utf-8")
        nameurl = url_quote(name, safe="") # encode any slashes too

        root = get_root(req)
        here = "{}/uri/{}/".format(root, url_quote(self.node.get_uri()))
        if self.node.is_unknown() or self.node.is_readonly():
            unlink = "-"
            rename = "-"
        else:
            # this creates a button which will cause our _POST_unlink method
            # to be invoked, which unlinks the file and then redirects the
            # browser back to this directory
            unlink = tags.form(
                [
                    tags.input(type='hidden', name='t', value='unlink'),
                    tags.input(type='hidden', name='name', value=name),
                    tags.input(type='hidden', name='when_done', value="."),
                    tags.input(type='submit', class_='btn', value='unlink', name="unlink"),
                ],
                action=here, method="post"
            )
            rename = tags.form(
                [
                    tags.input(type='hidden', name='t', value='rename-form'),
                    tags.input(type='hidden', name='name', value=name),
                    tags.input(type='hidden', name='when_done', value="."),
                    tags.input(type='submit', class_='btn', value='rename/relink', name="rename"),
                ],
                action=here, method="get",
            )

        slots = {
            "unlink": unlink,
            "rename": rename,
        }

        times = []
        linkcrtime = metadata.get('tahoe', {}).get("linkcrtime")
        if linkcrtime is not None:
            times.append("lcr: " + render_time(linkcrtime))
        else:
            # For backwards-compatibility with links last modified by Tahoe < 1.4.0:
            if "ctime" in metadata:
                ctime = render_time(metadata["ctime"])
                times.append("c: " + ctime)
        linkmotime = metadata.get('tahoe', {}).get("linkmotime")
        if linkmotime is not None:
            if times:
                times.append(tags.br())
            times.append("lmo: " + render_time(linkmotime))
        else:
            # For backwards-compatibility with links last modified by Tahoe < 1.4.0:
            if "mtime" in metadata:
                mtime = render_time(metadata["mtime"])
                if times:
                    times.append(tags.br())
                times.append("m: " + mtime)
        slots["times"] = times

        assert IFilesystemNode.providedBy(target), target
        target_uri = target.get_uri() or ""
        quoted_uri = url_quote(target_uri, safe="") # escape slashes too

        if IMutableFileNode.providedBy(target):
            # to prevent javascript in displayed .html files from stealing a
            # secret directory URI from the URL, send the browser to a URI-based
            # page that doesn't know about the directory at all
            dlurl = "%s/file/%s/@@named=/%s" % (root, quoted_uri, nameurl)
            slots["filename"] = tags.a(name, href=dlurl, rel="noreferrer")
            slots["type"] = "SSK"
            slots["size"] = "?"
            info_link = "{}/uri/{}?t=info".format(root, quoted_uri)

        elif IImmutableFileNode.providedBy(target):
            dlurl = "%s/file/%s/@@named=/%s" % (root, quoted_uri, nameurl)
            slots["filename"] = tags.a(name, href=dlurl, rel="noreferrer")
            slots["type"] = "FILE"
            slots["size"] = str(target.get_size())
            info_link = "{}/uri/{}?t=info".format(root, quoted_uri)

        elif IDirectoryNode.providedBy(target):
            # directory
            uri_link = "%s/uri/%s/" % (root, url_quote(target_uri))
            slots["filename"] = tags.a(name, href=uri_link)
            if not target.is_mutable():
                dirtype = "DIR-IMM"
            elif target.is_readonly():
                dirtype = "DIR-RO"
            else:
                dirtype = "DIR"
            slots["type"] = dirtype
            slots["size"] = "-"
            info_link = "%s/uri/%s/?t=info" % (root, quoted_uri)

        elif isinstance(target, ProhibitedNode):
            if IDirectoryNode.providedBy(target.wrapped_node):
                blacklisted_type = "DIR-BLACKLISTED"
            else:
                blacklisted_type = "BLACKLISTED"
            slots["type"] = blacklisted_type
            slots["size"] = "-"
            slots["info"] = ["Access Prohibited:", tags.br, target.reason]
            slots["filename"] = tags.strike(name)
            info_link = None

        else:
            # unknown
            if target.get_write_uri() is not None:
                unknowntype = "?"
            elif not self.node.is_mutable() or target.is_alleged_immutable():
                unknowntype = "?-IMM"
            else:
                unknowntype = "?-RO"
            slots["filename"] = name
            slots["type"] = unknowntype
            slots["size"] = "-"
            # use a directory-relative info link, so we can extract both the
            # writecap and the readcap
            info_link = "%s?t=info" % url_quote(name)

        if info_link:
            slots["info"] = tags.a("More Info", href=info_link)

        return slots

    # XXX: similar to render_upload_form and render_mkdir_form in root.py.
    # XXX: also, is generating so much HTML in code a great idea? -> templates?
    @renderer
    def forms(self, req, data):
        forms = []

        # making this consistent with the other forms, and also
        # because action="." doesn't get us back to the dir page (but
        # instead /uri itself)
        root = get_root(req)
        here = "{}/uri/{}/".format(root, url_quote(self.node.get_uri()))

        if self.node.is_readonly():
            return tags.div("No upload forms: directory is read-only")
        if self.dirnode_children is None:
            return tags.div("No upload forms: directory is unreadable")

        mkdir_sdmf = tags.input(
            type='radio',
            name='format',
            value='sdmf',
            id='mkdir-sdmf',
            checked='checked',
        )
        mkdir_mdmf = tags.input(
            type='radio',
            name='format',
            value='mdmf',
            id='mkdir-mdmf',
        )

        mkdir_form = tags.form(
            [
                tags.fieldset([
                    tags.input(type="hidden", name="t", value="mkdir"),
                    tags.input(type="hidden", name="when_done", value="."),
                    tags.legend("Create a new directory in this directory", class_="freeform-form-label"),
                    "New directory name:"+SPACE, tags.br,
                    tags.input(type="text", name="name"), SPACE,
                    tags.div(
                        [
                            mkdir_sdmf, tags.label(SPACE, "SDMF", for_='mutable-directory-sdmf'), SPACE*2,
                            mkdir_mdmf, tags.label(SPACE, "MDMF (experimental)", for_='mutable-directory-mdmf'),
                        ],
                        class_="form-inline",
                    ),
                    tags.input(
                        type="submit",
                        class_="btn",
                        value="Create",
                    ),
                ])
            ],
            action=here,
            method="post",
            enctype="multipart/form-data",
        )
        forms.append(tags.div(mkdir_form, class_="freeform-form"))

        upload_chk = tags.input(
            type='radio',
            name='format',
            value='chk',
            id='upload-chk',
            checked='checked',
        )
        upload_sdmf = tags.input(
            type='radio',
            name='format',
            value='sdmf',
            id='upload-sdmf',
        )
        upload_mdmf = tags.input(
            type='radio',
            name='format',
            value='mdmf',
            id='upload-mdmf',
        )

        upload_form = tags.form(
            tags.fieldset([
                tags.input(type="hidden", name="t", value="upload"),
                tags.input(type="hidden", name="when_done", value=req.uri),
                tags.legend("Upload a file to this directory", class_="freeform-form-label"),
                "Choose a file to upload:"+SPACE,
                tags.input(type="file", name="file", class_="freeform-input-file"), SPACE,
                tags.div([
                    upload_chk,  tags.label(SPACE, "Immutable", for_="upload-chk"), SPACE*2,
                    upload_sdmf, tags.label(SPACE, "SDMF", for_="upload-sdmf"), SPACE*2,
                    upload_mdmf, tags.label(SPACE, "MDMF (experimental)", for_="upload-mdmf"),
                ], class_="form-inline"),
                tags.input(type="submit", class_="btn", value="Upload"),             SPACE*2,
            ]),
            action=req.uri,
            method="post",
            enctype="multipart/form-data",
        )
        forms.append(tags.div(upload_form, class_="freeform-form"))

        attach_form = tags.form(
            tags.fieldset(
                tags.div([
                    tags.input(type="hidden", name="t", value="uri"),
                    tags.input(type="hidden", name="when_done", value="."),
                    tags.legend("Add a link to a file or directory which is already in Tahoe-LAFS.", class_="freeform-form-label"),
                    "New child name:"+SPACE,
                    tags.input(type="text", name="name"), SPACE*2, tags.br,
                    "URI of new child:"+SPACE,
                    tags.input(type="text", name="uri"), SPACE,
                    tags.input(type="submit", class_="btn", value="Attach"),
                    ], class_="form-inline"),
            ),
            action=here,
            method="post",
            enctype="multipart/form-data",
        )
        forms.append(tags.div(attach_form, class_="freeform-form"))
        return forms

    @renderer
    def results(self, req, tag):
        return get_arg(req, "results", "")

def _directory_json_metadata(req, dirnode):
    d = dirnode.list()
    def _got(children):
        kids = {}
        for name, (childnode, metadata) in list(children.items()):
            assert IFilesystemNode.providedBy(childnode), childnode
            rw_uri = childnode.get_write_uri()
            ro_uri = childnode.get_readonly_uri()
            if IFileNode.providedBy(childnode):
                kiddata = ("filenode", get_filenode_metadata(childnode))
            elif IDirectoryNode.providedBy(childnode):
                kiddata = ("dirnode", {'mutable': childnode.is_mutable()})
            else:
                kiddata = ("unknown", {})

            kiddata[1]["metadata"] = metadata
            if rw_uri:
                kiddata[1]["rw_uri"] = rw_uri
            if ro_uri:
                kiddata[1]["ro_uri"] = ro_uri
            verifycap = childnode.get_verify_cap()
            if verifycap:
                kiddata[1]['verify_uri'] = verifycap.to_string()

            kids[name] = kiddata

        drw_uri = dirnode.get_write_uri()
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
        return json.dumps(data, indent=1) + "\n"
    d.addCallback(_got)
    d.addCallback(text_plain, req)

    def error(f):
        message, code = humanize_failure(f)
        req.setResponseCode(code)
        return json.dumps({
            "error": message,
        })
    d.addErrback(error)
    return d


def _directory_uri(req, dirnode):
    return text_plain(dirnode.get_uri(), req)

def _directory_readonly_uri(req, dirnode):
    return text_plain(dirnode.get_readonly_uri(), req)

class RenameForm(Element, object):

    loader = XMLFile(FilePath(__file__).sibling("rename-form.xhtml"))

    def __init__(self, original):
        self.original = original
        super(RenameForm, self).__init__()

    @renderer
    def title(self, req, tag):
        return tag("Directory SI={}".format(str(abbreviated_dirnode(self.original), "ascii")))

    @renderer
    def header(self, req, tag):
        header = [
            "Rename "
            "in directory SI=%s" % str(abbreviated_dirnode(self.original), "ascii"),
        ]

        if self.original.is_readonly():
            header.append(" (readonly!)")
        header.append(":")
        return tag(header)

    @renderer
    def when_done(self, req, tag):
        return tags.input(type="hidden", name="when_done", value=".")

    @renderer
    def get_name(self, req, tag):
        name = get_arg(req, "name", "")
        tag.attributes['value'] = name
        return tag


class ReloadableMonitorElement(Element):
    """
    Like 'ReloadMixin', but for twisted.web.template style. This
    provides renderers for "reload" and "refesh" and a self.monitor
    attribute (which is an instance of IMonitor)
    """
    refresh_time = timedelta(seconds=60)

    def __init__(self, monitor):
        self.monitor = monitor
        super(ReloadableMonitorElement, self).__init__()

    @renderer
    def refresh(self, req, tag):
        if self.monitor.is_finished():
            return u""
        tag.attributes[u"http-equiv"] = u"refresh"
        tag.attributes[u"content"] = u"{}".format(self.refresh_time.seconds)
        return tag

    @renderer
    def reload(self, req, tag):
        if self.monitor.is_finished():
            return u""
        reload_url = URL.from_text(u"{}".format(req.path))
        cancel_button = tags.form(
            [
                tags.input(type=u"submit", value=u"Cancel"),
            ],
            action=reload_url.replace(query={u"t": u"cancel"}).to_uri().to_text(),
            method=u"POST",
            enctype=u"multipart/form-data",
        )

        return tag([
            u"Operation still running: ",
            tags.a(
                u"Reload",
                href=reload_url.replace(query={u"output": u"html"}).to_uri().to_text(),
            ),
            cancel_button,
        ])


def _slashify_path(path):
    """
    Converts a tuple from a 'manifest' path into a string with slashes
    in it
    """
    if not path:
        return b""
    return b"/".join([p.encode("utf-8") for p in path])


def _cap_to_link(root, path, cap):
    """
    Turns a capability-string into a WebAPI link tag

    :param text root: the root piece of the URI

    :param text cap: the capability-string

    :returns: something suitable for `IRenderable`, specifically
        either a valid local link (tags.a instance) to the capability
        or an empty string.
    """
    if cap:
        root_url = URL.from_text(u"{}".format(root))
        cap_obj = from_string(cap)
        if isinstance(cap_obj, (CHKFileURI, WriteableSSKFileURI, ReadonlySSKFileURI)):
            uri_link = root_url.child(
                u"file",
                u"{}".format(url_quote(cap)),
                u"{}".format(url_quote(path[-1])),
            )
        else:
            uri_link = root_url.child(
                u"uri",
                u"{}".format(url_quote(cap, safe="")),
            )
        return tags.a(cap, href=uri_link.to_text())
    else:
        return u""


class ManifestElement(ReloadableMonitorElement):
    loader = XMLFile(FilePath(__file__).sibling("manifest.xhtml"))

    def _si_abbrev(self):
        si = self.monitor.origin_si
        if not si:
            return "<LIT>"
        return str(base32.b2a(si)[:6], "utf-8")

    @renderer
    def title(self, req, tag):
        return tag(
            "Manifest of SI={}".format(self._si_abbrev())
        )

    @renderer
    def header(self, req, tag):
        return tag(
            "Manifest of SI={}".format(self._si_abbrev())
        )

    @renderer
    def items(self, req, tag):
        manifest = self.monitor.get_status()["manifest"]
        root = get_root(req)
        rows = [
            {
                "path": _slashify_path(path),
                "cap": _cap_to_link(root, path, cap),
            }
            for path, cap in manifest
        ]
        return SlotsSequenceElement(tag, rows)


class ManifestResults(MultiFormatResource, ReloadMixin):

    # Control MultiFormatResource
    formatArgument = "output"
    formatDefault = "html"

    def __init__(self, client, monitor):
        self.client = client
        self.monitor = monitor

    def render_HTML(self, req):
        return renderElement(
            req,
            ManifestElement(self.monitor)
        )

    def render_TEXT(self, req):
        req.setHeader("content-type", "text/plain")
        lines = []
        is_finished = self.monitor.is_finished()
        lines.append(b"finished: " + {True: b"yes", False: b"no"}[is_finished])
        for path, cap in self.monitor.get_status()["manifest"]:
            lines.append(_slashify_path(path) + b" " + cap)
        return b"\n".join(lines) + b"\n"

    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        m = self.monitor
        s = m.get_status()

        if m.origin_si:
            origin_base32 = base32.b2a(m.origin_si)
        else:
            origin_base32 = ""
        status = { "stats": s["stats"],
                   "finished": m.is_finished(),
                   "origin": origin_base32,
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
        return json.dumps(status, indent=1)


class DeepSizeResults(MultiFormatResource):

    # Control MultiFormatResource
    formatArgument = "output"
    formatDefault = "html"

    def __init__(self, client, monitor):
        self.client = client
        self.monitor = monitor

    def render_HTML(self, req):
        is_finished = self.monitor.is_finished()
        output = "finished: " + {True: "yes", False: "no"}[is_finished] + "\n"
        if is_finished:
            stats = self.monitor.get_status()
            total = (stats.get("size-immutable-files", 0)
                     + stats.get("size-mutable-files", 0)
                     + stats.get("size-directories", 0))
            output += "size: %d\n" % total
        return output.encode("utf-8")
    render_TEXT = render_HTML

    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        status = {"finished": self.monitor.is_finished(),
                  "size": self.monitor.get_status(),
                  }
        return json.dumps(status)


class DeepStatsResults(Resource, object):
    """
    Renders the results of a 'deep-stats' operation on a directory
    capability.
    """
    def __init__(self, client, monitor):
        self.client = client
        self.monitor = monitor

    def render(self, req):
        # JSON only
        req.setHeader("content-type", "text/plain")
        s = self.monitor.get_status().copy()
        s["finished"] = self.monitor.is_finished()
        return json.dumps(s, indent=1).encode("utf-8")


@implementer(IPushProducer)
class ManifestStreamer(dirnode.DeepStats):

    def __init__(self, req, origin):
        dirnode.DeepStats.__init__(self, origin)
        self.req = req

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
        d["verifycap"] = v or ""

        r = node.get_repair_cap()
        if r:
            r = r.to_string()
        d["repaircap"] = r or ""

        si = node.get_storage_index()
        if si:
            si = base32.b2a(si)
        d["storage-index"] = si or ""

        j = json.dumps(d, ensure_ascii=True)
        assert "\n" not in j
        self.req.write(j.encode("utf-8")+b"\n")

    def finish(self):
        stats = dirnode.DeepStats.get_results(self)
        d = {"type": "stats",
             "stats": stats,
             }
        j = json.dumps(d, ensure_ascii=True)
        assert "\n" not in j
        self.req.write(j.encode("utf-8")+b"\n")
        return b""

@implementer(IPushProducer)
class DeepCheckStreamer(dirnode.DeepStats):

    def __init__(self, req, origin, verify, repair, add_lease):
        dirnode.DeepStats.__init__(self, origin)
        self.req = req
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
        elif IFileNode.providedBy(node):
            data["type"] = "file"
        else:
            data["type"] = "unknown"

        v = node.get_verify_cap()
        if v:
            v = v.to_string()
        data["verifycap"] = v or ""

        r = node.get_repair_cap()
        if r:
            r = r.to_string()
        data["repaircap"] = r or ""

        si = node.get_storage_index()
        if si:
            si = base32.b2a(si)
        data["storage-index"] = si or ""

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
        j = json.dumps(data, ensure_ascii=True)
        assert "\n" not in j
        self.req.write(j.encode("utf-8")+b"\n")

    def finish(self):
        stats = dirnode.DeepStats.get_results(self)
        d = {"type": "stats",
             "stats": stats,
             }
        j = json.dumps(d, ensure_ascii=True)
        assert "\n" not in j
        self.req.write(j.encode("utf-8")+b"\n")
        return b""


class UnknownNodeHandler(Resource, object):
    def __init__(self, client, node, parentnode=None, name=None):
        super(UnknownNodeHandler, self).__init__()
        assert node
        self.node = node
        self.parentnode = parentnode
        self.name = name

    @render_exception
    def render_GET(self, req):
        t = str(get_arg(req, "t", "").strip(), "ascii")
        if t == "info":
            return MoreInfo(self.node)
        if t == "json":
            is_parent_known_immutable = self.parentnode and not self.parentnode.is_mutable()
            if self.parentnode and self.name:
                d = self.parentnode.get_metadata_for(self.name)
            else:
                d = defer.succeed(None)
            d.addCallback(lambda md: UnknownJSONMetadata(req, self.node, md, is_parent_known_immutable))
            return d
        raise WebError("GET unknown URI type: can only do t=info and t=json, not t=%s.\n"
                       "Using a webapi server that supports a later version of Tahoe "
                       "may help." % t)

def UnknownJSONMetadata(req, node, edge_metadata, is_parent_known_immutable):
    rw_uri = node.get_write_uri()
    ro_uri = node.get_readonly_uri()
    data = ("unknown", {})
    if ro_uri:
        data[1]['ro_uri'] = ro_uri
    if rw_uri:
        data[1]['rw_uri'] = rw_uri
        data[1]['mutable'] = True
    elif is_parent_known_immutable or node.is_alleged_immutable():
        data[1]['mutable'] = False
    # else we don't know whether it is mutable.

    if edge_metadata is not None:
        data[1]['metadata'] = edge_metadata
    return text_plain(json.dumps(data, indent=1) + "\n", req)
