URLs
====

The goal of this document is to completely specify the construction and use of the URLs by Tahoe-LAFS for service location.
This includes, but is not limited to, the original Foolscap-based URLs.
These are not to be confused with the URI-like capabilities Tahoe-LAFS uses to refer to stored data.
An attempt is also made to outline the rationale for certain choices about these URLs.
The intended audience for this document is Tahoe-LAFS maintainers and other developers interested in interoperating with Tahoe-LAFS or these URLs.

.. _furls:

Background
----------

Tahoe-LAFS first used Foolscap_ for network communication.
Foolscap connection setup takes as an input a Foolscap URL or a *fURL*.
A fURL includes three components:

* the base32-encoded SHA1 hash of the DER form of an x509v3 certificate
* zero or more network addresses [1]_
* an object identifier

A Foolscap client tries to connect to each network address in turn.
If a connection is established then TLS is negotiated.
The server is authenticated by matching its certificate against the hash in the fURL.
A matching certificate serves as proof that the handshaking peer is the correct server.
This serves as the process by which the client authenticates the server.

The client can then exercise further Foolscap functionality using the fURL's object identifier.
If the object identifier is an unguessable, secret string then it serves as a capability.
This unguessable identifier is sometimes called a `swiss number`_ (or swissnum).
The client's use of the swissnum is what allows the server to authorize the client.

.. _`swiss number`: http://wiki.erights.org/wiki/Swiss_number

.. _NURLs:

NURLs
-----

The authentication and authorization properties of fURLs are a good fit for Tahoe-LAFS' requirements.
These are not inherently tied to the Foolscap protocol itself.
In particular they are beneficial to :doc:`../proposed/http-storage-node-protocol` which uses HTTP instead of Foolscap.
It is conceivable they will also be used with WebSockets at some point as well.

Continuing to refer to these URLs as fURLs when they are being used for other protocols may cause confusion.
Therefore,
this document coins the name **NURL** for these URLs.
This can be considered to expand to "**N**\ ew URLs" or "Authe\ **N**\ ticating URLs" or "Authorizi\ **N**\ g URLs" as the reader prefers.

The anticipated use for a **NURL** will still be to establish a TLS connection to a peer.
The protocol run over that TLS connection could be Foolscap though it is more likely to be an HTTP-based protocol (such as GBS).

Unlike fURLs, only a single net-loc is included, for consistency with other forms of URLs.
As a result, multiple NURLs may be available for a single server.

Syntax
------

The EBNF for a NURL is as follows::

  nurl         = tcp-nurl | tor-nurl | i2p-nurl
  tcp-nurl     = "pb://", hash, "@", tcp-loc, "/", swiss-number, [ version1 ]
  tor-nurl     = "pb+tor://", hash, "@", tcp-loc, "/", swiss-number, [ version1 ]
  i2p-nurl     = "pb+i2p://", hash, "@", i2p-loc, "/", swiss-number, [ version1 ]

  hash         = unreserved

  tcp-loc      = hostname, [ ":" port ]
  hostname     = domain | IPv4address | IPv6address

  i2p-loc      = i2p-addr, [ ":" port ]
  i2p-addr     = { unreserved }, ".i2p"

  swiss-number = segment

  version1     = "#v=1"

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

In theory, a Foolscap fURL with a single netloc is considered the canonical definition of a version 0 NURL.
Notably,
the hash component is defined as the base32-encoded SHA1 hash of the DER form of an x509v3 certificate.
A version 0 NURL is identified by the absence of the ``v=1`` fragment.

In practice, real world fURLs may have more than one netloc, so lack of version fragment will likely just involve dispatching the fURL to a different parser.

Examples
~~~~~~~~

* ``pb://sisi4zenj7cxncgvdog7szg3yxbrnamy@tcp:127.1:34399/xphmwz6lx24rh2nxlinni``
* ``pb://2uxmzoqqimpdwowxr24q6w5ekmxcymby@localhost:47877/riqhpojvzwxujhna5szkn``

Version 1
---------

The hash component of a version 1 NURL differs in three ways from the prior version.

1. The hash function used is SHA-256, to match RFC 7469.
   The security of SHA1 `continues to be eroded`_; Latacora `SHA-2`_.
2. The hash is computed over the certificate's SPKI instead of the whole certificate.
   This allows certificate re-generation so long as the public key remains the same.
   This is useful to allow contact information to be updated or extension of validity period.
   Use of an SPKI hash has also been `explored by the web community`_ during its flirtation with using it for HTTPS certificate pinning
   (though this is now largely abandoned).

.. note::
   *Only* the certificate's keypair is pinned by the SPKI hash.
   The freedom to change every other part of the certificate is coupled with the fact that all other parts of the certificate contain arbitrary information set by the private key holder.
   It is neither guaranteed nor expected that a certificate-issuing authority has validated this information.
   Therefore,
   *all* certificate fields should be considered within the context of the relationship identified by the SPKI hash.

3. The hash is encoded using urlsafe-base64 (without padding) instead of base32.
   This provides a more compact representation and minimizes the usability impacts of switching from a 160 bit hash to a 256 bit hash.

A version 1 NURL is identified by the presence of the ``v=1`` fragment.
Though the length of the hash string (38 bytes) could also be used to differentiate it from a version 0 NURL,
there is no guarantee that this will be effective in differentiating it from future versions so this approach should not be used.

It is possible for a client to unilaterally upgrade a version 0 NURL to a version 1 NURL.
After establishing and authenticating a connection the client will have received a copy of the server's certificate.
This is sufficient to compute the new hash and rewrite the NURL to upgrade it to version 1.
This provides stronger authentication assurances for future uses but it is not required.

Examples
~~~~~~~~

* ``pb://1WUX44xKjKdpGLohmFcBNuIRN-8rlv1Iij_7rQ@tcp:127.1:34399/jhjbc3bjbhk#v=1``
* ``pb://azEu8vlRpnEeYm0DySQDeNY3Z2iJXHC_bsbaAw@localhost:47877/64i4aokv4ej#v=1``

.. _`continues to be eroded`: https://en.wikipedia.org/wiki/SHA-1#Cryptanalysis_and_validation
.. _`SHA-2`: https://latacora.micro.blog/2018/04/03/cryptographic-right-answers.html
.. _`explored by the web community`: https://www.rfc-editor.org/rfc/rfc7469
.. _Foolscap: https://github.com/warner/foolscap

.. [1] ``foolscap.furl.decode_furl`` is taken as the canonical definition of the syntax of a fURL.
       The **location hints** part of the fURL,
       as it is referred to in Foolscap,
       is matched by the regular expression fragment ``([^/]*)``.
       Since this matches the empty string,
       no network addresses are required to form a fURL.
       The supporting code around the regular expression also takes extra steps to allow an empty string to match here.

Open Questions
--------------

1. Should we make a hard recommendation that all certificate fields are ignored?
   The system makes no guarantees about validation of these fields.
   Is it just an unnecessary risk to let a user see them?

2. Should the version specifier be a query-arg-alike or a fragment-alike?
   The value is only necessary on the client side which makes it similar to an HTTP URL fragment.
   The current Tahoe-LAFS configuration parsing code has special handling of the fragment character (``#``) which makes it unusable.
   However,
   the configuration parsing code is easily changed.
