"""
HTTP client that talks to the HTTP storage server.
"""

# TODO Make sure to import Python version?
from cbor2 import loads, dumps

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


class StorageClient(object):
    """
    HTTP client that talks to the HTTP storage server.
    """

    def __init__(self, url: DecodedURL, swissnum, treq=treq):
        self._base_url = url
        self._swissnum = swissnum
        self._treq = treq

    @inlineCallbacks
    def get_version(self):
        """
        Return the version metadata for the server.
        """
        url = self._base_url.child("v1", "version")
        response = yield self._treq.get(url)
        decoded_response = yield _decode_cbor(response)
        returnValue(decoded_response)
