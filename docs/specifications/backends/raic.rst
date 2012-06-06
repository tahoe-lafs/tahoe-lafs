
=============================================================
Redundant Array of Independent Clouds: Share To Cloud Mapping
=============================================================


Introduction
============

This document describes a proposed design for the mapping of LAFS shares to
objects in a cloud storage service. It also analyzes the costs for each of the
functional requirements, including network, disk, storage and API usage costs.


Terminology
===========

*LAFS share*
   A Tahoe-LAFS share representing part of a file after encryption and
   erasure encoding.

*LAFS shareset*
   The set of shares stored by a LAFS storage server for a given storage index.
   The shares within a shareset are numbered by a small integer.

*Cloud storage service*
   A service such as Amazon S3 `²`_, Rackspace Cloud Files `³`_,
   Google Cloud Storage `⁴`_, or Windows Azure `⁵`_, that provides cloud storage.

*Cloud storage interface*
   A protocol interface supported by a cloud storage service, such as the
   S3 interface `⁶`_, the OpenStack Object Storage interface `⁷`_, the
   Google Cloud Storage interface `⁸`_, or the Azure interface `⁹`_. There may be
   multiple services implementing a given cloud storage interface. In this design,
   only REST-based APIs `¹⁰`_ over HTTP will be used as interfaces.

*Cloud object*
   A file-like abstraction provided by a cloud storage service, storing a
   sequence of bytes. Cloud objects are mutable in the sense that the contents
   and metadata of the cloud object with a given name in a given cloud container
   can be replaced. Cloud objects are called “blobs” in the Azure interface,
   and “objects” in the other interfaces.

*Cloud container*
   A container for cloud objects provided by a cloud service. Cloud containers
   are called “buckets” in the S3 and Google Cloud Storage interfaces, and
   “containers” in the Azure and OpenStack Storage interfaces.


Functional Requirements
=======================

* *Upload*: a LAFS share can be uploaded to an appropriately configured
  Tahoe-LAFS storage server and the data is stored to the cloud
  storage service.

 * *Scalable shares*: there is no hard limit on the size of LAFS share
   that can be uploaded.

   If the cloud storage interface offers scalable files, then this could be
   implemented by using that feature of the specific cloud storage
   interface. Alternately, it could be implemented by mapping from the LAFS
   abstraction of an unlimited-size immutable share to a set of size-limited
   cloud objects.

 * *Streaming upload*: the size of the LAFS share that is uploaded
   can exceed the amount of RAM and even the amount of direct attached
   storage on the storage server. I.e., the storage server is required to
   stream the data directly to the ultimate cloud storage service while
   processing it, instead of to buffer the data until the client is finished
   uploading and then transfer the data to the cloud storage service.

* *Download*: a LAFS share can be downloaded from an appropriately
  configured Tahoe-LAFS storage server, and the data is loaded from the
  cloud storage service.

 * *Streaming download*: the size of the LAFS share that is
   downloaded can exceed the amount of RAM and even the amount of direct
   attached storage on the storage server. I.e. the storage server is
   required to stream the data directly to the client while processing it,
   instead of to buffer the data until the cloud storage service is finished
   serving and then transfer the data to the client.

* *Modify*: a LAFS share can have part of its contents modified.

  If the cloud storage interface offers scalable mutable files, then this
  could be implemented by using that feature of the specific cloud storage
  interface. Alternately, it could be implemented by mapping from the LAFS
  abstraction of an unlimited-size mutable share to a set of size-limited
  cloud objects.

 * *Efficient modify*: the size of the LAFS share being
   modified can exceed the amount of RAM and even the amount of direct
   attached storage on the storage server. I.e. the storage server is
   required to download, patch, and upload only the segment(s) of the share
   that are being modified, instead of to download, patch, and upload the
   entire share.

* *Tracking leases*: The Tahoe-LAFS storage server is required to track when
  each share has its lease renewed so that unused shares (shares whose lease
  has not been renewed within a time limit, e.g. 30 days) can be garbage
  collected. This does not necessarily require code specific to each cloud
  storage interface, because the lease tracking can be performed in the
  storage server's generic component rather than in the component supporting
  each interface.


Mapping
=======

This section describes the mapping between LAFS shares and cloud objects.

A LAFS share will be split into one or more “chunks” that are each stored in a
cloud object. A LAFS share of size `C` bytes will be stored as `ceiling(C / chunksize)`
chunks. The last chunk has a size between 1 and `chunksize` bytes inclusive.
(It is not possible for `C` to be zero, because valid shares always have a header,
so, there is at least one chunk for each share.)

