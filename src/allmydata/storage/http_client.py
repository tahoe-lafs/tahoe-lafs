"""
HTTP client that talks to the HTTP storage server.
"""

import base64

# TODO Make sure to import Python version?
from cbor2 import loads, dumps


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
    return b"Tahoe-LAFS " + base64.encodestring(swissnum).strip()


class StorageClient(object):
    """
    HTTP client that talks to the HTTP storage server.
    """

    def __init__(self, url: DecodedURL, swissnum, treq=treq):
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
