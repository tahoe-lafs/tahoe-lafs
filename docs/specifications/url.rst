URLs
====

The goal of this document is to completely specify the construction and use of the URLs by Tahoe-LAFS for service location.
This includes, but is not limited to, the original Foolscap-based URLs.
These are not to be confused with the URI-like capabilities Tahoe-LAFS uses to refer to stored data.
An attempt is also made to outline the rationale for certain choices about these URLs.
The intended audience for this document is Tahoe-LAFS maintainers and other developers interested in interoperating with Tahoe-LAFS or these URLs.

Background
----------

Tahoe-LAFS first used Foolscap_ for network communication.
Foolscap connection setup takes as an input a Foolscap URL or a *fURL*.
A fURL includes three components:

* the base32-encoded SHA1 hash of the DER form of an x509v3 certificate
* zero or more network addresses
* an object identifier

A Foolscap client tries to connect to each network address in turn.
If a connection is established then TLS is negotiated.
The server is authenticated by matching its certificate against the hash in the fURL.
A matching certificate serves as proof that the handshaking peer is the correct server.
This serves as the process by which the client authenticates the server.

The client can then exercise further Foolscap functionality using the fURL's object identifier.
If the object identifier is an unguessable, secret string then it serves as a capability.
This serves as the process by which the server authorizes the client.

NURLs
-----

The authentication and authorization properties of fURLs are a good fit for Tahoe-LAFS' requirements.
These are not inherently tied to the Foolscap protocol itself.
In particular they are beneficial to :doc:`http-storage-node-protocol` which uses HTTP instead of Foolscap.
It is conceivable they will also be used with WebSockets at some point as well.

Continuing to refer to these URLs as fURLs when they are being used for other protocols may cause confusion.
Therefore,
this document coins the name *NURL* for these URLs.
This can be considered to expand to "New URLs" or "Authe*N*ticating URLs" or "Authorizi*N*g URLs" as the reader prefers.

Syntax
------

The EBNF for a NURL is as follows::

  nurl         = scheme, hash, "@", net-loc-list, "/", swiss-number

  scheme       = "pb://"

  hash         = unreserved

  net-loc-list = net-loc, [ { ",", net-loc } ]
  net-loc      = hostname, [ ":" port ]
  hostname     = domain | IPv4address | IPv6address

  swiss-number = segment

See https://tools.ietf.org/html/rfc3986#section-3.3 for the definition of ``segment``.
See https://tools.ietf.org/html/rfc2396#appendix-A for the definition of ``unreserved``.
See https://tools.ietf.org/html/draft-main-ipaddr-text-rep-02#section-3.1 for the definition of ``IPv4address``.
See https://tools.ietf.org/html/draft-main-ipaddr-text-rep-02#section-3.2 for the definition of ``IPv6address``.
See https://tools.ietf.org/html/rfc1035#section-2.3.1 for the definition of ``domain``.

Versions
--------

Though all NURLs are syntactically compatible some semantic differences are allowed.
These differences are separated into distinct versions.

Version 0
---------

A Foolscap fURL is considered the canonical definition of a version 0 NURL.
Notably,
the hash component is defined as the base32-encoded SHA1 hash of the DER form of an x509v3 certificate.
A version 0 NURL is identified by the length of the hash string which is always 32 bytes.

Version 1
---------

The hash component of a version 1 NURL differs in three ways from the prior version.

1. The hash function used is SHA3-224 instead of SHA1.
   The security of SHA1 `continues to be eroded`_.
   Contrariwise SHA3 is currently the most recent addition to the SHA family by NIST.
   The 224 bit instance is chosen to keep the output short and because it offers greater collision resistance than SHA1 was thought to offer even at its inception
   (prior to security research showing actual collision resistance is lower).
2. The hash is computed over the certificate's SPKI instead of the whole certificate.
   This allows certificate re-generation so long as the public key remains the same.
   This is useful to allow contact information to be updated or extension of validity period.
   Use of an SPKI hash has also been `explored by the web community`_ during its flirtation with using it for HTTPS certificate pinning
   (though this is now largely abandoned).
3. The hash is encoded using urlsafe-base64 (without padding) instead of base32.
   This provides a more compact representation and minimizes the usability impacts of switching from a 160 bit hash to a 224 bit hash.

A version 1 NURL is identified by the length of the hash string which is always 38 bytes.

It is possible for a client to unilaterally upgrade a version 0 NURL to a version 1 NURL.
After establishing and authenticating a connection the client will have received a copy of the server's certificate.
This is sufficient to compute the new hash and rewrite the NURL to upgrade it to version 1.
This provides stronger authentication assurances for future uses but it is not required.

.. _`continues to be eroded`: https://en.wikipedia.org/wiki/SHA-1#Cryptanalysis_and_validation
.. _`explored by the web community`: https://www.imperialviolet.org/2011/05/04/pinning.html