For an existing share, the chunk size is determined by the size of the first
chunk. For a new share, it is a parameter that may depend on the storage
interface. It is an error for any chunk to be larger than the first chunk, or
for any chunk other than the last to be smaller than the first chunk.
If a mutable share with total size less than the default chunk size for the
storage interface is being modified, the new contents are split using the
default chunk size.

  *Rationale*: this design allows the `chunksize` parameter to be changed for
  new shares written via a particular storage interface, without breaking
  compatibility with existing stored shares. All cloud storage interfaces
  return the sizes of cloud objects with requests to list objects, and so
  the size of the first chunk can be determined without an additional request.

The name of the cloud object for chunk `i` > 0 of a LAFS share with storage index
`STORAGEINDEX` and share number `SHNUM`, will be

  shares/`ST`/`STORAGEINDEX`/`SHNUM.i`

where `ST` is the first two characters of `STORAGEINDEX`. When `i` is 0, the
`.0` is omitted.

  *Rationale*: this layout maintains compatibility with data stored by the
  prototype S3 backend, for which Least Authority Enterprises has existing
  customers. This prototype always used a single cloud object to store each
  share, with name

    shares/`ST`/`STORAGEINDEX`/`SHNUM`

  By using the same prefix “shares/`ST`/`STORAGEINDEX`/” for old and new layouts,
  the storage server can obtain a list of cloud objects associated with a given
  shareset without having to know the layout in advance, and without having to
  make multiple API requests. This also simplifies sharing of test code between the
  disk and cloud backends.

Mutable and immutable shares will be “chunked” in the same way.


Rationale for Chunking
----------------------

Limiting the amount of data received or sent in a single request has the
following advantages:

* It is unnecessary to write separate code to take advantage of the
  “large object” features of each cloud storage interface, which differ
  significantly in their design.
* Data needed for each PUT request can be discarded after it completes.
  If a PUT request fails, it can be retried while only holding the data
  for that request in memory.


Costs
=====

In this section we analyze the costs of the proposed design in terms of network,
disk, memory, cloud storage, and API usage.


Network usage—bandwidth and number-of-round-trips
-------------------------------------------------

When a Tahoe-LAFS storage client allocates a new share on a storage server,
the backend will request a list of the existing cloud objects with the
appropriate prefix. This takes one HTTP request in the common case, but may
take more for the S3 interface, which has a limit of 1000 objects returned in
a single “GET Bucket” request.

If the share is to be read, the client will make a number of calls each
specifying the offset and length of the required span of bytes. On the first
request that overlaps a given chunk of the share, the server will make an
HTTP GET request for that cloud object. The server may also speculatively
make GET requests for cloud objects that are likely to be needed soon (which
can be predicted since reads are normally sequential), in order to reduce
latency.

Each read will be satisfied as soon as the corresponding data is available,
without waiting for the rest of the chunk, in order to minimize read latency.

All four cloud storage interfaces support GET requests using the
Range HTTP header. This could be used to optimize reads where the
Tahoe-LAFS storage client requires only part of a share.

If the share is to be written, the server will make an HTTP PUT request for
each chunk that has been completed. Tahoe-LAFS clients only write immutable
shares sequentially, and so we can rely on that property to simplify the
implementation.

When modifying shares of an existing mutable file, the storage server will
be able to make PUT requests only for chunks that have changed.
(Current Tahoe-LAFS v1.9 clients will not take advantage of this ability, but
future versions will probably do so for MDMF files.)

In some cases, it may be necessary to retry a request (see the `Structure of
Implementation`_ section below). In the case of a PUT request, at the point
at which a retry is needed, the new chunk contents to be stored will still be
in memory and so this is not problematic.

In the absence of retries, the maximum number of GET requests that will be made
when downloading a file, or the maximum number of PUT requests when uploading
or modifying a file, will be equal to the number of chunks in the file.

If the new mutable share content has fewer chunks than the old content,
then the remaining cloud objects for old chunks must be deleted (using one
HTTP request each). When reading a share, the backend must tolerate the case
where these cloud objects have not been deleted successfully.

The last write to a share will be reported as successful only when all
corresponding HTTP PUTs and DELETEs have completed successfully.



Disk usage (local to the storage server)
----------------------------------------

It is never necessary for the storage server to write the content of share
chunks to local disk, either when they are read or when they are written. Each
chunk is held only in memory.

A proposed change to the Tahoe-LAFS storage server implementation uses a sqlite
database to store metadata about shares. In that case the same database would
be used for the cloud backend. This would enable lease tracking to be implemented
in the same way for disk and cloud backends.


Memory usage
------------

The use of chunking simplifies bounding the memory usage of the storage server
when handling files that may be larger than memory. However, this depends on
limiting the number of chunks that are simultaneously held in memory.
Multiple chunks can be held in memory either because of pipelining of requests
for a single share, or because multiple shares are being read or written
(possibly by multiple clients).

