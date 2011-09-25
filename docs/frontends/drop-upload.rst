===============================
Tahoe-LAFS Drop-Upload Frontend
===============================

1.  `Introduction`_
2.  `Configuration`_
3.  `Known Issues and Limitations`_


Introduction
============

The drop-upload frontend allows an upload to a Tahoe-LAFS grid to be triggered
automatically whenever a file is created or changed in a specific local
directory. This is a preview of a feature that we expect to support across
several platforms, but it currently works only on Linux.

The implementation was written as a prototype at the First International
Tahoe-LAFS Summit in June 2011, and is not currently in as mature a state as
the other frontends (web, CLI, FTP and SFTP). This means that you probably
should not keep important data in the upload directory, and should not rely
on all changes to files in the local directory to result in successful uploads.
There might be incompatible changes to how the feature is configured in
future versions. There is even the possibility that it may be abandoned, for
example if unsolveable reliability issues are found.

We are very interested in feedback on how well this feature works for you, and
suggestions to improve its usability, functionality, and reliability.


Configuration
=============

The drop-upload frontend runs as part of a gateway node. To set it up, you
need to choose the local directory to monitor for file changes, and a mutable
directory on the grid to which files will be uploaded.

These settings are configured in the ``[drop_upload]`` section of the
gateway's ``tahoe.cfg`` file.

``[drop_upload]``

``enabled = (boolean, optional)``

    If this is ``True``, drop-upload will be enabled (provided that the
    ``upload.dircap`` and ``local.directory`` fields are also set). The
    default value is ``False``.

``upload.dircap = (directory writecap)``

    This is a writecap pointing to an existing mutable directory to be used
    as the target of uploads. It will start with ``URI:DIR2:``, and cannot
    include an alias or path.

``local.directory = (UTF-8 path)``

    This specifies the local directory to be monitored for new or changed
    files. If the path contains non-ASCII characters, it should be encoded
    in UTF-8 regardless of the system's filesystem encoding. Relative paths
    will be interpreted starting from the node's base directory.

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

This frontend only works on Linux. There is an even-more-experimental
implementation for Windows (`#1431`_), and a ticket to add support for
Mac OS X and BSD-based systems (`#1432`_).

Subdirectories of the local directory are not monitored. If a subdirectory
is created, it will be ignored. (`#1433`_)

If files are created or changed in the local directory just after the gateway
has started, it might not have connected to a sufficient number of servers
when the upload is attempted, causing the upload to fail. (`#1449`_)

Files that were created or changed in the local directory while the gateway
was not running, will not be uploaded. (`#1458`_)

The only way to determine whether uploads have failed is to look at the
'Operational Statistics' page linked from the Welcome page. This only shows
a count of failures, not the names of files. Uploads are never retried.

The drop-upload frontend performs its uploads sequentially (i.e. it waits
until each upload is finished before starting the next), even when there
would be enough memory and bandwidth to efficiently perform them in parallel.
A drop-upload can occur in parallel with an upload by a different frontend,
though. (`#1459`_)

If there are a large number of near-simultaneous file creation or
change events (greater than the number specified in the file
``/proc/sys/fs/inotify/max_queued_events``), it is possible that some events
could be missed. This is fairly unlikely under normal circumstances, because
the default value of ``max_queued_events`` in most Linux distributions is
16384, and events are removed from this queue immediately without waiting for
the corresponding upload to complete. (`#1430`_)

Some filesystems may not support the necessary change notifications.
So, it is recommended for the local directory to be on a directly attached
disk-based filesystem, not a network filesystem or one provided by a virtual
machine.

Attempts to read the mutable directory at about the same time as an uploaded
file is being linked into it, might fail, even if they are done through the
same gateway. (`#1105`_)

Files are always uploaded as immutable. If there is an existing mutable file
of the same name in the upload directory, it will be unlinked and replaced
with an immutable file.

If a file in the upload directory is changed (actually relinked to a new
file), then the old file is still present on the grid, and any other caps
to it will remain valid. See `docs/garbage-collection.rst
<../garbage-collection.rst>`_ for how to reclaim the space used by files
that are no longer needed.

Unicode names are supported, but the local name of a file must be encoded
correctly in order for it to be uploaded. The expected encoding is that
printed by ``python -c "import sys; print sys.getfilesystemencoding()"``.

.. _`#1105`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1105
.. _`#1430`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1430
.. _`#1431`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1431
.. _`#1432`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1432
.. _`#1433`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1433
.. _`#1449`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1449
.. _`#1458`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1458
.. _`#1459`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1459
