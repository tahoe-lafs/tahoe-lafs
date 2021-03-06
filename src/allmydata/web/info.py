"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os
from urllib.parse import quote as urlquote

from twisted.python.filepath import FilePath
from twisted.web.template import tags as T, Element, renderElement, XMLFile, renderer

from allmydata.util import base32
from allmydata.interfaces import IDirectoryNode, IFileNode, MDMF_VERSION
from allmydata.web.common import MultiFormatResource
from allmydata.mutable.common import UnrecoverableFileError # TODO: move


class MoreInfo(MultiFormatResource):
    """
    A ``Resource`` for describing more information about a node.

    :param node Node: The node to describe.
    """

    def __init__(self, node):
        super(MoreInfo, self).__init__()
        self.node = node

    def render_HTML(self, req):
        """
        Render an HTML template describing this node.
        """
        return renderElement(req, MoreInfoElement(self.node))

    render_INFO = render_HTML


class MoreInfoElement(Element):
    """
    An ``Element`` HTML template which can be flattened to describe this node.

    :param Node node: The node to describe.
    """

    loader = XMLFile(FilePath(__file__).sibling("info.xhtml"))

    def __init__(self, node):
        super(MoreInfoElement, self).__init__()
        self.original = node

    def abbrev(self, storage_index_or_none):
        if storage_index_or_none:
            return str(base32.b2a(storage_index_or_none)[:6], "ascii")
        return "LIT file"

    def get_type(self):
        node = self.original
        if IDirectoryNode.providedBy(node):
            if not node.is_mutable():
                return "immutable directory"
            return "directory"
        if IFileNode.providedBy(node):
            si = node.get_storage_index()
            if si:
                if node.is_mutable():
                    ret = "mutable file"
                    if node.get_version() == MDMF_VERSION:
                        ret += " (mdmf)"
                    else:
                        ret += " (sdmf)"
                    return ret
                return "immutable file"
            return "immutable LIT file"
        return "unknown"

    @renderer
    def title(self, req, tag):
        node = self.original
        si = node.get_storage_index()
        t = "More Info for %s" % self.get_type()
        if si:
            t += " (SI=%s)" % self.abbrev(si)
        return tag(t)

    @renderer
    def header(self, req, tag):
        return self.title(req, tag)

    @renderer
    def type(self, req, tag):
        return tag(self.get_type())

    @renderer
    def si(self, req, tag):
        si = self.original.get_storage_index()
        if not si:
            return "None"
        return tag(base32.b2a(si))

    @renderer
    def size(self, req, tag):
        node = self.original
        d = node.get_current_size()
        def _no_size(size):
            if size is None:
                return "?"
            return size
        d.addCallback(_no_size)
        def _handle_unrecoverable(f):
            f.trap(UnrecoverableFileError)
            return "?"
        d.addErrback(_handle_unrecoverable)
        d.addCallback(lambda size: tag(str(size)))
        return d

    @renderer
    def directory_writecap(self, req, tag):
        node = self.original
        if not IDirectoryNode.providedBy(node):
            return ""
        if node.is_readonly():
            return ""
        return tag(node.get_uri())

    @renderer
    def directory_readcap(self, req, tag):
        node = self.original
        if not IDirectoryNode.providedBy(node):
            return ""
        return tag(node.get_readonly_uri())

    @renderer
    def directory_verifycap(self, req, tag):
        node = self.original
        if not IDirectoryNode.providedBy(node):
            return ""
        verifier = node.get_verify_cap()
        if verifier:
            return tag(node.get_verify_cap().to_string())
        return ""

    @renderer
    def file_writecap(self, req, tag):
        node = self.original
        if IDirectoryNode.providedBy(node):
            node = node._node
        write_uri = node.get_write_uri()
        if not write_uri:
            return ""
        return tag(write_uri)

    @renderer
    def file_readcap(self, req, tag):
        node = self.original
        if IDirectoryNode.providedBy(node):
            node = node._node
        read_uri = node.get_readonly_uri()
        if not read_uri:
            return ""
        return tag(read_uri)

    @renderer
    def file_verifycap(self, req, tag):
        node = self.original
        if IDirectoryNode.providedBy(node):
            node = node._node
        verifier = node.get_verify_cap()
        if verifier:
            return tag(node.get_verify_cap().to_string())
        return ""

    def get_root(self, req):
        # the addSlash=True gives us one extra (empty) segment
        depth = len(req.prepath) + len(req.postpath) - 1
        link = "/".join([".."] * depth)
        return link

    @renderer
    def raw_link(self, req, tag):
        node = self.original
        if IDirectoryNode.providedBy(node):
            node = node._node
        elif IFileNode.providedBy(node):
            pass
        else:
            return ""
        root = self.get_root(req)
        quoted_uri = urlquote(node.get_uri())
        text_plain_url = "%s/file/%s/@@named=/raw.txt" % (root, quoted_uri)
        return T.li("Raw data as ", T.a("text/plain", href=text_plain_url))

    @renderer
    def is_checkable(self, req, tag):
        node = self.original
        si = node.get_storage_index()
        if si:
            return tag
        # don't show checker button for LIT files
        return ""

    @renderer
    def check_form(self, req, tag):
        node = self.original
        quoted_uri = urlquote(node.get_uri())
        target = self.get_root(req) + "/uri/" + quoted_uri
        if IDirectoryNode.providedBy(node):
            target += "/"
        check = T.form(action=target, method="post",
                       enctype="multipart/form-data")(
            T.fieldset(
            T.input(type="hidden", name="t", value="check"),
            T.input(type="hidden", name="return_to", value="."),
            T.legend("Check on this object", class_="freeform-form-label"),
            T.div(
            "Verify every bit? (EXPENSIVE):",
            T.input(type="checkbox", name="verify"),
            ),
            T.div("Repair any problems?: ",
                  T.input(type="checkbox", name="repair")),
            T.div("Add/renew lease on all shares?: ",
                  T.input(type="checkbox", name="add-lease")),
            T.div("Emit results in JSON format?: ",
                  T.input(type="checkbox", name="output", value="JSON")),

            T.input(type="submit", value="Check"),

            ))
        return tag(check)

    @renderer
    def is_mutable_file(self, req, tag):
        node = self.original
        if IDirectoryNode.providedBy(node):
            return ""
        if (IFileNode.providedBy(node)
            and node.is_mutable() and not node.is_readonly()):
            return tag
        return ""

    @renderer
    def overwrite_form(self, req, tag):
        node = self.original
        root = self.get_root(req)
        action = "%s/uri/%s" % (root, urlquote(node.get_uri()))
        done_url = "%s/uri/%s?t=info" % (root, urlquote(node.get_uri()))
        overwrite = T.form(action=action, method="post",
                           enctype="multipart/form-data")(
            T.fieldset(
            T.input(type="hidden", name="t", value="upload"),
            T.input(type='hidden', name='when_done', value=done_url),
            T.legend("Overwrite", class_="freeform-form-label"),
            "Upload new contents: ",
            T.input(type="file", name="file"),
            " ",
            T.input(type="submit", value="Replace Contents")
            ))
        return tag(overwrite)

    @renderer
    def is_directory(self, req, tag):
        node = self.original
        if IDirectoryNode.providedBy(node):
            return tag
        return ""

    @renderer
    def deep_check_form(self, req, tag):
        ophandle = base32.b2a(os.urandom(16))
        deep_check = T.form(action=req.path, method="post",
                            enctype="multipart/form-data")(
            T.fieldset(
            T.input(type="hidden", name="t", value="start-deep-check"),
            T.input(type="hidden", name="return_to", value="."),
            T.legend("Run a deep-check operation (EXPENSIVE)", class_="freeform-form-label"),
            T.div(
            "Verify every bit? (EVEN MORE EXPENSIVE):",
            T.input(type="checkbox", name="verify"),
            ),
            T.div("Repair any problems?: ",
                  T.input(type="checkbox", name="repair")),
            T.div("Add/renew lease on all shares?: ",
                  T.input(type="checkbox", name="add-lease")),
            T.div("Emit results in JSON format?: ",
                  T.input(type="checkbox", name="output", value="JSON")),

            T.input(type="hidden", name="ophandle", value=ophandle),
            T.input(type="submit", value="Deep-Check"),

            ))
        return tag(deep_check)

    @renderer
    def deep_size_form(self, req, tag):
        ophandle = base32.b2a(os.urandom(16))
        deep_size = T.form(action=req.path, method="post",
                            enctype="multipart/form-data")(
            T.fieldset(
            T.input(type="hidden", name="t", value="start-deep-size"),
            T.legend("Run a deep-size operation (EXPENSIVE)", class_="freeform-form-label"),
            T.input(type="hidden", name="ophandle", value=ophandle),
            T.input(type="submit", value="Deep-Size"),
            ))
        return tag(deep_size)

    @renderer
    def deep_stats_form(self, req, tag):
        ophandle = base32.b2a(os.urandom(16))
        deep_stats = T.form(action=req.path, method="post",
                            enctype="multipart/form-data")(
            T.fieldset(
            T.input(type="hidden", name="t", value="start-deep-stats"),
            T.legend("Run a deep-stats operation (EXPENSIVE)", class_="freeform-form-label"),
            T.input(type="hidden", name="ophandle", value=ophandle),
            T.input(type="submit", value="Deep-Stats"),
            ))
        return tag(deep_stats)

    @renderer
    def manifest_form(self, req, tag):
        ophandle = base32.b2a(os.urandom(16))
        manifest = T.form(action=req.path, method="post",
                            enctype="multipart/form-data")(
            T.fieldset(
            T.input(type="hidden", name="t", value="start-manifest"),
            T.legend("Run a manifest operation (EXPENSIVE)", class_="freeform-form-label"),
            T.div("Output Format: ",
                  T.select(name="output")
                  ( T.option("HTML", value="html", selected="true"),
                    T.option("text", value="text"),
                    T.option("JSON", value="json"),
                    ),
                  ),
            T.input(type="hidden", name="ophandle", value=ophandle),
            T.input(type="submit", value="Manifest"),
            ))
        return tag(manifest)


# TODO: edge metadata
