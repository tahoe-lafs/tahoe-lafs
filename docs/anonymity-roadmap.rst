.. -*- coding: utf-8-with-signature; fill-column: 77 -*-

=============================
Anonymity Development Roadmap
=============================


Development phases
==================

1. Use Tor for network connectivity and protect identity of client

**note:** Client side is endpoint agnostic and server side has TCP endpoint support.

**Dependencies** ::
 * txsocksx: get this merged upstream -->> https://github.com/david415/txsocksx/tree/endpoint_parsers_retry_socks
 * foolscap#203
 * #1010
 * #517


2. Use I2p for network connectivity and protect identity of client

* txi2p
* Add "endpoint parameters" to Tahoe
    * Servers provide the minimum client endpoint string required to connect to them:
        * ``tcp:example.org:1337``
        * ``ssl:example.org:443``
        * ``i2p:longstring.b32.i2p``
    * Clients may need to extend the strings with client-specific per-type parameters in order to successfully connect:
        * ``tcp:example.org:1337:timeout=60``
        * ``ssl:example.org:443:caCertsDir=/etc/ssl/certs``
        * ``i2p:longstring.b32.i2p:tunnelNick=tahoe:inport=10000``
    * These should be set in ``tahoe.cfg``:
        * ``[node]clientEndpointParams = tcp:timeout=60,ssl:caCertsDir=/etc/ssl/certs,i2p:tunnelNick=tahoe:inport=10000``
    * Tahoe parses, keeps an internal map, applies the relevant params to a client endpoint string before connecting
* Client endpoint string whitelisting
    * Server publishes an endpoint string for a client to connect to
    * A malicious server could publish strings containing client-specific parameters that compromise the user
        * Unsure what parameters could actually be used maliciously on their own, but definitely possible in concert with other attacks.
    * The client should not accept strings that contain client-specific parameters
        * How to tell the difference? Tahoe can't keep a list of everything that is safe.
        * Maybe an endpoint API method that takes a client endpoint string and returns a safe one.


3. endpoint-agnostic Foolscap server side

Completing these two tickets will make Foolscap endpoint agnostic on the server side. Therefore any Twisted server endpoint/parser can be used with Foolscap

* teach twisted to serialize a listeningPort into a client endpoint descriptor - https://twistedmatrix.com/trac/ticket/7603

* new foolscap ticket - Add getClientEndpoint() to use the feature from twisted trac ticket 7603


4. Integrated Tor Hidden Service feature for storage servers

* teach tor to create Hidden Service directories with group rx perms https://trac.torproject.org/projects/tor/ticket/11291

* new txtorcon ticket - Teach endpoint to use control port feature from tor trac ticket 11291