For immutable shares, the Tahoe-LAFS storage protocol requires the client to
specify in advance the maximum amount of data it will write. Also, a cooperative
client (including all existing released versions of the Tahoe-LAFS code) will
limit the amount of data that is pipelined, currently to 50 KiB. Since the chunk
size will be greater than that, it is possible to ensure that for each allocation,
the maximum chunk data memory usage is the lesser of two chunks, and the allocation
size. (There is some additional overhead but it is small compared to the chunk
data.) If the maximum memory usage of a new allocation would exceed the memory
available, the allocation can be delayed or possibly denied, so that the total
memory usage is bounded.

It is not clear that the existing protocol allows allocations for mutable
shares to be bounded in general; this may be addressed in a future protocol change.

The above discussion assumes that clients do not maliciously send large
messages as a denial-of-service attack. Foolscap (the protocol layer underlying
the Tahoe-LAFS storage protocol) does not attempt to resist denial of service.


Storage
-------

The storage requirements, including not-yet-collected garbage shares, are
the same as for the Tahoe-LAFS disk backend. That is, the total size of cloud
objects stored is equal to the total size of shares that the disk backend
would store.

Erasure coding causes the size of shares for each file to be a
factor `shares.total` / `shares.needed` times the file size, plus overhead
that is logarithmic in the file size `¹¹`_.


API usage
---------

Cloud storage backends typically charge a small fee per API request. The number of
requests to the cloud storage service for various operations is discussed under
“network usage” above.


Structure of Implementation
===========================

A generic “cloud backend”, based on the prototype S3 backend but with support
for chunking as described above, will be written.

An instance of the cloud backend can be attached to one of several
“cloud interface adapters”, one for each cloud storage interface. These
adapters will operate only on chunks, and need not distinguish between
mutable and immutable shares. They will be a relatively “thin” abstraction
layer over the HTTP APIs of each cloud storage interface, similar to the
S3Bucket abstraction in the prototype.

For some cloud storage services it may be necessary to transparently retry
requests in order to recover from transient failures. (Although the erasure
coding may enable a file to be retrieved even when shares are not stored by or
not readable from all cloud storage services used in a Tahoe-LAFS grid, it may
be desirable to retry cloud storage service requests in order to improve overall
reliability.) Support for this will be implemented in the generic cloud backend,
and used whenever a cloud storage adaptor reports a transient failure. Our
experience with the prototype suggests that it is necessary to retry on transient
failures for Amazon's S3 service.

There will also be a “mock” cloud interface adaptor, based on the prototype's
MockS3Bucket. This allows tests of the generic cloud backend to be run without
a connection to a real cloud service. The mock adaptor will be able to simulate
transient and non-transient failures.


Known Issues
============

This design worsens a known “write hole” issue in Tahoe-LAFS when updating
the contents of mutable files. An update to a mutable file can require changing
the contents of multiple chunks, and if the client fails or is disconnected
during the operation the resulting state of the stored cloud objects may be
inconsistent—no longer containing all of the old version, but not yet containing
all of the new version. A mutable share can be left in an inconsistent state
even by the existing Tahoe-LAFS disk backend if it fails during a write, but
that has a smaller chance of occurrence because the current client behavior
leads to mutable shares being written to disk in a single system call.

The best fix for this issue probably requires changing the Tahoe-LAFS storage
protocol, perhaps by extending it to use a two-phase or three-phase commit
(ticket #1755).



References
===========

¹ omitted

.. _²:

² “Amazon S3” Amazon (2012)

   https://aws.amazon.com/s3/

.. _³:

³ “Rackspace Cloud Files” Rackspace (2012)

   https://www.rackspace.com/cloud/cloud_hosting_products/files/

.. _⁴:

⁴ “Google Cloud Storage” Google (2012)

   https://developers.google.com/storage/

.. _⁵:

⁵ “Windows Azure Storage” Microsoft (2012)

   https://www.windowsazure.com/en-us/develop/net/fundamentals/cloud-storage/

.. _⁶:

⁶ “Amazon Simple Storage Service (Amazon S3) API Reference: REST API” Amazon (2012)

   http://docs.amazonwebservices.com/AmazonS3/latest/API/APIRest.html

.. _⁷:

⁷ “OpenStack Object Storage” openstack.org (2012)

   http://openstack.org/projects/storage/

.. _⁸:

⁸ “Google Cloud Storage Reference Guide” Google (2012)

   https://developers.google.com/storage/docs/reference-guide

.. _⁹:

⁹ “Windows Azure Storage Services REST API Reference” Microsoft (2012)

   http://msdn.microsoft.com/en-us/library/windowsazure/dd179355.aspx

.. _¹⁰:

¹⁰ “Representational state transfer” English Wikipedia (2012)

    https://en.wikipedia.org/wiki/Representational_state_transfer

.. _¹¹:

¹¹ “Performance costs for some common operations” tahoe-lafs.org (2012)

    https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/performance.rst
