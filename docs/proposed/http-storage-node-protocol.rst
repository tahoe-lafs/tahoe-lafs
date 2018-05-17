.. -*- coding: utf-8 -*-

HTTP Storage Node Protocol
==========================

The target audience for this document is Tahoe-LAFS developers.
After reading this document,
one should expect to understand how Tahoe-LAFS clients interact over the network with Tahoe-LAFS storage nodes.

The primary goal of the introduction of this protocol is to simplify the task of implementing a Tahoe-LAFS storage server.
Specifically, it should be possible to implement a Tahoe-LAFS storage server without a Foolscap implementation
(substituting an HTTP server implementation).
The Tahoe-LAFS client will also need to change but it is not expected that it will be noticably simplified by this change.

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

Communication with the storage node will take place using TLS.
The TLS version and configuration will be dictated by an ongoing understanding of best practices.
The only requirement is that the certificate have a valid signature.
The storage node will publish the corresponding public key
(e.g., via an introducer).
The public key will constitute the storage node's identity.

When connecting to a storage node,
the client will take the following steps to gain confidence it has reached the intended peer:

* It will perform the usual cryptographic verification of the certificate presented by the storage server
  (that is,
  that the certificate itself is well-formed
  and that the signature it carries is valid.
* It will compare the hash of the public key of the certificate to the expected public key.
  The specifics of the comparison are the same as for the comparison specified by `RFC 7469`_ with "sha256" [#]_.

To further clarify, consider this example.
Alice operates a storage node.
Alice generates a key pair and secures it properly.
Alice generates a self-signed storage node certificate with the key pair.
Alice's storage node announces a fURL containing (among other information) the public key to an introducer.
For example, ``pb://i5xb...@example.com:443/g3m5...``.
Bob creates a client node pointed at the same introducer.
Bob's client node receives the announcement from Alice's storage node.

Bob's client node can now perform a TLS handshake with a server at the address indicated by the storage node fURL
(``example.com:443`` in this example).
Following the above described validation procedures,
Bob's client node can determine whether it has reached Alice's storage node or not.
If and only if the public key hash matches the value in the published fURL
(``i5xb...`` in this example)
then Alice's storage node has been contacted.

Additionally,
by continuing to interact using TLS,
Bob's client and Alice's storage node are assured of the integrity of the communication.

Transition
~~~~~~~~~~

Storage nodes already possess an x509 certificate.
This is used with Foolscap to provide the same security properties described in the above requirements section.

* The certificate is self-signed.
  This remains the same.
* The certificate has a ``commonName`` of "newpb_thingy".
  This is not harmful to the new protocol.
* The validity of the certificate is determined by checking the certificate digest against a value carried in the fURL.
  Only a correctly signed certificate with a matching digest is accepted.
  This validation will be replaced with a public key hash comparison.

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

Server Details
--------------

``GET /v1/version``

Retrieve information about the version of the storage server.
Information is returned as an encoded mapping.
For example::

  { "http://allmydata.org/tahoe/protocols/storage/v1" :
    { "maximum-immutable-share-size": 1234,
      "maximum-mutable-share-size": 1235,
      "available-space": 123456,
      "tolerates-immutable-read-overrun": true,
      "delete-mutable-shares-with-zero-length-writev": true,
      "fills-holes-with-zero-bytes": true,
      "prevents-read-past-end-of-share-data": true,
      "http-protocol-available": true
      },
    "application-version": "1.13.0"
    }


Shares
------

Shares are immutable data stored in buckets.

Writing
~~~~~~~

``POST /v1/storage/:storage_index``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Create some new buckets in which to store some shares.
Details of the buckets to create are encoded in the request body.
For example::

  {"renew_secret": "efgh", "cancel_secret": "ijkl",
   "sharenums": [1, 7, ...], "allocated_size": 12345}

The response body includes encoded information about the created buckets.
For example::

  {"already_have": [1, ...], "allocated": {7: "bucket_id", ...}}

Discussion
``````````

We considered making this ``POST /v1/storage`` instead.
The motivation was to keep *storage index* out of the request URL.
Request URLs have an elevated chance of being logged by something.
We were concerned that having the *storage index* logged may increase some risks.
However, we decided this does not matter because the *storage index* can only be used to read the share (which is ciphertext).
TODO Verify this conclusion.

``PUT /v1/buckets/:bucket_id``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Write the share data to the indicated bucket.
The request body is the raw share data (i.e., ``application/octet-stream``).
*Range* requests are encouraged for large transfers.
For example,
for a 1MiB share the data can be broken in to 8 128KiB chunks.
Each chunk can be *PUT* separately with the appropriate *Range* headers.
The server must recognize when all of the data has been received and mark the bucket as filled.
Clients should upload chunks in re-assembly order.
Servers may reject out-of-order chunks for implementation simplicity.

``POST /v1/buckets/:bucket_id/:share_number/corrupt``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Advise the server the share data read from the indicated bucket was corrupt.
The request body includes an human-meaningful string with details about the corruption.
It also includes potentially important details about the share.

For example::

  {"share_type": "mutable", "storage_index": "abcd",
   "reason": "expected hash abcd, got hash efgh"}

Reading
~~~~~~~

``GET /v1/storage/:storage_index``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Retrieve a mapping describing buckets for the indicated storage index.
The mapping is returned as an encoded structured object
(JSON is used for the example here but is not necessarily the true encoding).
The mapping has share numbers as keys and bucket identifiers as values.
For example::

  .. XXX Share numbers are logically integers.
     JSON cannot encode integer mapping keys.
     So this is not valid JSON but you know what I mean.

  {0: "abcd", 1: "efgh"}

``GET /v1/buckets/:bucket_id``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Read data from the indicated bucket.
The data is returned raw (i.e., ``application/octet-stream``).
*Range* requests may be made to read only part of a bucket.

Slots
-----

Slots are mutable data.

Writing
~~~~~~~

``POST /v1/slots/:storage_index``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

General purpose test-read-and-set operation for mutable slots.
The request body includes the secrets necessary to write to the slot
and the test, read, and write vectors for the operation.
For example::

   {
       "secrets": {
           "write-enabler": "abcd",
           "lease-renew": "efgh",
           "lease-cancel": "ijkl"
       },
       "test-write-vectors": {
           0: {
               "test": [{
                   "offset": 3,
                   "size": 5,
                   "operator": "eq",
                   "specimen": "hello"
               }, ...],
               "write": [{
                   "offset": 9,
                   "data": "world"
               }, ...],
               "new-length": 5
           }
       },
       "read-vector": [{"offset": 3, "size": 12}, ...]
   }

The response body contains a boolean indicating whether the tests all succeed
(and writes were applied) and a mapping giving read data (pre-write).
For example::

  {
      "success": true,
      "data": {
          0: ["foo"],
          5: ["bar"],
          ...
      }
  }

Reading
~~~~~~~

``POST /v1/slots/:storage_index``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Read a vector from the numbered shares associated with the given storage index.
The request body contains the share numbers and read vector.
For example::

  {
      "shares": [3, 5, 7],
      "read-vector": [{"offset": 3, "size": 12}, ...]
  }

The response body contains a mapping giving the read data.
For example::

  {
      3: ["foo"],
      7: ["bar"]
  }


.. _RFC 7469: https://tools.ietf.org/html/rfc7469#section-2.4

.. [#]
   More simply::

    from hashlib import sha256
    from cryptography.hazmat.primitives.serialization import (
      Encoding,
      SubjectPublicKeyInfo,
    )
    from foolscap import base32

    spki_bytes = cert.public_key().public_bytes(DER, SubjectPublicKeyInfo)
    spki_sha256 = sha256(spki_bytes).digest()
    spki_digest32 = base32.encode(spki_sha256)
    assert spki_digest32 == tub_id

   Note we use the Tahoe-LAFS-preferred base32 encoding rather than base64.
