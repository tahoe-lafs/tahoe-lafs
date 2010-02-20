
import time
from zope.interface import implements
from nevow import rend, url, tags as T
from nevow.inevow import IRequest
from twisted.python.failure import Failure
from twisted.internet import reactor, defer
from twisted.web.http import NOT_FOUND
from twisted.web.html import escape
from twisted.application import service

from allmydata.web.common import IOpHandleTable, WebError, \
     get_root, get_arg, boolean_of_arg

MINUTE = 60
HOUR = 60*MINUTE
DAY = 24*HOUR

(MONITOR, RENDERER, WHEN_ADDED) = range(3)

class OphandleTable(rend.Page, service.Service):
    implements(IOpHandleTable)

    UNCOLLECTED_HANDLE_LIFETIME = 4*DAY
    COLLECTED_HANDLE_LIFETIME = 1*DAY

    def __init__(self, clock=None):
        # both of these are indexed by ophandle
        self.handles = {} # tuple of (monitor, renderer, when_added)
        self.timers = {}
        # The tests will provide a deterministic clock
        # (twisted.internet.task.Clock) that they can control so that
        # they can test ophandle expiration. If this is provided, I'll
        # use it schedule the expiration of ophandles.
        self.clock = clock

    def stopService(self):
        for t in self.timers.values():
            if t.active():
                t.cancel()
        del self.handles # this is not restartable
        del self.timers
        return service.Service.stopService(self)

    def add_monitor(self, ctx, monitor, renderer):
        ophandle = get_arg(ctx, "ophandle")
        assert ophandle
        now = time.time()
        self.handles[ophandle] = (monitor, renderer, now)
        retain_for = get_arg(ctx, "retain-for", None)
        if retain_for is not None:
            self._set_timer(ophandle, int(retain_for))
        monitor.when_done().addBoth(self._operation_complete, ophandle)

    def _operation_complete(self, res, ophandle):
        if ophandle in self.handles:
            if ophandle not in self.timers:
                # the client has not provided a retain-for= value for this
                # handle, so we set our own.
                now = time.time()
                added = self.handles[ophandle][WHEN_ADDED]
                when = max(self.UNCOLLECTED_HANDLE_LIFETIME, now - added)
                self._set_timer(ophandle, when)
            # if we already have a timer, the client must have provided the
            # retain-for= value, so don't touch it.

    def redirect_to(self, ctx):
        ophandle = get_arg(ctx, "ophandle")
        assert ophandle
        target = get_root(ctx) + "/operations/" + ophandle
        output = get_arg(ctx, "output")
        if output:
            target = target + "?output=%s" % output
        return url.URL.fromString(target)

    def childFactory(self, ctx, name):
        ophandle = name
        if ophandle not in self.handles:
            raise WebError("unknown/expired handle '%s'" % escape(ophandle),
                           NOT_FOUND)
        (monitor, renderer, when_added) = self.handles[ophandle]

        request = IRequest(ctx)
        t = get_arg(ctx, "t", "status")
        if t == "cancel" and request.method == "POST":
            monitor.cancel()
            # return the status anyways, but release the handle
            self._release_ophandle(ophandle)

        else:
            retain_for = get_arg(ctx, "retain-for", None)
            if retain_for is not None:
                self._set_timer(ophandle, int(retain_for))

            if monitor.is_finished():
                if boolean_of_arg(get_arg(ctx, "release-after-complete", "false")):
                    self._release_ophandle(ophandle)
                if retain_for is None:
                    # this GET is collecting the ophandle, so change its timer
                    self._set_timer(ophandle, self.COLLECTED_HANDLE_LIFETIME)

        status = monitor.get_status()
        if isinstance(status, Failure):
            return defer.fail(status)

        return renderer

    def _set_timer(self, ophandle, when):
        if ophandle in self.timers and self.timers[ophandle].active():
            self.timers[ophandle].cancel()
        if self.clock:
            t = self.clock.callLater(when, self._release_ophandle, ophandle)
        else:
            t = reactor.callLater(when, self._release_ophandle, ophandle)
        self.timers[ophandle] = t

    def _release_ophandle(self, ophandle):
        if ophandle in self.timers and self.timers[ophandle].active():
            self.timers[ophandle].cancel()
        self.timers.pop(ophandle, None)
        self.handles.pop(ophandle, None)

class ReloadMixin:
    REFRESH_TIME = 1*MINUTE

    def render_refresh(self, ctx, data):
        if self.monitor.is_finished():
            return ""
        # dreid suggests ctx.tag(**dict([("http-equiv", "refresh")]))
        # but I can't tell if he's joking or not
        ctx.tag.attributes["http-equiv"] = "refresh"
        ctx.tag.attributes["content"] = str(self.REFRESH_TIME)
        return ctx.tag

    def render_reload(self, ctx, data):
        if self.monitor.is_finished():
            return ""
        req = IRequest(ctx)
        # url.gethere would break a proxy, so the correct thing to do is
        # req.path[-1] + queryargs
        ophandle = req.prepath[-1]
        reload_target = ophandle + "?output=html"
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
