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
clients— that together represent the Magic Folder. Each client polls
the other clients' DMDs in order to detect remote changes.

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
own client DMD when changes are made by other . (It can potentially batch
changes, subject to latency requirements.)

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
operation starting from the parent Tahoe-LAFS DMD will find all of the
files and directories used in the Magic Folder representation. Therefore
the representation is compatible with `garbage collection`_, even when a
pre-Magic-Folder client does the lease marking.

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

We want to enable dynamic configuration of the set of clients subscribed
to a Magic Folder, without having to reconfigure or restart each client
when another client joins or leaves. To support this, we have a single
parent DMD that links to all of the client DMDs, named by their client
nicknames. Then it is possible to change the contents of the parent DMD
in order to add or remove clients.

A client needs to be able to write to its own DMD, and read from other DMDs.
To be consistent with the `Principle of Least Authority`_, each client's
reference to its own DMD is a write capability, whereas its reference
to the parent DMD is a read capability. The latter transitively grants
read access to all of the other client DMDs and the files linked from
them, as required.

.. _`Principle of Least Authority`: http://www.eros-os.org/papers/secnotsep.pdf

Design and implementation of the user interface for maintaining this
DMD structure and configuration will be addressed in Objectives 5 and 6.

During operation, each client will poll for changes on other clients
at a predetermined frequency. On each poll, it will reread the parent DMD
(to allow for added or removed clients), and then read each client DMD
linked from the parent.

[TODO: discuss how magic folder db is used -- or should this go in the
Fire Dragon section?]


Conflict Detection and Resolution
---------------------------------

The combination of local filesystems and distributed objects is
an example of shared state concurrency, which is highly error-prone
and can result in race conditions that are complex to analyse.
Unfortunately we have no option but to use shared state in this
situation.

We call the resulting design issues "dragons", which as a convenient
mnemonic we have named after the five classical Greek elements
(Earth, Air, Water, Fire and Aether). The example communication actors
Alice and Bob are also the users of corresponding software processes
designated by `alice` and `bob` respectively.

Note: all filenames used in the following sections are examples,
and the filename patterns we use in the actual implementation may
differ.


Earth Dragons: Write/download and read/download collisions
''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

Suppose that Alice's Magic Folder client is about to write a
version of ``foo`` that it has downloaded in response to a remote
change.

The criteria for distinguishing overwrites from conflicts are
described later in the `Fire Dragons`_ section. For now, suppose
that the remote change has been tentatively classified as an
overwrite. (As we will see below, it may be reclassified in some
circumstances.)

.. _`Fire Dragons`: #fire-dragons-distinguishing-conflicts-from-overwrites

A *write/download collision* occurs when another program writes
to ``foo`` in the local filesystem, concurrently with the new
version being written by the Magic Folder client. We need to
ensure that this does not cause data loss, as far as possible.

An important constraint on the design is that on Windows, it is
not possible to rename a file to the same name as an existing
file in that directory. Also, on Windows it may not be possible to
delete or rename a file that has been opened by another program
(depending on the sharing flags specified by that program).
Therefore we need to consider carefully how to handle failure
conditions.

Our proposed design is as follows:

1. Alice's Magic Folder client writes a temporary file, say
   ``.foo.tmp``.
2. If there are pending notifications of changes to ``foo``,
   reclassify as a conflict and stop.
3. Set the ``mtime`` of the replacement file to be *T* seconds
   before the current time (see below for further explanation).
4. Perform a ''file replacement'' operation (see below)
   with backup filename ``foo.old``, replaced file ``foo``,
   and replacement file ``.foo.tmp``. If any step of this
   operation fails, reclassify as a conflict and stop.

The implementation of file replacement differs between
Windows and Unix. On Unix, it can be implemented as follows:

4a. Set the permissions of the replacement file to be the
    same as the replaced file, bitwise-or'd with octal 600
    (``rw-------``).
4b. Attempt to move the replaced file (``foo``) to the
    backup filename (``foo.old``).
4c. Attempt to create a hard link at the replaced filename
    (``foo``) pointing to the replacement file (``.foo.tmp``).
4d. Attempt to unlink the replacement file (``.foo.tmp``),
    suppressing errors.

To reclassify as a conflict, attempt to rename ``.foo.tmp`` to
``foo.conflicted``, suppressing errors.

