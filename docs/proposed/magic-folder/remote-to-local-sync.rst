Magic Folder design for remote-to-local sync
============================================

Scope
-----

In this Objective we will design remote-to-local synchronization:

* How to efficiently determine which objects (files and directories) have
  to be downloaded in order to bring the current local filesystem into sync
  with the newly-discovered version of the remote filesystem.
* How to distinguish overwrites, in which the remote side was aware of
  your most recent version and overwrote it with a new version, from
  conflicts, in which the remote side was unaware of your most recent
  version when it published its new version. The latter needs to be raised
  to the user as an issue the user will have to resolve and the former must
  not bother the user.
* How to overwrite the (stale) local versions of those objects with the
  newly acquired objects, while preserving backed-up versions of those
  overwritten objects in case the user didn't want this overwrite and wants
  to recover the old version.

Tickets on the Tahoe-LAFS trac with the `otf-magic-folder-objective4`_
keyword are within the scope of the remote-to-local synchronization
design.

.. _otf-magic-folder-objective4: https://tahoe-lafs.org/trac/tahoe-lafs/query?status=!closed&keywords=~otf-magic-folder-objective4


Glossary
''''''''

Object: a file or directory

DMD: distributed mutable directory

Folder: an abstract directory that is synchronized between clients.
(A folder is not the same as the directory corresponding to it on
any particular client, nor is it the same as a DMD.)

Collective: the set of clients subscribed to a given Magic Folder.

Descendant: a direct or indirect child in a directory or folder tree

Subfolder: a folder that is a descendant of a magic folder

Subpath: the path from a magic folder to one of its descendants

Write: a modification to a local filesystem object by a client

Read: a read from a local filesystem object by a client

Upload: an upload of a local object to the Tahoe-LAFS file store

Download: a download from the Tahoe-LAFS file store to a local object

Pending notification: a local filesystem change that has been detected
but not yet processed.


Representing the Magic Folder in Tahoe-LAFS
-------------------------------------------

Unlike the local case where we use inotify or ReadDirectoryChangesW to
detect filesystem changes, we have no mechanism to register a monitor for
changes to a Tahoe-LAFS directory. Therefore, we must periodically poll
for changes.

