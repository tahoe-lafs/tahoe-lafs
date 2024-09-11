============================
Getting Started Step-by-Step
============================

Before you begin
================

Create and activate a local venv for tahoe::

    python -m venv .venv && source .venv/bin/activate

Update the new venv and install tahoe-lafs::

    pip install -U pip setuptools wheel && \
    pip install attrs==23.2.0 cryptography==42.0.8 tahoe-lafs


``tmux`` is your friend
-----------------------

Since you will be running several processes, it helps to have multiple terminal windows.
A Linux terminal user would create several sessions like this::

    $ tmux new -s storage_console
    $ tmux new -s client_console

Most IDEs also support this feature easily.

Step 1, create the simplest node
=================================

1. Create a simple node to serve as storage server ::

    $ tahoe --node-directory=./storage0 create-node \
     --hostname=localhost \
     --nickname=storage0 \
     --webport=none


.. note:: Ignore the response ``Please add introducers ... The node cannot connect to a grid without it.``

Start the node process
----------------------

Now, in the  terminal session called ``storage_console``::

    $ cd storage0 && tahoe run ./

At the end of the console listing, you should see something similar to::

    2024-09-10T12:54:21-0700 [-] client running

Congratulations, you have created a minimal storage node. It is is ready to serve clients

Step 2, a client node
======================

To interact with tahoe-lafs services, you need a client. Create a simple client configuration::

    $ tahoe --node-directory=client0 create-client \
    --nickname=client0

.. note:: Ignore the response ``Please add introducers ... The node cannot connect to a grid without it.``


Point the client to the server
------------------------------

For now, we will tell the client how to find server, using a static configuration setting.
Create a ``./client0/private/servers.yaml`` file in the client configuration directory::

    $ nano ./client0/private/servers.yaml

When complete, the contents of the file will look something like this::

    storage:
      v0-qacl3os464epv7olvwolv55tqlrimfj2bpwwjo43qfotlwxpfcsa:
        ann:
          nickname: storage0
          anonymous-storage-FURL: pb://wknlsj5cfrfogj7je2gjd2azakyf7amd@tcp:localhost:55316/iv6ilyybouwm4o5mbwhstduupkpyhiof

The value for ``storage:`` open the file ``storage0/node.pubkey`` and copy everything after ``pub-``.

The value for ``anonymous-storage-FURL:`` is the entire content of ``./storage0/private/storage.furl``. This is also called the anonymous :term:`fURL` of the storage server.


.. note::  Static server settings are described at https://tahoe-lafs.readthedocs.io/en/latest/configuration.html#static-server-definitions

Start the client process
-------------------------

In the console window called ``client_node``::

    $ cd client0 && tahoe run ./

The console output should include something like:
``2024-09-10T13:25:33-0700 [-] TahoeLAFSSite starting on 3456`` and end with ``- client running``

In the console output, you will notice that the client runs two network connections:
    - A web app using a REST API on TCP port 3456
    - A protobuf style client using Foolscap on TCP port 57635

Verify the the client
=====================

Open the client's web UI at http://localhost:3456

The landing page should show 1 of 1 storage servers connected, 0 introducers and 0 helpers.
This verifies that the client can run Tahoe requests and that the storage node successfully responds.

.. admonition:: Congratulations on completing Step 2 !
    :class: tip


---

Finding the server with a ``furl``
===================================

But there is no grid yet!
-------------------------

Third, the introducer node
==========================

``tahoe create-introducer --hostname=HOSTNAME .``





FAQ
===

Questions as we move through the process

    - WTF is the ``.tac`` file?
        A twisted config file.

    - Can I skip the CLI to "create-node", if I create files manually?

    - Do we need a separate ``quickstart`` instead?
