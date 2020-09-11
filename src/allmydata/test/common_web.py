
import treq
from twisted.internet import defer
from twisted.web.error import Error

@defer.inlineCallbacks
def do_http(method, url, **kwargs):
    response = yield treq.request(method, url, persistent=False, **kwargs)
    body = yield treq.content(response)
    # TODO: replace this with response.fail_for_status when
    # https://github.com/twisted/treq/pull/159 has landed
    if 400 <= response.code < 600:
        raise Error(response.code, response=body)
    defer.returnValue(body)