An important constraint on the solution is Tahoe-LAFS' "`write
coordination directive`_", which prohibits concurrent writes by different
storage clients to the same mutable object:

    Tahoe does not provide locking of mutable files and directories. If
    there is more than one simultaneous attempt to change a mutable file
    or directory, then an UncoordinatedWriteError may result. This might,
    in rare cases, cause the file or directory contents to be accidentally
    deleted.  The user is expected to ensure that there is at most one
    outstanding write or update request for a given file or directory at
    a time.  One convenient way to accomplish this is to make a different
    file or directory for each person or process that wants to write.

.. _`write coordination directive`: ../../write_coordination.rst

Since it is a goal to allow multiple users to write to a Magic Folder,
if the write coordination directive remains the same as above, then we
will not be able to implement the Magic Folder as a single Tahoe-LAFS
DMD. In general therefore, we will have multiple DMDs —spread across
clients— that together represent the Magic Folder. Each client in a
Magic Folder collective polls the other clients' DMDs in order to detect
remote changes.

Six possible designs were considered for the representation of subfolders
of the Magic Folder:

1. All subfolders written by a given Magic Folder client are collapsed
into a single client DMD, containing immutable files. The child name of
each file encodes the full subpath of that file relative to the Magic
Folder.

2. The DMD tree under a client DMD is a direct copy of the folder tree
written by that client to the Magic Folder. Not all subfolders have
corresponding DMDs; only those to which that client has written files or
child subfolders.

3. The directory tree under a client DMD is a ``tahoe backup`` structure
containing immutable snapshots of the folder tree written by that client
to the Magic Folder. As in design 2, only objects written by that client
are present.

4. *Each* client DMD contains an eventually consistent mirror of all
files and folders written by *any* Magic Folder client. Thus each client
must also copy changes made by other Magic Folder clients to its own
client DMD.

5. *Each* client DMD contains a ``tahoe backup`` structure containing
immutable snapshots of all files and folders written by *any* Magic
Folder client. Thus each client must also create another snapshot in its
own client DMD when changes are made by another client. (It can potentially
batch changes, subject to latency requirements.)

6. The write coordination problem is solved by implementing `two-phase
commit`_. Then, the representation consists of a single DMD tree which is
written by all clients.

.. _`two-phase commit`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1755

Here is a summary of advantages and disadvantages of each design:

+----------------------------+
| Key                        |
+=======+====================+
| \+\+  | major advantage    |
+-------+--------------------+
| \+    | minor advantage    |
+-------+--------------------+
| ‒     | minor disadvantage |
+-------+--------------------+
| ‒ ‒   | major disadvantage |
+-------+--------------------+
| ‒ ‒ ‒ | showstopper        |
+-------+--------------------+


123456+: All designs have the property that a recursive add-lease
operation starting from a *collective directory* containing all of
the client DMDs, will find all of the files and directories used in
the Magic Folder representation. Therefore the representation is
compatible with `garbage collection`_, even when a pre-Magic-Folder
client does the lease marking.

.. _`garbage collection`: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/garbage-collection.rst

123456+: All designs avoid "breaking" pre-Magic-Folder clients that read
a directory or file that is part of the representation.

456++: Only these designs allow a readcap to one of the client
directories —or one of their subdirectories— to be directly shared
with other Tahoe-LAFS clients (not necessarily Magic Folder clients),
so that such a client sees all of the contents of the Magic Folder.
Note that this was not a requirement of the OTF proposal, although it
is useful.

135+: A Magic Folder client has only one mutable Tahoe-LAFS object to
monitor per other client. This minimizes communication bandwidth for
polling, or alternatively the latency possible for a given polling
bandwidth.

1236+: A client does not need to make changes to its own DMD that repeat
changes that another Magic Folder client had previously made. This reduces
write bandwidth and complexity.

1‒: If the Magic Folder has many subfolders, their files will all be
collapsed into the same DMD, which could get quite large. In practice a
single DMD can easily handle the number of files expected to be written
by a client, so this is unlikely to be a significant issue.

123‒ ‒: In these designs, the set of files in a Magic Folder is
represented as the union of the files in all client DMDs. However,
when a file is modified by more than one client, it will be linked
from multiple client DMDs. We therefore need a mechanism, such as a
version number or a monotonically increasing timestamp, to determine
which copy takes priority.

35‒ ‒: When a Magic Folder client detects a remote change, it must
traverse an immutable directory structure to see what has changed.
Completely unchanged subtrees will have the same URI, allowing some of
this traversal to be shortcutted.

24‒ ‒ ‒: When a Magic Folder client detects a remote change, it must
traverse a mutable directory structure to see what has changed. This is
more complex and less efficient than traversing an immutable structure,
because shortcutting is not possible (each DMD retains the same URI even
if a descendant object has changed), and because the structure may change
while it is being traversed. Also the traversal needs to be robust
against cycles, which can only occur in mutable structures.

45‒ ‒: When a change occurs in one Magic Folder client, it will propagate
to all the other clients. Each client will therefore see multiple
representation changes for a single logical change to the Magic Folder
contents, and must suppress the duplicates. This is particularly
problematic for design 4 where it interacts with the preceding issue.

4‒ ‒ ‒, 5‒ ‒: There is the potential for client DMDs to get "out of sync"
with each other, potentially for long periods if errors occur. Thus each
client must be able to "repair" its client directory (and its
subdirectory structure) concurrently with performing its own writes. This
is a significant complexity burden and may introduce failure modes that
could not otherwise happen.

6‒ ‒ ‒: While two-phase commit is a well-established protocol, its
application to Tahoe-LAFS requires significant design work, and may still
leave some corner cases of the write coordination problem unsolved.


+------------------------------------------------+-----------------------------------------+
| Design Property                                | Designs Proposed                        |
+================================================+======+======+======+======+======+======+
| **advantages**                                 | *1*  | *2*  | *3*  | *4*  | *5*  | *6*  |
+------------------------------------------------+------+------+------+------+------+------+
| Compatible with garbage collection             |\+    |\+    |\+    |\+    |\+    |\+    |
+------------------------------------------------+------+------+------+------+------+------+
| Does not break old clients                     |\+    |\+    |\+    |\+    |\+    |\+    |
+------------------------------------------------+------+------+------+------+------+------+
| Allows direct sharing                          |      |      |      |\+\+  |\+\+  |\+\+  |
+------------------------------------------------+------+------+------+------+------+------+
| Efficient use of bandwidth                     |\+    |      |\+    |      |\+    |      |
+------------------------------------------------+------+------+------+------+------+------+
| No repeated changes                            |\+    |\+    |\+    |      |      |\+    |
+------------------------------------------------+------+------+------+------+------+------+
| **disadvantages**                              | *1*  | *2*  | *3*  | *4*  | *5*  | *6*  |
+------------------------------------------------+------+------+------+------+------+------+
| Can result in large DMDs                       |‒     |      |      |      |      |      |
+------------------------------------------------+------+------+------+------+------+------+
| Need version number to determine priority      |‒ ‒   |‒ ‒   |‒ ‒   |      |      |      |
+------------------------------------------------+------+------+------+------+------+------+
| Must traverse immutable directory structure    |      |      |‒ ‒   |      |‒ ‒   |      |
+------------------------------------------------+------+------+------+------+------+------+
| Must traverse mutable directory structure      |      |‒ ‒   |      |‒ ‒   |      |      |
+------------------------------------------------+------+------+------+------+------+------+
| Must suppress duplicate representation changes |      |      |      |‒ ‒   |‒ ‒   |      |
+------------------------------------------------+------+------+------+------+------+------+
| "Out of sync" problem                          |      |      |      |‒ ‒ ‒ |‒ ‒   |      |
+------------------------------------------------+------+------+------+------+------+------+
| Unsolved design problems                       |      |      |      |      |      |‒ ‒ ‒ |
+------------------------------------------------+------+------+------+------+------+------+


Evaluation of designs
'''''''''''''''''''''

Designs 2 and 3 have no significant advantages over design 1, while
requiring higher polling bandwidth and greater complexity due to the need
to create subdirectories. These designs were therefore rejected.

Design 4 was rejected due to the out-of-sync problem, which is severe
and possibly unsolvable for mutable structures.

For design 5, the out-of-sync problem is still present but possibly
solvable. However, design 5 is substantially more complex, less efficient
in bandwidth/latency, and less scalable in number of clients and
subfolders than design 1. It only gains over design 1 on the ability to
share directory readcaps to the Magic Folder (or subfolders), which was
not a requirement. It would be possible to implement this feature in
future by switching to design 6.

For the time being, however, design 6 was considered out-of-scope for
this project.

Therefore, design 1 was chosen. That is:

    All subfolders written by a given Magic Folder client are collapsed
    into a single client DMD, containing immutable files. The child name
    of each file encodes the full subpath of that file relative to the
    Magic Folder.

Each directory entry in a DMD also stores a version number, so that the
latest version of a file is well-defined when it has been modified by
multiple clients.

To enable representing empty directories, a client that creates a
directory should link a corresponding zero-length file in its DMD,
at a name that ends with the encoded directory separator character.

We want to enable dynamic configuration of the membership of a Magic
Folder collective, without having to reconfigure or restart each client
when another client joins. To support this, we have a single collective
directory that links to all of the client DMDs, named by their client
nicknames. If the collective directory is mutable, then it is possible
to change its contents in order to add clients. Note that a client DMD
should not be unlinked from the collective directory unless all of its
files are first copied to some other client DMD.

A client needs to be able to write to its own DMD, and read from other DMDs.
To be consistent with the `Principle of Least Authority`_, each client's
reference to its own DMD is a write capability, whereas its reference
to the collective directory is a read capability. The latter transitively
grants read access to all of the other client DMDs and the files linked
from them, as required.

.. _`Principle of Least Authority`: http://www.eros-os.org/papers/secnotsep.pdf

Design and implementation of the user interface for maintaining this
DMD structure and configuration will be addressed in Objectives 5 and 6.

During operation, each client will poll for changes on other clients
at a predetermined frequency. On each poll, it will reread the collective
directory (to allow for added or removed clients), and then read each
client DMD linked from it.

"Hidden" files, and files with names matching the patterns used for backup,
temporary, and conflicted files, will be ignored, i.e. not synchronized
in either direction. A file is hidden if it has a filename beginning with
"." (on any platform), or has the hidden or system attribute on Windows.


Conflict Detection and Resolution
---------------------------------

The combination of local filesystems and distributed objects is
an example of shared state concurrency, which is highly error-prone
and can result in race conditions that are complex to analyze.
Unfortunately we have no option but to use shared state in this
situation.

We call the resulting design issues "dragons" (as in "Here be dragons"),
which as a convenient mnemonic we have named after the classical
Greek elements Earth, Fire, Air, and Water.

Note: all filenames used in the following sections are examples,
and the filename patterns we use in the actual implementation may
differ. The actual patterns will probably include timestamps, and
for conflicted files, the nickname of the client that last changed
the file.


Earth Dragons: Collisions between local filesystem operations and downloads
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

Write/download collisions
~~~~~~~~~~~~~~~~~~~~~~~~~

Suppose that Alice's Magic Folder client is about to write a
version of ``foo`` that it has downloaded in response to a remote
change.

The criteria for distinguishing overwrites from conflicts are
described later in the `Fire Dragons`_ section. Suppose that the
remote change has been initially classified as an overwrite.
(As we will see, it may be reclassified in some circumstances.)

.. _`Fire Dragons`: #fire-dragons-distinguishing-conflicts-from-overwrites

Note that writing a file that does not already have an entry in
the `magic folder db`_ is initially classed as an overwrite.

A *write/download collision* occurs when another program writes
to ``foo`` in the local filesystem, concurrently with the new
version being written by the Magic Folder client. We need to
ensure that this does not cause data loss, as far as possible.

An important constraint on the design is that on Windows, it is
not possible to rename a file to the same name as an existing
file in that directory. Also, on Windows it may not be possible to
delete or rename a file that has been opened by another process
(depending on the sharing flags specified by that process).
Therefore we need to consider carefully how to handle failure
conditions.

In our proposed design, Alice's Magic Folder client follows
this procedure for an overwrite in response to a remote change:

1. Write a temporary file, say ``.foo.tmp``.
2. Use the procedure described in the `Fire Dragons_` section
   to obtain an initial classification as an overwrite or a
   conflict. (This takes as input the ``last_downloaded_uri``
   field from the directory entry of the changed ``foo``.)
3. Set the ``mtime`` of the replacement file to be *T* seconds
   before the current local time.
4. Perform a ''file replacement'' operation (explained below)
   with backup filename ``foo.backup``, replaced file ``foo``,
   and replacement file ``.foo.tmp``. If any step of this
   operation fails, reclassify as a conflict and stop.

To reclassify as a conflict, attempt to rename ``.foo.tmp`` to
``foo.conflicted``, suppressing errors.

The implementation of file replacement differs between Unix
and Windows. On Unix, it can be implemented as follows:

* 4a. Stat the replaced path, and set the permissions of the
  replacement file to be the same as the replaced file,
  bitwise-or'd with octal 600 (``rw-------``). If the replaced
  file does not exist, set the permissions according to the
  user's umask. If there is a directory at the replaced path,
  fail.
* 4b. Attempt to move the replaced file (``foo``) to the
  backup filename (``foo.backup``). If an ``ENOENT`` error
  occurs because the replaced file does not exist, ignore this
  error and continue with steps 4c and 4d.
* 4c. Attempt to create a hard link at the replaced filename
  (``foo``) pointing to the replacement file (``.foo.tmp``).
* 4d. Attempt to unlink the replacement file (``.foo.tmp``),
  suppressing errors.

Note that, if there is no conflict, the entry for ``foo``
recorded in the `magic folder db`_ will reflect the ``mtime``
set in step 3. The move operation in step 4b will cause a
``MOVED_FROM`` event for ``foo``, and the link operation in
step 4c will cause an ``IN_CREATE`` event for ``foo``.
However, these events will not trigger an upload, because they
are guaranteed to be processed only after the file replacement
has finished, at which point the metadata recorded in the
database entry will exactly match the metadata for the file's
inode on disk. (The two hard links — ``foo`` and,  while it
still exists, ``.foo.tmp`` — share the same inode and
therefore the same metadata.)

.. _`magic folder db`: filesystem_integration.rst#local-scanning-and-database

On Windows, file replacement can be implemented by a call to
the `ReplaceFileW`_ API (with the
``REPLACEFILE_IGNORE_MERGE_ERRORS`` flag). If an error occurs
because the replaced file does not exist, then we ignore this
error and attempt to move the replacement file to the replaced
file.

Similar to the Unix case, the `ReplaceFileW`_ operation will
cause one or more change notifications for ``foo``. The replaced
``foo`` has the same ``mtime`` as the replacement file, and so any
such notification(s) will not trigger an unwanted upload.

.. _`ReplaceFileW`: https://msdn.microsoft.com/en-us/library/windows/desktop/aa365512%28v=vs.85%29.aspx

To determine whether this procedure adequately protects against data
loss, we need to consider what happens if another process attempts to
update ``foo``, for example by renaming ``foo.other`` to ``foo``.
This requires us to analyze all possible interleavings between the
operations performed by the Magic Folder client and the other process.
(Note that atomic operations on a directory are totally ordered.)
The set of possible interleavings differs between Windows and Unix.

On Unix, for the case where the replaced file already exists, we have:

* Interleaving A: the other process' rename precedes our rename in
  step 4b, and we get an ``IN_MOVED_TO`` event for its rename by
  step 2. Then we reclassify as a conflict; its changes end up at
  ``foo`` and ours end up at ``foo.conflicted``. This avoids data
  loss.

* Interleaving B: its rename precedes ours in step 4b, and we do
  not get an event for its rename by step 2. Its changes end up at
  ``foo.backup``, and ours end up at ``foo`` after being linked there
  in step 4c. This avoids data loss.

* Interleaving C: its rename happens between our rename in step 4b,
  and our link operation in step 4c of the file replacement. The
  latter fails with an ``EEXIST`` error because ``foo`` already
  exists. We reclassify as a conflict; the old version ends up at
  ``foo.backup``, the other process' changes end up at ``foo``, and
  ours at ``foo.conflicted``. This avoids data loss.

* Interleaving D: its rename happens after our link in step 4c,
  and causes an ``IN_MOVED_TO`` event for ``foo``. Its rename also
  changes the ``mtime`` for ``foo`` so that it is different from
  the ``mtime`` calculated in step 3, and therefore different
  from the metadata recorded for ``foo`` in the magic folder db.
  (Assuming no system clock changes, its rename will set an ``mtime``
  timestamp corresponding to a time after step 4c, which is not
  equal to the timestamp *T* seconds before step 4a, provided that
  *T* seconds is sufficiently greater than the timestamp granularity.)
  Therefore, an upload will be triggered for ``foo`` after its
  change, which is correct and avoids data loss.

If the replaced file did not already exist, an ``ENOENT`` error
occurs at step 4b, and we continue with steps 4c and 4d. The other
process' rename races with our link operation in step 4c. If the
other process wins the race then the effect is similar to
Interleaving C, and if we win the race this it is similar to
Interleaving D. Either case avoids data loss.


On Windows, the internal implementation of `ReplaceFileW`_ is similar
to what we have described above for Unix; it works like this:

* 4a′. Copy metadata (which does not include ``mtime``) from the
  replaced file (``foo``) to the replacement file (``.foo.tmp``).

* 4b′. Attempt to move the replaced file (``foo``) onto the
  backup filename (``foo.backup``), deleting the latter if it
  already exists.

* 4c′. Attempt to move the replacement file (``.foo.tmp``) to the
  replaced filename (``foo``); fail if the destination already
  exists.

Notice that this is essentially the same as the algorithm we use
for Unix, but steps 4c and 4d on Unix are combined into a single
step 4c′. (If there is a failure at steps 4c′ after step 4b′ has
completed, the `ReplaceFileW`_ call will fail with return code
``ERROR_UNABLE_TO_MOVE_REPLACEMENT_2``. However, it is still
preferable to use this API over two `MoveFileExW`_ calls, because
it retains the attributes and ACLs of ``foo`` where possible.
Also note that if the `ReplaceFileW`_ call fails with
``ERROR_FILE_NOT_FOUND`` because the replaced file does not exist,
then the replacment operation ignores this error and continues with
the equivalent of step 4c′, as on Unix.)

However, on Windows the other application will not be able to
directly rename ``foo.other`` onto ``foo`` (which would fail because
the destination already exists); it will have to rename or delete
``foo`` first. Without loss of generality, let's say ``foo`` is
deleted. This complicates the interleaving analysis, because we
have two operations done by the other process interleaving with
three done by the magic folder process (rather than one operation
interleaving with four as on Unix).

So on Windows, for the case where the replaced file already exists,
we have:

* Interleaving A′: the other process' deletion of ``foo`` and its
  rename of ``foo.other`` to ``foo`` both precede our rename in
  step 4b. We get an event corresponding to its rename by step 2.
  Then we reclassify as a conflict; its changes end up at ``foo``
  and ours end up at ``foo.conflicted``. This avoids data loss.

* Interleaving B′: the other process' deletion of ``foo`` and its
  rename of ``foo.other`` to ``foo`` both precede our rename in
  step 4b. We do not get an event for its rename by step 2.
  Its changes end up at ``foo.backup``, and ours end up at ``foo``
  after being moved there in step 4c′. This avoids data loss.

* Interleaving C′: the other process' deletion of ``foo`` precedes
  our rename of ``foo`` to ``foo.backup`` done by `ReplaceFileW`_,
  but its rename of ``foo.other`` to ``foo`` does not, so we get
  an ``ERROR_FILE_NOT_FOUND`` error from `ReplaceFileW`_ indicating
  that the replaced file does not exist. We ignore this error and
  attempt to move ``foo.tmp`` to ``foo``, racing with the other
  process which is attempting to move ``foo.other`` to ``foo``.
  If we win the race, then our changes end up at ``foo``, and the
  other process' move fails. If the other process wins the race,
  then its changes end up at ``foo``, our move fails, and we
  reclassify as a conflict, so that our changes end up at
  ``foo.conflicted``. Either possibility avoids data loss.

* Interleaving D′: the other process' deletion and/or rename happen
  during the call to `ReplaceFileW`_, causing the latter to fail.
  There are two subcases:

  * if the error is ``ERROR_UNABLE_TO_MOVE_REPLACEMENT_2``, then
    ``foo`` is renamed to ``foo.backup`` and ``.foo.tmp`` remains
    at its original name after the call.
  * for all other errors, ``foo`` and ``.foo.tmp`` both remain at
    their original names after the call.

  In both subcases, we reclassify as a conflict and rename ``.foo.tmp``
  to ``foo.conflicted``. This avoids data loss.

* Interleaving E′: the other process' deletion of ``foo`` and attempt
  to rename ``foo.other`` to ``foo`` both happen after all internal
  operations of `ReplaceFileW`_ have completed. This causes deletion
  and rename events for ``foo`` (which will in practice be merged due
  to the pending delay, although we don't rely on that for correctness).
  The rename also changes the ``mtime`` for ``foo`` so that it is
  different from the ``mtime`` calculated in step 3, and therefore
  different from the metadata recorded for ``foo`` in the magic folder
  db. (Assuming no system clock changes, its rename will set an
  ``mtime`` timestamp corresponding to a time after the internal
  operations of `ReplaceFileW`_ have completed, which is not equal to
  the timestamp *T* seconds before `ReplaceFileW`_ is called, provided
  that *T* seconds is sufficiently greater than the timestamp
  granularity.) Therefore, an upload will be triggered for ``foo``
  after its change, which is correct and avoids data loss.

.. _`MoveFileExW`: https://msdn.microsoft.com/en-us/library/windows/desktop/aa365240%28v=vs.85%29.aspx

If the replaced file did not already exist, we get an
``ERROR_FILE_NOT_FOUND`` error from `ReplaceFileW`_, and attempt to
move ``foo.tmp`` to ``foo``. This is similar to Interleaving C, and
either possibility for the resulting race avoids data loss.

We also need to consider what happens if another process opens ``foo``
and writes to it directly, rather than renaming another file onto it:

* On Unix, open file handles refer to inodes, not paths. If the other
  process opens ``foo`` before it has been renamed to ``foo.backup``,
  and then closes the file, changes will have been written to the file
  at the same inode, even if that inode is now linked at ``foo.backup``.
  This avoids data loss.

* On Windows, we have two subcases, depending on whether the sharing
  flags specified by the other process when it opened its file handle
  included ``FILE_SHARE_DELETE``. (This flag covers both deletion and
  rename operations.)

  i.  If the sharing flags *do not* allow deletion/renaming, the
      `ReplaceFileW`_ operation will fail without renaming ``foo``.
      In this case we will end up with ``foo`` changed by the other
      process, and the downloaded file still in ``foo.tmp``.
      This avoids data loss.

  ii. If the sharing flags *do* allow deletion/renaming, then
      data loss or corruption may occur. This is unavoidable and
      can be attributed to other process making a poor choice of
      sharing flags (either explicitly if it used `CreateFile`_, or
      via whichever higher-level API it used).

.. _`CreateFile`: https://msdn.microsoft.com/en-us/library/windows/desktop/aa363858%28v=vs.85%29.aspx

Note that it is possible that another process tries to open the file
between steps 4b and 4c (or 4b′ and 4c′ on Windows). In this case the
open will fail because ``foo`` does not exist. Nevertheless, no data
will be lost, and in many cases the user will be able to retry the
operation.

Above we only described the case where the download was initially
classified as an overwrite. If it was classed as a conflict, the
procedure is the same except that we choose a unique filename
for the conflicted file (say, ``foo.conflicted_unique``). We write
the new contents to ``.foo.tmp`` and then rename it to
``foo.conflicted_unique`` in such a way that the rename will fail
if the destination already exists. (On Windows this is a simple
rename; on Unix it can be implemented as a link operation followed
by an unlink, similar to steps 4c and 4d above.) If this fails
because another process wrote ``foo.conflicted_unique`` after we
chose the filename, then we retry with a different filename.


Read/download collisions
~~~~~~~~~~~~~~~~~~~~~~~~

A *read/download collision* occurs when another program reads
from ``foo`` in the local filesystem, concurrently with the new
version being written by the Magic Folder client. We want to
ensure that any successful attempt to read the file by the other
program obtains a consistent view of its contents.

On Unix, the above procedure for writing downloads is sufficient
to achieve this. There are three cases:

* A. The other process opens ``foo`` for reading before it is
  renamed to ``foo.backup``. Then the file handle will continue to
  refer to the old file across the rename, and the other process
  will read the old contents.

* B. The other process attempts to open ``foo`` after it has been
  renamed to ``foo.backup``, and before it is linked in step c.
  The open call fails, which is acceptable.

* C. The other process opens ``foo`` after it has been linked to
  the new file. Then it will read the new contents.

On Windows, the analysis is very similar, but case A′ needs to
be split into two subcases, depending on the sharing mode the other
process uses when opening the file for reading:

* A′. The other process opens ``foo`` before the Magic Folder
  client's attempt to rename ``foo`` to ``foo.backup`` (as part
  of the implementation of `ReplaceFileW`_). The subcases are:

  i.  The other process uses sharing flags that deny deletion and
      renames. The `ReplaceFileW`_ call fails, and the download is
      reclassified as a conflict. The downloaded file ends up at
      ``foo.conflicted``, which is correct.

  ii. The other process uses sharing flags that allow deletion
      and renames. The `ReplaceFileW`_ call succeeds, and the
      other process reads inconsistent data. This can be attributed
      to a poor choice of sharing flags by the other process.

* B′. The other process attempts to open ``foo`` at the point
  during the `ReplaceFileW`_ call where it does not exist.
  The open call fails, which is acceptable.

* C′. The other process opens ``foo`` after it has been linked to
  the new file. Then it will read the new contents.


For both write/download and read/download collisions, we have
considered only interleavings with a single other process, and
only the most common possibilities for the other process'
interaction with the file. If multiple other processes are
involved, or if a process performs operations other than those
considered, then we cannot say much about the outcome in general;
however, we believe that such cases will be much less common.



Fire Dragons: Distinguishing conflicts from overwrites
''''''''''''''''''''''''''''''''''''''''''''''''''''''

When synchronizing a file that has changed remotely, the Magic Folder
client needs to distinguish between overwrites, in which the remote
side was aware of your most recent version and overwrote it with a
new version, and conflicts, in which the remote side was unaware of
your most recent version when it published its new version. Those two
cases have to be handled differently — the latter needs to be raised
to the user as an issue the user will have to resolve and the former
must not bother the user.

For example, suppose that Alice's Magic Folder client sees a change
to ``foo`` in Bob's DMD. If the version it downloads from Bob's DMD
is "based on" the version currently in Alice's local filesystem at
the time Alice's client attempts to write the downloaded file ‒or if
there is no existing version in Alice's local filesystem at that time‒
then it is an overwrite. Otherwise it is initially classified as a
conflict.

This initial classification is used by the procedure for writing a
file described in the `Earth Dragons`_ section above. As explained
in that section, we may reclassify an overwrite as a conflict if an
error occurs during the write procedure.

.. _`Earth Dragons`: #earth-dragons-collisions-between-local-filesystem-operations-and-downloads

In order to implement this policy, we need to specify how the
"based on" relation between file versions is recorded and updated.

We propose to record this information:

* in the `magic folder db`_, for local files;
* in the Tahoe-LAFS directory metadata, for files stored in the
  Magic Folder.

In the magic folder db we will add a *last-downloaded record*,
consisting of ``last_downloaded_uri`` and ``last_downloaded_timestamp``
fields, for each path stored in the database. Whenever a Magic Folder
client downloads a file, it stores the downloaded version's URI and
the current local timestamp in this record. Since only immutable
files are used, the URI will be an immutable file URI, which is
deterministically and uniquely derived from the file contents and
the Tahoe-LAFS node's `convergence secret`_.

(Note that the last-downloaded record is updated regardless of
whether the download is an overwrite or a conflict. The rationale
for this to avoid "conflict loops" between clients, where every
new version after the first conflict would be considered as another
conflict.)

.. _`convergence secret`: https://tahoe-lafs.org/trac/tahoe-lafs/browser/docs/convergence-secret.rst

Later, in response to a local filesystem change at a given path, the
Magic Folder client reads the last-downloaded record associated with
that path (if any) from the database and then uploads the current
file. When it links the uploaded file into its client DMD, it
includes the ``last_downloaded_uri`` field in the metadata of the
directory entry, overwriting any existing field of that name. If
there was no last-downloaded record associated with the path, this
field is omitted.

Note that ``last_downloaded_uri`` field does *not* record the URI of
the uploaded file (which would be redundant); it records the URI of
the last download before the local change that caused the upload.
The field will be absent if the file has never been downloaded by
this client (i.e. if it was created on this client and no change
by any other client has been detected).

A possible refinement also takes into account the
``last_downloaded_timestamp`` field from the magic folder db, and
compares it to the timestamp of the change that caused the upload
(which should be later, assuming no system clock changes).
If the duration between these timestamps is very short, then we
are uncertain about whether the process on Bob's system that wrote
the local file could have taken into account the last download.
We can use this information to be conservative about treating
changes as conflicts. So, if the duration is less than a configured
threshold, we omit the ``last_downloaded_uri`` field from the
metadata. This will have the effect of making other clients treat
this change as a conflict whenever they already have a copy of the
file.

Now we are ready to describe the algorithm for determining whether a
download for the file ``foo`` is an overwrite or a conflict (refining
step 2 of the procedure from the `Earth Dragons`_ section).

Let ``last_downloaded_uri`` be the field of that name obtained from
the directory entry metadata for ``foo`` in Bob's DMD (this field
may be absent). Then the algorithm is:

* 2a. If Alice has no local copy of ``foo``, classify as an overwrite.

* 2b. Otherwise, "stat" ``foo`` to get its *current statinfo* (size
  in bytes, ``mtime``, and ``ctime``).

* 2c. Read the following information for the path ``foo`` from the
  local magic folder db:

  * the *last-uploaded statinfo*, if any (this is the size in
    bytes, ``mtime``, and ``ctime`` stored in the ``local_files``
    table when the file was last uploaded);
  * the ``last_uploaded_uri`` field of the ``local_files`` table
    for this file, which is the URI under which the file was last
    uploaded.

* 2d. If any of the following are true, then classify as a conflict:

  * there are pending notifications of changes to ``foo``;
  * the last-uploaded statinfo is either absent, or different
    from the current statinfo;
  * either ``last_downloaded_uri`` or ``last_uploaded_uri``
    (or both) are absent, or they are different.

  Otherwise, classify as an overwrite.


Air Dragons: Collisions between local writes and uploads
''''''''''''''''''''''''''''''''''''''''''''''''''''''''

Short of filesystem-specific features on Unix or the `shadow copy service`_
on Windows (which is per-volume and therefore difficult to use in this
context), there is no way to *read* the whole contents of a file
atomically. Therefore, when we read a file in order to upload it, we
may read an inconsistent version if it was also being written locally.

.. _`shadow copy service`: https://technet.microsoft.com/en-us/library/ee923636%28v=ws.10%29.aspx

A well-behaved application can avoid this problem for its writes:

* On Unix, if another process modifies a file by renaming a temporary
  file onto it, then we will consistently read either the old contents
  or the new contents.
* On Windows, if the other process uses sharing flags to deny reads
  while it is writing a file, then we will consistently read either
  the old contents or the new contents, unless a sharing error occurs.
  In the case of a sharing error we should retry later, up to a
  maximum number of retries.

In the case of a not-so-well-behaved application writing to a file
at the same time we read from it, the magic folder will still be
eventually consistent, but inconsistent versions may be visible to
other users' clients.

In Objective 2 we implemented a delay, called the *pending delay*,
after the notification of a filesystem change and before the file is
read in order to upload it (Tahoe-LAFS ticket `#1440`_). If another
change notification occurs within the pending delay time, the delay
is restarted. This helps to some extent because it means that if
files are written more quickly than the pending delay and less
frequently than the pending delay, we shouldn't encounter this
inconsistency.

