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

Glossary
--------

.. glossary::

   `Foolscap <https://github.com/warner/foolscap/>`_
     an RPC/RMI (Remote Procedure Call / Remote Method Invocation) protocol for use with Twisted

   storage server
     a Tahoe-LAFS process configured to offer storage and reachable over the network for store and retrieve operations

   storage service
     a Python object held in memory in the storage server which provides the implementation of the storage protocol

   introducer
     a Tahoe-LAFS process at a known location configured to re-publish announcements about the location of storage servers

   fURL
     a self-authenticating URL-like string which can be used to locate a remote object using the Foolscap protocol
     (the storage service is an example of such an object)

   NURL
     a self-authenticating URL-like string almost exactly like a fURL but without being tied to Foolscap

   swissnum
     a short random string which is part of a fURL and which acts as a shared secret to authorize clients to use a storage service

   lease
     state associated with a share informing a storage server of the duration of storage desired by a client

   share
     a single unit of client-provided arbitrary data to be stored by a storage server
     (in practice, one of the outputs of applying ZFEC encoding to some ciphertext with some additional metadata attached)

   bucket
     a group of one or more immutable shares held by a storage server and having a common storage index

   slot
     a group of one or more mutable shares held by a storage server and having a common storage index
     (sometimes "slot" is considered a synonym for "storage index of a slot")

   storage index
     a 16 byte string which can address a slot or a bucket
     (in practice, derived by hashing the encryption key associated with contents of that slot or bucket)

   write enabler
     a short secret string which storage servers require to be presented before allowing mutation of any mutable share

   lease renew secret
     a short secret string which storage servers required to be presented before allowing a particular lease to be renewed

Motivation
----------

Foolscap
~~~~~~~~

Foolscap is a remote method invocation protocol with several distinctive features.
At its core it allows separate processes to refer each other's objects and methods using a capability-based model.
This allows for extremely fine-grained access control in a system that remains highly securable without becoming overwhelmingly complicated.
Supporting this is a flexible and extensible serialization system which allows data to be exchanged between processes in carefully controlled ways.

Tahoe-LAFS avails itself of only a small portion of these features.
A Tahoe-LAFS storage server typically only exposes one object with a fixed set of methods to clients.
A Tahoe-LAFS introducer node does roughly the same.
Tahoe-LAFS exchanges simple data structures that have many common, standard serialized representations.

In exchange for this slight use of Foolscap's sophisticated mechanisms,
Tahoe-LAFS pays a substantial price:

* Foolscap is implemented only for Python.
  Tahoe-LAFS is thus limited to being implemented only in Python.
* There is only one Python implementation of Foolscap.
  The implementation is therefore the de facto standard and understanding of the protocol often relies on understanding that implementation.
* The Foolscap developer community is very small.
  The implementation therefore advances very little and some non-trivial part of the maintenance cost falls on the Tahoe-LAFS project.
* The extensible serialization system imposes substantial complexity compared to the simple data structures Tahoe-LAFS actually exchanges.

HTTP
~~~~

HTTP is a request/response protocol that has become the lingua franca of the internet.
Combined with the principles of Representational State Transfer (REST) it is widely employed to create, update, and delete data in collections on the internet.
HTTP itself provides only modest functionality in comparison to Foolscap.
However its simplicity and widespread use have led to a diverse and almost overwhelming ecosystem of libraries, frameworks, toolkits, and so on.

By adopting HTTP in place of Foolscap Tahoe-LAFS can realize the following concrete benefits:

* Practically every language or runtime has an HTTP protocol implementation (or a dozen of them) available.
  This change paves the way for new Tahoe-LAFS implementations using tools better suited for certain situations
  (mobile client implementations, high-performance server implementations, easily distributed desktop clients, etc).
* The simplicity of and vast quantity of resources about HTTP make it a very easy protocol to learn and use.
  This change reduces the barrier to entry for developers to contribute improvements to Tahoe-LAFS's network interactions.
* For any given language there is very likely an HTTP implementation with a large and active developer community.
  Tahoe-LAFS can therefore benefit from the large effort being put into making better libraries for using HTTP.
