.. -*- coding: utf-8 -*-

============================
Glossary of Tahoe-LAFS Terms
============================


.. glossary::

   `Foolscap <https://github.com/warner/foolscap/>`_
     an RPC/RMI (Remote Procedure Call / Remote Method Invocation) protocol for use with Twisted

   storage server
     a Tahoe-LAFS process configured to offer storage and reachable over the network for store and retrieve operations

   storage service
     a Python object held in memory in the storage server which provides the implementation of the storage protocol

   introducer
     a Tahoe-LAFS process at a known location configured to re-publish announcements about the location of storage servers

   :ref:`fURLs <fURLs>`
     a self-authenticating URL-like string which can be used to locate a remote object using the Foolscap protocol
     (the storage service is an example of such an object)

   :ref:`NURLs <NURLs>`
     a self-authenticating URL-like string almost exactly like a fURL but without being tied to Foolscap

   swissnum
     a short random string which is part of a fURL/NURL and which acts as a shared secret to authorize clients to use a storage service

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