.. _`#1440`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1440

The likelihood of inconsistency could be further reduced, even for
writes by not-so-well-behaved applications, by delaying the actual
upload for a further period —called the *stability delay*— after the
file has finished being read. If a notification occurs between the
end of the pending delay and the end of the stability delay, then
the read would be aborted and the notification requeued.

This would have the effect of ensuring that no write notifications
have been received for the file during a time window that brackets
the period when it was being read, with margin before and after
this period defined by the pending and stability delays. The delays
are intended to account for asynchronous notification of events, and
caching in the filesystem.

Note however that we cannot guarantee that the delays will be long
enough to prevent inconsistency in any particular case. Also, the
stability delay would potentially affect performance significantly
because (unlike the pending delay) it is not overlapped when there
are multiple files on the upload queue. This performance impact
could be mitigated by uploading files in parallel where possible
(Tahoe-LAFS ticket `#1459`_).

We have not yet decided whether to implement the stability delay, and
it is not planned to be implemented for the OTF objective 4 milestone.
Ticket `#2431`_ has been opened to track this idea.

.. _`#1459`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1459
.. _`#2431`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2431

Note that the situation of both a local process and the Magic Folder
client reading a file at the same time cannot cause any inconsistency.


Water Dragons: Handling deletion and renames
''''''''''''''''''''''''''''''''''''''''''''

