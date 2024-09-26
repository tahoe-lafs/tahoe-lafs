=================================
Private sharing with Magic Folder
=================================

Summary

You will be able to detects local changes to files and uploads those changes to a Tahoe-LAFS grid. Magic Folder monitors a Tahoe-LAFS grid and downloads changes to the local filesystem.
    - Tahoe-lafs for the cryptography and resource sharing.
    - Magic wormhole for the transport between nodes
    - Magic folder for the filesystem access

We'll call the host with storage-only the ``storage`` node
the hosts syncing the content of the magic folder are ``remote clients``
the processes handling the transfers are the ``magic folder services``
the directories holding the files are the "magic folders"



Steps
-------

Step 0 - Create and start a Tahoe-LAFS storage-only node on a mutually accessible host (public VPS?) (Transit server?)
Step 0.1 Create and start (recommended) run the node as a daemon/service
Step 2- Create client(s)

Prepare the storage node
========================

Needs to be internet accessible because it will be accessed by several services running on other machines.

 install tahoe-lafs using the :ref:`install tahoe client`_ process.

.. code-block::
    # configure the storage node...
    tahoe --node-directory=./storage0 \
    create-node --hostname $STORAGE_FQDN \
    --nickname storage0 \
    --webport=none \
    --shares-happy=1 \
    --shares-needed=1 \
    --shares-total=1

.. note:: Pro tip: create a separate terminal session (tmux new -s storage_console)

Start/Stop the node to create the keys and fURL

.. code-block::

    # Start the storage node process to create the pubkeys and fURL
    tahoe --node-directory ~/tahoe/storage0/ run
    # Now copy the Tahoe fURL for the storage node, for the remote clients later.
    cat storage0/private/storage.furl
    # Next copy the node pubkey...
    cat storage0/node.pubkey

Run the storage node
--------------------

`   #Now start the Tahoe storage processes
    tahoe run storage0
    # later, "daemonize" this, so it runs in the background


.. note:: Congratulations, you are 30% complete


Prepare each remote client
==========================

Each remote client will need
    - A tahoe-lafs client installation, with the addresses and keys for the storage server
    - Magic folder services installed and configured with the "invite" feature enabled.
    - A "magic folder" directory to save the synced contents.

Before you begin
-----------------

Install the tahoe client in a venv using :ref:`install tahoe client`_ process

Point the tahoe client to the storage node
------------------------------------------

:ref:`Prepare the client`_ using the fURL and pubkey from the storage node.


Install Magic-Folder on each remote client
===========================================

.. code-block::

    pip install -U 'git+https://github.com/tahoe-lafs/magic-folder.git#egg=magic-folder'


.. note:: The installation process is temporarily cumbersome


Configure the Magic Folder services on each remote client
=========================================================

On each remote client::

    magic-folder --config ./mf0 init --node-directory ./client0 --listen-endpoint tcp:8999:interface=localhost
    magic-folder --config ~/tahoe/mf0 init --node-directory ~/tahoe/tahoe_client --listen-endpoint tcp:8999:interface=localhost


Enable the ``invite`` feature
-----------------------------

The invite feature supports the ``join`` option and the access codes for other clients.

.. warning:: Currently considered experimental. ``invite`` needs to be enabled. It uses magic-wormhole to transmit the secrets.

magic-folder  --config=./mf0 set-config --enable invites



Configure the Magic Folder
===========================

magic-folder --config ./mf0 init --node-directory ./client0 --listen-endpoint tcp:8999:interface=localhost


magic-folder --config ./mf0 add --name funny-photos --author $USER ~/photos


Run the Magic Folder service on the remote client
==================================================

magic-folder --config ~/tahoe/mf0 run

Confirm that magic folder is running
-------------------------------------

The magic folder service should be able to connect to the storage node.

you should see something like::
    2024-09-19T16:00:47-0400 Completed initial Magic Folder setup
    2024-09-19T16:00:47-0400 Connected to 1 storage-servers


Prepare the magic folder content for syncing
=============================================


Create the Invite Name
----------------------

Use the magic folder command::

    magic-folder --config ~/.mf0 invite --node-directory ./client0 --name funny-photos --mode read-write


Each guest joins the Magic Folder
=================================

 magic-folder  --config=./mf0 join --name=demo 6-narrative-endorse ~/magicf

