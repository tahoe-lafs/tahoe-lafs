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
The storage server writes (or rewrites) a mutable object on itself and potentially many other storage servers it knows of.
It uses 1 of 1 erasure encoding for this object so that any single share is sufficient to reconstruct the document.

When a storage server is enrolled in a grid,
the read capability for this mutable object is linked into a Tahoe-LAFS directory.

Each entry in the directory corresponds to a storage server that has been enrolled.
The name is the v0 public key string with the ".v1" as a suffix.
The target of the entry is the mutable readcap where the storage server writes its announcement.

Storage clients are configured with the readcap for the directory.
This allows them to read all announcements for enrolled servers.
