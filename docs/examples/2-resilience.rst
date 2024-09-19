==========
Resilience
==========

.. note:: Estimated time is 35 minutes

Now we want to see how Tahoe provides :term:`resilience`.

Summary
=======
    - Upload the first file
    - add another storage node
    - change the client to use the new storage (share settings and storage :term:`fURL`
    - Upload the second file
    - Shut down the first storage node
    - Download the first file = fail
    - Download the second file = success




.. note:: Details about :ref:`Reliability`

Upload the first file
=====================

``tahoe put`` ...

Check with CLI
--------------

.. note:: Save the URI !


Add a new storage-only node
===========================

.. code-block::

    $ tahoe --node-directory=./storage1 create-node \
     --hostname=localhost \
     --nickname=storage1 \
     --webport=none


Start the tahoe with CLI
------------------------



Edit tahoe.cfg
----------------

Sadly, no CLI

Change the client share settings
================================

.. note:: Learn about shares in :ref:`Client Configuration`

Edit tahoe.cfg
--------------

See the storage replication
===========================

Check with CLI
---------------


Download the first file
=======================

it should fail, __because....__

.. note:: The files are not permanently lost. Learn more about availability in the TODO section on failure scenarios.

Upload a second file
=====================

Check with CLI
------------------

.. note:: Save the URI !

A node goes offline
===================

Check with CLI
---------------


Download the second file
=========================

.. note:: This was uploaded with the new share settings


Download the second file = success
==================================

