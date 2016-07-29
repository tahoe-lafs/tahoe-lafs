.. -*- coding: utf-8-with-signature; fill-column: 77 -*-

==============================
Anonymity Development Roadmap
==============================



Development phases
==================


Phase 1. Use Tor and I2P for network connectivity and to protect identity of client
-----------------------------------------------------------------------------------

**note:** Client side is endpoint agnostic and server side has TCP endpoint support only.

**Dependencies**

* txsocksx: get this merged upstream -->> https://github.com/david415/txsocksx/tree/endpoint_parsers_retry_socks - *client Twisted endpoint for Tor*
* `Foolscap trac ticket 203`_: *switch to using Twisted Endpoints*
* `Tahoe-LAFS trac ticket 1010`_: *anonymous client mode*
* `Tahoe-LAFS trac ticket 517`_: *make tahoe Tor- and I2P-friendly*

.. _`Foolscap trac ticket 203`: http://foolscap.lothar.com/trac/ticket/203
.. _`Tahoe-LAFS trac ticket 1010`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1010
.. _`Tahoe-LAFS trac ticket 517`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/517




Phase 2. Endpoint-agnostic Foolscap server side
-----------------------------------------------

Completing these two tickets will make Foolscap endpoint agnostic
on the server side. Therefore any Twisted server endpoint/parser
can be used with Foolscap

#. Teach twisted to serialize a listeningPort into a client endpoint
   descriptor - https://twistedmatrix.com/trac/ticket/7603
#. open new foolscap ticket - Add getClientEndpoint() to use the feature
   from twisted trac ticket 7603


Phase 3. Integrated Tor and I2P Hidden Service feature for storage servers
--------------------------------------------------------------------------

#. open new txtorcon ticket - Teach endpoint to use control port feature
   from tor trac ticket 11291

#. open new txi2p ticket - Add support for starting a local instance
