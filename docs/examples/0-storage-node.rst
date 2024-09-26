============================
Create a simple storage node
============================

.. note:: Estimated time is 5 minutes

1. Create a simple node to serve as storage server:

.. code-block::

    $ tahoe --node-directory=./storage0 create-node \
     --hostname=localhost \
     --nickname=storage0 \
     --webport=none

.. note:: Ignore the response ``Please add introducers ... The node cannot connect to a grid without it.``

Start the node process
----------------------

Now, in the terminal session you created earlier and called ``storage_console``::

    $ tahoe run storage0

At the end of the console listing, you should see something similar to::

    2024-09-10T12:54:21-0700 [-] client running

Congratulations, you have created a minimal storage node, ready to serve clients.

