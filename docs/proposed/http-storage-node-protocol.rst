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
In this way, the client node is vulnerable to attacks which cause the data not to be persisted.
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
Imagine the SPKI hash is ``i5xb...``.
This results in a fURL of ``pb://i5xb...@example.com:443/g3m5...#v=2`` [#]_.
Bob creates a client node pointed at the same introducer.
Bob's client node receives the announcement from Alice's storage node
(indirected through the introducer).

Bob's client node recognizes the fURL as referring to an HTTP-dialect server due to the ``v=2`` fragment.
Bob's client node can now perform a TLS handshake with a server at the address in the fURL location hints
(``example.com:443`` in this example).
Following the above described validation procedures,
Bob's client node can determine whether it has reached Alice's storage node or not.
If and only if the validation procedure is successful does Bob's client node conclude it has reached Alice's storage node.
**Peer authentication** has been achieved.

Additionally,
by continuing to interact using TLS,
Bob's client and Alice's storage node are assured of both **message authentication** and **message confidentiality**.

.. note::

   Foolscap TubIDs are 20 bytes (SHA1 digest of the certificate).
   They are encoded with Base32 for a length of 32 bytes.
   SPKI information discussed here is 32 bytes (SHA256 digest).
   They would be encoded in Base32 for a length of 52 bytes.
   `base64url`_ provides a more compact encoding of the information while remaining URL-compatible.
   This would encode the SPKI information for a length of merely 43 bytes.
   SHA1,
   the current Foolscap hash function,
   is not a practical choice at this time due to advances made in `attacking SHA1`_.
   The selection of a safe hash function with output smaller than SHA256 could be the subject of future improvements.
   A 224 bit hash function (SHA3-224, for example) might be suitable -
   improving the encoded length to 38 bytes.


Transition
~~~~~~~~~~

To provide a seamless user experience during this protocol transition,
there should be a period during which both protocols are supported by storage nodes.
The GBS announcement will be introduced in a way that *updated client* software can recognize.
Its introduction will also be made in such a way that *non-updated client* software disregards the new information
(of which it cannot make any use).

Storage nodes will begin to operate a new GBS server.
They may re-use their existing x509 certificate or generate a new one.
Generation of a new certificate allows for certain non-optimal conditions to be addressed:

* The ``commonName`` of ``newpb_thingy`` may be changed to a more descriptive value.
* A ``notValidAfter`` field with a timestamp in the past may be updated.

Storage nodes will announce a new fURL for this new HTTP-based server.
This fURL will be announced alongside their existing Foolscap-based server's fURL.
Such an announcement will resemble this::

  {
      "anonymous-storage-FURL": "pb://...",          # The old key
      "gbs-anonymous-storage-url": "pb://...#v=2"    # The new key
  }

The transition process will proceed in three stages:

1. The first stage represents the starting conditions in which clients and servers can speak only Foolscap.
#. The intermediate stage represents a condition in which some clients and servers can both speak Foolscap and GBS.
#. The final stage represents the desired condition in which all clients and servers speak only GBS.

During the first stage only one client/server interaction is possible:
the storage server announces only Foolscap and speaks only Foolscap.
During the final stage there is only one supported interaction:
the client and server are both updated and speak GBS to each other.

During the intermediate stage there are four supported interactions:

1. Both the client and server are non-updated.
   The interaction is just as it would be during the first stage.
#. The client is updated and the server is non-updated.
   The client will see the Foolscap announcement and the lack of a GBS announcement.
   It will speak to the server using Foolscap.
#. The client is non-updated and the server is updated.
   The client will see the Foolscap announcement.
   It will speak Foolscap to the storage server.
#. Both the client and server are updated.
   The client will see the GBS announcement and disregard the Foolscap announcement.
   It will speak GBS to the server.

There is one further complication:
the client maintains a cache of storage server information
(to avoid continuing to rely on the introducer after it has been introduced).
The follow sequence of events is likely:

1. The client connects to an introducer.
#. It receives an announcement for a non-updated storage server (Foolscap only).
#. It caches this announcement.
#. At some point, the storage server is updated.
#. The client uses the information in its cache to open a Foolscap connection to the storage server.

Ideally,
the client would not rely on an update from the introducer to give it the GBS fURL for the updated storage server.
Therefore,
when an updated client connects to a storage server using Foolscap,
it should request the server's version information.
If this information indicates that GBS is supported then the client should cache this GBS information.
On subsequent connection attempts,
it should make use of this GBS information.