Deletion of a file
~~~~~~~~~~~~~~~~~~

When a file is deleted from the filesystem of a Magic Folder client,
the most intuitive behavior is for it also to be deleted under that
name from other clients. To avoid data loss, the other clients should
actually rename their copies to a backup filename.

It would not be sufficient for a Magic Folder client that deletes
a file to implement this simply by removing the directory entry from
its DMD. Indeed, the entry may not exist in the client's DMD if it
has never previously changed the file.

Instead, the client links a zero-length file into its DMD and sets
``deleted: true`` in the directory entry metadata. Other clients
take this as a signal to rename their copies to the backup filename.

Note that the entry for this zero-length file has a version number as
usual, and later versions may restore the file.

When the downloader deletes a file (or renames it to a filename
ending in ``.backup``) in response to a remote change, a local
filesystem notification will occur, and we must make sure that this
is not treated as a local change. To do this we have the downloader
set the ``size`` field in the magic folder db to ``None`` (SQL NULL)
just before deleting the file, and suppress notifications for which
the local file does not exist, and the recorded ``size`` field is
``None``.

When a Magic Folder client restarts, we can detect files that had
been downloaded but were deleted while it was not running, because
their paths will have last-downloaded records in the magic folder db
with a ``size`` other than ``None``, and without any corresponding
local file.

Deletion of a directory
~~~~~~~~~~~~~~~~~~~~~~~

Local filesystems (unlike a Tahoe-LAFS filesystem) normally cannot
unlink a directory that has any remaining children. Therefore a
Magic Folder client cannot delete local copies of directories in
general, because they will typically contain backup files. This must
be done manually on each client if desired.

Nevertheless, a Magic Folder client that deletes a directory should
set ``deleted: true`` on the metadata entry for the corresponding
zero-length file. This avoids the directory being recreated after
it has been manually deleted from a client.

Renaming
~~~~~~~~

It is sufficient to handle renaming of a file by treating it as a
deletion and an addition under the new name.

This also applies to directories, although users may find the
resulting behavior unintuitive: all of the files under the old name
will be renamed to backup filenames, and a new directory structure
created under the new name. We believe this is the best that can be
done without imposing unreasonable implementation complexity.


Summary
-------

This completes the design of remote-to-local synchronization.
We realize that it may seem very complicated. Anecdotally, proprietary
filesystem synchronization designs we are aware of, such as Dropbox,
are said to incur similar or greater design complexity.
