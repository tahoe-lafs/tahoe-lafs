.. -*- coding: utf-8 -*-

HTTP Introducer
===============

This document explains use and operation of the "HTTP Introducer" system.
This system replaces the earlier "Introducer node" system.

The "HTTP Introducer" system is a means for client nodes to initially learn about available storage nodes.
It also allows client nodes to receive updates to this information.
The goal of this document is to explain how to *use* this system,
either as a user of a Tahoe-LAFS client node or as the operator of a Tahoe-LAFS grid or storage node.


Use As A Client
---------------

You want your Tahoe-LAFS client to be able to find storage servers to access.
This means your client needs both current and future connection information for those storage servers.
By configuring your client with one small static connection hint,
the "HTTP Introducer" system enables the client to find more information and future updates to that information.

Before attempting this configuration,
you should have an HTTP-style *introducer fURL* from the operator of an HTTP Introducer-enabled Tahoe-LAFS storage grid.

The HTTP Introducer is configured in two parts.
First,
the *introducer fURL* is written to a new file.
This configuration should be treated as a secret.
The node's ``private`` directory is a good location to consider for this file.
For example,
for a grid you refer to as the "foo grid" you might use ``private/foogrid-introducer``.

Next,
in ``tahoe.cfg`` the ``grid-introducer-path`` item in the ``[client]`` section is set to refer to this file.
For example::

  [client]
  http-introducer-path = private/foogrid-introduction

Start a Tahoe-LAFS client node with these items configured and the client will be able to find and follow all storage servers that are part of that grid.

Use As A Storage Provider
-------------------------

You want your Tahoe-LAFS storage node to be able to publish its connection details where clients can find it.
A storage server can publish its storage announcement to the HTTP Introducer.
The HTTP Introducer is then responsible for delivering it to interested parties.

The configuration for a storage node is exactly the same as the configuration for a client node,
as described above.

After the storage server has been configured this way and started it will publish its announcement to the HTTP Introducer.

Use As A Grid Coordinator
-------------------------

Setup
~~~~~

You want to offer a collection of storage servers as a Tahoe-LAFS storage grid.

The ``http-introducer`` tool is a stand-alone server which implements basic publish/subscribe functionality.

The first step is to create the introducer's persistent state::

  http-introducer create --config <path> --listen-endpoint <endpoint> [--certificate-path <path> --private-key-path <path>]

This is an error if ``<path>`` exists already.
If it does not then a new HTTP Introducer configuration is written there.
If ``<path>`` is ``-`` then the configuration is written to stdout.

``--listen-endpoint`` may be used repeatedly to listen on multiple addresses.
Whatever endpoint type is chosen,
``http-introducer`` will *always* _automatically_ negotiate TLS over it.

If given,
the files given by ``--certificate-path`` and ``--private-key-path`` have their contents read and added to the configuration state.
After the execution of this command,
these files are not read again.
If not given,
a new private key and self-signed certificate is generated and used.

This is the HTTP Introducer's state and is required by future commands.
It is the operator's responsibility to persist this state.

The next step is to start the long-running HTTP Introducer process.
As long as this is running,
storage nodes will be able to publish announcements and clients will be able to subscribe to them.

::
   http-introducer run --config <path>

``<path>`` should have previously been created by ``http-introducer create``.
If ``<path>`` is ``-`` then the configuration is read from stdin.


Enrolling Servers / Inviting Clients
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The configuration to make a storage server or a client node use an HTTP Introducer is the same.
They each require the *introducer fURL*.
To retrieve this value,
run this command::

  http-introducer get-introducer-furl --config <path>

``<path>`` is handled here as elsewhere.
The output is the *introducer fURL* which should be made available to a client node and referenced from that client's configuration.
The configuration should be treated as secret because it includes secrets that allow access to the grid.
The configuration is used in the process described by `Use As A Client`_ .

Example Scenario
----------------

Alice operates an HTTP introducer.
Bob operates a storage server.
Carol operates a client node.

::

   [alice@aaa:~]$ CFG=myintroducer.json
   [alice@aaa:~]$ http-introducer create --config $CFG --listen-endpoint tcp:12345
   [alice@aaa:~]$ http-introducer get-introducer-furl --config $CFG
   xxx://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz@somewhere:12345/pqpqpqpqpqpqpqpqpqpqpqpqpqpqpqpq
   [alice@aaa:~]$ daemonize http-introducer run --config $CFG
   [alice@aaa:~]$

::

   [bob@bbb:~]$ cat >> storage-node/tahoe.cfg
   [client]
   http-introducer-path = storage-node/private/alicegrid.json
   ^D
   [bob@bbb:~]$ cat > storage-node/private/alicegrid.json
   xxx://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz@somewhere:12345/pqpqpqpqpqpqpqpqpqpqpqpqpqpqpqpq
   ^D
   [bob@bbb:~]$ kill $(cat storage-node/twistd.pid)
   [bob@bbb:~]$ daemonize tahoe run storage-node
   [bob@bbb:~]$

::

   [carol@ccc:~]$ cat >> storage-node/tahoe.cfg
   [client]
   http-introducer-path = storage-node/private/alicegrid.json
   ^D
   [carol@ccc:~]$ cat > storage-node/private/alicegrid.json
   xxx://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz@somewhere:12345/pqpqpqpqpqpqpqpqpqpqpqpqpqpqpqpq
   ^D
   [carol@ccc:~]$ kill $(cat storage-node/twistd.pid)
   [carol@ccc:~]$ daemonize tahoe run storage-node
   [carol@ccc:~]$
