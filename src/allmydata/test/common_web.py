from future.utils import PY2

import treq
from twisted.internet.defer import (
    maybeDeferred,
    inlineCallbacks,
    returnValue,
)
from twisted.web.error import Error

@inlineCallbacks
def do_http(method, url, **kwargs):
    response = yield treq.request(method, url, persistent=False, **kwargs)
    body = yield treq.content(response)
    # TODO: replace this with response.fail_for_status when
    # https://github.com/twisted/treq/pull/159 has landed
    if 400 <= response.code < 600:
        raise Error(response.code, response=body)
    returnValue(body)


if PY2:
    # We can only use Nevow on Python 2 and Tahoe-LAFS still *does* use Nevow
    # so prefer the Nevow-based renderer if we can get it.
    from .common_nevow import (
        render,
    )
else:
    # However, Tahoe-LAFS *will* use Twisted Web before too much longer so go
    # ahead and let some tests run against the Twisted Web-based renderer on
    # Python 3.  Later this will become the only codepath.
    from .common_tweb import (
        render,
    )
