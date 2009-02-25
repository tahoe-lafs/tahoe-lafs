
import re
from twisted.internet import defer
from nevow.testutil import FakeRequest
from nevow import inevow, context

class WebRenderingMixin:
    # d=page.renderString() or s=page.renderSynchronously() will exercise
    # docFactory, render_*/data_* . It won't exercise want_json(), or my
    # renderHTTP() override which tests want_json(). To exercise args=, we
    # must build a context. Pages which use a return_to= argument need a
    # context.

    # d=page.renderHTTP(ctx) will exercise my renderHTTP, want_json, and
    # docFactory/render_*/data_*, but it requires building a context. Since
    # we're already building a context, it is easy to exercise args= .

    # so, use at least two d=page.renderHTTP(ctx) per page (one for json, one
    # for html), then use lots of simple s=page.renderSynchronously() to
    # exercise the fine details (the ones that don't require args=).

    def make_context(self, req):
        ctx = context.RequestContext(tag=req)
        ctx.remember(req, inevow.IRequest)
        ctx.remember(None, inevow.IData)
        ctx = context.WovenContext(parent=ctx, precompile=False)
        return ctx

    def render1(self, page, **kwargs):
        # use this to exercise an overridden renderHTTP, usually for
        # output=json or render_GET. It always returns a Deferred.
        req = FakeRequest(**kwargs)
        req.fields = None
        ctx = self.make_context(req)
        d = defer.maybeDeferred(page.renderHTTP, ctx)
        def _done(res):
            if isinstance(res, str):
                return res + req.v
            return req.v
        d.addCallback(_done)
        return d

    def render2(self, page, **kwargs):
        # use this to exercise the normal Nevow docFactory rendering. It
        # returns a string. If one of the render_* methods returns a
        # Deferred, this will throw an exception. (note that
        # page.renderString is the Deferred-returning equivalent)
        req = FakeRequest(**kwargs)
        req.fields = None
        ctx = self.make_context(req)
        return page.renderSynchronously(ctx)

    def failUnlessIn(self, substring, s):
        self.failUnless(substring in s, s)

    def remove_tags(self, s):
        s = re.sub(r'<[^>]*>', ' ', s)
        s = re.sub(r'\s+', ' ', s)
        return s

