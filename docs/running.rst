=====================
How To Run Tahoe-LAFS
=====================

Intro
=====

This is how to run a Tahoe-LAFS client or a complete Tahoe-LAFS grid.
First you have to install the Tahoe-LAFS software, as documented in
`quickstart.rst <quickstart.rst>`_.

The ``tahoe`` program in the ``bin`` directory is used to create,
start, and stop nodes. Each node lives in a separate base directory, in
which there is a configuration file named ``tahoe.cfg``. Nodes read and
write files within this base directory.

A grid consists of a set of *storage nodes* and *client nodes* running
the Tahoe-LAFS code. There is also an *introducer node* that is
responsible for getting the other nodes talking to each other.

If you're getting started we recommend you try connecting to
the `the public test grid
<http://tahoe-lafs.org/trac/tahoe-lafs/wiki/TestGrid>`_ as you only
need to create a client node. When you want to create your own grid
you'll need to create the introducer and several initial storage nodes
(see the note about small grids below).

If the Tahoe-LAFS ``bin`` directory is not on your PATH, then in all
the command lines below, specify the full path to ``bin/tahoe``.

To construct a client node, run "``tahoe create-client``", which will
create ``~/.tahoe`` to be the node's base directory. Acquire a copy of
the ``introducer.furl`` from the introducer and put it into this
directory, then use "``tahoe run``". After that, the node should be off
and running. The first thing it will do is connect to the introducer
and get itself connected to all other nodes on the grid.  By default,
"``tahoe create-client``" creates a client-only node, that does not
offer its disk space to other nodes. To configure other behavior, use
"``tahoe create-node``" or see `configuration.rst <configuration.rst>`_.

To construct an introducer, create a new base directory for it (the
name of the directory is up to you), ``cd`` into it, and run
"``tahoe create-introducer .``". Now run the introducer using
"``tahoe start .``". After it starts, it will write a file named
``introducer.furl`` in that base directory. This file contains the URL
the other nodes must use in order to connect to this introducer. (Note
that "``tahoe run .``" doesn't work for introducers, this is a known
issue: `#937 <http://allmydata.org/trac/tahoe-lafs/ticket/937>`_.)

The "``tahoe run``" command above will run the node in the foreground.
On Unix, you can run it in the background instead by using the
"``tahoe start``" command. To stop a node started in this way, use
"``tahoe stop``". ``tahoe --help`` gives a summary of all commands.

See `configuration.rst <configuration.rst>`_ for more details about how
to configure Tahoe-LAFS, including how to get other clients to connect
to your node if it is behind a firewall or NAT device.

A note about small grids
------------------------

By default, Tahoe-LAFS ships with the configuration parameter
``shares.happy`` set to 7. If you are using Tahoe-LAFS on a
grid with fewer than 7 storage nodes, this won't work well for
you -- none of your uploads will succeed. To fix this, see
`configuration.rst <configuration.rst>`_ to learn how to set
``shares.happy`` to a more suitable value for your grid.

Do Stuff With It
================

This is how to use your Tahoe-LAFS node.

The WUI
-------

Point your web browser to `http://127.0.0.1:3456
<http://127.0.0.1:3456>`_ -- which is the URL of the gateway running on
your own local computer -- to use your newly created node.

Create a new directory (with the button labelled "create a directory").
Your web browser will load the new directory.  Now if you want to be
able to come back to this directory later, you have to bookmark it, or
otherwise save a copy of the URL.  If you lose the URL to this directory,
then you can never again come back to this directory.

You can do more or less everything you want to do with a decentralized
filesystem through the WUI.

The CLI
-------

Prefer the command-line? Run "``tahoe --help``" (the same command-line
tool that is used to start and stop nodes serves to navigate and use
the decentralized filesystem). To get started, create a new directory
and mark it as the 'tahoe:' alias by running
"``tahoe create-alias tahoe``". Once you've done that, you can do
"``tahoe ls tahoe:``" and "``tahoe cp LOCALFILE tahoe:foo.txt``" to
work with your filesystem. The Tahoe-LAFS CLI uses similar syntax to
the well-known scp and rsync tools. See `CLI.rst <frontends/CLI.rst>`_
for more details.

As with the WUI (and with all current interfaces to Tahoe-LAFS), you
are responsible for remembering directory capabilities yourself. If you
create a new directory and lose the capability to it, then you cannot
access that directory ever again.

The SFTP and FTP frontends
--------------------------

You can access your Tahoe-LAFS grid via any `SFTP
<http://en.wikipedia.org/wiki/SSH_file_transfer_protocol>`_ or `FTP
<http://en.wikipedia.org/wiki/File_Transfer_Protocol>`_ client.
See `FTP-and-SFTP.rst <frontends/FTP-and-SFTP.rst>`_ for how to set
this up. On most Unix platforms, you can also use SFTP to plug
Tahoe-LAFS into your computer's local filesystem via ``sshfs``.

The `SftpFrontend
<http://tahoe-lafs.org/trac/tahoe-lafs/wiki/SftpFrontend>`_ page on the
wiki has more information about using SFTP with Tahoe-LAFS.

The WAPI
--------

Want to program your Tahoe-LAFS node to do your bidding?  Easy!  See
`webapi.rst <frontends/webapi.rst>`_.

Socialize
=========

You can chat with other users of and hackers of this software on the
#tahoe-lafs IRC channel at ``irc.freenode.net``, or on the `tahoe-dev
mailing list
<http://tahoe-lafs.org/cgi-bin/mailman/listinfo/tahoe-dev>`_.
