.. -*- coding: utf-8 -*-

Storage Node Protocol ("Great Black Swamp", "GBS")
==================================================

The target audience for this document is Tahoe-LAFS developers.
After reading this document,
one should expect to understand how Tahoe-LAFS clients interact over the network with Tahoe-LAFS storage nodes.

The primary goal of the introduction of this protocol is to simplify the task of implementing a Tahoe-LAFS storage server.
Specifically, it should be possible to implement a Tahoe-LAFS storage server without a Foolscap implementation
(substituting a simpler GBS server implementation).
The Tahoe-LAFS client will also need to change but it is not expected that it will be noticably simplified by this change
(though this may be the first step towards simplifying it).

Requirements
------------

Security
~~~~~~~~

Summary
!!!!!!!

The storage node protocol should offer at minimum the security properties offered by the Foolscap-based protocol.
The Foolscap-based protocol offers:

* **Peer authentication** by way of checked x509 certificates
* **Message authentication** by way of TLS
* **Message confidentiality** by way of TLS

  * A careful configuration of the TLS connection parameters *may* also offer **forward secrecy**.
    However, Tahoe-LAFS' use of Foolscap takes no steps to ensure this is the case.

Discussion
!!!!!!!!!!

A client node relies on a storage node to persist certain data until a future retrieval request is made.
In this way, the node is vulnerable to attacks which cause the data not to be persisted.
Though this vulnerability can be (and typically is) mitigated by including redundancy in the share encoding parameters for stored data,
it is still sensible to attempt to minimize unnecessary vulnerability to this attack.

One way to do this is for the client to be confident the storage node with which it is communicating is really the expected node.
That is, for the client to perform **peer authentication** of the storage node it connects to.
This allows it to develop a notion of that node's reputation over time.
The more retrieval requests the node satisfies correctly the more it probably will satisfy correctly.
Therefore, the protocol must include some means for verifying the identify of the storage node.
The initialization of the client with the correct identity information is out of scope for this protocol
(the system may be trust-on-first-use, there may be a third-party identity broker, etc).

With confidence that communication is proceeding with the intended storage node,
it must also be possible to trust that data is exchanged without modification.
That is, the protocol must include some means to perform **message authentication**.
This is most likely done using cryptographic MACs (such as those used in TLS).

The messages which enable the mutable shares feature include secrets related to those shares.
For example, the write enabler secret is used to restrict the parties with write access to mutable shares.
It is exchanged over the network as part of a write operation.
An attacker learning this secret can overwrite share data with garbage
(lacking a separate encryption key,
there is no way to write data which appears legitimate to a legitimate client).
Therefore, **message confidentiality** is necessary when exchanging these secrets.
**Forward secrecy** is preferred so that an attacker recording an exchange today cannot launch this attack at some future point after compromising the necessary keys.

Functionality
-------------

Tahoe-LAFS application-level information must be transferred using this protocol.
This information is exchanged with a dozen or so request/response-oriented messages.
Some of these messages carry large binary payloads.
Others are small structured-data messages.
Some facility for expansion to support new information exchanges should also be present.

Solutions
---------

An HTTP-based protocol, dubbed "Great Black Swamp" (or "GBS"), is described below.
This protocol aims to satisfy the above requirements at a lower level of complexity than the current Foolscap-based protocol.

Communication with the storage node will take place using TLS.
The TLS version and configuration will be dictated by an ongoing understanding of best practices.
The storage node will present an x509 certificate during the TLS handshake.
Storage clients will require that the certificate have a valid signature.
The Subject Public Key Information (SPKI) hash of the certificate will constitute the storage node's identity.
The **tub id** portion of the storage node fURL will be replaced with the SPKI hash.

When connecting to a storage node,
the client will take the following steps to gain confidence it has reached the intended peer:

