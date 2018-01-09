"""Results of the manifest operation."""

from nevow import rend, inevow, tags as T
import json
import urllib

from allmydata.util import base32
from allmydata.web import common
from allmydata.web.common import MultiFormatPage
from allmydata.web.operations import ReloadMixin


class ManifestResults(MultiFormatPage, ReloadMixin):
    """Container for results of the manifest operation."""

    docFactory = common.getxmlfile("manifest.xhtml")

    # Control MultiFormatPage
    formatArgument = "output"
    formatDefault = "html"

    # Json API version.
    # Rules:
    # - increment each time a field is removed or changes meaning.
    # - it's ok to add a new field without incrementing the version.
    # Note this does not apply to inner stats object which will have its own
    # versioning.
    API_VERSION = 1

    def __init__(self, client, monitor):
        """Initialize class."""
        self.client = client
        self.monitor = monitor

    def renderHTTP(self, ctx):
        """Render HTTP page with the results."""
        req = inevow.IRequest(ctx)
        output = common.get_arg(req, "output", "html").lower()
        if output == "text":
            return self._text(req)
        if output == "json":
            return self._json(req)
        return rend.Page.renderHTTP(self, ctx)

    def _slashify_path(self, path):
        if not path:
            return ""
        return "/".join([p.encode("utf-8") for p in path])

    def _text(self, req):
        req.setHeader("content-type", "text/plain")
        lines = []
        is_finished = self.monitor.is_finished()
        lines.append("finished: " + {True: "yes", False: "no"}[is_finished])
        for (path, cap) in self.monitor.get_status()["manifest"]:
            lines.append(self._slashify_path(path) + " " + cap)
        return "\n".join(lines) + "\n"

    def _json(self, req):
        req.setHeader("content-type", "text/plain")
        m = self.monitor
        s = m.get_status()

        if m.origin_si:
            origin_base32 = base32.b2a(m.origin_si)
        else:
            origin_base32 = ""
        status = {"stats": s["stats"],
                  "finished": m.is_finished(),
                  "origin": origin_base32,
                  "api-version": self.API_VERSION
                  }
        if m.is_finished():
            # don't return manifest/verifycaps/SIs unless the operation is
            # done, to save on CPU/memory (both here and in the HTTP client
            # who has to unpack the JSON). Tests show that the ManifestWalker
            # needs about 1092 bytes per item, the JSON we generate here
            # requires about 503 bytes per item, and some internal overhead
            # (perhaps transport-layer buffers in twisted.web?) requires an
            # additional 1047 bytes per item.
            status.update({"manifest": s["manifest"],
                           "verifycaps": [i for i in s["verifycaps"]],
                           "storage-index": [i for i in s["storage-index"]],
                           })
            # json doesn't know how to serialize a set. We use a
            # generator that walks the set rather than list(setofthing) to
            # save a small amount of memory (4B*len) and a moderate amount of
            # CPU.
        return json.dumps(status, indent=1)

    def _si_abbrev(self):
        si = self.monitor.origin_si
        if not si:
            return "<LIT>"
        return base32.b2a(si)[:6]

    def render_title(self, ctx):
        """Render title of the page."""
        return T.title["Manifest of SI=%s" % self._si_abbrev()]

    def render_header(self, ctx):
        """Render page header."""
        return T.p["Manifest of SI=%s" % self._si_abbrev()]

    def data_items(self, ctx, data):
        """Return data items."""
        return self.monitor.get_status()["manifest"]

    def render_row(self, ctx, (path, cap)):
        """Render row of the manifest."""
        ctx.fillSlots("path", self._slashify_path(path))
        root = common.get_root(ctx)
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
