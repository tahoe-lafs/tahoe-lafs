"""
Support for listening with both HTTPS and Foolscap on the same port.

The goal is to make the transition from Foolscap to HTTPS-based protocols as
simple as possible, with no extra configuration needed.  Listening on the same
port means a user upgrading Tahoe-LAFS will automatically get HTTPS working
with no additional changes.

Use ``create_foolscap_or_http_class()`` to create a new subclass per ``Tub``,
and then ``update_foolscap_or_http_class()`` to add the relevant information to
the subclass once it becomes available later in the configuration process.
"""

from twisted.internet.protocol import Protocol
from twisted.internet.interfaces import IDelayedCall
from twisted.internet.ssl import CertificateOptions, PrivateCertificate
from twisted.web.server import Site
from twisted.protocols.tls import TLSMemoryBIOFactory
from twisted.internet import reactor

from foolscap.negotiate import Negotiation

from .storage.http_server import HTTPServer
from .storage.server import StorageServer


class _PretendToBeNegotiation(type):
    """
    Metaclass that allows ``_FoolscapOrHttps`` to pretend to be a ``Negotiation``
    instance, since Foolscap has some ``assert isinstance(protocol,
    Negotiation`` checks.
    """

    def __instancecheck__(self, instance):
        return (instance.__class__ == self) or isinstance(instance, Negotiation)


class _FoolscapOrHttps(Protocol, metaclass=_PretendToBeNegotiation):
    """
    Based on initial query, decide whether we're talking Foolscap or HTTP.

    Additionally, pretends to be a ``foolscap.negotiate.Negotiation`` instance,
    since these are created by Foolscap's ``Tub``, by setting this to be the
    tub's ``negotiationClass``.

    Do not use directly; this needs to be subclassed per ``Tub``.
    """

    # These three will be set by a subclass in update_foolscap_or_http_class()
    # below.
    swissnum: bytes
    certificate: PrivateCertificate
    storage_server: StorageServer

    _timeout: IDelayedCall

    def __init__(self, *args, **kwargs):
        self._foolscap: Negotiation = Negotiation(*args, **kwargs)
        self._buffer: bytes = b""

    def __setattr__(self, name, value):
        if name in {"_foolscap", "_buffer", "transport", "__class__", "_timeout"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._foolscap, name, value)

    def __getattr__(self, name):
        return getattr(self._foolscap, name)

    def _convert_to_negotiation(self):
        """
        Convert self to a ``Negotiation`` instance.
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

    def connectionMade(self):
        self._timeout = reactor.callLater(30, self.transport.abortConnection)

    def dataReceived(self, data: bytes) -> None:
        """Handle incoming data.

        Once we've decided which protocol we are, update self.__class__, at
        which point all methods will be called on the new class.
        """
        self._buffer += data
        if len(self._buffer) < 8:
            return

        # Check if it looks like a Foolscap request. If so, it can handle this
        # and later data, otherwise assume HTTPS.
        self._timeout.cancel()
        if self._buffer.startswith(b"GET /id/"):
            # We're a Foolscap Negotiation server protocol instance:
            transport = self.transport
            buf = self._buffer
            self._convert_to_negotiation()
            self.makeConnection(transport)
            self.dataReceived(buf)
            return
        else:
            # We're a HTTPS protocol instance, serving the storage protocol:
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
    """
    Create a new Foolscap-or-HTTPS protocol class for a specific ``Tub``
    instance.
    """

    class FoolscapOrHttpWithCert(_FoolscapOrHttps):
        pass

    return FoolscapOrHttpWithCert


def update_foolscap_or_http_class(cls, certificate, storage_server, swissnum):
    """
    Add the various parameters needed by a ``Tub``-specific
    ``_FoolscapOrHttps`` subclass.
    """
    cls.certificate = certificate
    cls.storage_server = storage_server
    cls.swissnum = swissnum
