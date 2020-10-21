
__all__ = [
    "do_http",
    "render",
]

from future.utils import PY2

import treq
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.web.error import Error

from .common_tweb import (
    render,
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
