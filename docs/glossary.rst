.. -*- coding: utf-8-with-signature -*-

=======================
Glossary of Tahoe terms
=======================


.. glossary::

    cap
    capability
        dircap, filecap, write cap, read cap, verify cap; all these refer to a capability or privilege associated with a key.

    client
        Be aware that the term "client" is ambiguously used as a "client (to gateway) program" and as a gateway node. It generally refers to the network process for a data or control plane.

    foolscap
        Foolscap is an RPC/RMI (Remote Procedure Call / Remote Method Invocation) protocol for use with Twisted, derived/inspired by Twisted's built-in "Perspective Broker" package. https://github.com/warner/foolscap

    fURL
        A Foolscap URL. A Foolscap connection setup takes as an input.

    grid
    storage grid
        A collection of storage nodes working in concert. A Tahoe client may access a grid via an introducer or via static settings.

    helper
        The “Helper” is a service that can mitigate the expansion penalty by arranging for the client node to send data to a central Helper node instead of sending it directly to the storage servers. :doc:`Helper Overview </helper>`

    introducer
        A collection of Tahoe servers is called a Grid and usually has 1 Introducer (but sometimes more, and it’s possible to run with zero). The Introducer announces which storage servers constitute the Grid and how to contact them. There is a secret “fURL” you need to know to talkto the Introducer.

    log gatherer
        (A foolscap thing) A server subscribes to hear about every single event published by the connected nodes, regardless of severity. This server writes these log events into a large flogfile that is rotated (closed, compressed, and replaced with a new one) on a periodic basis. There are three major logging systems: Foolscap, Eliot, Twisted Logging

    object
         aka a Tahoe file or directory

    read-only
        Refers to a subset of Tahoe URI permitting view, but not modification.  Directories, for example, have a read-cap which is derived from the write-cap: anyone with read/write access to the directory can produce a limited URI that grants read-only access, but not the other way around.

    share
        A share is a piece of a file that is stored on a server. The complete file is encrypted and then encoded into blocks. An instance of those blocks is called a "share". (Eventually we need a whole section on out data structures.)

    stats gatherer
        Each Tahoe node collects and publishes statistics about its operations as it runs. These include counters of how many files have been uploaded and downloaded, CPU usage information, performance numbers like latency of storage server operations, and available disk space.

    storage
        Persistence of {client,server,protocol}.

    Tahoe URI
        Each file and directory in a Tahoe-LAFS file store is described by a “URI”. There are different kinds of URIs for different kinds of objects, and there are different kinds of URIs to provide different kinds of access to those objects.

    Zooko's Triangle
       `Wikipedia <https://en.wikipedia.org/wiki/Zooko%27s_triangle>`_ defines it as "a trilemma which defines three traits of a network protocol identifier as Human-meaningful, Decentralized and Secure."