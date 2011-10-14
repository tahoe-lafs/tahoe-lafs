===========================
The Tahoe-LAFS CLI commands
===========================

1.  `Overview`_
2.  `CLI Command Overview`_

    1.  `Unicode Support`_

3.  `Node Management`_
4.  `Filesystem Manipulation`_

    1.  `Starting Directories`_
    2.  `Command Syntax Summary`_
    3.  `Command Examples`_

5.  `Storage Grid Maintenance`_
6.  `Debugging`_


Overview
========

Tahoe-LAFS provides a single executable named "``tahoe``", which can be used to
create and manage client/server nodes, manipulate the filesystem, and perform
several debugging/maintenance tasks.

This executable lives in the source tree at "``bin/tahoe``". Once you've done a
build (by running "``make``" or "``python setup.py build``"), ``bin/tahoe`` can
be run in-place: if it discovers that it is being run from within a Tahoe-LAFS
source tree, it will modify ``sys.path`` as necessary to use all the source code
and dependent libraries contained in that tree.

If you've installed Tahoe-LAFS (using "``make install``" or
"``python setup.py install``", or by installing a binary package), then the
``tahoe`` executable will be available somewhere else, perhaps in
``/usr/bin/tahoe``. In this case, it will use your platform's normal
PYTHONPATH search path to find the Tahoe-LAFS code and other libraries.


CLI Command Overview
====================

The "``tahoe``" tool provides access to three categories of commands.

* node management: create a client/server node, start/stop/restart it
* filesystem manipulation: list files, upload, download, unlink, rename
* debugging: unpack cap-strings, examine share files

To get a list of all commands, just run "``tahoe``" with no additional
arguments. "``tahoe --help``" might also provide something useful.

Running "``tahoe --version``" will display a list of version strings, starting
with the "allmydata" module (which contains the majority of the Tahoe-LAFS
functionality) and including versions for a number of dependent libraries,
like Twisted, Foolscap, pycryptopp, and zfec. "``tahoe --version-and-path``"
will also show the path from which each library was imported.

On Unix systems, the shell expands filename wildcards (``*`` and ``?``)
before the program is able to read them, which may produce unexpected
results for many ``tahoe`` comands. We recommend that you avoid using them.
On Windows, wildcards cannot be used to specify multiple filenames to
``tahoe``.

Unicode Support
---------------

As of Tahoe-LAFS v1.7.0 (v1.8.0 on Windows), the ``tahoe`` tool supports
non-ASCII characters in command lines and output. On Unix, the command-line
arguments are assumed to use the character encoding specified by the
current locale (usually given by the ``LANG`` environment variable).

If a name to be output contains control characters or characters that
cannot be represented in the encoding used on your terminal, it will be
quoted. The quoting scheme used is similar to `POSIX shell quoting`_: in
a "double-quoted" string, backslashes introduce escape sequences (like
those in Python strings), but in a 'single-quoted' string all characters
stand for themselves. This quoting is only used for output, on all
operating systems. Your shell interprets any quoting or escapes used on
the command line.

.. _`POSIX shell quoting`: http://pubs.opengroup.org/onlinepubs/009695399/utilities/xcu_chap02.html


Node Management
===============

"``tahoe create-node [NODEDIR]``" is the basic make-a-new-node command. It
creates a new directory and populates it with files that will allow the
"``tahoe start``" command to use it later on. This command creates nodes that
have client functionality (upload/download files), web API services
(controlled by the '[node]web.port' configuration), and storage services
(unless ``--no-storage`` is specified).

NODEDIR defaults to ``~/.tahoe/`` , and newly-created nodes default to
publishing a web server on port 3456 (limited to the loopback interface, at
127.0.0.1, to restrict access to other programs on the same host). All of the
other "``tahoe``" subcommands use corresponding defaults (with the exception
that "``tahoe run``" defaults to running a node in the current directory).

"``tahoe create-client [NODEDIR]``" creates a node with no storage service.
That is, it behaves like "``tahoe create-node --no-storage [NODEDIR]``".
(This is a change from versions prior to v1.6.0.)

"``tahoe create-introducer [NODEDIR]``" is used to create the Introducer node.
This node provides introduction services and nothing else. When started, this
node will produce an ``introducer.furl`` file, which should be published to all
clients.

"``tahoe create-key-generator [NODEDIR]``" is used to create a special
"key-generation" service, which allows a client to offload their RSA key
generation to a separate process. Since RSA key generation takes several
seconds, and must be done each time a directory is created, moving it to a
separate process allows the first process (perhaps a busy web-API server) to
continue servicing other requests. The key generator exports a FURL that can
be copied into a node to enable this functionality.

