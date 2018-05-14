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

Reading
-------

``GET /v1/storage/:storage_index``:

Retrieve a mapping describing buckets for the indicated storage index.
The mapping is returned as an encoded structured object
(JSON is used for the example here but is not necessarily the true encoding).
The mapping has share numbers as keys and bucket identifiers as values.
For example::

  .. XXX Share numbers are logically integers and probably sequential starting from 0.
     But JSON cannot encode them as integers if they are keys in a mapping.
     Is this really a mapping or would an array (with share number implied by array index) work as well?

  {0: "abcd", 1: "efgh"}

``GET /v1/buckets/:bucket_id``

Read data from the indicated bucket.
The data is returned raw (i.e., ``application/octet-stream``).
Range requests may be made to read only part of a bucket.

``POST /v1/buckets/:bucket_id/corrupt``

Advise the server the share data read from the indicated bucket was corrupt.
The request body includes an human-meaningful string with details about the corruption.
It also includes potentially important details about the share.

For example::

  {"share_type": "mutable", "storage_index": "abcd", "share_number": 3,
   "reason": "expected hash abcd, got hash efgh"}

Writing
-------

``POST /v1/buckets``

Create some new buckets in which to store some shares.
Details of the buckets to create are encoded in the request body.
For example::

  {"storage_index": "abcd", "renew_secret": "efgh", "cancel_secret": "ijkl",
   "sharenums": [1, 7, ...], "allocated_size": 12345}

The response body includes encoded information about the created buckets.
For example::

  .. XXX Same deal about share numbers as integers/strings here.
     But here it's clear we can't just use an array as mentioned above.
  {"already_have": [1, ...],
   "allocated": {"7": "bucket_id", ...}}

.. [#] What are best practices regarding TLS version?
       Would a policy of "use the newest version shared between the two endpoints" be better?
       Is it necessary to specify more than a TLS version number here?
       For example, should we be specifying a set of ciphers as well?
       Or is that a quality of implementation issue rather than a protocol specification issue?
.. [#] TODO
.. [#] TODO
.. [#] URL?  IRI?
