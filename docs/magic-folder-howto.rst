=========================
Magic Folder Set-up Howto
=========================

1.  `This document`_
2.  `Preparation`_
3.  `Setting up a local test grid`_
4.  `Setting up Magic Folder`_
5.  `Testing`_


This document
=============

This is preliminary documentation of how to set up the
Magic Folder pre-release using a test grid on a single Linux
or Windows machine, with two clients and one server. It is
aimed at a fairly technical audience.

For an introduction to Magic Folder and how to configure it
more generally, see `docs/frontends/magic-folder.rst`_.

It it possible to adapt these instructions to run the nodes on
different machines, to synchronize between three or more clients,
to mix Windows and Linux clients, and to use multiple servers
(if the Tahoe-LAFS encoding parameters are changed).

.. _`docs/frontends/magic-folder.rst`: ../docs/frontends/magic-folder.rst


Preparation
===========

Linux
-----

Install ``git`` from your distribution's package manager.
Then run these commands::

  git clone -b 2438.magic-folder-stable.8 https://github.com/tahoe-lafs/tahoe-lafs.git
  cd tahoe-lafs
  python setup.py test

The test suite usually takes about 15 minutes to run.
Note that it is normal for some tests to be skipped.
In the current branch, the Magic Folder tests produce
considerable debugging output.

If you see an error like ``fatal error: Python.h: No such file or directory``
while compiling the dependencies, you need the Python development headers. If
you are on a Debian or Ubuntu system, you can install them with ``sudo
apt-get install python-dev``. On RedHat/Fedora, install ``python-devel``.


Windows
-------

Windows 7 or above is required.

For 64-bit Windows:

* Install Python 2.7 from
  https://www.python.org/ftp/python/2.7/python-2.7.amd64.msi
* Install pywin32 from
  https://tahoe-lafs.org/source/tahoe-lafs/deps/windows/pywin32-219.win-amd64-py2.7.exe
* Install git from
  https://github.com/git-for-windows/git/releases/download/v2.6.2.windows.1/Git-2.6.2-64-bit.exe

For 32-bit Windows:

* Install Python 2.7 from
  https://www.python.org/ftp/python/2.7/python-2.7.msi
* Install pywin32 from
  https://tahoe-lafs.org/source/tahoe-lafs/deps/windows/pywin32-219.win32-py2.7.exe
* Install git from
  https://github.com/git-for-windows/git/releases/download/v2.6.2.windows.1/Git-2.6.2-32-bit.exe

Then (for any version) run these commands in a Command Prompt::

  git clone -b 2438.magic-folder-stable.5 https://github.com/tahoe-lafs/tahoe-lafs.git
  cd tahoe-lafs
  python setup.py build

Open a new Command Prompt with the same current directory,
then run::

  bin\tahoe --version-and-path

It is normal for this command to print warnings and debugging output
on some systems. ``python setup.py test`` can also be run, but there
are some known sources of nondeterministic errors in tests on Windows
that are unrelated to Magic Folder.


Setting up a local test grid
============================

Linux
-----

Run these commands::

  mkdir ../grid
  bin/tahoe create-introducer ../grid/introducer
  bin/tahoe start ../grid/introducer
  export FURL=`cat ../grid/introducer/private/introducer.furl`
  bin/tahoe create-node --introducer="$FURL" ../grid/server
  bin/tahoe create-client --introducer="$FURL" ../grid/alice
  bin/tahoe create-client --introducer="$FURL" ../grid/bob


Windows
-------

Run::

  mkdir ..\grid
  bin\tahoe create-introducer ..\grid\introducer
  bin\tahoe start ..\grid\introducer

Leave the introducer running in that Command Prompt,
and in a separate Command Prompt (with the same current
directory), run::

  set /p FURL=<..\grid\introducer\private\introducer.furl
  bin\tahoe create-node --introducer=%FURL% ..\grid\server
  bin\tahoe create-client --introducer=%FURL% ..\grid\alice
  bin\tahoe create-client --introducer=%FURL% ..\grid\bob


Both Linux and Windows
----------------------

(Replace ``/`` with ``\`` for Windows paths.)

Edit ``../grid/alice/tahoe.cfg``, and make the following
changes to the ``[node]`` and ``[client]`` sections::

  [node]
  nickname = alice
  web.port = tcp:3457:interface=127.0.0.1

  [client]
  shares.needed = 1
  shares.happy = 1
  shares.total = 1

Edit ``../grid/bob/tahoe.cfg``, and make the following
change to the ``[node]`` section, and the same change as
above to the ``[client]`` section::

  [node]
  nickname = bob
  web.port = tcp:3458:interface=127.0.0.1

Note that when running nodes on a single machine,
unique port numbers must be used for each node (and they
must not clash with ports used by other server software).
Here we have used the default of 3456 for the server,
3457 for alice, and 3458 for bob.

Now start all of the nodes (the introducer should still be
running from above)::

  bin/tahoe start ../grid/server
  bin/tahoe start ../grid/alice
  bin/tahoe start ../grid/bob

On Windows, a separate Command Prompt is needed to run each
node.

Open a web browser on http://127.0.0.1:3457/ and verify that
alice is connected to the introducer and one storage server.
Then do the same for http://127.0.0.1:3568/ to verify that
bob is connected. Leave all of the nodes running for the
next stage.


Setting up Magic Folder
=======================

Linux
-----

Run::

  mkdir -p ../local/alice ../local/bob
  bin/tahoe -d ../grid/alice magic-folder create magic: alice ../local/alice
  bin/tahoe -d ../grid/alice magic-folder invite magic: bob >invitecode
  export INVITECODE=`cat invitecode`
  bin/tahoe -d ../grid/bob magic-folder join "$INVITECODE" ../local/bob

  bin/tahoe restart ../grid/alice
  bin/tahoe restart ../grid/bob

Windows
-------

Run::

  mkdir ..\local\alice ..\local\bob
  bin\tahoe -d ..\grid\alice magic-folder create magic: alice ..\local\alice
  bin\tahoe -d ..\grid\alice magic-folder invite magic: bob >invitecode
  set /p INVITECODE=<invitecode
  bin\tahoe -d ..\grid\bob magic-folder join %INVITECODE% ..\local\bob

Then close the Command Prompt windows that are running the alice and bob
nodes, and open two new ones in which to run::

  bin\tahoe start ..\grid\alice
  bin\tahoe start ..\grid\bob


Testing
=======

You can now experiment with creating files and directories in
``../local/alice`` and ``/local/bob``; any changes should be
propagated to the other directory.

Note that when a file is deleted, the corresponding file in the
other directory will be renamed to a filename ending in ``.backup``.
Deleting a directory will have no effect.

For other known issues and limitations, see
https://github.com/tahoe-lafs/tahoe-lafs/blob/2438.magic-folder-stable.8/docs/frontends/magic-folder.rst#known-issues-and-limitations

As mentioned earlier, it is also possible to run the nodes on
different machines, to synchronize between three or more clients,
to mix Windows and Linux clients, and to use multiple servers
(if the Tahoe-LAFS encoding parameters are changed).
