"""
Common HTTP infrastructure for the storge server.
"""

from enum import Enum
from base64 import urlsafe_b64encode, b64encode
from hashlib import sha256
from typing import Optional

from cryptography.x509 import Certificate
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from werkzeug.http import parse_options_header
from twisted.web.http_headers import Headers
from twisted.web.iweb import IResponse

CBOR_MIME_TYPE = "application/cbor"


def get_content_type(headers: Headers) -> Optional[str]:
    """
    Get the content type from the HTTP ``Content-Type`` header.

    Returns ``None`` if no content-type was set.
    """
    values = headers.getRawHeaders("content-type", [None]) or [None]
    content_type = parse_options_header(values[0])[0] or None
    return content_type


def response_is_not_html(response: IResponse) -> None:
    """
    During tests, this is registered so we can ensure the web server
    doesn't give us text/html.

    HTML is never correct except in 404, but it's the default for
    Twisted's web server so we assert nothing unexpected happened.
    """
    if response.code != 404:
        assert get_content_type(response.headers) != "text/html"


def swissnum_auth_header(swissnum: bytes) -> bytes:
    """Return value for ``Authorization`` header."""
    return b"Tahoe-LAFS " + b64encode(swissnum).strip()


class Secrets(Enum):
    """Different kinds of secrets the client may send."""

    LEASE_RENEW = "lease-renew-secret"
    LEASE_CANCEL = "lease-cancel-secret"
    UPLOAD = "upload-secret"
    WRITE_ENABLER = "write-enabler"


def get_spki(certificate: Certificate) -> bytes:
    """
    Get the bytes making up the DER encoded representation of the
    `SubjectPublicKeyInfo` (RFC 7469) for the given certificate.
    """
    return certificate.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )

def get_spki_hash(certificate: Certificate) -> bytes:
    """
    Get the public key hash, as per RFC 7469: base64 of sha256 of the public
    key encoded in DER + Subject Public Key Info format.

    We use the URL-safe base64 variant, since this is typically found in NURLs.
    """
    spki_bytes = get_spki(certificate)
    return urlsafe_b64encode(sha256(spki_bytes).digest()).strip().rstrip(b"=")
