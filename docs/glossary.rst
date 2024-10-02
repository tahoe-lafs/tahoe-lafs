.. -*- coding: utf-8-with-signature -*-

=======================
Glossary of Tahoe terms
=======================


.. glossary::

    cap
    capability
        In the context of Tahoe-LAFS these capability objects are somewhat short strings of data giving the holder necessary and sufficient secret information to carry out the operation and turn the capability into a less-capable one.
        For example, a "verify cap" gives only enough information to confirm the ciphertext is available (but not to decode or read it); a "read cap" can decode and read the data (but not modify it) and can be turned into a "verify cap".

    client
        Be aware that the term "client" is ambiguously used as a "client (to gateway) program" and as a gateway node. It generally refers to the network process for a data or control plane.

    `Foolscap <https://github.com/warner/foolscap/>`_
        an RPC/RMI (Remote Procedure Call / Remote Method Invocation) protocol for use with Twisted

    :ref:`fURLs <fURLs>`
        a self-authenticating URL-like string which can be used to locate a remote object using the Foolscap protocol
     (the storage service is an example of such an object)

    grid
        A collection of storage nodes represented as a single "pool".

    helper
        The “Helper” is a service that can mitigate the expansion penalty by arranging for the client node to send data to a central Helper node instead of sending it directly to the storage servers. :doc:`Helper Overview </helper>`

    introducer
        A Tahoe-LAFS process at a known location configured to re-publish announcements about the location of storage servers. A collection of Tahoe servers is called a Grid and usually has 1 Introducer (but sometimes more, and it’s possible to run with zero). The Introducer announces which storage servers constitute the Grid and how to contact them. There is a secret “fURL” you need to know to talkto the Introducer.

    log gatherer
        (A foolscap thing) A server subscribes to hear about every single event published by the connected nodes, regardless of severity. This server writes these log events into a large flogfile that is rotated (closed, compressed, and replaced with a new one) on a periodic basis. There are three major logging systems: Foolscap, Eliot, Twisted Logging

    object
         aka a Tahoe file or directory

    read-only
        Refers to a subset of Tahoe URI permitting view, but not modification.  Directories, for example, have a read-cap which is derived from the write-cap: anyone with read/write access to the directory can produce a limited URI that grants read-only access, but not the other way around.

    share
        A share is a piece of a file that is stored on a server. The complete file is encrypted and then encoded into blocks. An instance of those blocks is called a "share". :doc:`specifications/file-encoding` Technically, this could be any client-provided arbitrary data; in normal Tahoe-LAFS operation it is one of the outputs of applying ZFEC encoding to some ciphertext (with some additional metadata attached).

    storage
        The media device that provides the persistence.

    storage server
        a Tahoe-LAFS process configured to offer storage and reachable over the network for store and retrieve operations

    Tahoe URI
        Each file and directory in a Tahoe-LAFS file store is described by a “URI”. This string must be treated as a secret because it will provide access to the content in the clear. There are different kinds of URIs for different kinds of objects, and there are different kinds of URIs to provide different kinds of access to those objects.

    Zooko's Triangle
       `Wikipedia <https://en.wikipedia.org/wiki/Zooko%27s_triangle>`_ defines it as "a trilemma which defines three traits of a network protocol identifier as Human-meaningful, Decentralized and Secure."
