"""
Support for listening with both HTTP and Foolscap on the same port.
"""

from typing import Optional, Tuple

from twisted.internet.protocol import Protocol
from twisted.internet.interfaces import ITransport
from twisted.internet.ssl import CertificateOptions, PrivateCertificate
from twisted.web.server import Site
from twisted.protocols.tls import TLSMemoryBIOFactory

from foolscap.negotiate import Negotiation

from .storage.http_server import HTTPServer
from .storage.server import StorageServer


class PretendToBeNegotiation(type):
    """😱"""

    def __instancecheck__(self, instance):
        return (instance.__class__ == self) or isinstance(instance, Negotiation)


class FoolscapOrHttp(Protocol, metaclass=PretendToBeNegotiation):
    """
    Based on initial query, decide whether we're talking Foolscap or HTTP.

    Pretends to be a ``foolscap.negotiate.Negotiation`` instance.
    """

    # These three will be set by a subclass in update_foolscap_or_http_class()
    # below.
    swissnum: bytes
    certificate: PrivateCertificate
    storage_server: StorageServer

    def __init__(self, *args, **kwargs):
        self._foolscap: Negotiation = Negotiation(*args, **kwargs)
        self._buffer: bytes = b""

    def __setattr__(self, name, value):
        if name in {
            "_foolscap",
            "_buffer",
            "transport",
            "__class__",
        }:
            object.__setattr__(self, name, value)
        else:
            setattr(self._foolscap, name, value)

    def __getattr__(self, name):
        return getattr(self._foolscap, name)

    def _convert_to_negotiation(self):
        """
        Convert self to a ``Negotiation`` instance, return any buffered
        bytes and the transport if any.
        """
        self.__class__ = Negotiation  # type: ignore
        self.__dict__ = self._foolscap.__dict__

    def initClient(self, *args, **kwargs):
        # After creation, a Negotiation instance either has initClient() or
        # initServer() called. Since this is a client, we're never going to do
        # HTTP, so we can immediately become a Negotiation instance.
        assert not self._buffer
        self._convert_to_negotiation()
        return self.initClient(*args, **kwargs)

    def dataReceived(self, data: bytes) -> None:
        """Handle incoming data.

        Once we've decided which protocol we are, update self.__class__, at
        which point all methods will be called on the new class.
        """
        self._buffer += data
        if len(self._buffer) < 8:
            return

        # Check if it looks like a Foolscap request. If so, it can handle this
        # and later data:
        if self._buffer.startswith(b"GET /id/"):
            transport = self.transport
            buf = self._buffer
            self._convert_to_negotiation()
            self.makeConnection(transport)
            self.dataReceived(buf)
            return
        else:
            certificate_options = CertificateOptions(
                privateKey=self.certificate.privateKey.original,
                certificate=self.certificate.original,
            )
            http_server = HTTPServer(self.storage_server, self.swissnum)
            factory = TLSMemoryBIOFactory(
                certificate_options, False, Site(http_server.get_resource())
            )
            assert self.transport is not None
            protocol = factory.buildProtocol(self.transport.getPeer())
            protocol.makeConnection(self.transport)
            protocol.dataReceived(self._buffer)
            self.__class__ = protocol.__class__
            self.__dict__ = protocol.__dict__


def create_foolscap_or_http_class():
    class FoolscapOrHttpWithCert(FoolscapOrHttp):
        pass

    return FoolscapOrHttpWithCert


def update_foolscap_or_http_class(cls, certificate, storage_server, swissnum):
    cls.certificate = certificate
    cls.storage_server = storage_server
    cls.swissnum = swissnum
