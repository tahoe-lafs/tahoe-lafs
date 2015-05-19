Magic Folder design for remote-to-local sync
============================================

*Scope*

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

*Glossary*

Object: a file or directory
DMD: distributed mutable directory
Folder: an abstract directory that is synchronized between clients.
  (A folder is not the same as the directory corresponding to it on
  any particular client, nor is it the same as a DMD.)
Descendant: a direct or indirect child in a directory or folder tree
Subfolder: a folder that is a descendant of a magic folder
Subpath: the path from a magic folder to one of its descendants


*Detecting remote changes*

Unlike the local case where we use inotify or ReadDirectoryChangesW to
detect filesystem changes, we have no mechanism to register a monitor for
changes to a Tahoe-LAFS directory. Therefore, we must periodically poll
for changes.

An important constraint on the solution is Tahoe-LAFS' "`write
coordination directive`_", which prohibits concurrent writes by different
storage clients to the same mutable object:

    Tahoe does not provide locking of mutable files and directories. If
there is more than one simultaneous attempt to change a mutable file or
directory, then an UncoordinatedWriteError may result. This might, in
rare cases, cause the file or directory contents to be accidentally
deleted.  The user is expected to ensure that there is at most one
outstanding write or update request for a given file or directory at a
time.  One convenient way to accomplish this is to make a different file
or directory for each person or process that wants to write.

.. _`write coordination directive`:
https://github.com/tahoe-lafs/tahoe-lafs/blob/master/docs/
write_coordination.rst

So, in order to achieve the goal of allowing multiple users to write to a
Magic Folder, we cannot implement the Magic Folder as a single Tahoe-LAFS
DMD. [FIXME reword to allow for design 6.]
[In designs 1 to 5 inclusive] Instead, we create one DMD per client. The
contents of the Magic Folder will be represented by the union of these
client DMDs. Each client polls the other client DMDs in order to detect
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
own client DMD when changes are made by other . (It can potentially batch
changes, subject to latency requirements.)

6. The write coordination problem is solved by implementing `two-phase
commit`_. Then, the representation consists of a single DMD tree which is
written by all clients.

.. _`two-phase commit`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1755

Here is a summary of advantages and disadvantages of each design: [TODO:
express this as a table with the properties as rows and the designs as
columns. It may be useful to simplify/merge some of the properties and
use footnotes for more detailed explanation.]

Key:
++ major advantage
+   minor advantage
-    minor disadvantage
--   major disadvantage
---  showstopper

123456+: All designs have the property that a recursive add-lease
operation starting from the parent Tahoe-LAFS DMD will find all of the
files and directories used in the Magic Folder representation. Therefore
the representation is compatible with `garbage collection`_, even when a
pre-Magic-Folder client does the lease marking.

.. _`garbage collection`: https://tahoe-lafs.org/trac/tahoe-lafs/browser/trunk/docs/garbage-collection.rst

123456+: All designs avoid "breaking" pre-Magic-Folder clients that read
a directory or file that is part of the representation.

456++: Only these designs allow a readcap to one of the client
directories --or one of their subdirectories-- to be shared with other
Tahoe-LAFS clients (not necessarily Magic Folder clients), so that such a
client sees all of the contents of the Magic Folder. Note that this was
not a requirement of the OTF proposal, although it is useful.

135+: A Magic Folder client has only one mutable Tahoe-LAFS object to
monitor per other client. This minimises communication bandwidth for
polling, or alternatively the latency possible for a given polling
bandwidth.

1-: If the Magic Folder has many subfolders, their files will all be
collapsed into the same DMD, which could get quite large. In practice a
single DMD can easily handle the number of files expected to be written
by a client, so this is unlikely to be a significant issue.

35--: When a Magic Folder client detects a remote change, it must
traverse an immutable directory structure to see what has changed.
Completely unchanged subtrees will have the same URI, allowing some of
this traversal to be shortcutted.

24---: When a Magic Folder client detects a remote change, it must
traverse a mutable directory structure to see what has changed. This is
more complex and less efficient than traversing an immutable structure,
because shortcutting is not possible (each DMD retains the same URI even
if a descendant object has changed), and because the structure may change
while it is being traversed. Also the traversal needs to be robust
against cycles, which can only occur in mutable structures.

45--: When a change occurs in one Magic Folder client, it will propagate
to all the other clients. Each client will therefore see multiple
representation changes for a single logical change to the Magic Folder
contents, and must suppress the duplicates. This is particularly
problematic for design 4 where it interacts with the preceding issue.

1236+: A client does not need to make changes to its own DMD that repeat
changesthat another Magic Folder client had previously made. This reduces
write bandwidth and complexity.

4---, 5--: There is the potential for client DMDs to get "out of sync"
with each other, potentially for long periods if errors occur. Thus each
client must be able to "repair" its client directory (and its
subdirectory structure) concurrently with performing its own writes. This
is a significant complexity burden and may introduce failure modes that
could not otherwise happen.

6---: While two-phase commit is a well-established protocol, its
application to Tahoe-LAFS requires significant design work, and may still
leave some corner cases of the write coordination problem unsolved.

[Daira:
* designs 2 and 3 have no significant advantages over design 1, while
requiring higher polling bandwidth and greater complexity due to the need
to create subdirectories. They should be rejected.
* design 4 should be rejected due to the out-of-sync problem, which is
severe and possibly unsolvable for mutable structures.
* for design 5, the out-of-sync problem is still present but possibly
solvable. However, design 5 is substantially more complex, less efficient
in bandwidth/latency, and less scalable in number of clients and
subfolders than design 1. It only gains over design 1 on the ability to
share directory readcaps to the Magic Folder (or subfolders) which was
not a requirement, and IMHO could be better satisfied by design 6 in
future.
* design 6 is an unsolved design problem and should be considered out
of scope for the time being. We can benefit from experience with design 1
when switching to design 6 later.]

*Conflict detection*

there are several kinds of dragon [*]

earth dragons: write/download and read/download collisions

alice changes 'foo' locally while alice's gateway is writing 'foo'
locally (in response to a remote change)

alice's gateway
* writes a temporary file foo.tmp
* if 'foo' is clean, i.e. there are no pending notifications, it moves
foo.tmp over foo [FIXME: if we want to preserve old versions then it
should rename the old version first; see below]
there is a race condition where the local write notification occurs
concurrently with the move, in which case we may clobber the local write.
it is impossible to detect this (even after the fact) because we can't
distinguish whether the notification was for the move or for the local
write.
(assertion: the type of event doesn't help, because the local write may
also be a move --in fact should be for a maximally well-behaved app--
and a move event doesn't include the from filename. also Windows which
doesn't support atomic move-onto.)
this race has a small window (milliseconds or less)

OR: alice's gateway
* writes a temporary file foo.new
* if 'foo' is clean, i.e. there are no pending notifications, it moves
foo to foo.old and then foo.new to foo
(this would work on Windows; note that the rename to foo.old will fail if
the file is locked for writing. We should probably handle that case as a
conflict.)

TODO: on Unix, what happens with reference to inotify events if we rename a file while
it is open? Does the filename for the CLOSE_WRITE event reflect the new
name?

did the notification event for the local change precede the write?


air dragons: write/upload collisions

we can't read a file atomically. therefore, when we read a file in order
to upload it, we may read an inconsistent version if it was also being
written locally.

the magic folder is still eventually consistent, but inconsistent
versions may be visible to other users' clients,
and may interact with conflict/overwrite detection for those users
the queuing of notification events helps because it means that if files
are written more quickly than the
pending delay and less frequently than the pending delay, we shouldn't
encounter this dragon at all.

also, a well-behaved app will give us enough information to detect this
case in principle, because if we get a notification
of a rename-to while we're reading the file but before we commit the
write to the Tahoe directory, then we can abort that write and requeue
the file to read/upload
(there is another potential race condition here due to the latency in
responding to the notification. We can make it very unlikely by pausing
after reading the file and before uploading it, to allow time to detect
any notification that occurred as a result of a write-during-read)

we have implemented the pending delay but we will not implement the
abort/re-upload for the OTF grant


fire dragons: distinguishing conflicts from overwrites

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


water dragons: resolving conflict loops

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


aether dragons: handling renames

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

other design issues:
* choice of conflicted filenames (e.g.
foo.by_bob_at_YYYYMMDD_HHMMSS[v].type)

[*] the association of dragons with the classical Greek elements
admittedly owes more to modern fantasy gaming than historically or
culturally accurate dragon mythology. consider them just as codenames for
now
