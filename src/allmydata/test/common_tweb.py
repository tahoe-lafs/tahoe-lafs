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

from ..webish import (
    TahoeLAFSRequest,
)


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
