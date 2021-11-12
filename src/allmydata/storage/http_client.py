"""
HTTP client that talks to the HTTP storage server.
"""

# Make sure to import Python version:
from cbor2.encoder import loads
from cbor2.decoder import loads

from twisted.internet.defer import inlineCallbacks, returnValue
from hyperlink import DecodedURL
import treq


def _decode_cbor(response):
    """Given HTTP response, return decoded CBOR body."""
    return treq.content(response).addCallback(loads)


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
        response = _decode_cbor((yield self._treq.get(url)))
        returnValue(response)