Note that, if there is no conflict, the entry for ``foo``
recorded in the `magic folder db`_ will reflect the ``mtime``
set in step 3. The link operation in step 4c will cause an
``IN_CREATE`` event for ``foo``, but this will not trigger an
upload, because the metadata recorded in the database entry
will exactly match the metadata for the file's inode on disk.
(The two hard links — ``foo`` and, while it still exists,
``.foo.tmp`` — share the same inode and therefore the same
metadata.)

.. _`magic folder db`: filesystem_integration.rst#local-scanning-and-database

[TODO: on Unix, what happens with reference to inotify events if we
rename a file while it is open? Does the path for the ``CLOSE_WRITE``
event reflect the new name?]

On Windows, file replacement can be implemented as a single
call to the `ReplaceFileW`_ API (with the
``REPLACEFILE_IGNORE_MERGE_ERRORS`` flag).

Similar to the Unix case, the `ReplaceFileW`_ operation will
cause a change notification for ``foo`` [TODO: check which
notifications we actually get]. The replaced ``foo`` has the
same ``mtime`` as the replacement file, and so this notification
will not trigger an unwanted upload.

.. _`ReplaceFileW`: https://msdn.microsoft.com/en-us/library/windows/desktop/aa365512%28v=vs.85%29.aspx

To determine whether this procedure adequately protects against data
loss, we need to consider what happens if another process attempts to
update ``foo``, for example by renaming ``foo.other`` to ``foo``.
This differs between Windows and Unix.

On Unix, we need to consider all possible interleavings between the
operations performed by the Magic Folder client and the other process.
(Note that atomic operations on a directory are totally ordered.)

* Interleaving A: the other process' rename precedes our rename in
  step 4b, and we get an ``IN_MOVED_TO`` event for its rename by
  step 2. Then we reclassify as a conflict; its changes end up at
  ``foo`` and ours end up at ``foo.conflicted``. This avoids data
  loss.

* Interleaving B: its rename precedes ours in step 4b, and we do
  not get an event for its rename by step 2. Its changes end up at
  ``foo.old``, and ours end up at ``foo`` after being linked there
  in step 4c. This avoids data loss.

* Interleaving C: its rename happens between our rename in step 4b,
  and our link operation in step 4c of the file replacement. The
  latter fails with an ``EEXIST`` error because ``foo`` already
  exists. We reclassify as a conflict; the old version ends up at
  ``foo.old``, the other process' changes end up at ``foo``, and
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

On Windows, the internal implementation of `ReplaceFileW`_ is similar
to what we have described above for Unix; it works like this:

4a′. Copy metadata (which does not include ``mtime``) from the
     replaced file (``foo``) to the replacement file (``.foo.tmp``).
4b′. Attempt to move the replaced file (``foo``) onto the
     backup filename (``foo.old``), deleting the latter if it
     already exists.
4c′. Attempt to move the replacement file (``.foo.tmp``) to the
     replaced filename (``foo``); fail if the destination already
     exists.

Notice that this is essentially the same as the algorithm we use
for Unix, but steps 4c and 4d on Unix are combined into a single
step 4c′. (If there is a failure at steps 4c′ after step 4b′ has
completed, the `ReplaceFileW`_ call will fail with return code
``ERROR_UNABLE_TO_MOVE_REPLACEMENT_2``. However, it is still
preferable to use this API over two `MoveFileExW`_ calls, because
it retains the attributes and ACLs of ``foo`` where possible.)

However, on Windows the other application will not be able to
directly rename ``foo.other`` onto ``foo`` (which would fail because
the destination already exists); it will have to rename or delete
``foo`` first. Without loss of generality, let's say ``foo`` is
deleted. This complicates the interleaving analysis, because we
have two operations done by the other process interleaving with
three done by the magic folder process (rather than one operation
interleaving with four as on Unix). The cases are:

* Interleaving A′: the other process' deletion of ``foo`` and its
  rename of ``foo.other`` to ``foo`` both precede our rename in
  step 4b. We get an event corresponding to its rename by step 2.
  Then we reclassify as a conflict; its changes end up at ``foo``
  and ours end up at ``foo.conflicted``. This avoids data loss.

* Interleaving B′: the other process' deletion of ``foo`` and its
  rename of ``foo.other`` to ``foo`` both precede our rename in
  step 4b. We do not get an event for its rename by step 2.
  Its changes end up at ``foo.old``, and ours end up at ``foo``
  after being linked there in step 4c. This avoids data loss.

