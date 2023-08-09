"""
Support for listening with both HTTPS and Foolscap on the same port.

The goal is to make the transition from Foolscap to HTTPS-based protocols as
simple as possible, with no extra configuration needed.  Listening on the same
port means a user upgrading Tahoe-LAFS will automatically get HTTPS working
with no additional changes.

Use ``create_tub_with_https_support()`` creates a new ``Tub`` that has its
``negotiationClass`` modified to be a new subclass tied to that specific
``Tub`` instance.  Calling ``tub.negotiationClass.add_storage_server(...)``
then adds relevant information for a storage server once it becomes available
later in the configuration process.
"""

from __future__ import annotations

from itertools import chain
from typing import cast

from twisted.internet.protocol import Protocol
from twisted.internet.interfaces import IDelayedCall, IReactorFromThreads
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
    Metaclass that allows ``_FoolscapOrHttps`` to pretend to be a
    ``Negotiation`` instance, since Foolscap does some checks like
    ``assert isinstance(protocol, tub.negotiationClass)`` in its internals,
    and sometimes that ``protocol`` is a ``_FoolscapOrHttps`` instance, but
    sometimes it's a ``Negotiation`` instance.
    """

    def __instancecheck__(self, instance):
        return issubclass(instance.__class__, self) or isinstance(instance, Negotiation)


class _FoolscapOrHttps(Protocol, metaclass=_PretendToBeNegotiation):
    """
    Based on initial query, decide whether we're talking Foolscap or HTTP.

    Additionally, pretends to be a ``foolscap.negotiate.Negotiation`` instance,
    since these are created by Foolscap's ``Tub``, by setting this to be the
    tub's ``negotiationClass``.

    Do not instantiate directly, use ``create_tub_with_https_support(...)``
    instead.  The way this class works is that a new subclass is created for a
    specific ``Tub`` instance.
    """

    # These are class attributes; they will be set by
    # create_tub_with_https_support() and add_storage_server().

    # The Twisted HTTPS protocol factory wrapping the storage server HTTP API:
    https_factory: TLSMemoryBIOFactory
    # The tub that created us:
    tub: Tub

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

        http_storage_server = HTTPServer(cast(IReactorFromThreads, reactor), storage_server, swissnum)
        cls.https_factory = TLSMemoryBIOFactory(
            certificate_options,
            False,
            Site(http_storage_server.get_resource()),
        )

        storage_nurls = set()
        # Individual hints can be in the form
        # "tcp:host:port,tcp:host:port,tcp:host:port".
        for location_hint in chain.from_iterable(
            hints.split(",") for hints in cls.tub.locationHints
        ):
            if location_hint.startswith("tcp:") or location_hint.startswith("tor:"):
                scheme, hostname, port = location_hint.split(":")
                if scheme == "tcp":
                    subscheme = None
                else:
                    subscheme = "tor"
                    # If we're listening on Tor, the hostname needs to have an
                    # .onion TLD.
                    assert hostname.endswith(".onion")
                # The I2P scheme is yet not supported by the HTTP client, so we
                # don't want generate a NURL that won't work. This will be
                # fixed in https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4037
                port = int(port)
                storage_nurls.add(
                    build_nurl(
                        hostname,
                        port,
                        str(swissnum, "ascii"),
                        cls.tub.myCertificate.original.to_cryptography(),
                        subscheme
                    )
                )

        return storage_nurls

    def __init__(self, *args, **kwargs):
        self._foolscap: Negotiation = Negotiation(*args, **kwargs)

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
        assert not hasattr(self, "_buffer")
        self._convert_to_negotiation()
        return self.initClient(*args, **kwargs)

    def connectionMade(self):
        self._buffer: bytes = b""
        self._timeout: IDelayedCall = reactor.callLater(
            30, self.transport.abortConnection
        )

    def connectionLost(self, reason):
        if self._timeout.active():
            self._timeout.cancel()

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


def create_tub_with_https_support(**kwargs) -> Tub:
    """
    Create a new Tub that also supports HTTPS.

    This involves creating a new protocol switch class for the specific ``Tub``
    instance.
    """
    the_tub = Tub(**kwargs)

    class FoolscapOrHttpForTub(_FoolscapOrHttps):
        tub = the_tub

    the_tub.negotiationClass = FoolscapOrHttpForTub  # type: ignore
    return the_tub
