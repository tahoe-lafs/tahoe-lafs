.. -*- coding: utf-8 -*-

HTTP Introducer Internals
=========================

This document explains the implementation of the "HTTP Introducer" system.
The intended audience is Tahoe-LAFS maintainers and other developers interested in the inner-workings of the software.
For user-focused documentation see :doc:`http-introducer`.

Overview
--------

The HTTP Introducer is very similar to the Foolscap-based introducer it replaces.
The primary difference is that it replaces the Foolscap-based protocol with a WebSocket-based protocol.
In every other way,
the HTTP Introducer is intended to replicate the behavior (including security properties) of the Foolscap-based introducer.

http-introducer
---------------

The HTTP Introducer comprises a new command-line interface,
a long-running process,
and configuration for that process.


Configuration
-------------

The command-line interface is configured with a JSON document.
The JSON document follows this form::

  { "version": 1
  , "listen-endpoints": [ <string> ]
  , "network-location": [ <string> ]
  , "certificate": <string>
  , "private-key": <string>
  , "swissnum": <string>
  }

The *version* property must be **1**.
The values for the *listen-endpoints*, *certificate*, and *private-key* properties are those given to the ``create`` command.
The *swissnum* property is a string that is randomly generated at ``create``-time.

Introducer NURL
---------------

The HTTP Introducer uses the same :ref:`NURL <../specification/url>` as the Foolscap-based Introducer it is intended to replace.

Protocol
--------

The long-running process operates a TLS server on its configured endpoint.
It uses the configured certificate and private key for the necessary TLS negotiation.
On top of TLS,
it runs a WebSocket server with one endpoint.

The client validates the server according to the general rules for NURLs.
The server validates the client only to the extent that the client must already know the swissnum to access the endpoint.

The WebSocket URL to access is derived from the introducer NURL.
The scheme is changed from "pb" to "wss".
The netloc is changed to the ``net-loc`` where the connection attempt succeeded.
The certificate hash and "@" are dropped.
The path remains the same.
The fragment is dropped.

For example if *example.invalid.:123* accepts the connection then the NURL::

  pb://aaaaaaaa@example.invalid.:123,example2.invalid.:234/bbbbbbb#v=1

becomes::

  wss://example.invalid.:123/bbbbbbb

This is exactly the endpoint where the introduction protocol is used to exchange announcements.

.. TODO: Add docs about the WebSocket protocol negotiation that happens for the pub/sub protocol
.. TODO: Add discussion of connection management, esp reconnection on lost connection.

Publish
~~~~~~~

To publish an announcement a client sends a JSON message that represents the announcement.
A storage announcement is a small JSON document that looks something like this::

   {"pub-v0-p46y...":
     { "ann":
       { anonymous-storage-FURL": "pb://sokl...@192.168.69.247:44801/eqpw..."
       , "nickname": "storage001"
       }
     }
   }

The top-level key is a v0 node public key identifying the node the announcement describes.
The next level key is always the string ``"ann"``.
Keys in the next level give specific information for how to connect to the storage server.
Depending on how the storage server is exposed,
these details may vary in structure.

A storage announcement is created by a storage server trying to expose a storage service.
The storage server can send one of these messages at any time to initially announce itself or update a previous announcement.

Subscribe
~~~~~~~~~

To subscribe to announcements a client only needs to read messages sent by the server.
On initial connection every storage announcement that has been received by the server is produced.
Subsequently,
whenever a storage announcement is published it is delivered to all subscribers.
The JSON documents delivered to a subscriber are exactly the same as the JSON documents published to the server.

Failure Modes
-------------

HTTP Introducer imposes the following requirements:

* It must be possible to establish a network connection from storage servers and client nodes to HTTP introducer.
  * If these connections cannot be established then announcements cannot be published or delivered to subscribers.
* Storage servers must retain the *introducer fURL*.
  If they lose it they can no longer publish announcements.
* Clients must retain the *introducer fURL*.
  If they lose it they can no longer subscribe to announcements.
* All participants *may* be required to keep the *introducer fURL* secret.
  The client is not discerning about choosing between storage announcements.
  Anyone who holds the *introducer fURL* may send an announcement to all clients using that introducer.
  Any client receiving such an announcement may use it.
* An administrator must retain the HTTP Introducer state.
  If it is lost the introducer will be unable to operate.
  In this case new configuration must be distributed to all storage servers and client nodes.
* An administrator must keep the HTTP Introducer state secret.
  It must be kept secret or another agent will be able to pose as the introducer.
  The other agent can use this state to send announcements to the real introducer.
  It can also use it to (fraudulently) prove to a client that it is the real introducer.
  This would allow it generate arbitrary announcements for clients or deny service.

Discussion
----------

Protocol Negotiation
~~~~~~~~~~~~~~~~~~~~

HTTP Introducer uses the same URL scheme as the Foolscap Introducer.
How does a client know what protocol to speak to the introducer?

Both introducers actually use TLS between their application-level protocol and TCP.
This presents one option for negotiating a protocol to speak: TLS ALPN.

There are three possible client types:

* Foolscap-only introducer client
* Foolscap and HTTP introducer client
* HTTP-only introducer client

And likewise three possible server types:

* Foolscap-only introducer server
* Foolscap and HTTP introducer server
* HTTP-only introducer server

A Foolscap-only introducer client includes no ALPN section in its TLS handshake.
A combined Foolscap and HTTP introducer client places the protocols "http/1.1" and "pb" in its TLS ALPN section.
An HTTP-only introducer client places only the protocol "http/1.1" in its TLS ALPN section.

A Foolscap-only introducer server ignores the TLS ALPN information.
It returns no protocol selection and always speaks Foolscap.

A Foolscap and HTTP introducer server will try to negotiate the HTTP protocol.
It will return "http/1.1" and speak the HTTP protocol whenever the client offers this.
Otherwise it will return "pb" and speak Foolscap.

An HTTP-only introducer server will try to negotiate the HTTP protocol.
It will return "http/1.1" and speak the HTTP protocol whenever the client offers this.
Otherwise it will return `a fatal TLS alert`_ and end the session.

This allows provides a transition path from the Foolscap-only world to an HTTP-only world.
Existing clients and servers can be upgraded independently to dual-protocol versions.
New HTTP-only clients can be introduced during this period as long as they are used with HTTP-capable servers.
New HTTP-only servers can also be developed for use by HTTP-capable clients.
After operators and users have had ample time to perform these upgrades the Foolscap capabilities can be removed from client and server.
As operators and users continue to upgrade Foolscap support will dwindle and eventually disappear from the ecosystem.

.. _`a fatal TLS alert`: https://tools.ietf.org/html/rfc7301#section-3.2