* Interleaving C′: the other process' deletion of ``foo`` precedes
  our rename of ``foo`` to ``foo.old`` done by `ReplaceFileW`_,
  but its rename of ``foo.other`` to ``foo`` does not, so we get
  an ``ERROR_FILE_NOT_FOUND`` error from `ReplaceFileW`_ indicating
  that the replaced file does not exist. Then we reclassify as a
  conflict; the other process' changes end up at ``foo`` (after
  it has renamed ``foo.other`` to ``foo``) and our changes end up
  at ``foo.conflicted``. This avoids data loss.

* Interleaving D′: the other process' deletion and/or rename happen
  during the call to `ReplaceFileW`_, causing the latter to fail.
  There are two subcases:
  * if the error is ``ERROR_UNABLE_TO_MOVE_REPLACEMENT_2``, then
    ``foo`` is renamed to ``foo.old`` and ``.foo.tmp`` remains
    at its original name after the call.
  * for all other errors, ``foo`` and ``.foo.tmp`` both remain at
    their original names after the call.
  In both cases, we reclassify as a conflict and rename ``.foo.tmp``
  to ``foo.conflicted``. This avoids data loss.

* Interleaving E′: the other process' deletion of ``foo`` and attempt
  to rename ``foo.other`` to ``foo`` both happen after all internal
  operations of `ReplaceFileW`_ have completed. This causes an event
  for ``foo`` (the deletion and rename events are merged due to the
  pending delay). The rename also changes the ``mtime`` for ``foo`` so
  that it is different from the ``mtime`` calculated in step 3, and
  therefore different from the metadata recorded for ``foo`` in the
  magic folder db. (Assuming no system clock changes, its rename will
  set an ``mtime`` timestamp corresponding to a time after the
  internal operations of `ReplaceFileW`_ have completed, which is not
  equal to the timestamp *T* seconds before `ReplaceFileW`_ is called,
  provided that *T* seconds is sufficiently greater than the timestamp
  granularity.) Therefore, an upload will be triggered for ``foo``
  after its change, which is correct and avoids data loss.

.. _`MoveFileExW`: https://msdn.microsoft.com/en-us/library/windows/desktop/aa365240%28v=vs.85%29.aspx

We also need to consider what happens if another process opens ``foo``
and writes to it directly, rather than renaming another file onto it:

* On Unix, open file handles refer to inodes, not paths. If the other
  process opens ``foo`` before it has been renamed to ``foo.old``,
  and then closes the file, changes will have been written to the file
  at the same inode, even if that inode is now linked at ``foo.old``.
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

[TODO: on Windows, what is the default sharing of a file opened for
writing by _open/_wopen?]


A *read/download collision* occurs when another program reads
from ``foo`` in the local filesystem, concurrently with the new
version being written by the Magic Folder client. We want to
ensure that any successful attempt to read the file by the other
program obtains a consistent view of its contents.

On Unix, the above procedure for writing downloads is sufficient
to achieve this. There are three cases:

* The other process opens ``foo`` for reading before it is
  renamed to ``foo.old``. Then the file handle will continue to
  refer to the old file across the rename, and the other process
  will read the old contents.
* The other process attempts to open ``foo`` after it has been
  renamed to ``foo.old``, and before it is linked in step c.
  The open call fails, which is acceptable.
* The other process opens ``foo`` after it has been linked to
  the new file. Then it will read the new contents.

On Windows, [TODO].

Above we have considered only interleavings with a single other process,
and only the most common possibilities for the other process' interaction
with the file. If multiple other processes are involved, or if a process
performs operations other than those considered, then we cannot say much
about the outcome in general; however, we believe that such cases will be
much less common.


Air Dragons: Write/upload collisions
''''''''''''''''''''''''''''''''''''

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
other users' clients. This may also interfere with conflict/overwrite
detection for those users [TODO EXPLAIN].

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


Fire Dragons: Distinguishing conflicts from overwrites
''''''''''''''''''''''''''''''''''''''''''''''''''''''

It is also necessary to distinguish between overwrites, in which the
remote side was aware of your most recent version and overwrote it with a
new version, and conflicts, in which the remote side was unaware of your
most recent version when it published its new version. Those two cases
have to be handled differently — the latter needs to be raised to the
user as an issue the user will have to resolve and the former must not
bother the user.

