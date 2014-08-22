.. -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-

There are several ways you could use Tahoe-LAFS as a key-value store.

Looking only at things that are *already implemented*, there are three
options:

1. Immutable files

   API:

    * key ← put(value)

      This is spelled "`PUT /uri`_" in the API.

      Note: the user (client code) of this API does not get to choose the key!
      The key is determined programmatically using secure hash functions and
      encryption of the value and of the optional "added convergence secret".

    * value ← get(key)

      This is spelled "`GET /uri/$FILECAP`_" in the API. "$FILECAP" is the
      key.

   For details, see "immutable files" in `performance.rst`_, but in summary:
   the performance is not great but not bad.

   That document doesn't mention that if the size of the A-byte mutable file
   is less than or equal to `55 bytes`_ then the performance cost is much
   smaller, because the value gets packed into the key. Added a ticket:
   `#2226`_.

2. Mutable files

   API:

    * key ← create()

      This is spelled "`PUT /uri?format=mdmf`_".

      Note: again, the key cannot be chosen by the user! The key is
      determined programmatically using secure hash functions and RSA public
      key pair generation.

    * set(key, value)

    * value ← get(key)

      This is spelled "`GET /uri/$FILECAP`_". Again, the "$FILECAP" is the
      key. This is the same API as for getting the value from an immutable,
      above. Whether the value you get this way is immutable (i.e. it will
      always be the same value) or mutable (i.e. an authorized person can
      change what value you get when you read) depends on the type of the
      key.

   Again, for details, see "mutable files" in `performance.rst`_ (and
   `these tickets`_ about how that doc is incomplete), but in summary, the
   performance of the create() operation is *terrible*! (It involves
   generating a 2048-bit RSA key pair.) The performance of the set and get
   operations are probably merely not great but not bad.

3. Directories

   API:

    * directory ← create()

      This is spelled "`POST /uri?t=mkdir`_".

      `performance.rst`_ does not mention directories (`#2228`_), but in order
      to understand the performance of directories you have to understand how
      they are implemented. Mkdir creates a new mutable file, exactly the
      same, and with exactly the same performance, as the "create() mutable"
      above.

    * set(directory, key, value)

      This is spelled "`PUT /uri/$DIRCAP/[SUBDIRS../]FILENAME`_". "$DIRCAP"
      is the directory, "FILENAME" is the key. The value is the body of the
      HTTP PUT request. The part about "[SUBDIRS../]" in there is for
      optional nesting which you can ignore for the purposes of this
      key-value store.

      This way, you *do* get to choose the key to be whatever you want (an
      arbitrary unicode string).

      To understand the performance of ``PUT /uri/$directory/$key``,
      understand that this proceeds in two steps: first it uploads the value
      as an immutable file, exactly the same as the "put(value)" API from the
      immutable API above. So right there you've already paid exactly the
      same cost as if you had used that API. Then after it has finished
      uploading that, and it has the immutable file cap from that operation
      in hand, it downloads the entire current directory, changes it to
      include the mapping from key to the immutable file cap, and re-uploads
      the entire directory. So that has a cost which is easy to understand:
      you have to download and re-upload the entire directory, which is the
      entire set of mappings from user-chosen keys (Unicode strings) to
      immutable file caps. Each entry in the directory occupies something on
      the order of 300 bytes.

      So the "set()" call from this directory-based API has obviously much
      worse performance than the the equivalent "set()" calls from the
      immutable-file-based API or the mutable-file-based API. This is not
      necessarily worse overall than the performance of the
      mutable-file-based API if you take into account the cost of the
      necessary create() calls.

    * value ← get(directory, key)

      This is spelled "`GET /uri/$DIRCAP/[SUBDIRS../]FILENAME`_". As above,
      "$DIRCAP" is the directory, "FILENAME" is the key.

      The performance of this is determined by the fact that it first
      downloads the entire directory, then finds the immutable filecap for
      the given key, then does a GET on that immutable filecap. So again,
      it is strictly worse than using the immutable file API (about twice
      as bad, if the directory size is similar to the value size).

What about ways to use LAFS as a key-value store that are not yet
implemented? Well, Zooko has lots of ideas about ways to extend Tahoe-LAFS to
support different kinds of storage APIs or better performance. One that he
thinks is pretty promising is just the Keep It Simple, Stupid idea of "store a
sqlite db in a Tahoe-LAFS mutable". ☺

.. _PUT /uri: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/frontends/webapi.rst#writing-uploading-a-file

.. _GET /uri/$FILECAP: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/frontends/webapi.rst#viewing-downloading-a-file

.. _55 bytes: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/src/allmydata/immutable/upload.py?rev=196bd583b6c4959c60d3f73cdcefc9edda6a38ae#L1504

.. _PUT /uri?format=mdmf: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/frontends/webapi.rst#writing-uploading-a-file

.. _performance.rst: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/performance.rst

.. _#2226: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2226

.. _these tickets: https://tahoe-lafs.org/trac/tahoe-lafs/query?status=assigned&status=new&status=reopened&keywords=~doc&description=~performance.rst&col=id&col=summary&col=status&col=owner&col=type&col=priority&col=milestone&order=priority

.. _POST /uri?t=mkdir: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/frontends/webapi.rst#creating-a-new-directory

.. _#2228: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2228

.. _PUT /uri/$DIRCAP/[SUBDIRS../]FILENAME: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/frontends/webapi.rst#creating-a-new-directory

.. _GET /uri/$DIRCAP/[SUBDIRS../]FILENAME: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/frontends/webapi.rst#reading-a-file

