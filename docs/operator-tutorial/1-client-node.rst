=====================
Create a client node
=====================

.. note:: Estimated time is 15 minutes


To interact with tahoe-lafs services, you need to creat and start a client.

.. note:: Ignore the response ``Please add introducers ... The node cannot connect to a grid without it.``

Create a simple client configuration::

    $ tahoe --node-directory=client0 create-client \
    --shares-happy=1 \
    --shares-needed=1 \
    --shares-total=1 \
    --nickname=client0

.. info:: These options are explained in `Client Configuration`_ .

You will see the console output end with something like:

.. code-block::console

    2024-09-19T13:31:13-0400 [foolscap.pb.Listener#info] Starting factory <Listener at 0x10f1624e0 on CleanupEndpoint(_wrapped=<twisted.internet.endpoints.AdoptedStreamServerEndpoint object at 0x10f161ca0>, _fd=10, _listened=True) with tub x2hgwovdakx3kdelyetg3duzh4chyt22>
    2024-09-19T13:31:13-0400 [-] client running


Prepare the client
===================

Point the client to the storage node
------------------------------------

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

    $ tahoe run client0/

The console output should include something like:
``2024-09-10T13:25:33-0700 [-] TahoeLAFSSite starting on 3456`` and end with ``- client running``

In the console output, you will notice that the client runs two network connections:
    - A web app using a REST API on TCP port 3456
    - A protobuf style client using Foolscap on TCP port 57635

Verify the HTML client
======================

Open the client's web UI at http://localhost:3456

The landing page should show 1 of 1 storage servers connected, 0 introducers and 0 helpers.
This verifies that the client can run Tahoe requests and that the storage node successfully responds.

.. admonition:: Congratulations on completing Step 2 !
    :class: tip

