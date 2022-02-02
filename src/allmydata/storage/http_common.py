"""
Common HTTP infrastructure for the storge server.
"""
from future.utils import PY2

if PY2:
    # fmt: off
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
    # fmt: on

from enum import Enum
from base64 import b64encode


def swissnum_auth_header(swissnum):  # type: (bytes) -> bytes
    """Return value for ``Authentication`` header."""
    return b"Tahoe-LAFS " + b64encode(swissnum).strip()


class Secrets(Enum):
    """Different kinds of secrets the client may send."""

    LEASE_RENEW = "lease-renew-secret"
    LEASE_CANCEL = "lease-cancel-secret"
    UPLOAD = "upload-secret"
