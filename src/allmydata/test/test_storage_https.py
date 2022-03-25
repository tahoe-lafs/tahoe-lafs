"""
Tests for the TLS part of the HTTP Storage Protocol.

More broadly, these are tests for HTTPS usage as replacement for Foolscap's
server authentication logic, which may one day apply outside of HTTP Storage
Protocol.
"""

from cryptography.x509 import load_pem_x509_certificate

from .common import SyncTestCase
from ..storage.http_common import get_spki_hash


class HTTPFurlTests(SyncTestCase):
    """Tests for HTTP furls."""

    def test_spki_hash(self):
        """The output of ``get_spki_hash()`` matches the semantics of RFC 7469.

        The expected hash was generated using Appendix A instructions in the
        RFC::

            openssl x509 -noout -in certificate.pem -pubkey | \
                openssl asn1parse -noout -inform pem -out public.key
            openssl dgst -sha256 -binary public.key | openssl enc -base64
        """
        expected_hash = b"JIj6ezHkdSBlHhrnezAgIC/mrVQHy4KAFyL+8ZNPGPM"
        certificate_text = b"""\
-----BEGIN CERTIFICATE-----
MIIDWTCCAkECFCf+I+3oEhTfqt+6ruH4qQ4Wst1DMA0GCSqGSIb3DQEBCwUAMGkx
CzAJBgNVBAYTAlpaMRAwDgYDVQQIDAdOb3doZXJlMRQwEgYDVQQHDAtFeGFtcGxl
dG93bjEcMBoGA1UECgwTRGVmYXVsdCBDb21wYW55IEx0ZDEUMBIGA1UEAwwLZXhh
bXBsZS5jb20wHhcNMjIwMzAyMTUyNTQ3WhcNMjMwMzAyMTUyNTQ3WjBpMQswCQYD
VQQGEwJaWjEQMA4GA1UECAwHTm93aGVyZTEUMBIGA1UEBwwLRXhhbXBsZXRvd24x
HDAaBgNVBAoME0RlZmF1bHQgQ29tcGFueSBMdGQxFDASBgNVBAMMC2V4YW1wbGUu
Y29tMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv9vqtA8Toy9D6xLG
q41iUafSiAXnuirWxML2ct/LAcGJzATg6JctmJxxZQL7vkmaFFPBF6Y39bOGbbEC
M2iQYn2Qemj5fl3IzKTnYLqzryGM0ZwwnNbPyetSe/sksAIYRLzn49d6l+AHR+Dj
GyvoLzIyGUTn41MTDafMNtPgWx1i+65lFW3GHYpEmugu4bjeUPizNja2LrqwvwFu
YXwmKxbIMdioCoRvDGX9SI3/euFstuR4rbOEUDxniYRF5g6reP8UMF30zJzF5j0k
yDg8Z5b1XpKFNZAeyRYxcs9wJCqVlP6BLPDnvNVpMXodnWLeTK+r6YWvGadGVufk
YNC1PwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQByrhn78GSS3dJ0pJ6czmhMX5wH
+fauCtt1+Wbn+ctTodTycS+pfULO4gG7wRzhl8KNoOqLmWMjyA2A3mon8kdkD+0C
i8McpoPaGS2wQcqC28Ud6kP9YO81YFyTl4nHVKQ0nmplT+eoLDTCIWMVxHHzxIgs
2ybUluAc+THSjpGxB6kWSAJeg3N+f2OKr+07Yg9LiQ2b8y0eZarpiuuuXCzWeWrQ
PudP0aniyq/gbPhxq0tYF628IBvhDAnr/2kqEmVF2TDr2Sm/Y3PDBuPY6MeIxjnr
ox5zO3LrQmQw11OaIAs2/kviKAoKTFFxeyYcpS5RuKNDZfHQCXlLwt9bySxG
-----END CERTIFICATE-----
"""
        certificate = load_pem_x509_certificate(certificate_text)
        self.assertEqual(get_spki_hash(certificate), expected_hash)
