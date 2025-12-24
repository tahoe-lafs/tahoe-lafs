.. -*- coding: utf-8 -*-

Grid Introducer
===============

This document explains use and operation of the "Grid Introducer" system.
This system replaces the earlier "Introducer node" system.

The "Grid Introducer" system is a means for client nodes to initially learn about available storage nodes.
It also allows client nodes to receive updates to this information.
The goal of this document is to explain how to *use* this system,
either as a user of a Tahoe-LAFS client node or as the operator of a Tahoe-LAFS grid or storage node.


Use As A Client
---------------

You want your Tahoe-LAFS client to be able to find storage servers to access.
This means your client needs both current and future connection information for those storage servers.
By configuring your client with one small static connection hint,
the "Grid Introducer" system enables the client to find more information and future updates to that information.

Before attempting this configuration,
you should have an introduction configuration string from the operator of a Grid Introducer-enabled Tahoe-LAFS storage grid.

The Grid Introducer is configured in two parts.
First,
the introduction configuration string is written to a new file.
This configuration should be treated as a secret.
The node's ``private`` directory is a good location to consider for this file.
For example,
for a grid you refer to as the "foo grid" you might use ``private/foogrid-introduction``.

Next,
in ``tahoe.cfg`` the ``grid-introducer-path`` item in the ``[client]`` section is set to refer to this file.
For example::

  [client]
  grid-introducer-path = private/foogrid-introduction

Start a Tahoe-LAFS client node with these items configured and the client will be able to find and follow all storage servers that are part of that grid.

Use As A Storage Provider
-------------------------

You want your Tahoe-LAFS storage node to be able to publish its connection details where clients can find it.
Using Grid Introducer,
a storage server will discover other storage servers and write its connection details to all of them.
Then the capability to read this information is linked into a collection stored on the grid and known to clients.

A storage server must be configured just like a storage client so it can discover other storage servers.
See `Use As A Client`_ for this part of the configuration.

Next, a storage server must be configured to maintain its announcement on the grid.
This is also done in ``tahoe.cfg``,
in the ``[storage]`` section's ``grid-introducer.enable`` item.

For example::

  [storage]
  grid-introducer.enable = true

After the storage server has been configured this way and started it will upload its announcement.
After the initial announcement upload has finished two new files are written to the ``private`` area.
The write capability for the mutable announcement is written to ``grid-introducer-announcement.write-cap``.
The read capability is written to ``grid-introducer-announcement.read-cap``.
The capability in ``grid-introducer-announcement.read-cap`` is the capability that is shared with the grid coordinate for enrollment
(see below).
It is also possible to pre-allocate a mutable object and write it to ``grid-introducer-announcement.write-cap``.
When the storage server starts up it will discover and use this value instead of allocating a new one.

The ``grid-introducer-announcement.write-cap`` is essential state.
Without it the storage node cannot update its announcement.
It should be made as durable as the rest of the private storage node state.

Use As A Grid Coordinator
-------------------------

Setup
~~~~~

You want to offer a collection of storage servers as a Tahoe-LAFS storage grid.

The ``grid-introducer`` tool requires a client node it can use to store and publish announcements.
With that in place,
the first step is to create the introducer's persistent state::

  grid-introducer create --config <path> --client-api-root <Tahoe-LAFS client node HTTP API root>

This is an error if ``<path>`` exists already.
If it does not then a new grid introducer configuration is written there.
If ``<path>`` is ``-`` then the configuration is written to stdout.

This is the grid introducer's state and is required by future commands.
It is the operator's responsibility to persist this state.

Enrolling Servers
~~~~~~~~~~~~~~~~~

A Tahoe-LAFS storage server which is to be enrolled first shares its announcement readcap with you.
Then, you will add it to the announcement directory::

   grid-introducer add-storage-server --config <path> --announcement-readcap URI:CHK-RO:5cmy...

``<path>`` should have previously been created by ``grid-introducer create``.
If ``<path>`` is ``-`` then the configuration is read from stdin.


Inviting Clients
~~~~~~~~~~~~~~~~

A Tahoe-LAFS client node which is to use the grid introducer needs to be configured with a couple items.
A configuration blob for clients can be generated like this::

  grid-introducer generate-client-config --config <path>

``<path>`` is handled here as elsewhere.
The output is a configuration string which should be made available to a client node and referenced from that client's configuration.
The configuration should be treated as secret because it includes secrets that allow access to the grid.
The configuration is used in the process described by `Use As A Client`_ .
