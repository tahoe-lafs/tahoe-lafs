"""
Support for listening with both HTTP and Foolscap on the same port.
"""

from enum import Enum
from typing import Optional

from twisted.internet.protocol import Protocol
from twisted.python.failure import Failure
from twisted.internet.ssl import CertificateOptions
from twisted.web.server import Site
from twisted.protocols.tls import TLSMemoryBIOFactory

from foolscap.negotiate import Negotiation

from .storage.http_server import HTTPServer


class ProtocolMode(Enum):
    """Listening mode."""

    UNDECIDED = 0
    FOOLSCAP = 1
    HTTP = 2


class PretendToBeNegotiation(type):
    """😱"""

    def __instancecheck__(self, instance):
        return (instance.__class__ == self) or isinstance(instance, Negotiation)


class FoolscapOrHttp(Protocol, metaclass=PretendToBeNegotiation):
    """
    Based on initial query, decide whether we're talking Foolscap or HTTP.

    Pretends to be a ``foolscap.negotiate.Negotiation`` instance.
    """

    _foolscap: Optional[Negotiation] = None
    _protocol_mode: ProtocolMode = ProtocolMode.UNDECIDED
    _buffer: bytes = b""

    def __init__(self, *args, **kwargs):
        self._foolscap = Negotiation(*args, **kwargs)

    def __setattr__(self, name, value):
        if name in {
            "_foolscap",
            "_protocol_mode",
            "_buffer",
            "transport",
            "__class__",
            "_http",
        }:
            object.__setattr__(self, name, value)
        else:
            setattr(self._foolscap, name, value)

    def __getattr__(self, name):
        return getattr(self._foolscap, name)

    def makeConnection(self, transport):
        Protocol.makeConnection(self, transport)
        self._foolscap.makeConnection(transport)

    def initClient(self, *args, **kwargs):
        # After creation, a Negotiation instance either has initClient() or
        # initServer() called. SInce this is a client, we're never going to do
        # HTTP. Relying on __getattr__/__setattr__ doesn't work, for some
        # reason, so just mutate ourselves appropriately.
        assert not self._buffer
        self.__class__ = Negotiation
        self.__dict__ = self._foolscap.__dict__
        return self.initClient(*args, **kwargs)

    def dataReceived(self, data: bytes) -> None:
        if self._protocol_mode == ProtocolMode.FOOLSCAP:
            return self._foolscap.dataReceived(data)
        if self._protocol_mode == ProtocolMode.HTTP:
            return self._http.dataReceived(data)

        # UNDECIDED mode.
        self._buffer += data
        if len(self._buffer) < 8:
            return

        # Check if it looks like a Foolscap request. If so, it can handle this
        # and later data:
        if self._buffer.startswith(b"GET /id/"):
            # TODO or maybe just self.__class__ here too?
            self._protocol_mode = ProtocolMode.FOOLSCAP
            buf, self._buffer = self._buffer, b""
            return self._foolscap.dataReceived(buf)
        else:
            self._protocol_mode = ProtocolMode.HTTP

            certificate_options = CertificateOptions(
                privateKey=self.certificate.privateKey.original,
                certificate=self.certificate.original,
            )
            http_server = HTTPServer(self.storage_server, self.swissnum)
            factory = TLSMemoryBIOFactory(
                certificate_options, False, Site(http_server.get_resource())
            )
            protocol = factory.buildProtocol(self.transport.getPeer())
            protocol.makeConnection(self.transport)
            protocol.dataReceived(self._buffer)
            # TODO maybe change the __class__
            self._http = protocol

    def connectionLost(self, reason: Failure) -> None:
        if self._protocol_mode == ProtocolMode.FOOLSCAP:
            return self._foolscap.connectionLost(reason)
