"""
HTTP client that talks to the HTTP storage server.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2

if PY2:
    # fmt: off
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
    # fmt: on
else:
    # typing module not available in Python 2, and we only do type checking in
    # Python 3 anyway.
    from typing import Union
    from treq.testing import StubTreq

import base64

# TODO Make sure to import Python version?
from cbor2 import loads


from twisted.web.http_headers import Headers
from twisted.internet.defer import inlineCallbacks, returnValue, fail
from hyperlink import DecodedURL
import treq


class ClientException(Exception):
    """An unexpected error."""


def _decode_cbor(response):
    """Given HTTP response, return decoded CBOR body."""
    if response.code > 199 and response.code < 300:
        return treq.content(response).addCallback(loads)
    return fail(ClientException(response.code, response.phrase))


def swissnum_auth_header(swissnum):  # type: (bytes) -> bytes
    """Return value for ``Authentication`` header."""
    return b"Tahoe-LAFS " + base64.b64encode(swissnum).strip()


class StorageClient(object):
    """
    HTTP client that talks to the HTTP storage server.
    """

    def __init__(
        self, url, swissnum, treq=treq
    ):  # type: (DecodedURL, bytes, Union[treq,StubTreq]) -> None
        self._base_url = url
        self._swissnum = swissnum
        self._treq = treq

    def _get_headers(self):  # type: () -> Headers
        """Return the basic headers to be used by default."""
        headers = Headers()
        headers.addRawHeader(
            "Authorization",
            swissnum_auth_header(self._swissnum),
        )
        return headers

    @inlineCallbacks
    def get_version(self):
        """
        Return the version metadata for the server.
        """
        url = self._base_url.click("/v1/version")
        response = yield self._treq.get(url, headers=self._get_headers())
        decoded_response = yield _decode_cbor(response)
        returnValue(decoded_response)
