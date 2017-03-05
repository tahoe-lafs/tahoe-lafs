
import simplejson

from twisted.internet import defer
from nevow import url, rend
from nevow.inevow import IRequest

from allmydata.interfaces import ExistingChildError, IFileNode
from allmydata.monitor import Monitor
from allmydata.mutable.publish import MutableFileHandle
from allmydata.mutable.common import MODE_READ
from allmydata.util import base32
from allmydata.util.encodingutil import quote_output

from allmydata.web.common import text_plain, WebError, RenderMixin, \
     boolean_of_arg, get_arg, \
     parse_replace_arg, parse_offset_arg, \
     get_filenode_metadata
from allmydata.web.check_results import CheckResultsRenderer, \
     CheckAndRepairResultsRenderer, LiteralCheckResultsRenderer
from allmydata.web.info import MoreInfo


class VerifyNodeHandler(RenderMixin, rend.Page):
    def __init__(self, client, node, parentnode=None, name=None):
        rend.Page.__init__(self)
        self.client = client
        assert node
        self.node = node
        self.parentnode = parentnode
        self.name = name

    def childFactory(self, ctx, name):
        raise WebError("Verifycaps can't have children, certainly not named %s"
                       % quote_output(name, encoding='utf-8'))

    def render_GET(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        isFileNode = IFileNode.providedBy(self.node)

        # t=info contains variable ophandles, so is not allowed an ETag.
        FIXED_OUTPUT_TYPES = ["", "json", "uri", "readonly-uri"]
        if isFileNode and not self.node.is_mutable() and t in FIXED_OUTPUT_TYPES:
            # if the client already has the ETag then we can
            # short-circuit the whole process.
            si = self.node.get_storage_index()
            if si and req.setETag('%s-%s' % (base32.b2a(si), t or "")):
                return ""

        if not t:
            # Delegate if our node is an IFileNode; otherwise, assume that
            # they wanted to see t=info.
            if isFileNode:
                # just get the contents
                return self._get_contents(req)
            else:
                t = "info"

        if t == "json":
            # We do this to make sure that fields like size and
            # mutable-type (which depend on the file on the grid and not
            # just on the cap) are filled in. The latter gets used in
            # tests, in particular.
            #
            # TODO: Make it so that the servermap knows how to update in
            # a mode specifically designed to fill in these fields, and
            # then update it in that mode.
            if self.node.is_mutable():
                d = self.node.get_servermap(MODE_READ)
            else:
                d = defer.succeed(None)
            if self.parentnode and self.name:
                d.addCallback(lambda ignored:
                    self.parentnode.get_metadata_for(self.name))
            else:
                d.addCallback(lambda ignored: None)
            d.addCallback(lambda md: FileJSONMetadata(ctx, self.node, md))
            return d
        if t == "info":
            return MoreInfo(self.node)
        if t == "uri":
            return FileURI(ctx, self.node)
        if t == "readonly-uri":
            return FileReadOnlyURI(ctx, self.node)
        raise WebError("GET file: bad t=%s" % t)

    def render_PUT(self, ctx):
        req = IRequest(ctx)
        t = get_arg(req, "t", "").strip()
        replace = parse_replace_arg(get_arg(req, "replace", "true"))
        offset = parse_offset_arg(get_arg(req, "offset", None))

        if not t:
            if not replace:
                # this is the early trap: if someone else modifies the
                # directory while we're uploading, the add_file(overwrite=)
                # call in replace_me_with_a_child will do the late trap.
                raise ExistingChildError()

            if self.node.is_mutable():
                # Are we a readonly filenode? We shouldn't allow callers
                # to try to replace us if we are.
                if self.node.is_readonly():
                    raise WebError("PUT to a mutable file: replace or update"
                                   " requested with read-only cap")
                if offset is None:
                    return self.replace_my_contents(req)

                if offset >= 0:
                    return self.update_my_contents(req, offset)

                raise WebError("PUT to a mutable file: Invalid offset")

            else:
                if offset is not None:
                    raise WebError("PUT to a file: append operation invoked "
                                   "on an immutable cap")

                assert self.parentnode and self.name
                return self.replace_me_with_a_child(req, self.client, replace)

        if t == "uri":
            if not replace:
                raise ExistingChildError()
            assert self.parentnode and self.name
            return self.replace_me_with_a_childcap(req, self.client, replace)

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
                d = self.replace_my_contents_with_a_formpost(req)
            else:
                if not replace:
                    raise ExistingChildError()
                assert self.parentnode and self.name
                d = self.replace_me_with_a_formpost(req, self.client, replace)
        else:
            raise WebError("POST to file: bad t=%s" % t)

        when_done = get_arg(req, "when_done", None)
        if when_done:
            d.addCallback(lambda res: url.URL.fromString(when_done))
        return d

    def _maybe_literal(self, res, Results_Class):
        if res:
            return Results_Class(self.client, res)
        return LiteralCheckResultsRenderer(self.client)

    def _POST_check(self, req):
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

    def render_DELETE(self, ctx):
        assert self.parentnode and self.name
        d = self.parentnode.delete(self.name)
        d.addCallback(lambda res: self.node.get_uri())
        return d

    def replace_my_contents(self, req):
        req.content.seek(0)
        new_contents = MutableFileHandle(req.content)
        d = self.node.overwrite(new_contents)
        d.addCallback(lambda res: self.node.get_uri())
        return d


    def update_my_contents(self, req, offset):
        req.content.seek(0)
        added_contents = MutableFileHandle(req.content)

        d = self.node.get_best_mutable_version()
        d.addCallback(lambda mv:
            mv.update(added_contents, offset))
        d.addCallback(lambda ignored:
            self.node.get_uri())
        return d


    def replace_my_contents_with_a_formpost(self, req):
        # we have a mutable file. Get the data from the formpost, and replace
        # the mutable file's contents with it.
        new_contents = req.fields['file']
        new_contents = MutableFileHandle(new_contents.file)

        d = self.node.overwrite(new_contents)
        d.addCallback(lambda res: self.node.get_uri())
        return d


def FileJSONMetadata(ctx, filenode, edge_metadata):
    rw_uri = filenode.get_write_uri()
    ro_uri = filenode.get_readonly_uri()
    data = ("filenode", get_filenode_metadata(filenode))
    if ro_uri:
        data[1]['ro_uri'] = ro_uri
    if rw_uri:
        data[1]['rw_uri'] = rw_uri
    verifycap = filenode.get_verify_cap()
    if verifycap:
        data[1]['verify_uri'] = verifycap.to_string()
    if edge_metadata is not None:
        data[1]['metadata'] = edge_metadata

    return text_plain(simplejson.dumps(data, indent=1) + "\n", ctx)

def FileURI(ctx, filenode):
    return text_plain(filenode.get_uri(), ctx)

def FileReadOnlyURI(ctx, filenode):
    if filenode.is_readonly():
        return text_plain(filenode.get_uri(), ctx)
    return text_plain(filenode.get_readonly_uri(), ctx)