"``tahoe run [NODEDIR]``" will start a previously-created node in the foreground.

"``tahoe start [NODEDIR]``" will launch a previously-created node. It will
launch the node into the background, using the standard Twisted "``twistd``"
daemon-launching tool. On some platforms (including Windows) this command is
unable to run a daemon in the background; in that case it behaves in the
same way as "``tahoe run``".

"``tahoe stop [NODEDIR]``" will shut down a running node.

"``tahoe restart [NODEDIR]``" will stop and then restart a running node. This
is most often used by developers who have just modified the code and want to
start using their changes.


Filesystem Manipulation
=======================

These commands let you exmaine a Tahoe-LAFS filesystem, providing basic
list/upload/download/unlink/rename/mkdir functionality. They can be used as
primitives by other scripts. Most of these commands are fairly thin wrappers
around web-API calls, which are described in `<webapi.rst>`_.

By default, all filesystem-manipulation commands look in ``~/.tahoe/`` to
figure out which Tahoe-LAFS node they should use. When the CLI command makes
web-API calls, it will use ``~/.tahoe/node.url`` for this purpose: a running
Tahoe-LAFS node that provides a web-API port will write its URL into this
file. If you want to use a node on some other host, just create ``~/.tahoe/``
and copy that node's web-API URL into this file, and the CLI commands will
contact that node instead of a local one.

These commands also use a table of "aliases" to figure out which directory
they ought to use a starting point. This is explained in more detail below.

Starting Directories
--------------------

As described in `docs/architecture.rst <../architecture.rst>`_, the
Tahoe-LAFS distributed filesystem consists of a collection of directories
and files, each of which has a "read-cap" or a "write-cap" (also known as
a URI). Each directory is simply a table that maps a name to a child file
or directory, and this table is turned into a string and stored in a
mutable file. The whole set of directory and file "nodes" are connected
together into a directed graph.

To use this collection of files and directories, you need to choose a
starting point: some specific directory that we will refer to as a
"starting directory".  For a given starting directory, the
"``ls [STARTING_DIR]``" command would list the contents of this directory,
the "``ls [STARTING_DIR]/dir1``" command would look inside this directory
for a child named "``dir1``" and list its contents,
"``ls [STARTING_DIR]/dir1/subdir2``" would look two levels deep, etc.

Note that there is no real global "root" directory, but instead each
starting directory provides a different, possibly overlapping
perspective on the graph of files and directories.

Each Tahoe-LAFS node remembers a list of starting points, called "aliases",
which are short Unicode strings that stand in for a directory read- or
write- cap. They are stored (encoded as UTF-8) in the file
``NODEDIR/private/aliases`` .  If you use the command line "``tahoe ls``"
without any "[STARTING_DIR]" argument, then it will use the default alias,
which is ``tahoe:``, therefore "``tahoe ls``" has the same effect as
"``tahoe ls tahoe:``".  The same goes for the other commands that can
reasonably use a default alias: ``get``, ``put``, ``mkdir``, ``mv``, and
``rm``.

For backwards compatibility with Tahoe-LAFS v1.0, if the ``tahoe:`` alias
is not found in ``~/.tahoe/private/aliases``, the CLI will use the contents
of ``~/.tahoe/private/root_dir.cap`` instead. Tahoe-LAFS v1.0 had only a
single starting point, and stored it in this ``root_dir.cap`` file, so v1.1
and later will use it if necessary. However, once you've set a ``tahoe:``
alias with "``tahoe set-alias``", that will override anything in the old
``root_dir.cap`` file.

The Tahoe-LAFS CLI commands use the same path syntax as ``scp`` and
``rsync`` -- an optional ``ALIAS:`` prefix, followed by the pathname or
filename. Some commands (like "``tahoe cp``") use the lack of an alias to
mean that you want to refer to a local file, instead of something from the
Tahoe-LAFS filesystem. [TODO] Another way to indicate this is to start
the pathname with a dot, slash, or tilde.

When you're dealing a single starting directory, the ``tahoe:`` alias is
all you need. But when you want to refer to something that isn't yet
attached to the graph rooted at that starting directory, you need to
refer to it by its capability. The way to do that is either to use its
capability directory as an argument on the command line, or to add an
alias to it, with the "``tahoe add-alias``" command. Once you've added an
alias, you can use that alias as an argument to commands.