Server Details
--------------

The protocol primarily enables interaction with "resources" of two types:
storage indexes
and shares.
A particular resource is addressed by the HTTP request path.
Details about the interface are encoded in the HTTP message body.

Message Encoding
~~~~~~~~~~~~~~~~

The preferred encoding for HTTP message bodies is `CBOR`_.
A request may be submitted using an alternate encoding by declaring this in the ``Content-Type`` header.
A request may indicate its preference for an alternate encoding in the response using the ``Accept`` header.
These two headers are used in the typical way for an HTTP application.

The only other encoding support for which is currently recommended is JSON.
For HTTP messages carrying binary share data,
this is expected to be a particularly poor encoding.
However,
for HTTP messages carrying small payloads of strings, numbers, and containers
it is expected that JSON will be more convenient than CBOR for ad hoc testing and manual interaction.

For this same reason,
JSON is used throughout for the examples presented here.
Because of the simple types used throughout
and the equivalence described in `RFC 7049`_
these examples should be representative regardless of which of these two encodings is chosen.

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
      "gbs-anonymous-storage-url": "pb://...#v=2"
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

Discussion
``````````

Offset and size of the requested data are specified here as query arguments.
Instead, this information could be present in a ``Range`` header in the request.
This is the more obvious choice and leverages an HTTP feature built for exactly this use-case.
However, HTTP requires that the ``Content-Type`` of the response to "range requests" be ``multipart/...``.
The ``multipart`` major type brings along string sentinel delimiting as a means to frame the different response parts.
There are many drawbacks to this framing technique:

1. It is resource-intensive to generate.
2. It is resource-intensive to parse.
3. It is complex to parse safely [#]_ [#]_ [#]_ [#]_.

Mutable
-------

Writing
~~~~~~~

``POST /v1/mutable/:storage_index/read-test-write``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

General purpose read-test-and-write operation for mutable storage indexes.
A mutable storage index is also called a "slot"
(particularly by the existing Tahoe-LAFS codebase).
The first write operation on a mutable storage index creates it
(that is,
there is no separate "create this storage index" operation as there is for the immutable storage index type).

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

``GET /v1/mutable/:storage_index?share=:s0&share=:sN&offset=:o1&size=:z0&offset=:oN&size=:zN``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Read data from the indicated mutable shares.
Just like ``GET /v1/mutable/:storage_index``.

``POST /v1/mutable/:storage_index/:share_number/corrupt``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Advise the server the data read from the indicated share was corrupt.
Just like the immutable version.

.. _RFC 7469: https://tools.ietf.org/html/rfc7469#section-2.4

.. _RFC 7049: https://tools.ietf.org/html/rfc7049#section-4

.. _CBOR: http://cbor.io/

.. [#]
   The security value of checking ``notValidBefore`` and ``notValidAfter`` is not entirely clear.
   The arguments which apply to web-facing certificates do not seem to apply
   (due to the decision for Tahoe-LAFS to operate independently of the web-oriented CA system).

   Arguably, complexity is reduced by allowing an existing TLS implementation which wants to make these checks make them
   (compared to including additional code to either bypass them or disregard their results).
   Reducing complexity, at least in general, is often good for security.

   On the other hand, checking the validity time period forces certificate regeneration
   (which comes with its own set of complexity).

   A possible compromise is to recommend certificates with validity periods of many years or decades.
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
    from pybase64 import urlsafe_b64encode

    def check_tub_id(tub_id):
        spki_bytes = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        spki_sha256 = sha256(spki_bytes).digest()
        spki_encoded = urlsafe_b64encode(spki_sha256)
        assert spki_encoded == tub_id

   Note we use `base64url`_ rather than the Foolscap- and Tahoe-LAFS-preferred Base32.

.. [#]
   Other schemes for differentiating between the two server types is possible.
   If the tubID length remains different,
   that provides an unambiguous (if obscure) signal about which protocol to use.
   Or a different scheme could be adopted
   (``[x-]pb+http``, ``x-tahoe+http``, ``x-gbs`` come to mind).

.. [#]
   https://www.cvedetails.com/cve/CVE-2017-5638/
.. [#]
   https://pivotal.io/security/cve-2018-1272
.. [#]
   https://nvd.nist.gov/vuln/detail/CVE-2017-5124
.. [#]
   https://efail.de/

.. _base64url: https://tools.ietf.org/html/rfc7515#appendix-C

.. _attacking SHA1: https://en.wikipedia.org/wiki/SHA-1#Attacks
