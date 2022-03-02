"""
Common HTTP infrastructure for the storge server.
"""
from enum import Enum
from base64 import b64encode
from hashlib import sha256

from cryptography.x509 import Certificate
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def swissnum_auth_header(swissnum):  # type: (bytes) -> bytes
    """Return value for ``Authentication`` header."""
    return b"Tahoe-LAFS " + b64encode(swissnum).strip()


class Secrets(Enum):
    """Different kinds of secrets the client may send."""

    LEASE_RENEW = "lease-renew-secret"
    LEASE_CANCEL = "lease-cancel-secret"
    UPLOAD = "upload-secret"


def get_spki_hash(certificate: Certificate) -> bytes:
    """
    Get the public key hash, as per RFC 7469: base64 of sha256 of the public
    key encoded in DER + Subject Public Key Info format.
    """
    public_key_bytes = certificate.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return b64encode(sha256(public_key_bytes).digest()).strip()
