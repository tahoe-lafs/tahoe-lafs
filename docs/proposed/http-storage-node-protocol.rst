.. -*- coding: utf-8 -*-

HTTP Storage Node Protocol
==========================

The target audience for this document is Tahoe-LAFS developers.
After reading this document,
one should expect to understand how Tahoe-LAFS clients interact over the network with Tahoe-LAFS storage nodes.

Security
--------

Requirements
~~~~~~~~~~~~

A client node relies on a storage node to persist certain data until a future retrieval request is made.
In this way, the node is vulnerable to attacks which cause the data not to be persisted.
Though this vulnerability can be mitigated by including redundancy in the share encoding parameters for stored data, it is still sensible to attempt to minimize unnecessary vulnerability to this attack.

One way to do this is for the client to be confident it the storage node with which it is communicating is really the expected node.
Therefore, the protocol must include some means for cryptographically verifying the identify of the storage node.
The initialization of the client with the correct identity information is out of scope for this protocol
(the system may be trust-on-first-use, there may be a third-party identity broker, etc).

With confidence that communication is proceeding with the intended storage node,
it must also be possible to trust that data is exchanged without modification.
That is, the protocol must include some means to cryptographically verify the integrity of exchanged messages.

Solutions
~~~~~~~~~

Communication with the storage node will take place using TLS 1.2 [#]_.

  * The storage node will present a certificate proving its identity.
  * The certificate will include a ``subjectAltName`` containing ... [#]_.
  * The certificate will be signed by an entity known to and trusted by the client.
    This entity will *not* be a standard web-focused Certificate Authority.

When connecting to a storage node,
the client will take the following steps to gain confidence it has reached the intended peer:

  * It will perform the usual cryptographic verification of the certificate presented by the storage server
    (that is,
    that the certificate itself is well-formed,
    that the signature it carries is valid,
    that the signature was created by a "trusted entity").
  * It will consider the only "trusted entity" to be an entity explicitly configured for the intended storage node
    (specifically, it will not considered the standard web-focused Certificate Authorities to be trusted).
  * It will check the ``subjectAltName`` against ... [#]_.

To further clarify, consider this example.
Alice operates a storage node.
Alice generates a Certificate Authority certificate and secures the private key appropriately.
Alice generates a Storage Node certificate and signs it with the Certificate Authority certificate's private key.
Alice prints out the Certificate Authority certificate and storage node URI [#]_ and hands it to Bob.
Bob creates a client node.
Bob configures the client node with the storage node URI and the Certificate Authority certificate received from Alice.

Bob's client node can now perform a TLS handshake with a server at the address indicated by the storage node URI.
Following the above described validation procedures,
Bob's client node can determine whether it has reached Alice's storage node or not.

Additionally,
by continuing to interact using TLS,
Bob's client and Alice's storage node are assured of the integrity of the communication.

Transition
~~~~~~~~~~

Storage nodes already possess an x509 certificate.
This is used with Foolscap to provide the same security properties described in the above requirements section.
There are some differences.

  * The certificate is self-signed.
  * The certificate has a ``commonName`` of "newpb_thingy".
  * The validity of the certificate is determined by checking the certificate digest against a value carried in the fURL.
    Only a correctly signed certificate with a matching digest is accepted.

A mixed-protocol storage node should:

  * Start the Foolscap server as it has always done.
  * Start a TLS server dispatching to an HTTP server.
    * Use the same certificate as the Foolscap server uses.
    * Accept anonymous client connections.

A mixed-protocol client node should:

  * If it is configured with a storage URI, connect using HTTP over TLS.
  * If it is configured with a storage fURL, connect using Foolscap.
    If the server version indicates support for the new protocol:
    * Attempt to connect using the new protocol.
    * Drop the Foolscap connection if this new connection succeeds.

Client node implementations could cache a successful protocol upgrade.
This would avoid the double connection on subsequent startups.
This is left as a decision for the implementation, though.

.. [#] What are best practices regarding TLS version?
       Would a policy of "use the newest version shared between the two endpoints" be better?
       Is it necessary to specify more than a TLS version number here?
       For example, should we be specifying a set of ciphers as well?
       Or is that a quality of implementation issue rather than a protocol specification issue?
.. [#] TODO
.. [#] TODO
.. [#] URL?  IRI?