alice sees a change by bob to 'foo' and needs to know whether that change
is an overwrite or a conflict
i.e. is it "based on" the version that alice already had
for the definition of "based on", we build on the solution to the earth
dragon

when any client uploads a file, it includes Tahoe-side metadata giving
the URI of the last remote version that it saved
before the notification of the local write that caused the upload
the metadata also includes the length of time between the last save and
the notification; if this is very short,
then we are uncertain about whether the writing app took into account the
last save (and we can use that information
to be conservative about treating changes as conflicts).
so, when alice sees bob's change, it can compare the URI in the metadata
for the downloaded file, with the URI that
is alice's magic folder db.
(if alice had that version but had not recorded the URI, we count that as
a conflict.

this is justified because bob could not have learnt an URI matching
alice's version unless [alice created that version
and had uploaded it] or [someone else created that version and alice had
downloaded it])

alice does this comparison only when it is about to write bob's change.
if it is a conflict, then it just creates a
new file for the conflicted copy (and doesn't update its own copy at the
bare filename, nor does it change its
magic folder db)
filesystem notifications for filenames that match the conflicted pattern
are ignored


Water Dragons: Resolving conflict loops
'''''''''''''''''''''''''''''''''''''''

suppose that we've detected a remote write to file 'foo' that conflicts
with a local write
(alice is the local user that has detected the conflict, and bob is the
user who did the remote write)

alice's gateway creates a 'foo.conflict_by_bob_at_timestamp' file
alice-the-human at some point notices the conflict and updates hir copy
of 'foo' to take into account bob's writes

but, there is no way to know whether that update actually took into
account 'foo.conflict_by_bob_at_timestamp' or not
alice could have failed to notice 'foo.conflict_by_bob_at_timestamp' at
all, and just saved hir copy of 'foo' again
so, when there is another remote write, how do we know whether it should
be treated as a conflict or not?
well, alice could delete or rename 'foo.conflict_by_bob_at_timestamp' in
order to indicate that ze'd taken it into account. but I'm not sure about
the usability properties of that
the issue is whether, after 'foo.conflict_by_bob_at_timestamp' has been
written, alice's magic folder db should be updated to indicate (for the
purpose of conflict detection) that ze has seen bob's version of 'foo'
so, I think that alice's magic folder db should *not* be updated to
indicate ze has seen bob's version of 'foo'. in that case, when ze
updates hir local copy of 'foo' (with no suffix), the metadata of the
copy of 'foo' that hir client uploads will indicate only that it was
based on the previous version of 'foo'. then when bob gets that copy, it
will be treated as a conflict and called
'foo.conflict_by_alice_at_timestamp2'
which I think is the desired behaviour
oh, but then how do alice and bob exit the conflict loop? that's the
usability issue I was worried about [...]
if alice's client does update hir magic folder db, then bob will see hir
update as an overwrite
even though ze didn't necessarily take into account bob's changes
which seems wrong :-(
(bob's changes haven't been lost completely; they are still on alice's
filesystem. but they have been overwritten in bob's filesystem!)
so maybe we need alice to delete 'foo.conflict_by_bob_at_timestamp', and
use that as the signal that ze has seen bob's changes and to break the
conflict loop
(or rename it; actually any change to that file is sufficient to indicate
that alice has seen it)


Aether Dragons: Handling deletion and renames
'''''''''''''''''''''''''''''''''''''''''''''

*Deletion*

deletion of a file is like overwriting it with a "deleted" marker

[TODO: deletion of a directory?]

*Renames*

suppose that a subfolder of the Magic Folder is renamed on one of the
Magic Folder clients. it is not clear how to handle this at all:

* if the folder is renamed automatically on other clients, then apps that
  were using files in that folder may break. The behavior differs between
  Windows and Unix: on Windows, it might not be possible to rename the
  folder at all if it contains open files, while on Unix, open file handles
  will stay open but operations involving the old path will fail. either
  way the behaviour is likely to be confusing.

* for conflict detection, it is unclear whether existing entries in the
  magic folder db under the old path should be updated to their new path.

* another possibility is treat the rename like a copy, i.e. all clients
  end up with a copy of the directory under both names. effectively we
  treat the move event as a directory creation, and also pretend that there
  has been a modification of the directory at the old name by all other
  Magic Folder clients. this is the easiest option to implement.


Other design issues
'''''''''''''''''''

* choice of conflicted filenames (e.g. ``foo.by_bob_at_YYYYMMDD_HHMMSS[v].type``)
