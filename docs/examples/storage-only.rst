

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

Congratulations, you have created a minimal storage node, ready to serve clients.
