=============
Availability
=============

.. warning:: The console commands in this section have not yet been validated. This warning will be removed when it is safe to copy and paste the code examples.


.. Once done, we'll add the time estimate .. note:: Estimated time is 35 minutes

Now we want to see how Tahoe provides availability in the event of a storage failure. So we will add storage nodes, upload files and see what happens in a failure scenario.


.. consider using conrete concrete names like desktop storage, laptop, etc.


See how Tahoe handles failures
==============================


    - Upload ``image0`` to ``storage00``
    - add storage1, a local storage node
    - modify the client to use storage1 (share settings and storage :term:`fURL`
    - Upload ``image1`` file
    - Shut down the ``storage00`` node
    - Download the ``image0`` = fail
    - Download the ``image1``= success

.. note:: Learn more about failure scenarios in the section about Reliability under architecture.

Upload a file to Tahoe
======================

``tahoe put`` ...

Check with CLI
--------------


Upload the first file
=====================

Tahoe -d ./ put ~/image0.png > image0_uri (notice the long ugly URI...)

Notice the long URI
-------------------

cat image0_uri

Download the first file
=======================

tahoe -d ./ get $(cat image0_uri) > image0_download


open both files to compare the contents....

Now let's skip the long URIs
----------------------------

``tahoe -d ./ create-alias pictures:``  (creates directory on the :term:`storage grid`)

``tahoe -d ./ put ~/image0.png  pictures:`` (see? much nicer)

``$ tahoe ls pictures:`` to see the contents of the pictures "directory"



Add a new storage node
===========================

.. code-block::

    $ tahoe --node-directory=./storage1 create-node \
     --hostname=localhost \
     --nickname=storage1 \
    --shares-happy=1 \
    --shares-needed=1 \
    --shares-total=1
     --webport=none


Add the storage1 fURL and key
=============================

edit rhwe client config as you did in :ref:`Prepare the client`_

Start the tahoe with CLI
------------------------

tahoe run...

See the storage replication
===========================

Check with CLI
---------------

 tahoe status

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

tahoe status ...

Download the second file
=========================

.. note:: This was uploaded with the new share settings


Download the second file = success
==================================

