"""
Implement a work-around for <https://github.com/twisted/nevow/issues/106>.
"""

from __future__ import (
    print_function,
    unicode_literals,
    absolute_import,
    division,
)

from nevow import inevow
from twisted.internet import defer

def renderHTTP(self, ctx):
    request = inevow.IRequest(ctx)
    if self.real_prepath_len is not None:
        request.postpath = request.prepath + request.postpath
        request.prepath = request.postpath[:self.real_prepath_len]
        del request.postpath[:self.real_prepath_len]
    result = defer.maybeDeferred(self.original.render, request).addCallback(
        self._handle_NOT_DONE_YET, request)
    return result


def patch():
    """
    Monkey-patch the proposed fix into place.
    """
    from nevow.appserver import OldResourceAdapter
    OldResourceAdapter.renderHTTP = renderHTTP