The best way to get started with Tahoe-LAFS is to create a node, start it,
then use the following command to create a new directory and set it as your
``tahoe:`` alias::

 tahoe create-alias tahoe

After that you can use "``tahoe ls tahoe:``" and
"``tahoe cp local.txt tahoe:``", and both will refer to the directory that
you've just created.

SECURITY NOTE: For users of shared systems
``````````````````````````````````````````

Another way to achieve the same effect as the above "``tahoe create-alias``"
command is::

 tahoe add-alias tahoe `tahoe mkdir`

However, command-line arguments are visible to other users (through the
``ps`` command or ``/proc`` filesystem, or the Windows Process Explorer tool),
so if you are using a Tahoe-LAFS node on a shared host, your login neighbors
will be able to see (and capture) any directory caps that you set up with the
"``tahoe add-alias``" command.

The "``tahoe create-alias``" command avoids this problem by creating a new
directory and putting the cap into your aliases file for you. Alternatively,
you can edit the ``NODEDIR/private/aliases`` file directly, by adding a line
like this::

 fun: URI:DIR2:ovjy4yhylqlfoqg2vcze36dhde:4d4f47qko2xm5g7osgo2yyidi5m4muyo2vjjy53q4vjju2u55mfa

By entering the dircap through the editor, the command-line arguments are
bypassed, and other users will not be able to see them. Once you've added the
alias, no other secrets are passed through the command line, so this
vulnerability becomes less significant: they can still see your filenames and
other arguments you type there, but not the caps that Tahoe-LAFS uses to permit
access to your files and directories.


Command Syntax Summary
----------------------

``tahoe add-alias ALIAS[:] DIRCAP``

``tahoe create-alias ALIAS[:]``

``tahoe list-aliases``

``tahoe mkdir``

``tahoe mkdir PATH``

``tahoe ls [PATH]``

``tahoe webopen [PATH]``

``tahoe put [--mutable] [FROMLOCAL|-]``

``tahoe put [--mutable] FROMLOCAL|- TOPATH``

``tahoe put [FROMLOCAL|-] mutable-file-writecap``

``tahoe get FROMPATH [TOLOCAL|-]``

``tahoe cp [-r] FROMPATH TOPATH``

``tahoe rm PATH``

``tahoe mv FROMPATH TOPATH``

``tahoe ln FROMPATH TOPATH``

``tahoe backup FROMLOCAL TOPATH``

In these summaries, ``PATH``, ``TOPATH`` or ``FROMPATH`` can be one of:

* ``[SUBDIRS/]FILENAME`` for a path relative to the default ``tahoe:`` alias;
* ``ALIAS:[SUBDIRS/]FILENAME`` for a path relative to another alias;
* ``DIRCAP/[SUBDIRS/]FILENAME`` or ``DIRCAP:./[SUBDIRS/]FILENAME`` for a path
  relative to a directory cap.


Command Examples
----------------

``tahoe add-alias ALIAS[:] DIRCAP``

 An example would be::

  tahoe add-alias fun URI:DIR2:ovjy4yhylqlfoqg2vcze36dhde:4d4f47qko2xm5g7osgo2yyidi5m4muyo2vjjy53q4vjju2u55mfa

 This creates an alias ``fun:`` and configures it to use the given directory
 cap. Once this is done, "``tahoe ls fun:``" will list the contents of this
 directory. Use "``tahoe add-alias tahoe DIRCAP``" to set the contents of the
 default ``tahoe:`` alias.

 Since Tahoe-LAFS v1.8.2, the alias name can be given with or without the
 trailing colon.

``tahoe create-alias fun``

 This combines "``tahoe mkdir``" and "``tahoe add-alias``" into a single step.

``tahoe list-aliases``

 This displays a table of all configured aliases.

``tahoe mkdir``

 This creates a new empty unlinked directory, and prints its write-cap to
 stdout. The new directory is not attached to anything else.

``tahoe mkdir subdir``

``tahoe mkdir /subdir``

 This creates a new empty directory and attaches it below the root directory
 of the default ``tahoe:`` alias with the name "``subdir``".

``tahoe ls``

``tahoe ls /``

``tahoe ls tahoe:``

``tahoe ls tahoe:/``

 All four list the root directory of the default ``tahoe:`` alias.

``tahoe ls subdir``

 This lists a subdirectory of your filesystem.

``tahoe webopen``

``tahoe webopen tahoe:``

``tahoe webopen tahoe:subdir/``

