
import treq
from twisted.internet.defer import (
    maybeDeferred,
    inlineCallbacks,
    returnValue,
)
from twisted.web.error import Error

from nevow.context import WebContext
from nevow.testutil import FakeRequest
from nevow.appserver import (
    processingFailed,
    DefaultExceptionHandler,
)
from nevow.inevow import (
    ICanHandleException,
    IRequest,
    IResource as INevowResource,
    IData,
)

@inlineCallbacks
def do_http(method, url, **kwargs):
    response = yield treq.request(method, url, persistent=False, **kwargs)
    body = yield treq.content(response)
    # TODO: replace this with response.fail_for_status when
    # https://github.com/twisted/treq/pull/159 has landed
    if 400 <= response.code < 600:
        raise Error(response.code, response=body)
    returnValue(body)


def render(resource, query_args):
    """
    Render (in the manner of the Nevow appserver) a Nevow ``Page`` or a
    Twisted ``Resource`` against a request with the given query arguments .

    :param resource: The page or resource to render.

    :param query_args: The query arguments to put into the request being
        rendered.  A mapping from ``bytes`` to ``list`` of ``bytes``.

    :return Deferred: A Deferred that fires with the rendered response body as
        ``bytes``.
    """
    ctx = WebContext(tag=resource)
    req = FakeRequest(args=query_args)
    ctx.remember(DefaultExceptionHandler(), ICanHandleException)
    ctx.remember(req, IRequest)
    ctx.remember(None, IData)

    def maybe_concat(res):
        if isinstance(res, bytes):
            return req.v + res
        return req.v

    resource = INevowResource(resource)
    d = maybeDeferred(resource.renderHTTP, ctx)
    d.addErrback(processingFailed, req, ctx)
    d.addCallback(maybe_concat)
    return d