* One of the core features of HTTP is the mundane transfer of bulk data and implementions are often capable of doing this with extreme efficiency.
  The alignment of this core feature with a core activity of Tahoe-LAFS of transferring bulk data means that a substantial barrier to improved Tahoe-LAFS runtime performance will be eliminated.

TLS
~~~

The Foolscap-based protocol provides *some* of Tahoe-LAFS's confidentiality, integrity, and authentication properties by leveraging TLS.
An HTTP-based protocol can make use of TLS in largely the same way to provide the same properties.
Provision of these properties *is* dependant on implementers following Great Black Swamp's rules for x509 certificate validation
(rather than the standard "web" rules for validation).

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

* **Storage authorization** by way of a capability contained in the fURL addressing a storage service.

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

A storage service offers service only to some clients.
A client proves their authorization to use the storage service by presenting a shared secret taken from the fURL.
In this way **storage authorization** is performed to prevent disallowed parties from consuming any storage resources.

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
This results in a fURL of ``pb://i5xb...@example.com:443/g3m5...#v=1``.
Bob creates a client node pointed at the same introducer.
Bob's client node receives the announcement from Alice's storage node
(indirected through the introducer).

Bob's client node recognizes the fURL as referring to an HTTP-dialect server due to the ``v=1`` fragment.
Bob's client node can now perform a TLS handshake with a server at the address in the fURL location hints
(``example.com:443`` in this example).
Following the above described validation procedures,
Bob's client node can determine whether it has reached Alice's storage node or not.
If and only if the validation procedure is successful does Bob's client node conclude it has reached Alice's storage node.
**Peer authentication** has been achieved.

Additionally,
by continuing to interact using TLS,
Bob's client and Alice's storage node are assured of both **message authentication** and **message confidentiality**.

Bob's client further inspects the fURL for the *swissnum*.
When Bob's client issues HTTP requests to Alice's storage node it includes the *swissnum* in its requests.
**Storage authorization** has been achieved.

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
      "gbs-anonymous-storage-url": "pb://...#v=1"    # The new key
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

HTTP Design
~~~~~~~~~~~

The HTTP interface described here is informed by the ideas of REST
(Representational State Transfer).
For ``GET`` requests query parameters are preferred over values encoded in the request body.
For other requests query parameters are encoded into the message body.

Many branches of the resource tree are conceived as homogenous containers:
one branch contains all of the share data;
another branch contains all of the lease data;
etc.

Authorization is required for all endpoints.
The standard HTTP authorization protocol is used.
The authentication *type* used is ``Tahoe-LAFS``.
The swissnum from the NURL used to locate the storage service is used as the *credentials*.
If credentials are not presented or the swissnum is not associated with a storage service then no storage processing is performed and the request receives an ``UNAUTHORIZED`` response.

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
      "gbs-anonymous-storage-url": "pb://...#v=1"
      },
    "application-version": "1.13.0"
    }

``PUT /v1/lease/:storage_index``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Either renew or create a new lease on the bucket addressed by ``storage_index``.
The details of the lease are encoded in the request body.
For example::

  {"renew-secret": "abcd", "cancel-secret": "efgh"}

If the ``renew-secret`` value matches an existing lease
then the expiration time of that lease will be changed to 31 days after the time of this operation.
If it does not match an existing lease
then a new lease will be created with this ``renew-secret`` which expires 31 days after the time of this operation.

``renew-secret`` and ``cancel-secret`` values must be 32 bytes long.
The server treats them as opaque values.
:ref:`Share Leases` gives details about how the Tahoe-LAFS storage client constructs these values.

In these cases the response is ``NO CONTENT`` with an empty body.

It is possible that the storage server will have no shares for the given ``storage_index`` because:

* no such shares have ever been uploaded.
* a previous lease expired and the storage server reclaimed the storage by deleting the shares.

In these cases the server takes no action and returns ``NOT FOUND``.


Discussion
``````````

We considered an alternative where ``renew-secret`` and ``cancel-secret`` are placed in query arguments on the request path.
We chose to put these values into the request body to make the URL simpler.