``tahoe webopen subdir/``

 This uses the python 'webbrowser' module to cause a local web browser to
 open to the web page for the given directory. This page offers interfaces to
 add, download, rename, and unlink files and subdirectories in that directory.
 If no alias or path is given, this command opens the root directory of the
 default ``tahoe:`` alias.

``tahoe put file.txt``

``tahoe put ./file.txt``

``tahoe put /tmp/file.txt``

``tahoe put ~/file.txt``

 These upload the local file into the grid, and prints the new read-cap to
 stdout. The uploaded file is not attached to any directory. All one-argument
 forms of "``tahoe put``" perform an unlinked upload.

``tahoe put -``

``tahoe put``

 These also perform an unlinked upload, but the data to be uploaded is taken
 from stdin.

``tahoe put file.txt uploaded.txt``

``tahoe put file.txt tahoe:uploaded.txt``

 These upload the local file and add it to your ``tahoe:`` root with the name
 "``uploaded.txt``".

``tahoe put file.txt subdir/foo.txt``

``tahoe put - subdir/foo.txt``

``tahoe put file.txt tahoe:subdir/foo.txt``

``tahoe put file.txt DIRCAP/foo.txt``

``tahoe put file.txt DIRCAP/subdir/foo.txt``

 These upload the named file and attach them to a subdirectory of the given
 root directory, under the name "``foo.txt``". When a directory write-cap is
 given, you can use either ``/`` (as shown above) or ``:./`` to separate it
 from the following path. When the source file is named "``-``", the contents
 are taken from stdin.

``tahoe put file.txt --mutable``

 Create a new (SDMF) mutable file, fill it with the contents of ``file.txt``,
 and print the new write-cap to stdout.

``tahoe put file.txt MUTABLE-FILE-WRITECAP``

 Replace the contents of the given mutable file with the contents of
 ``file.txt`` and print the same write-cap to stdout.

``tahoe cp file.txt tahoe:uploaded.txt``

``tahoe cp file.txt tahoe:``

``tahoe cp file.txt tahoe:/``

``tahoe cp ./file.txt tahoe:``

 These upload the local file and add it to your ``tahoe:`` root with the name
 "``uploaded.txt``".

``tahoe cp tahoe:uploaded.txt downloaded.txt``

``tahoe cp tahoe:uploaded.txt ./downloaded.txt``

``tahoe cp tahoe:uploaded.txt /tmp/downloaded.txt``

``tahoe cp tahoe:uploaded.txt ~/downloaded.txt``

 This downloads the named file from your ``tahoe:`` root, and puts the result on
 your local filesystem.

``tahoe cp tahoe:uploaded.txt fun:stuff.txt``

 This copies a file from your ``tahoe:`` root to a different directory, set up
 earlier with "``tahoe add-alias fun DIRCAP``" or "``tahoe create-alias fun``".

``tahoe unlink uploaded.txt``

``tahoe unlink tahoe:uploaded.txt``

 This unlinks a file from your ``tahoe:`` root (that is, causes there to no
 longer be an entry ``uploaded.txt`` in the root directory that points to it).
 Note that this does not delete the file from the grid.
 For backward compatibility, ``tahoe rm`` is accepted as a synonym for
 ``tahoe unlink``.

``tahoe mv uploaded.txt renamed.txt``

``tahoe mv tahoe:uploaded.txt tahoe:renamed.txt``

 These rename a file within your ``tahoe:`` root directory.

``tahoe mv uploaded.txt fun:``

``tahoe mv tahoe:uploaded.txt fun:``

``tahoe mv tahoe:uploaded.txt fun:uploaded.txt``

 These move a file from your ``tahoe:`` root directory to the directory
 set up earlier with "``tahoe add-alias fun DIRCAP``" or
 "``tahoe create-alias fun``".

``tahoe backup ~ work:backups``

 This command performs a full versioned backup of every file and directory
 underneath your "``~``" home directory, placing an immutable timestamped
 snapshot in e.g. ``work:backups/Archives/2009-02-06_04:00:05Z/`` (note that
 the timestamp is in UTC, hence the "Z" suffix), and a link to the latest
 snapshot in work:backups/Latest/ . This command uses a small SQLite database
 known as the "backupdb", stored in ``~/.tahoe/private/backupdb.sqlite``, to
 remember which local files have been backed up already, and will avoid
 uploading files that have already been backed up. It compares timestamps and
 filesizes when making this comparison. It also re-uses existing directories
 which have identical contents. This lets it run faster and reduces the
 number of directories created.

 If you reconfigure your client node to switch to a different grid, you
 should delete the stale backupdb.sqlite file, to force "``tahoe backup``"
 to upload all files to the new grid.

