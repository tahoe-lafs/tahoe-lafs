.. -*- coding: utf-8 -*-

.. _share leases:

Share Leases
============

A lease is a marker attached to a share indicating that some client has asked for that share to be retained for some amount of time.
The intent is to allow clients and servers to collaborate to determine which data should still be retained and which can be discarded to reclaim storage space.
Zero or more leases may be attached to any particular share.

Renewal Secrets
---------------

Each lease is uniquely identified by its **renewal secret**.
This is a 32 byte string which can be used to extend the validity period of that lease.

To a storage server a renewal secret is an opaque value which is only ever compared to other renewal secrets to determine equality.

Storage clients will typically want to follow a scheme to deterministically derive the renewal secret for a particular share from information the client already holds about that share.
This allows a client to maintain and renew single long-lived lease without maintaining additional local state.

The scheme in use in Tahoe-LAFS as of 1.16.0 is as follows.

* The **netstring encoding** of a byte string is the concatenation of:

  * the ascii encoding of the base 10 representation of the length of the string
  * ``":"``
  * the string itself
  * ``","``

* The **sha256d digest** is the **sha256 digest** of the **sha256 digest** of a string.
* The **sha256d tagged digest** is the **sha256d digest** of the concatenation of the **netstring encoding** of one string with one other unmodified string.
* The **sha256d tagged pair digest** the **sha256d digest** of the concatenation of the **netstring encodings** of each of three strings.
* The **bucket renewal tag** is ``"allmydata_bucket_renewal_secret_v1"``.
* The **file renewal tag** is ``"allmydata_file_renewal_secret_v1"``.
* The **client renewal tag** is ``"allmydata_client_renewal_secret_v1"``.
* The **lease secret** is a 32 byte string, typically randomly generated once and then persisted for all future uses.
* The **client renewal secret** is the **sha256d tagged digest** of (**lease secret**, **client renewal tag**).
* The **storage index** is constructed using a capability-type-specific scheme.
  See ``storage_index_hash`` and ``ssk_storage_index_hash`` calls in ``src/allmydata/uri.py``.
* The **file renewal secret** is the **sha256d tagged pair digest** of (**file renewal tag**, **client renewal secret**, **storage index**).
* The **base32 encoding** is ``base64.b32encode`` lowercased and with trailing ``=`` stripped.
* The **peer id** is the **base32 encoding** of the SHA1 digest of the server's x509 certificate.
* The **renewal secret** is the **sha256d tagged pair digest** of (**bucket renewal tag**, **file renewal secret**, **peer id**).

A reference implementation is available.

.. literalinclude:: derive_renewal_secret.py
   :language: python
   :linenos:

Cancel Secrets
--------------

Lease cancellation is unimplemented.
Nevertheless,
a cancel secret is sent by storage clients to storage servers and stored in lease records.

The scheme for deriving **cancel secret** in use in Tahoe-LAFS as of 1.16.0 is similar to that used to derive the **renewal secret**.

The differences are:

* Use of **client renewal tag** is replaced by use of **client cancel tag**.
* Use of **file renewal secret** is replaced by use of **file cancel tag**.
* Use of **bucket renewal tag** is replaced by use of **bucket cancel tag**.
* **client cancel tag** is ``"allmydata_client_cancel_secret_v1"``.
* **file cancel tag** is ``"allmydata_file_cancel_secret_v1"``.
* **bucket cancel tag** is ``"allmydata_bucket_cancel_secret_v1"``.
