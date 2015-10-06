.. -*- coding: utf-8-with-signature -*-

================================
Tahoe-LAFS Magic Folder Frontend
================================

1.  `Introduction`_
2.  `Configuration`_
3.  `Known Issues and Limitations`_


Introduction
============

The Magic Folder frontend allows an upload to a Tahoe-LAFS grid to be triggered
automatically whenever a file is created or changed in a specific local
directory. It currently works on Linux and Windows.

The implementation was written as a prototype at the First International
Tahoe-LAFS Summit in June 2011, and is not currently in as mature a state as
the other frontends (web, CLI, SFTP and FTP). This means that you probably
should not rely on all changes to files in the local directory to result in
successful uploads. There might be (and have been) incompatible changes to
how the feature is configured.

We are very interested in feedback on how well this feature works for you, and
suggestions to improve its usability, functionality, and reliability.


Configuration
=============

The Magic Folder frontend runs as part of a gateway node. To set it up, you
must use the tahoe magic-folder CLI. For detailed information see our
`Magic-Folder CLI design documentation`_. For a given Magic-Folder collective
directory you need to run the ``tahoe magic-folder create`` command. After that
the ``tahoe magic-folder invite`` command must used to generate an invite code for
each member of the magic-folder collective. A confidential, authenticated communications
channel should be used to transmit the invite code to each member, who will be joining
using the ``tahoe magic-folder join`` command.

These settings are persisted in the ``[magic_folder]`` section of the
gateway's ``tahoe.cfg`` file.

``[magic_folder]``

``enabled = (boolean, optional)``

    If this is ``True``, Magic Folder will be enabled. The default value is
    ``False``.

``local.directory = (UTF-8 path)``

    This specifies the local directory to be monitored for new or changed
    files. If the path contains non-ASCII characters, it should be encoded
    in UTF-8 regardless of the system's filesystem encoding. Relative paths
    will be interpreted starting from the node's base directory.

In addition:
 * the file ``private/magic_folder_dircap`` must contain a writecap pointing
   to an existing mutable directory to be used as the target of uploads.
   It will start with ``URI:DIR2:``, and cannot include an alias or path.
 * the file ``private/collective_dircap`` must contain a readcap

After setting the above fields and starting or restarting the gateway,
you can confirm that the feature is working by copying a file into the
local directory. Then, use the WUI or CLI to check that it has appeared
in the upload directory with the same filename. A large file may take some
time to appear, since it is only linked into the directory after the upload
has completed.

The 'Operational Statistics' page linked from the Welcome page shows
counts of the number of files uploaded, the number of change events currently
queued, and the number of failed uploads. The 'Recent Uploads and Downloads'
page and the node log_ may be helpful to determine the cause of any failures.

.. _log: ../logging.rst


Known Issues and Limitations
============================

This frontend only works on Linux and Windows. There is a ticket to add
support for Mac OS X and BSD-based systems (`#1432`_).

The only way to determine whether uploads have failed is to look at the
'Operational Statistics' page linked from the Welcome page. This only shows
a count of failures, not the names of files. Uploads are never retried.

The Magic Folder frontend performs its uploads sequentially (i.e. it waits
until each upload is finished before starting the next), even when there
would be enough memory and bandwidth to efficiently perform them in parallel.
A Magic Folder upload can occur in parallel with an upload by a different
frontend, though. (`#1459`_)

On Linux, if there are a large number of near-simultaneous file creation or
change events (greater than the number specified in the file
``/proc/sys/fs/inotify/max_queued_events``), it is possible that some events
could be missed. This is fairly unlikely under normal circumstances, because
the default value of ``max_queued_events`` in most Linux distributions is
16384, and events are removed from this queue immediately without waiting for
the corresponding upload to complete. (`#1430`_)

The Windows implementation might also occasionally miss file creation or
change events, due to limitations of the underlying Windows API
(ReadDirectoryChangesW). We do not know how likely or unlikely this is.
(`#1431`_)

Some filesystems may not support the necessary change notifications.
So, it is recommended for the local directory to be on a directly attached
disk-based filesystem, not a network filesystem or one provided by a virtual
machine.

Attempts to read the mutable directory at about the same time as an uploaded
file is being linked into it, might fail, even if they are done through the
same gateway. (`#1105`_)

When a local file is changed and closed several times in quick succession,
it may be uploaded more times than necessary to keep the remote copy
up-to-date. (`#1440`_)

Files deleted from the local directory will not be unlinked from the upload
directory. (`#1710`_)

The ``private/magic_folder_dircap`` and ``private/collective_dircap`` files
cannot use an alias or path to specify the upload directory. (`#1711`_)

Files are always uploaded as immutable. If there is an existing mutable file
of the same name in the upload directory, it will be unlinked and replaced
with an immutable file. (`#1712`_)

If a file in the upload directory is changed (actually relinked to a new
file), then the old file is still present on the grid, and any other caps to
it will remain valid. See `docs/garbage-collection.rst`_ for how to reclaim
the space used by files that are no longer needed. Garbage collection is
not included as part of the OTF Magic-Folder grant... however we've documented
this feature here `#2440`_

Unicode filenames are supported on both Linux and Windows, but on Linux, the
local name of a file must be encoded correctly in order for it to be uploaded.
The expected encoding is that printed by
``python -c "import sys; print sys.getfilesystemencoding()"``.

On Windows, local directories with non-ASCII names are not currently working.
(`#2219`_)

On Windows, when a node has Magic Folder enabled, it is unresponsive to Ctrl-C
(it can only be killed using Task Manager or similar). (`#2218`_)

.. _`#1105`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1105
.. _`#1430`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1430
.. _`#1431`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1431
.. _`#1432`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1432
.. _`#1433`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1433
.. _`#1440`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1440
.. _`#1449`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1449
.. _`#1458`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1458
.. _`#1459`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1459
.. _`#1710`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1710
.. _`#1711`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1711
.. _`#1712`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1712
.. _`#2218`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2218
.. _`#2219`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2219
.. _`#2440`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2440

.. _docs/garbage-collection.rst: ../garbage-collection.rst
.. _`Magic-Folder CLI design documentation`: ../proposed/magic-folder/user-interface-design.rst