``tahoe backup --exclude=*~ ~ work:backups``

 Same as above, but this time the backup process will ignore any
 filename that will end with '~'. ``--exclude`` will accept any standard
 Unix shell-style wildcards, as implemented by the
 `Python fnmatch module <http://docs.python.org/library/fnmatch.html>`_.
 You may give multiple ``--exclude`` options.  Please pay attention that
 the pattern will be matched against any level of the directory tree;
 it's still impossible to specify absolute path exclusions.

``tahoe backup --exclude-from=/path/to/filename ~ work:backups``

 ``--exclude-from`` is similar to ``--exclude``, but reads exclusion
 patterns from ``/path/to/filename``, one per line.

``tahoe backup --exclude-vcs ~ work:backups``

 This command will ignore any file or directory name known to be used by
 version control systems to store metadata. The excluded names are:

  * CVS
  * RCS
  * SCCS
  * .git
  * .gitignore
  * .cvsignore
  * .svn
  * .arch-ids
  * {arch}
  * =RELEASE-ID
  * =meta-update
  * =update
  * .bzr
  * .bzrignore
  * .bzrtags
  * .hg
  * .hgignore
  * _darcs

Storage Grid Maintenance
========================

``tahoe manifest tahoe:``

``tahoe manifest --storage-index tahoe:``

``tahoe manifest --verify-cap tahoe:``

``tahoe manifest --repair-cap tahoe:``

``tahoe manifest --raw tahoe:``

 This performs a recursive walk of the given directory, visiting every file
 and directory that can be reached from that point. It then emits one line to
 stdout for each object it encounters.

 The default behavior is to print the access cap string (like ``URI:CHK:..``
 or ``URI:DIR2:..``), followed by a space, followed by the full path name.

 If ``--storage-index`` is added, each line will instead contain the object's
 storage index. This (string) value is useful to determine which share files
 (on the server) are associated with this directory tree. The ``--verify-cap``
 and ``--repair-cap`` options are similar, but emit a verify-cap and repair-cap,
 respectively. If ``--raw`` is provided instead, the output will be a
 JSON-encoded dictionary that includes keys for pathnames, storage index
 strings, and cap strings. The last line of the ``--raw`` output will be a JSON
 encoded deep-stats dictionary.

``tahoe stats tahoe:``

 This performs a recursive walk of the given directory, visiting every file
 and directory that can be reached from that point. It gathers statistics on
 the sizes of the objects it encounters, and prints a summary to stdout.


Debugging
=========

For a list of all debugging commands, use "``tahoe debug``". For more detailed
help on any of these commands, use "``tahoe debug COMMAND --help``".

"``tahoe debug find-shares STORAGEINDEX NODEDIRS..``" will look through one or
more storage nodes for the share files that are providing storage for the
given storage index.

"``tahoe debug catalog-shares NODEDIRS..``" will look through one or more
storage nodes and locate every single share they contain. It produces a report
on stdout with one line per share, describing what kind of share it is, the
storage index, the size of the file is used for, etc. It may be useful to
concatenate these reports from all storage hosts and use it to look for
anomalies.

"``tahoe debug dump-share SHAREFILE``" will take the name of a single share file
(as found by "``tahoe find-shares``") and print a summary of its contents to
stdout. This includes a list of leases, summaries of the hash tree, and
information from the UEB (URI Extension Block). For mutable file shares, it
will describe which version (seqnum and root-hash) is being stored in this
share.

"``tahoe debug dump-cap CAP``" will take any Tahoe-LAFS URI and unpack it
into separate pieces. The most useful aspect of this command is to reveal the
storage index for any given URI. This can be used to locate the share files
that are holding the encoded+encrypted data for this file.

"``tahoe debug repl``" will launch an interactive Python interpreter in which
the Tahoe-LAFS packages and modules are available on ``sys.path`` (e.g. by using
'``import allmydata``'). This is most useful from a source tree: it simply sets
the PYTHONPATH correctly and runs the Python executable.

"``tahoe debug corrupt-share SHAREFILE``" will flip a bit in the given
sharefile. This can be used to test the client-side verification/repair code.
Obviously, this command should not be used during normal operation.

"``tahoe debug trial [OPTIONS] [TESTSUITE]``" will run the tests specified by
TESTSUITE (defaulting to the whole Tahoe test suite), using Twisted Trial.
