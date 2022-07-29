"""
Support for listening with both HTTPS and Foolscap on the same port.

The goal is to make the transition from Foolscap to HTTPS-based protocols as
simple as possible, with no extra configuration needed.  Listening on the same
port means a user upgrading Tahoe-LAFS will automatically get HTTPS working
with no additional changes.

Use ``support_foolscap_and_https()`` to create a new subclass for a ``Tub``
instance, and then ``add_storage_server()`` on the resulting class to add the
relevant information for a storage server once it becomes available later in
the configuration process.
"""

from __future__ import annotations

from twisted.internet.protocol import Protocol
from twisted.internet.interfaces import IDelayedCall
from twisted.internet.ssl import CertificateOptions
from twisted.web.server import Site
from twisted.protocols.tls import TLSMemoryBIOFactory
from twisted.internet import reactor

from hyperlink import DecodedURL
from foolscap.negotiate import Negotiation
from foolscap.api import Tub

from .storage.http_server import HTTPServer, build_nurl
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

    Do not instantiate directly, use ``support_foolscap_and_https(tub)``
    instead.  The way this class works is that a new subclass is created for a
    specific ``Tub`` instance.
    """

    # These are class attributes; they will be set by
    # support_foolscap_and_https() and add_storage_server().

    # The Twisted HTTPS protocol factory wrapping the storage server HTTP API:
    https_factory: TLSMemoryBIOFactory
    # The tub that created us:
    tub: Tub

    # This is an instance attribute; it will be set in connectionMade().
    _timeout: IDelayedCall

    @classmethod
    def add_storage_server(
        cls, storage_server: StorageServer, swissnum: bytes
    ) -> set[DecodedURL]:
        """
        Update a ``_FoolscapOrHttps`` subclass for a specific ``Tub`` instance
        with the class attributes it requires for a specific storage server.

        Returns the resulting NURLs.
        """
        # We need to be a subclass:
        assert cls != _FoolscapOrHttps
        # The tub instance must already be set:
        assert hasattr(cls, "tub")
        assert isinstance(cls.tub, Tub)

        # Tub.myCertificate is a twisted.internet.ssl.PrivateCertificate
        # instance.
        certificate_options = CertificateOptions(
            privateKey=cls.tub.myCertificate.privateKey.original,
            certificate=cls.tub.myCertificate.original,
        )

        http_storage_server = HTTPServer(storage_server, swissnum)
        cls.https_factory = TLSMemoryBIOFactory(
            certificate_options,
            False,
            Site(http_storage_server.get_resource()),
        )

        storage_nurls = set()
        for location_hint in cls.tub.locationHints:
            if location_hint.startswith("tcp:"):
                _, hostname, port = location_hint.split(":")
                port = int(port)
                storage_nurls.add(
                    build_nurl(
                        hostname,
                        port,
                        str(swissnum, "ascii"),
                        cls.tub.myCertificate.original.to_cryptography(),
                    )
                )
            # TODO this is probably where we'll have to support Tor and I2P?
            # See https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3888#comment:9
            # for discussion (there will be separate tickets added for those at
            # some point.)
        return storage_nurls

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
            assert self.transport is not None
            protocol = self.https_factory.buildProtocol(self.transport.getPeer())
            protocol.makeConnection(self.transport)
            protocol.dataReceived(self._buffer)

            # Update the factory so it knows we're transforming to a new
            # protocol object (we'll do that next)
            value = self.https_factory.protocols.pop(protocol)
            self.https_factory.protocols[self] = value

            # Transform self into the TLS protocol 🪄
            self.__class__ = protocol.__class__
            self.__dict__ = protocol.__dict__


def support_foolscap_and_https(tub: Tub):
    """
    Create a new Foolscap-or-HTTPS protocol class for a specific ``Tub``
    instance.
    """
    the_tub = tub

    class FoolscapOrHttpForTub(_FoolscapOrHttps):
        tub = the_tub

    tub.negotiationClass = FoolscapOrHttpForTub  # type: ignore
