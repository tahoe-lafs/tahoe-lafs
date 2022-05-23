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

from six import ensure_str

__all__ = [
    "do_http",
    "render",
]

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.web.error import (
    Error,
)
from twisted.python.reflect import (
    fullyQualifiedName,
)
from twisted.internet.defer import (
    succeed,
)
from twisted.web.test.requesthelper import (
    DummyChannel,
)
from twisted.web.error import (
    UnsupportedMethod,
)
from twisted.web.http import (
    NOT_ALLOWED,
)
from twisted.web.server import (
    NOT_DONE_YET,
)

import treq

from ..webish import (
    TahoeLAFSRequest,
)


class VerboseError(Error):
    """Include the HTTP body response too."""

    def __str__(self):
        return Error.__str__(self) + " " + ensure_str(self.response)


@inlineCallbacks
def do_http(method, url, **kwargs):
    """
    Run HTTP query, return Deferred of body as bytes.
    """
    response = yield treq.request(method, url, persistent=False, **kwargs)
    body = yield treq.content(response)
    # TODO: replace this with response.fail_for_status when
    # https://github.com/twisted/treq/pull/159 has landed
    if 400 <= response.code < 600:
        raise VerboseError(
            response.code, response="For request {!r} to {!r}, got: {!r}".format(
                method, url, body))
    returnValue(body)


def render(resource, query_args):
    """
    Render (in the manner of the Twisted Web Site) a Twisted ``Resource``
    against a request with the given query arguments .

    :param resource: The page or resource to render.

    :param query_args: The query arguments to put into the request being
        rendered.  A mapping from ``bytes`` to ``list`` of ``bytes``.

    :return Deferred: A Deferred that fires with the rendered response body as
        ``bytes``.
    """
    channel = DummyChannel()
    request = TahoeLAFSRequest(channel)
    request.method = b"GET"
    request.args = query_args
    request.prepath = [b""]
    request.postpath = []
    try:
        result = resource.render(request)
    except UnsupportedMethod:
        request.setResponseCode(NOT_ALLOWED)
        result = b""

    if isinstance(result, bytes):
        request.write(result)
        done = succeed(None)
    elif result == NOT_DONE_YET:
        if request.finished:
            done = succeed(None)
        else:
            done = request.notifyFinish()
    else:
        raise ValueError(
            "{!r} returned {!r}, required bytes or NOT_DONE_YET.".format(
                fullyQualifiedName(resource.render),
                result,
            ),
        )
    def get_body(ignored):
        complete_response = channel.transport.written.getvalue()
        header, body = complete_response.split(b"\r\n\r\n", 1)
        return body
    done.addCallback(get_body)
    return done
