from zope.interface import (
    classImplements,
)
from twisted.python.reflect import (
    fullyQualifiedName,
)
from twisted.internet.defer import (
    succeed,
)
from twisted.web.test.requesthelper import (
    DummyRequest,
)
from twisted.web.iweb import (
    IRequest,
)
from twisted.web.server import (
    NOT_DONE_YET,
)

classImplements(DummyRequest, IRequest)

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
    request = DummyRequest([])
    request.args = query_args
    result = resource.render(request)
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
    done.addCallback(lambda ignored: b"".join(request.written))
    return done
