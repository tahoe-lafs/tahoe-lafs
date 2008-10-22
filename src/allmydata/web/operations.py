
from zope.interface import implements
from nevow import rend, url, tags as T
from nevow.inevow import IRequest
from twisted.web import html

from allmydata.web.common import IOpHandleTable, get_root, get_arg, WebError

class OphandleTable(rend.Page):
    implements(IOpHandleTable)

    def __init__(self):
        self.monitors = {}
        self.handles = {}

    def add_monitor(self, ophandle, monitor, renderer):
        self.monitors[ophandle] = monitor
        self.handles[ophandle] = renderer
        # TODO: expiration

    def redirect_to(self, ophandle, ctx):
        target = get_root(ctx) + "/operations/" + ophandle + "?t=status"
        output = get_arg(ctx, "output")
        if output:
            target = target + "&output=%s" % output
        return url.URL.fromString(target)

    def childFactory(self, ctx, name):
        ophandle = name
        if ophandle not in self.handles:
            raise WebError("unknown/expired handle '%s'" %html.escape(ophandle))
        t = get_arg(ctx, "t", "status")
        if t == "cancel":
            monitor = self.monitors[ophandle]
            monitor.cancel()
            # return the status anyways

        return self.handles[ophandle]

class ReloadMixin:

    def render_reload(self, ctx, data):
        if self.monitor.is_finished():
            return ""
        req = IRequest(ctx)
        # url.gethere would break a proxy, so the correct thing to do is
        # req.path[-1] + queryargs
        ophandle = req.prepath[-1]
        reload_target = ophandle + "?t=status&output=html"
        cancel_target = ophandle + "?t=cancel"
        cancel_button = T.form(action=cancel_target, method="POST",
                               enctype="multipart/form-data")[
            T.input(type="submit", value="Cancel"),
            ]

        return [T.h2["Operation still running: ",
                     T.a(href=reload_target)["Reload"],
                     ],
                cancel_button,
                ]
