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
    from typing import Union, Set, List
    from treq.testing import StubTreq

from base64 import b64encode

import attr

# TODO Make sure to import Python version?
from cbor2 import loads


from twisted.web.http_headers import Headers
from twisted.internet.defer import inlineCallbacks, returnValue, fail, Deferred
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
    return b"Tahoe-LAFS " + b64encode(swissnum).strip()


@attr.s
class ImmutableCreateResult(object):
    """Result of creating a storage index for an immutable."""

    already_have = attr.ib(type=Set[int])
    allocated = attr.ib(type=Set[int])


class StorageClientImmutables(object):
    """
    APIs for interacting with immutables.
    """

    def __init__(self, client):  # type: (StorageClient) -> None
        self._client = client

    @inlineCallbacks
    def create(
        self,
        storage_index,
        share_numbers,
        allocated_size,
        upload_secret,
        lease_renew_secret,
        lease_cancel_secret,
    ):  # type: (bytes, List[int], int, bytes, bytes, bytes) -> Deferred[ImmutableCreateResult]
        """
        Create a new storage index for an immutable.

        TODO retry internally on failure, to ensure the operation fully
        succeeded.  If sufficient number of failures occurred, the result may
        fire with an error, but there's no expectation that user code needs to
        have a recovery codepath; it will most likely just report an error to
        the user.

        Result fires when creating the storage index succeeded, if creating the
        storage index failed the result will fire with an exception.
        """

    @inlineCallbacks
    def write_share_chunk(
        self, storage_index, share_number, upload_secret, offset, data
    ):  # type: (bytes, int, bytes, int, bytes) -> Deferred[bool]
        """
        Upload a chunk of data for a specific share.

        TODO The implementation should retry failed uploads transparently a number
        of times, so that if a failure percolates up, the caller can assume the
        failure isn't a short-term blip.

        Result fires when the upload succeeded, with a boolean indicating
        whether the _complete_ share (i.e. all chunks, not just this one) has
        been uploaded.
        """

    @inlineCallbacks
    def read_share_chunk(
        self, storage_index, share_number, offset, length
    ):  # type: (bytes, int, int, int) -> Deferred[bytes]
        """
        Download a chunk of data from a share.

        TODO Failed downloads should be transparently retried and redownloaded
        by the implementation a few times so that if a failure percolates up,
        the caller can assume the failure isn't a short-term blip.

        NOTE: the underlying HTTP protocol is much more flexible than this API,
        so a future refactor may expand this in order to simplify the calling
        code and perhaps download data more efficiently.  But then again maybe
        the HTTP protocol will be simplified, see
        https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3777
        """


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

    def _request(self, method, url, secrets, **kwargs):
        """
        Like ``treq.request()``, but additional argument of secrets mapping
        ``http_server.Secret`` to the bytes value of the secret.
        """
        headers = self._get_headers()
        for key, value in secrets.items():
            headers.addRawHeader(
                "X-Tahoe-Authorization",
                b"%s %s" % (key.value.encode("ascii"), b64encode(value).strip()),
            )
        return self._treq.request(method, url, headers=headers, **kwargs)

    @inlineCallbacks
    def get_version(self):
        """
        Return the version metadata for the server.
        """
        url = self._base_url.click("/v1/version")
        response = yield self._request("GET", url, {})
        decoded_response = yield _decode_cbor(response)
        returnValue(decoded_response)