* It will perform the usual cryptographic verification of the certificate presented by the storage server.
  That is,
  it will check that the certificate itself is well-formed,
  that it is currently valid [#]_,
  and that the signature it carries is valid.
* It will compare the SPKI hash of the certificate to the expected value.
  The specifics of the comparison are the same as for the comparison specified by `RFC 7469`_ with "sha256" [#]_.

To further clarify, consider this example.
Alice operates a storage node.
Alice generates a key pair and secures it properly.
Alice generates a self-signed storage node certificate with the key pair.
Alice's storage node announces (to an introducer) a fURL containing (among other information) the SPKI hash.
For example, ``pb://i5xb...@example.com:443/g3m5...#v=2`` [#]_.
Bob creates a client node pointed at the same introducer.
Bob's client node receives the announcement from Alice's storage node.

Bob's client node recognizes the fURL as referring to an HTTP-dialect server due to the ``v=2`` fragment.
Bob's client node can now perform a TLS handshake with a server at the address in the fURL location hints
(``example.com:443`` in this example).
Following the above described validation procedures,
Bob's client node can determine whether it has reached Alice's storage node or not.
If and only if the SPKI hash matches the value in the published fURL
(``i5xb...`` in this example)
then Alice's storage node has been contacted.
**Peer authentication** has been achieved.

Additionally,
by continuing to interact using TLS,
Bob's client and Alice's storage node are assured of both **message authentication** and **message confidentiality**.

.. note::

   Foolscap TubIDs are 20 bytes (SHA1 digest of the certificate).
   They are presented with base32 encoding at a length of 32 bytes.
   SPKI information discussed here is 32 bytes (SHA256 digest).
   They will present in base32 as 52 bytes.
   https://tools.ietf.org/html/rfc7515#appendix-C may prove a better (more compact) choice for encoding the information into a fURL.
   It will encode 32 bytes into merely 43...
   We could also choose to reduce the hash size of the SPKI information through use of another cryptographic hash (replacing sha256).
   A 224 bit hash function (SHA3-224, for example) might be suitable -
   improving the encoded length to 38 bytes.
   Or we could stick with the Foolscap digest function - SHA1.


Transition
~~~~~~~~~~

To provide a seamless user experience during this protocol transition,
there should be a period during which both protocols are supported by storage nodes.
The HTTP protocol announcement will be introduced in a way that updated client software can recognize.
Its introduction will also be made in such a way that non-updated client software disregards the new information
(of which it cannot make any use).

Therefore, concurrent with the following, storage nodes will continue to operate their Foolscap server unaltered compared to their previous behavior.

Storage nodes will begin to operate a new HTTP-based server.
They may re-use their existing x509 certificate or generate a new one.
Generation of a new certificate allows for certain non-optimal conditions to be address::
* The ``commonName`` of ``newpb_thingy`` may be changed to a more descriptive value.
* A ``notValidAfter`` field with a timestamp in the past may be updated.

Storage nodes will announce a new fURL for this new HTTP-based server.
This fURL will be announced alongside their existing Foolscap-based server's fURL.

Non-updated clients will see the Foolscap fURL and continue with their current behavior.
Updated clients will see the Foolscap fURL *and* the HTTP fURL and prefer the HTTP fURL.

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

JSON is used throughout for the examples but is likely not the preferred encoding.
The structure of the examples should nevertheless be representative.

General
~~~~~~~

``GET /v1/version``
!!!!!!!!!!!!!!!!!!!

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

Immutable
---------

Writing
~~~~~~~

``POST /v1/immutable/:storage_index``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Initialize an immutable storage index with some buckets.
The buckets may have share data written to them once.
Details of the buckets to create are encoded in the request body.
For example::

  {"renew-secret": "efgh", "cancel-secret": "ijkl",
   "share-numbers": [1, 7, ...], "allocated-size": 12345}

The response body includes encoded information about the created buckets.
For example::

  {"already-have": [1, ...], "allocated": [7, ...]}

Discussion
``````````

We considered making this ``POST /v1/immutable`` instead.
The motivation was to keep *storage index* out of the request URL.
Request URLs have an elevated chance of being logged by something.
We were concerned that having the *storage index* logged may increase some risks.
However, we decided this does not matter because the *storage index* can only be used to read the share (which is ciphertext).
TODO Verify this conclusion.

``PUT /v1/immutable/:storage_index/:share_number``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Write data for the indicated share.
The share number must belong to the storage index.
The request body is the raw share data (i.e., ``application/octet-stream``).
*Content-Range* requests are encouraged for large transfers.
For example,
for a 1MiB share the data can be broken in to 8 128KiB chunks.
Each chunk can be *PUT* separately with the appropriate *Content-Range* header.
The server must recognize when all of the data has been received and mark the share as complete
(which it can do because it was informed of the size when the storage index was initialized).
Clients should upload chunks in re-assembly order.
Servers may reject out-of-order chunks for implementation simplicity.
If an individual *PUT* fails then only a limited amount of effort is wasted on the necessary retry.

.. think about copying https://developers.google.com/drive/api/v2/resumable-upload

``POST /v1/immutable/:storage_index/:share_number/corrupt``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Advise the server the data read from the indicated share was corrupt.
The request body includes an human-meaningful string with details about the corruption.
It also includes potentially important details about the share.

For example::

  {"reason": "expected hash abcd, got hash efgh"}

.. share-type, storage-index, and share-number are inferred from the URL

Reading
~~~~~~~

``GET /v1/immutable/:storage_index/shares``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Retrieve a list indicating all shares available for the indicated storage index.
For example::

  [1, 5]

``GET /v1/immutable/:storage_index?share=:s0&share=:sN&offset=o1&size=z0&offset=oN&size=zN``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Read data from the indicated immutable shares.
If ``share`` query parameters are given, selecte only those shares for reading.
Otherwise, select all shares present.
If ``size`` and ``offset`` query parameters are given,
only the portions thus identified of the selected shares are returned.
Otherwise, all data is from the selected shares is returned.

The response body contains a mapping giving the read data.
For example::

  {
      3: ["foo", "bar"],
      7: ["baz", "quux"]
  }

Mutable
-------

Writing
~~~~~~~

``POST /v1/mutable/:storage_index``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Initialize a mutable storage index with some buckets.
Essentially the same as the API for initializing an immutable storage index.

``POST /v1/mutable/:storage_index/read-test-write``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

General purpose read-test-and-write operation for mutable storage indexes.
The request body includes the secrets necessary to rewrite to the shares
along with test, read, and write vectors for the operation.
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

``GET /v1/mutable/:storage_index/shares``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Retrieve a list indicating all shares available for the indicated storage index.
For example::

  [1, 5]

``GET /v1/mutable/:storage_index?share=:s0&share=:sN&offset=o1&size=z0&offset=oN&size=zN``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Read data from the indicated mutable shares.
Just like ``GET /v1/mutable/:storage_index``.

``POST /v1/mutable/:storage_index/:share_number/corrupt``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Advise the server the data read from the indicated share was corrupt.
Just like the immutable version.

.. _RFC 7469: https://tools.ietf.org/html/rfc7469#section-2.4

.. [#]
   The security value of checking ``notValidBefore`` and ``notValidAfter`` is not entirely clear.
   The arguments which apply to web-facing certificates do not seem to apply
   (due to the decision for Tahoe-LAFS to operate independently of the web-oriented CA system).

   There is an argument to make that complexity is reduced by allowing an existing TLS implementation which wants to make these checks make them
   (compared to including additional code to either bypass them or disregard their results).
   Reducing complexity, at least in general, is often good for security.

   On the other hand, checking the validity time period forces certificate regeneration
   (which comes with its own set of complexity).

   A possible compromise is to recommend very certificates with validity periods of many years or decades.
   "Recommend" may be read as "provide software supporting the generation of".

   What about key theft?
   If certificates are valid for years then a successful attacker can pretend to be a valid storage node for years.
   However, short-validity-period certificates are no help in this case.
   The attacker can generate new, valid certificates using the stolen keys.

   Therefore, the only recourse to key theft
   (really *identity theft*)
   is to burn the identity and generate a new one.
   Burning the identity is a non-trivial task.
   It is worth solving but it is not solved here.

.. [#]
   More simply::

    from hashlib import sha256
    from cryptography.hazmat.primitives.serialization import (
      Encoding,
      PublicFormat,
    )
    from foolscap import base32

    spki_bytes = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    spki_sha256 = sha256(spki_bytes).digest()
    spki_digest32 = base32.encode(spki_sha256)
    assert spki_digest32 == tub_id

   Note we use the Tahoe-LAFS-preferred base32 encoding rather than base64.

.. [#]
   Other schemes for differentiating between the two server types is possible.
   If the tubID length remains different,
   that provides an unambiguous (if obscure) signal about which protocol to use.
   Or a different scheme could be adopted
   (``[x-]pb+http``, ``x-tahoe+http``, ``x-gbs`` come to mind).