Several behaviors here are blindly copied from the Foolscap-based storage server protocol.

* There is a cancel secret but there is no API to use it to cancel a lease (see ticket:3768).
* The lease period is hard-coded at 31 days.

These are not necessarily ideal behaviors
but they are adopted to avoid any *semantic* changes between the Foolscap- and HTTP-based protocols.
It is expected that some or all of these behaviors may change in a future revision of the HTTP-based protocol.

Immutable
---------

Writing
~~~~~~~

``POST /v1/immutable/:storage_index``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Initialize an immutable storage index with some buckets.
The buckets may have share data written to them once.
A lease is also created for the shares.
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
However, we decided this does not matter because:

* the *storage index* can only be used to retrieve (not decrypt) the ciphertext-bearing share.
* the *storage index* is already persistently present on the storage node in the form of directory names in the storage servers ``shares`` directory.
* the request is made via HTTPS and so only Tahoe-LAFS can see the contents,
  therefore no proxy servers can perform any extra logging.
* Tahoe-LAFS itself does not currently log HTTP request URLs.

The response includes ``already-have`` and ``allocated`` for two reasons:

* If an upload is interrupted and the client loses its local state that lets it know it already uploaded some shares
  then this allows it to discover this fact (by inspecting ``already-have``) and only upload the missing shares (indicated by ``allocated``).

* If an upload has completed a client may still choose to re-balance storage by moving shares between servers.
  This might be because a server has become unavailable and a remaining server needs to store more shares for the upload.
  It could also just be that the client's preferred servers have changed.

``PUT /v1/immutable/:storage_index/:share_number``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Write data for the indicated share.
The share number must belong to the storage index.
The request body is the raw share data (i.e., ``application/octet-stream``).
*Content-Range* requests are encouraged for large transfers to allow partially complete uploads to be resumed.
For example,
a 1MiB share can be divided in to eight separate 128KiB chunks.
Each chunk can be uploaded in a separate request.
Each request can include a *Content-Range* value indicating its placement within the complete share.
If any one of these requests fails then at most 128KiB of upload work needs to be retried.

The server must recognize when all of the data has been received and mark the share as complete
(which it can do because it was informed of the size when the storage index was initialized).
Clients should upload chunks in re-assembly order.

* When a chunk that does not complete the share is successfully uploaded the response is ``OK``.
* When the chunk that completes the share is successfully uploaded the response is ``CREATED``.
* If the *Content-Range* for a request covers part of the share that has already been uploaded the response is ``CONFLICT``.
  The response body indicates the range of share data that has yet to be uploaded.
  That is::

    { "required":
      [ { "begin": <byte position, inclusive>
        , "end":   <byte position, exclusive>
        }
      ,
      ...
      ]
    }


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

``GET /v1/immutable/:storage_index/:share_number``
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Read a contiguous sequence of bytes from one share in one bucket.
The response body is the raw share data (i.e., ``application/octet-stream``).
The ``Range`` header may be used to request exactly one ``bytes`` range.
Interpretation and response behavior is as specified in RFC 7233 § 4.1.
Multiple ranges in a single request are *not* supported.

Discussion
``````````

Multiple ``bytes`` ranges are not supported.
HTTP requires that the ``Content-Type`` of the response in that case be ``multipart/...``.
The ``multipart`` major type brings along string sentinel delimiting as a means to frame the different response parts.
There are many drawbacks to this framing technique:

