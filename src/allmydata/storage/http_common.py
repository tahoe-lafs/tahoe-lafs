"""
Common HTTP infrastructure for the storge server.
"""

from enum import Enum
from base64 import b64encode
from typing import Optional

from werkzeug.http import parse_options_header
from twisted.web.http_headers import Headers

CBOR_MIME_TYPE = "application/cbor"


def get_content_type(headers: Headers) -> Optional[str]:
    """
    Get the content type from the HTTP ``Content-Type`` header.

    Returns ``None`` if no content-type was set.
    """
    values = headers.getRawHeaders("content-type") or [None]
    content_type = parse_options_header(values[0])[0] or None
    return content_type


def swissnum_auth_header(swissnum):  # type: (bytes) -> bytes
    """Return value for ``Authentication`` header."""
    return b"Tahoe-LAFS " + b64encode(swissnum).strip()


class Secrets(Enum):
    """Different kinds of secrets the client may send."""

    LEASE_RENEW = "lease-renew-secret"
    LEASE_CANCEL = "lease-cancel-secret"
    UPLOAD = "upload-secret"
