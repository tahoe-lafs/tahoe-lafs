.. -*- coding: utf-8-with-signature-unix; fill-column: 73; -*-
.. -*- indent-tabs-mode: nil -*-

=====================
How To Run Tahoe-LAFS
=====================

Intro
=====

This is how to run a Tahoe-LAFS client or a complete Tahoe-LAFS grid.
First you have to install the Tahoe-LAFS software, as documented in
quickstart.rst_.

The ``tahoe`` program in the ``bin`` directory is used to create,
start, and stop nodes. Each node lives in a separate base directory, in
which there is a configuration file named ``tahoe.cfg``. Nodes read and
write files within this base directory.

A grid consists of a set of *storage nodes* and *client nodes* running
the Tahoe-LAFS code. There is also an *introducer node* that is
responsible for getting the other nodes talking to each other.

If you're getting started we recommend you try connecting to the `public test
grid`_ as you only need to create a client node. When you want to create your
own grid you'll need to create the introducer and several initial storage
nodes (see the note about small grids below).

If the Tahoe-LAFS ``bin`` directory is not on your PATH, then in all
the command lines below, specify the full path to ``bin/tahoe``.

To construct a client node, run “``tahoe create-client``”, which will create
``~/.tahoe`` to be the node's base directory. Acquire the ``introducer.furl``
(see below if you are running your own introducer, or use the one from the
`TestGrid page`_), and paste it after ``introducer.furl =`` in the
``[client]`` section of ``~/.tahoe/tahoe.cfg``. Then use “``tahoe run
~/.tahoe``”. After that, the node should be off and running. The first thing
it will do is connect to the introducer and get itself connected to all other
nodes on the grid.

By default, “``tahoe create-client``” creates a client-only node, that
does not offer its disk space to other nodes. To configure other behavior,
use “``tahoe create-node``” or see configuration.rst_.

To construct an introducer, create a new base directory for it (the
name of the directory is up to you), ``cd`` into it, and run
“``tahoe create-introducer .``”. Now run the introducer using
“``tahoe start .``”. After it starts, it will write a file named
``introducer.furl`` into the ``private/`` subdirectory of that base
directory. This file contains the URL the other nodes must use in order
to connect to this introducer. (Note that “``tahoe run .``” doesn't
work for introducers, this is a known issue: `#937`_.)

The “``tahoe run``” command above will run the node in the foreground.
On Unix, you can run it in the background instead by using the
“``tahoe start``” command. To stop a node started in this way, use
“``tahoe stop``”. ``tahoe --help`` gives a summary of all commands.

See configuration.rst_ for more details about how to configure Tahoe-LAFS,
including how to get other clients to connect to your node if it is behind a
firewall or NAT device.

.. _quickstart.rst: quickstart.rst
.. _public test grid: https://tahoe-lafs.org/trac/tahoe-lafs/wiki/TestGrid
.. _TestGrid page: https://tahoe-lafs.org/trac/tahoe-lafs/wiki/TestGrid
.. _configuration.rst: configuration.rst
.. _#937:  https://tahoe-lafs.org/trac/tahoe-lafs/ticket/937


A note about small grids
------------------------

By default, Tahoe-LAFS ships with the configuration parameter
``shares.happy`` set to 7. If you are using Tahoe-LAFS on a grid with fewer
than 7 storage nodes, this won't work well for you — none of your uploads
will succeed. To fix this, see configuration.rst_ to learn how to set
``shares.happy`` to a more suitable value for your grid.


Do Stuff With It
================

This is how to use your Tahoe-LAFS node.

The WUI
-------

Point your web browser to `http://127.0.0.1:3456`_ — which is the URL of the
gateway running on your own local computer — to use your newly created node.

Create a new directory (with the button labelled “create a directory”).
Your web browser will load the new directory.  Now if you want to be
able to come back to this directory later, you have to bookmark it, or
otherwise save a copy of the URL.  If you lose the URL to this directory,
then you can never again come back to this directory.

.. _http://127.0.0.1:3456: http://127.0.0.1:3456


The CLI
-------

Prefer the command-line? Run “``tahoe --help``” (the same command-line tool
that is used to start and stop nodes serves to navigate and use the
decentralized filesystem). To get started, create a new directory and mark it
as the 'tahoe:' alias by running “``tahoe create-alias tahoe``”. Once you've
done that, you can do “``tahoe ls tahoe:``” and “``tahoe cp LOCALFILE
tahoe:foo.txt``” to work with your filesystem. The Tahoe-LAFS CLI uses
similar syntax to the well-known scp and rsync tools. See CLI.rst_ for more
details.

To backup a directory full of files and subdirectories, run “``tahoe backup
LOCALDIRECTORY tahoe:``”. This will create a new LAFS subdirectory inside the
“tahoe” LAFS directory named “Archive”, and inside “Archive”, it will create
a new subdirectory whose name is the current date and time. That newly
created subdirectory will be populated with a snapshot copy of all files and
directories currently reachable from LOCALDIRECTORY. Then ``tahoe backup``
will make a link to that snapshot directory from the “tahoe” LAFS directory,
and name the link “Latest”.

``tahoe backup`` cleverly avoids uploading any files or directories that
haven't changed, and it also cleverly deduplicates any files or directories
that have identical contents to other files or directories that it has
previously backed-up. This means that running ``tahoe backup`` is a nice
incremental operation that backs up your files and directories efficiently,
and if it gets interrupted (for example by a network outage, or by you
rebooting your computer during the backup, or so on), it will resume right
where it left off the next time you run ``tahoe backup``.

See `<frontends/CLI.rst>`__ for more information about the ``tahoe backup``
command, as well as other commands.

As with the WUI (and with all current interfaces to Tahoe-LAFS), you
are responsible for remembering directory capabilities yourself. If you
create a new directory and lose the capability to it, then you cannot
access that directory ever again.

.. _CLI.rst: frontends/CLI.rst


The SFTP and FTP frontends
--------------------------

You can access your Tahoe-LAFS grid via any SFTP_ or FTP_ client.
See `FTP-and-SFTP.rst`_ for how to set
this up. On most Unix platforms, you can also use SFTP to plug
Tahoe-LAFS into your computer's local filesystem via ``sshfs``, but see 
the `FAQ about performance problems`_.

The SftpFrontend_ page on the wiki has more information about using SFTP with
Tahoe-LAFS.

.. _SFTP:  https://en.wikipedia.org/wiki/SSH_file_transfer_protocol
.. _FTP: https://en.wikipedia.org/wiki/File_Transfer_Protocol
.. _FTP-and-SFTP.rst: frontends/FTP-and-SFTP.rst
.. _FAQ about performance problems: https://tahoe-lafs.org/trac/tahoe-lafs/wiki/FAQ#Q23_FUSE
.. _SftpFrontend: https://tahoe-lafs.org/trac/tahoe-lafs/wiki/SftpFrontend


The WAPI
--------

Want to program your Tahoe-LAFS node to do your bidding?  Easy!  See
webapi.rst_.

.. _webapi.rst: frontends/webapi.rst


Socialize
=========

You can chat with other users of and hackers of this software on the
#tahoe-lafs IRC channel at ``irc.freenode.net``, or on the `tahoe-dev mailing
list`_.

.. _tahoe-dev mailing list: https://tahoe-lafs.org/cgi-bin/mailman/listinfo/tahoe-dev
