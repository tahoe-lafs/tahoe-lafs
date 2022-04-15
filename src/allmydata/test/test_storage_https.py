"""
Tests for the TLS part of the HTTP Storage Protocol.

More broadly, these are tests for HTTPS usage as replacement for Foolscap's
server authentication logic, which may one day apply outside of HTTP Storage
Protocol.
"""

from contextlib import asynccontextmanager

from cryptography import x509

from twisted.internet.endpoints import serverFromString
from twisted.internet import reactor
from twisted.internet.task import deferLater
from twisted.web.server import Site
from twisted.web.static import Data
from twisted.web.client import Agent, HTTPConnectionPool, ResponseNeverReceived
from twisted.python.filepath import FilePath
from treq.client import HTTPClient

from .common import SyncTestCase, AsyncTestCase, SameProcessStreamEndpointAssigner
from .certs import (
    generate_certificate,
    generate_private_key,
    private_key_to_file,
    cert_to_file,
)
from ..storage.http_common import get_spki_hash
from ..storage.http_client import _StorageClientHTTPSPolicy
from ..storage.http_server import _TLSEndpointWrapper
from ..util.deferredutil import async_to_deferred


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
        expected_hash = b"JIj6ezHkdSBlHhrnezAgIC_mrVQHy4KAFyL-8ZNPGPM"
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


class PinningHTTPSValidation(AsyncTestCase):
    """
    Test client-side validation logic of HTTPS certificates that uses
    Tahoe-LAFS's pinning-based scheme instead of the traditional certificate
    authority scheme.

    https://cryptography.io/en/latest/x509/tutorial/#creating-a-self-signed-certificate
    """

    def setUp(self):
        self._port_assigner = SameProcessStreamEndpointAssigner()
        self._port_assigner.setUp()
        self.addCleanup(self._port_assigner.tearDown)
        return AsyncTestCase.setUp(self)

    @asynccontextmanager
    async def listen(self, private_key_path: FilePath, cert_path: FilePath):
        """
        Context manager that runs a HTTPS server with the given private key
        and certificate.

        Returns a URL that will connect to the server.
        """
        location_hint, endpoint_string = self._port_assigner.assign(reactor)
        underlying_endpoint = serverFromString(reactor, endpoint_string)
        endpoint = _TLSEndpointWrapper.from_paths(
            underlying_endpoint, private_key_path, cert_path
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
            await deferLater(reactor, 0.01)

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
        private_key = generate_private_key()
        certificate = generate_certificate(private_key)
        async with self.listen(
            private_key_to_file(FilePath(self.mktemp()), private_key),
            cert_to_file(FilePath(self.mktemp()), certificate),
        ) as url:
            response = await self.request(url, certificate)
            self.assertEqual(await response.content(), b"YOYODYNE")

    @async_to_deferred
    async def test_server_certificate_has_wrong_hash(self):
        """
        If the server's certificate hash doesn't match the hash the client
        expects, the request to the server fails.
        """
        private_key1 = generate_private_key()
        certificate1 = generate_certificate(private_key1)
        private_key2 = generate_private_key()
        certificate2 = generate_certificate(private_key2)

        async with self.listen(
            private_key_to_file(FilePath(self.mktemp()), private_key1),
            cert_to_file(FilePath(self.mktemp()), certificate1),
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
        private_key = generate_private_key()
        certificate = generate_certificate(private_key, expires_days=-10)

        async with self.listen(
            private_key_to_file(FilePath(self.mktemp()), private_key),
            cert_to_file(FilePath(self.mktemp()), certificate),
        ) as url:
            response = await self.request(url, certificate)
            self.assertEqual(await response.content(), b"YOYODYNE")

    @async_to_deferred
    async def test_server_certificate_not_valid_yet(self):
        """
        If the server's certificate is only valid starting in The Future, the
        request to the server succeeds if the hash matches the one the client
        expects; start time has no effect.
        """
        private_key = generate_private_key()
        certificate = generate_certificate(
            private_key, expires_days=10, valid_in_days=5
        )

        async with self.listen(
            private_key_to_file(FilePath(self.mktemp()), private_key),
            cert_to_file(FilePath(self.mktemp()), certificate),
        ) as url:
            response = await self.request(url, certificate)
            self.assertEqual(await response.content(), b"YOYODYNE")

    # A potential attack to test is a private key that doesn't match the
    # certificate... but OpenSSL (quite rightly) won't let you listen with that
    # so I don't know how to test that! See
    # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3884
