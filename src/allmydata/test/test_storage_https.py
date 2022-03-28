"""
Tests for the TLS part of the HTTP Storage Protocol.

More broadly, these are tests for HTTPS usage as replacement for Foolscap's
server authentication logic, which may one day apply outside of HTTP Storage
Protocol.
"""

import datetime
from functools import wraps
from contextlib import asynccontextmanager

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization, hashes

from twisted.internet.endpoints import quoteStringArgument, serverFromString
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.task import deferLater
from twisted.web.server import Site
from twisted.web.static import Data
from twisted.web.client import Agent, HTTPConnectionPool, ResponseNeverReceived
from treq.client import HTTPClient

from .common import SyncTestCase, AsyncTestCase
from ..storage.http_common import get_spki_hash
from ..storage.http_client import _StorageClientHTTPSPolicy


class HTTPSNurlTests(SyncTestCase):
    """Tests for HTTPS NURLs."""

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
        certificate = x509.load_pem_x509_certificate(certificate_text)
        self.assertEqual(get_spki_hash(certificate), expected_hash)


def async_to_deferred(f):
    """
    Wrap an async function to return a Deferred instead.
    """

    @wraps(f)
    def not_async(*args, **kwargs):
        return Deferred.fromCoroutine(f(*args, **kwargs))

    return not_async


class PinningHTTPSValidation(AsyncTestCase):
    """
    Test client-side validation logic of HTTPS certificates that uses
    Tahoe-LAFS's pinning-based scheme instead of the traditional certificate
    authority scheme.

    https://cryptography.io/en/latest/x509/tutorial/#creating-a-self-signed-certificate
    """

    def to_file(self, key_or_cert) -> str:
        """
        Write the given key or cert to a temporary file on disk, return the
        path.
        """
        path = self.mktemp()
        with open(path, "wb") as f:
            if isinstance(key_or_cert, x509.Certificate):
                data = key_or_cert.public_bytes(serialization.Encoding.PEM)
            else:
                data = key_or_cert.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            f.write(data)
        return path

    def generate_private_key(self):
        """Create a RSA private key."""
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def generate_certificate(
        self, private_key, expires_days: int = 10, org_name: str = "Yoyodyne"
    ):
        """Generate a certificate from a RSA private key."""
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.ORGANIZATION_NAME, org_name)]
        )
        expires = datetime.datetime.utcnow() + datetime.timedelta(days=expires_days)
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(min(datetime.datetime.utcnow(), expires))
            .not_valid_after(expires)
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                critical=False,
                # Sign our certificate with our private key
            )
            .sign(private_key, hashes.SHA256())
        )

    @asynccontextmanager
    async def listen(self, private_key_path, cert_path):
        """
        Context manager that runs a HTTPS server with the given private key
        and certificate.

        Returns a URL that will connect to the server.
        """
        endpoint = serverFromString(
            reactor,
            "ssl:privateKey={}:certKey={}:port=0:interface=127.0.0.1".format(
                quoteStringArgument(str(private_key_path)),
                quoteStringArgument(str(cert_path)),
            ),
        )
        root = Data(b"YOYODYNE", "text/plain")
        root.isLeaf = True
        listening_port = await endpoint.listen(Site(root))
        try:
            yield f"https://127.0.0.1:{listening_port.getHost().port}/"
        finally:
            await listening_port.stopListening()
            # Make sure all server connections are closed :( No idea why this
            # is necessary when it's not for IStorageServer HTTPS tests.
            await deferLater(reactor, 0.001)

    def request(self, url: str, expected_certificate: x509.Certificate):
        """
        Send a HTTPS request to the given URL, ensuring that the given
        certificate is the one used via SPKI-hash-based pinning comparison.
        """
        # No persistent connections, so we don't have dirty reactor at the end
        # of the test.
        treq_client = HTTPClient(
            Agent(
                reactor,
                _StorageClientHTTPSPolicy(
                    expected_spki_hash=get_spki_hash(expected_certificate)
                ),
                pool=HTTPConnectionPool(reactor, persistent=False),
            )
        )
        return treq_client.get(url)

    @async_to_deferred
    async def test_success(self):
        """
        If all conditions are met, a TLS client using the Tahoe-LAFS policy can
        connect to the server.
        """
        private_key = self.generate_private_key()
        certificate = self.generate_certificate(private_key)
        async with self.listen(
            self.to_file(private_key), self.to_file(certificate)
        ) as url:
            response = await self.request(url, certificate)
            self.assertEqual(await response.content(), b"YOYODYNE")

    @async_to_deferred
    async def test_server_certificate_has_wrong_hash(self):
        """
        If the server's certificate hash doesn't match the hash the client
        expects, the request to the server fails.
        """
        private_key1 = self.generate_private_key()
        certificate1 = self.generate_certificate(private_key1)
        private_key2 = self.generate_private_key()
        certificate2 = self.generate_certificate(private_key2)

        async with self.listen(
            self.to_file(private_key1), self.to_file(certificate1)
        ) as url:
            with self.assertRaises(ResponseNeverReceived):
                await self.request(url, certificate2)

    @async_to_deferred
    async def test_server_certificate_expired(self):
        """
        If the server's certificate has expired, the request to the server
        succeeds if the hash matches the one the client expects; expiration has
        no effect.
        """
        private_key = self.generate_private_key()
        certificate = self.generate_certificate(private_key, expires_days=-10)

        async with self.listen(
            self.to_file(private_key), self.to_file(certificate)
        ) as url:
            response = await self.request(url, certificate)
            self.assertEqual(await response.content(), b"YOYODYNE")

    # TODO an obvious attack is a private key that doesn't match the
    # certificate... but OpenSSL (quite rightly) won't let you listen with that
    # so I don't know how to test that!
