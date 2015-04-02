Magic Folders local filesystem integration design
=================================================

*Scope*

This document describes how to integrate the local filesystem with Magic
Folders in an efficient and reliable manner. For now we ignore Remote to
Local synchronization; the design and implementation of this is scheduled
for a later time. We also ignore multiple writers for the same Magic
Folder, which may or may not be supported in future. The design here will
be updated to account for those features in later Objectives. Objective 3
may require modifying the database schema or operation, and Objective 5
may modify the User interface.


*Local scanning and database*

When a Magic-Folder-enabled node starts up, it scans all directories
under the local directory and adds every file to a first-in first-out
"scan queue". When processing the scan queue, redundant uploads are
avoided by using the same mechanism the Tahoe backup command uses: we
keep track of previous uploads by recording each file's metadata such as
size, CTIME and MTIME. This information is stored in a database referred
to from now on as the magic folder db. Using this recorded state, we
ensure that when Magic Folder is subsequently started, the local
directory tree can be scanned quickly by comparing current filesystem
metadata with the previously recorded metadata. Each file referenced in
the scan queue is uploaded only if its metadata differs at the time it is
processed. If a change event is detected for a file that is already
queued (and therefore will be processed later), the redundant event is
ignored.

To implement the magic folder db, we will use an SQLite schema that
initially is the existing Tahoe-LAFS backup schema. This schema may
change in later objectives; this will cause no backward compatibility
problems, because this new feature will be developed on a branch that
makes no compatibility guarantees. However we will have a separate SQLite
database file and separate mutex lock just for Magic Folder. This avoids
usability problems related to mutual exclusion. (If a single file and
lock were used, a backup would block Magic Folder updates for a long
time, and a user would not be able to tell when backups are possible
because Magic Folder acquires a lock at arbitrary times.)


*Eventual consistency property*

It is not possible to prevent local writes to a file while it is being
read in order to upload it. Such writes will result in temporary
inconsistency. Eventual consistency is reached when the queue of pending
uploads is empty. That is, a consistent snapshot will be achieved
eventually when local writes to the target folder cease for a
sufficiently long period of time.


*Detecting filesystem changes*

For the Linux implementation we will use the inotify Linux kernel
subsystem to gather events on the local Magic Folder directory tree. This
implementation was already present in Tahoe-LAFS 1.9.0, but needs to be
changed to gather directory creation and move events, as well as events
indicating that a file has been written.

For the Windows implementation we will use the ``ReadDirectoryChangesW``
Win32 API. The prototype implementation simulates the inotify API in
terms of ``ReadDirectoryChangesW``, allowing most of the code to be
shared across platforms.

When we detect the creation of a new directory below the local Magic
Folder directory, we create it in the Tahoe-LAFS filesystem, and also
scan the new local directory for new files. This scan is necessary to
avoid missing events for creation of files in a new directory before it
can be watched, and to correctly handle cases where an existing directory
is moved to be under the local Magic Folder directory.


*User interface*

The Magic Folders local filesystem integration will initially have a
provisional configuration file-based interface that may not be ideal from
a usability perspective. Creating our local filesystem integration in
this manner will allow us to use and test it indepently of the rest of
the Magic Folder software components. We will focus greater attention on
user interface design as a later milestone in our development roadmap.

The configuration file, ``tahoe.cfg``, must define a target local
directory to be synchronized. Provisionally, this configuration will
replace the current "drop-upload" section::

 [magic_folders]
 enabled = true
 local.directory = "/home/human"

When a filesystem directory is first "added" to Magic Folders, the user
needs to create the remote Tahoe-LAFS directory using ``tahoe   mkdir``,
and configure the Magic-Folder-enabled node with its URI (e.g. by putting
it in a file ``private/magic_folder_dircap``). If there are existing
files in the local directory, they will be uploaded as a result of the
initial scan described earlier.