1. It is resource-intensive to generate.
2. It is resource-intensive to parse.
3. It is complex to parse safely [#]_ [#]_ [#]_ [#]_.

A previous revision of this specification allowed requesting one or more contiguous sequences from one or more shares.
This *superficially* mirrored the Foolscap based interface somewhat closely.
The interface was simplified to this version because this version is all that is required to let clients retrieve any desired information.
It only requires that the client issue multiple requests.
This can be done with pipelining or parallel requests to avoid an additional latency penalty.
In the future,
if there are performance goals,
benchmarks can demonstrate whether they are achieved by a more complicated interface or some other change.

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

Sample Interactions
-------------------

Immutable Data
~~~~~~~~~~~~~~

1. Create a bucket for storage index ``AAAAAAAAAAAAAAAA`` to hold two immutable shares, discovering that share ``1`` was already uploaded::

     POST /v1/immutable/AAAAAAAAAAAAAAAA
     {"renew-secret": "efgh", "cancel-secret": "ijkl",
      "share-numbers": [1, 7], "allocated-size": 48}

     200 OK
     {"already-have": [1], "allocated": [7]}

#. Upload the content for immutable share ``7``::

     PUT /v1/immutable/AAAAAAAAAAAAAAAA/7
     Content-Range: bytes 0-15/48
     <first 16 bytes of share data>

     200 OK

     PUT /v1/immutable/AAAAAAAAAAAAAAAA/7
     Content-Range: bytes 16-31/48
     <second 16 bytes of share data>

     200 OK

     PUT /v1/immutable/AAAAAAAAAAAAAAAA/7
     Content-Range: bytes 32-47/48
     <final 16 bytes of share data>

     201 CREATED

#. Download the content of the previously uploaded immutable share ``7``::

     GET /v1/immutable/AAAAAAAAAAAAAAAA?share=7&offset=0&size=48

     200 OK
     <complete 48 bytes of previously uploaded data>

#. Renew the lease on all immutable shares in bucket ``AAAAAAAAAAAAAAAA``::

     PUT /v1/lease/AAAAAAAAAAAAAAAA
     {"renew-secret": "efgh", "cancel-secret": "ijkl"}

     204 NO CONTENT

Mutable Data
~~~~~~~~~~~~

1. Create mutable share number ``3`` with ``10`` bytes of data in slot ``BBBBBBBBBBBBBBBB``::

     POST /v1/mutable/BBBBBBBBBBBBBBBB/read-test-write
     {
         "secrets": {
             "write-enabler": "abcd",
             "lease-renew": "efgh",
             "lease-cancel": "ijkl"
         },
         "test-write-vectors": {
             3: {
                 "test": [{
                     "offset": 0,
                     "size": 1,
                     "operator": "eq",
                     "specimen": ""
                 }],
                 "write": [{
                     "offset": 0,
                     "data": "xxxxxxxxxx"
                 }],
                 "new-length": 10
             }
         },
         "read-vector": []
     }

     200 OK
     {
         "success": true,
         "data": []
     }

#. Safely rewrite the contents of a known version of mutable share number ``3`` (or fail)::

     POST /v1/mutable/BBBBBBBBBBBBBBBB/read-test-write
     {
         "secrets": {
             "write-enabler": "abcd",
             "lease-renew": "efgh",
             "lease-cancel": "ijkl"
         },
         "test-write-vectors": {
             3: {
                 "test": [{
                     "offset": 0,
                     "size": <checkstring size>,
                     "operator": "eq",
                     "specimen": "<checkstring>"
                 }],
                 "write": [{
                     "offset": 0,
                     "data": "yyyyyyyyyy"
                 }],
                 "new-length": 10
             }
         },
         "read-vector": []
     }

     200 OK
     {
         "success": true,
         "data": []
     }

#. Download the contents of share number ``3``::

     GET /v1/mutable/BBBBBBBBBBBBBBBB?share=3&offset=0&size=10
     <complete 16 bytes of previously uploaded data>

#. Renew the lease on previously uploaded mutable share in slot ``BBBBBBBBBBBBBBBB``::

     PUT /v1/lease/BBBBBBBBBBBBBBBB
     {"renew-secret": "efgh", "cancel-secret": "ijkl"}

     204 NO CONTENT

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
   https://www.cvedetails.com/cve/CVE-2017-5638/
.. [#]
   https://pivotal.io/security/cve-2018-1272
.. [#]
   https://nvd.nist.gov/vuln/detail/CVE-2017-5124
.. [#]
   https://efail.de/

.. _base64url: https://tools.ietf.org/html/rfc7515#appendix-C

.. _attacking SHA1: https://en.wikipedia.org/wiki/SHA-1#Attacks
