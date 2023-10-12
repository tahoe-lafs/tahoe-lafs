.. -*- coding: utf-8 -*-

Grid Introducer Internals
=========================

This document explains the implementation of the "Grid Introducer" system.

Storage Announcements
---------------------

The purpose of the Grid Introducer is to inform storage clients about the connection details for storage servers they can use.
These connection details are represented in a "storage announcement".

A storage announcement is a small JSON document that looks something like this::

   {"v0-p46y...":
     { "ann":
       { anonymous-storage-FURL": "pb://sokl...@192.168.69.247:44801/eqpw..."
       , "nickname": "storage001"
       }
     }
   }

The top-level key is a v0 node public key identifying the node the announcement describes.
The next level key is always the string ``"ann"``.
Keys in the next level give specific information for how to connect to the storage server.
Depending on how the storage server is exposed,
these details may vary in structure.

A storage announcement is created by a storage server trying to expose a storage service.
The storage server writes (or rewrites) a mutable object on itself and all other storage servers it knows of.
It uses 1 of 1 erasure encoding for this object so that any single share is sufficient to reconstruct the document.

When a storage server is enrolled in a grid,
the read capability for this mutable object is linked into a Tahoe-LAFS directory.
This is the **announcement directory**.

Each entry in the directory corresponds to a storage server that has been enrolled.
The name is grid-manager assigned human-meaningful string (a "petname") with "v1." as a prefix.
The "v1." prefix versions this entry in the directory to better support future changes to the structure of this directory.
Placing the version information directly in the directory entry name avoids the need for additional round-trips to interrogate the version.
The target of the entry is the read capability for a mutable object where the storage server writes its announcement.

Management Command Line
-----------------------

Storage server enrollment is done using the ``grid-introducer`` command line tool.
The tool requires:

* a Tahoe-LAFS client node which it can use to create and modify stored objects
* the write-cap of a mutable directory where it can link and unlink announcements

This state is persisted in "configuration" which can either:

* be written to the local fileystem
* or written to stdout and read from stdin for persistence by another system.

The configuration consists of a simple JSON document along the lines of::

  { "version": 1
  , "client-api-root": "http://127.0.0.1:4567/"
  , "collection-writecap": "URI:DIR2:5cmy..."
  }

The two pieces of client configuration required by the system can be generated from this state.

To add a storage server,
a *read capability* provided by the storage server for a mutable announcement is linked into the directory referenced by ``collection-writecap``.

To remove a storage server,
the capability for its announcement is unlinked from the directory.

A storage server can change its own announcement details at any time by rewriting the mutable object.

Client Configuration
--------------------

The grid introducer configuration string provided to a client is a JSON string.
The structure of the JSON document is::

  { "version": 1
  , "cap": "URI:DIR2-RO:4bnx..."
  , "furls": [ "pb://sokl...@192.168.69.247:44801/eqpw..." ]
  }

The string value for the ``cap`` property is the read-only capability for ``collection-writecap`` in the ``grid-introducer``\ 's persistent state.
The list of strings value for the ``furls`` property gives storage server hints for bootstrapping purposes
(see `Operation`_).
These values can be extracted from announcements already present in the directory referenced by ``collective-writecap``.

Operation
---------

Storage clients are configured with the readcap for the **announcement directory**.
They are also configured with one or more bootstrap storage fURLs.
These two pieces of information allow them to read all announcements for enrolled servers.

When a client starts it checks its local state for a cache of announcements.
If found these storage servers are added to a pool of candidates for further announcement discovery.
The configured bootstrap storage fURLs are also added to the pool of candidates.
Next, an attempt is made to download the **announcement directory**.
Only one share is required to reconstruct the value so if any single server from the candidate pool can supply that share then recent announcements will be available.

After the **announcement directory** is downloaded each of its children can be downloaded following the same process.
Each announcement is added to the local cache of announcements.

Finally,
all locally cached announcements are available to be used to initialize ``NativeStorageServer`` instances.

The client can periodically repeat this process to discover new announcements and changes to existing announcements.

Failure Modes
-------------

Grid Introducer imposes the following requirements:

* Storage servers must pro-actively publish their announcement to N servers.
  If a new server joins the grid the storage server must push their existing announcement to it.
  If a storage server's announcement changes it must push the new announcement to all storage servers on the grid.

  * If storage servers cannot communicate with each other then announcements cannot be uploaded or updated.
  * If storage servers are full then announcements cannot be uploaded
    (and possibly cannot be updated).

* Storage servers must retain the write capability for their announcement object.
  It must be kept secret or another agent will be able to forge announcements.
  It must not be lost or the storage server will be unable to update its announcement without re-enrollment.

* An administrator must retain the grid introducer state.
  It must be kept secret or another agent will be able to control server enrollment.
  It must not be lost or the administrator will be unable to manage the grid without distributing new configuration to all clients.

* At least one storage server from the list of bootstrap storage servers must remain reachable as long as any clients exist which will bootstrap from that list.
