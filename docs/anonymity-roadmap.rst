.. -*- coding: utf-8-with-signature; fill-column: 77 -*-

=============================
Anonymity Development Roadmap
=============================


Development phases
==================

1. Use Tor for network connectivity and to protect identity of client

**note:** Client side is endpoint agnostic and server side has TCP endpoint support.

**Dependencies** ::
 * txsocksx: get this merged upstream -->> https://github.com/david415/txsocksx/tree/endpoint_parsers_retry_socks
 * foolscap#203
 * #1010
 * #517


2. Use I2p for network connectivity and to protect identity of client

**Dependencies** ::
 * new Tahoe-LAFS trac ticket regarding client endpoint string parameter concatenation
 * txi2p

3. endpoint-agnostic Foolscap server side

Completing these two tickets will make Foolscap endpoint agnostic on the server side. Therefore any Twisted server endpoint/parser can be used with Foolscap

* teach twisted to serialize a listeningPort into a client endpoint descriptor - https://twistedmatrix.com/trac/ticket/7603

* new foolscap ticket - Add getClientEndpoint() to use the feature from twisted trac ticket 7603


4. Integrated Tor Hidden Service feature for storage servers

* teach tor to create Hidden Service directories with group rx perms https://trac.torproject.org/projects/tor/ticket/11291

* new txtorcon ticket - Teach endpoint to use control port feature from tor trac ticket 11291

