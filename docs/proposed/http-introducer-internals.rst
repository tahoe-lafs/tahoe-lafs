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


Open Questions
--------------

URL Scheme
~~~~~~~~~~

What URL scheme does the HTTP Introducer URL use?
Foolscap URLs use *pb*
(deriving from Foolscap's origin as the successor of Twisted **P**\ erspective **B**\ roker).

PB
```

HTTP Introducer could continue to use *pb*.
This would sensibly reflect the fact that two properties remain the same:

* The URL has the same self-certifying capability nature as the Foolscap URL is supersedes.
  "tubid" has been replaced with the SPKI hash which serves a parallel purpose.
  "swissnum" remains an unguessable string which imbues the URL with the capability nature.
* It refers to the same underlying service.
  Only the protocol is changing.

It is common to think of a URL scheme as identifying a protocol
(https, wss, xmpp, etc).
However this only reflects the fact that the vast majority of resources seem to be accessible via only one protocol and so that protocol and the resource itself become conflated.
There *are* URL schemes which are protocol agnostic
(mailto, im, pres, etc).
The "https" example is also something of a trick.
The resource identified by an "https"-scheme URL may be reachable via any one (or more!) of several different HTTP-family protocols
(0.9, 1.0, 1.1, SPDY, 2.0, and likely soon 3.0).

In implementation terms the complication introduced by using "pb" is that the client is not given a signal as to which protocol to use to attempt to interact with the resource.
This could be addressed in one of at least two ways:

1. Try all protocols the client supports concurrently and allow the attempts with the incorrect protocol to fail gracefully.
   The result of this could also be remembered to avoid the need for such concurrent efforts on all future connection attempts.
2. Use one of the protocol negotiation features of TLS (eg ALPN).
   This is exactly the mechanism used to negotiate the version of the HTTP protocol.
   A substantial challenge here is that it intertwines ALPN with certificate negotiation and this may be difficult to navigate with some or all TLS implementations.
   This *could* be mitigated by replacing the SPKI hash with the tubid.
   This would cause the certificate validation logic to be the same regardless of whether Foolscap or this new protocol is negotiated.

pb+http
```````

HTTP Introducer could continue to use *pb* but with a hint about its divergence from the default/historical wire protocol.
This is similar to the "git+https" scheme.
An advantage of this scheme is that it makes the protocol explicit and removes the need for further negotiation.
A disadvantage is that since it forces the new HTTP protocol it requires two different URLs if there is to be a period where both protocols are offered.
The two URLs must each be transmitted to to clients somehow.
Clients must persist both of them.
etc.

This also lends itself well to future protocol transitions.
For example,
we might have *pb+wss* or *pb+X* for some yet-to-be-invented *X*.


x-ocap
``````

Or some other nice-looking brand new string.

This option has the least obvious semantics.
In inventing an entirely new scheme,
we have to invent all of its behavior.

This could be like the *pb* option but with a new name and no baggage from Foolscap.
As soon as we decide to make another protocol change then we're back in the same place as we are now.
The only difference is that the "HTTP Introducer" protocol would play the part Foolscap now plays.

For the purposes of migrating from Foolscap it has the same drawback as *pb+http*:
there are two URLs clients must be made aware of.
