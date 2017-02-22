Multi-party Conflict Detection
==============================

The current Magic-Folder remote conflict detection design does not properly detect remote conflicts
for groups of three or more parties. This design is specified in the "Fire Dragon" section of this document:
https://github.com/tahoe-lafs/tahoe-lafs/blob/2551.wip.2/docs/proposed/magic-folder/remote-to-local-sync.rst#fire-dragons-distinguishing-conflicts-from-overwrites

This Tahoe-LAFS trac ticket comment outlines a scenario with
three parties in which a remote conflict is falsely detected:

.. _`ticket comment`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2551#comment:22


Summary and definitions
=======================

Abstract file: a file being shared by a Magic Folder.

Local file: a file in a client's local filesystem corresponding to an abstract file.

Relative path: the path of an abstract or local file relative to the Magic Folder root.

Version: a snapshot of an abstract file, with associated metadata, that is uploaded by a Magic Folder client.

A version is associated with the file's relative path, its contents, and
mtime and ctime timestamps. Versions also have a unique identity.

Follows relation:
* If and only if a change to a client's local file at relative path F that results in an upload of version V',
was made when the client already had version V of that file, then we say that V' directly follows V.
* The follows relation is the irreflexive transitive closure of the "directly follows" relation.

The follows relation is transitive and acyclic, and therefore defines a DAG called the
Version DAG. Different abstract files correspond to disconnected sets of nodes in the Version DAG
(in other words there are no "follows" relations between different files).

The DAG is only ever extended, not mutated.

The desired behaviour for initially classifying overwrites and conflicts is as follows:

* if a client Bob currently has version V of a file at relative path F, and it sees a new version V'
  of that file in another client Alice's DMD, such that V' follows V, then the write of the new version
  is initially an overwrite and should be to the same filename.
* if, in the same situation, V' does not follow V, then the write of the new version should be
  classified as a conflict.

The existing :doc:`remote-to-local-sync` document defines when an initial
overwrite should be reclassified as a conflict.

The above definitions completely specify the desired solution of the false
conflict behaviour described in the `ticket comment`_. However, they do not give
a concrete algorithm to compute the follows relation, or a representation in the
Tahoe-LAFS file store of the metadata needed to compute it.

We will consider two alternative designs, proposed by Leif Ryge and
Zooko Wilcox-O'Hearn, that aim to fill this gap.



Leif's Proposal: Magic-Folder "single-file" snapshot design
===========================================================

Abstract
--------

We propose a relatively simple modification to the initial Magic Folder design which
adds merkle DAGs of immutable historical snapshots for each file. The full history
does not necessarily need to be retained, and the choice of how much history to retain
can potentially be made on a per-file basis.

Motivation:
-----------

no SPOFs, no admins
```````````````````

Additionally, the initial design had two cases of excess authority:

1. The magic folder administrator (inviter) has everyone's write-caps and is thus essentially "root"
2. Each client shares ambient authority and can delete anything or everything and
   (assuming there is not a conflict) the data will be deleted from all clients. So, each client
   is effectively "root" too.

Thus, while it is useful for file synchronization, the initial design is a much less safe place
to store data than in a single mutable tahoe directory (because more client computers have the
possibility to delete it).


Glossary
--------

- merkle DAG: like a merkle tree but with multiple roots, and with each node potentially having multiple parents
- magic folder: a logical directory that can be synchronized between many clients
  (devices, users, ...) using a Tahoe-LAFS storage grid
- client: a Magic-Folder-enabled Tahoe-LAFS client instance that has access to a magic folder
- DMD: "distributed mutable directory", a physical Tahoe-LAFS mutable directory.
  Each client has the write cap to their own DMD, and read caps to all other client's DMDs
  (as in the original Magic Folder design).
- snapshot: a reference to a version of a file; represented as an immutable directory containing
  an entry called "content" (pointing to the immutable file containing the file's contents),
  and an entry called "parent0" (pointing to a parent snapshot), and optionally parent1 through
  parentN pointing at other parents. The Magic Folder snapshot object is conceptually very similar
  to a git commit object, except for that it is created automatically and it records the history of an
  individual file rather than an entire repository. Also, commits do not need to have authors
  (although an author field could be easily added later).
- deletion snapshot: immutable directory containing no content entry (only one or more parents)
- capability: a Tahoe-LAFS diminishable cryptographic capability
- cap: short for capability
- conflict: the situation when another client's current snapshot for a file is different than our current snapshot, and is not a descendant of ours.
- overwrite: the situation when another client's current snapshot for a file is a (not necessarily direct) descendant of our current snapshot.


Overview
--------

This new design will track the history of each file using "snapshots" which are
created at each upload. Each snapshot will specify one or more parent snapshots,
forming a directed acyclic graph. A Magic-Folder user's DMD uses a flattened directory
hierarchy naming scheme, as in the original design. But, instead of pointing directly
at file contents, each file name will link to that user's latest snapshot for that file.

Inside the dmd there will also be an immutable directory containing the client's subscriptions
(read-caps to other clients' dmds).

Clients periodically poll each other's DMDs. When they see the current snapshot for a file is
different than their own current snapshot for that file, they immediately begin downloading its
contents and then walk backwards through the DAG from the new snapshot until they find their own
snapshot or a common ancestor.

For the common ancestor search to be efficient, the client will need to keep a local store (in the magic folder db) of all of the snapshots
(but not their contents) between the oldest current snapshot of any of their subscriptions and their own current snapshot.
See "local cache purging policy" below for more details.

If the new snapshot is a descendant of the client's existing snapshot, then this update
is an "overwrite" - like a git fast-forward. So, when the download of the new file completes it can overwrite
the existing local file with the new contents and update its dmd to point at the new snapshot.

If the new snapshot is not a descendant of the client's current snapshot, then the update is a
conflict. The new file is downloaded and named $filename.conflict-$user1,$user2 (including a list
of other subscriptions who have that version as their current version).

Changes to the local .conflict- file are not tracked. When that file disappears
(either by deletion, or being renamed) a new snapshot for the conflicting file is
created which has two parents - the client's snapshot prior to the conflict, and the
new conflicting snapshot. If multiple .conflict files are deleted or renamed in a short
period of time, a single conflict-resolving snapshot with more than two parents can be created.

! I think this behavior will confuse users. 

Tahoe-LAFS snapshot objects
---------------------------

These Tahoe-LAFS snapshot objects only track the history of a single file, not a directory hierarchy.
Snapshot objects contain only two field types:
- ``Content``: an immutable capability of the file contents (omitted if deletion snapshot)
- ``Parent0..N``: immutable capabilities representing parent snapshots

Therefore in this system an interesting side effect of this Tahoe snapshot object is that there is no
snapshot author. The only notion of an identity in the Magic-Folder system is the write capability of the user's DMD.

The snapshot object is an immutable directory which looks like this:
content -> immutable cap to file content
parent0 -> immutable cap to a parent snapshot object
parent1..N -> more parent snapshots


Snapshot Author Identity
------------------------

Snapshot identity might become an important feature so that bad actors
can be recognized and other clients can stop "subscribing" to (polling for) updates from them.

Perhaps snapshots could be signed by the user's Magic-Folder write key for this purpose? Probably a bad idea to reuse the write-cap key for this. Better to introduce ed25519 identity keys which can (optionally) sign snapshot contents and store the signature as another member of the immutable directory.


Conflict Resolution
-------------------

detection of conflicts
``````````````````````

A Magic-Folder client updates a given file's current snapshot link to a snapshot which is a descendent
of the previous snapshot. For a given file, let's say "file1", Alice can detect that Bob's DMD has a "file1"
that links to a snapshot which conflicts. Two snapshots conflict if one is not an ancestor of the other.


a possible UI for resolving conflicts
`````````````````````````````````````

If Alice links a conflicting snapshot object for a file named "file1",
Bob and Carole will see a file in their Magic-Folder called "file1.conflicted.Alice".
Alice conversely will see an additional file called "file1.conflicted.previous".
If Alice wishes to resolve the conflict with her new version of the file then
she simply deletes the file called "file1.conflicted.previous". If she wants to
choose the other version then she moves it into place:

   mv file1.conflicted.previous file1


This scheme works for N number of conflicts. Bob for instance could choose
the same resolution for the conflict, like this:
   
   mv file1.Alice file1


Deletion propagation and eventual Garbage Collection
----------------------------------------------------

When a user deletes a file, this is represented by a link from their DMD file
object to a deletion snapshot. Eventually all users will link this deletion
snapshot into their DMD. When all users have the link then they locally cache
the deletion snapshot and remove the link to that file in their DMD.
Deletions can of course be undeleted; this means creating a new snapshot
object that specifies itself a descent of the deletion snapshot.

Clients periodically renew leases to all capabilities recursively linked
to in their DMD. Files which are unlinked by ALL the users of a
given Magic-Folder will eventually be garbage collected.

Lease expirey duration must be tuned properly by storage servers such that
Garbage Collection does not occur too frequently.



Performance Considerations
--------------------------

local changes
`````````````

Our old scheme requires two remote Tahoe-LAFS operations per local file modification:
1. upload new file contents (as an immutable file)
2. modify mutable directory (DMD) to link to the immutable file cap

Our new scheme requires three remote operations:
1. upload new file contents (as in immutable file)
2. upload immutable directory representing Tahoe-LAFS snapshot object
3. modify mutable directory (DMD) to link to the immutable snapshot object

remote changes
``````````````

Our old scheme requires one remote Tahoe-LAFS operation per remote file modification (not counting the polling of the dmd):
1. Download new file content

Our new scheme requires a minimum of two remote operations (not counting the polling of the dmd) for conflicting downloads, or three remote operations for overwrite downloads:
1. Download new snapshot object
2. Download the content it points to
3. If the download is an overwrite, modify the DMD to indicate that the downloaded version is their current version.

If the new snapshot is not a direct descendant of our current snapshot or the other party's previous snapshot we saw, we will also need to download more snapshots to determine if it is a conflict or an overwrite. However, those can be done in
parallel with the content download since we will need to download the content in either case.

While the old scheme is obviously more efficient, we think that the properties provided by the new scheme make it worth the additional cost.

Physical updates to the DMD overiouslly need to be serialized, so multiple logical updates should be combined when an update is already in progress.

conflict detection and local caching
````````````````````````````````````

Local caching of snapshots is important for performance.
We refer to the client's local snapshot cache as the ``magic-folder db``.

Conflict detection can be expensive because it may require the client
to download many snapshots from the other user's DMD in order to try
and find it's own current snapshot or a descendent. The cost of scanning
the remote DMDs should not be very high unless the client conducting the
scan has lots of history to download because of being offline for a long
time while many new snapshots were distributed.


local cache purging policy
``````````````````````````

The client's current snapshot for each file should be cached at all times.
When all clients' views of a file are synchronized (they all have the same
snapshot for that file), no ancestry for that file needs to be cached.
When clients' views of a file are *not* synchronized, the most recent
common ancestor of all clients' snapshots must be kept cached, as must
all intermediate snapshots.


Local Merge Property
--------------------

Bob can in fact, set a pre-existing directory (with files) as his new Magic-Folder directory, resulting
in a merge of the Magic-Folder with Bob's local directory. Filename collisions will result in conflicts
because Bob's new snapshots are not descendent's of the existing Magic-Folder file snapshots.


Example: simultaneous update with four parties:
    
1. A, B, C, D are in sync for file "foo" at snapshot X
2. A and B simultaneously change the file, creating snapshots XA and XB (both descendants of X).
3. C hears about XA first, and D hears about XB first. Both accept an overwrite.
4. All four parties hear about the other update they hadn't heard about yet.
5. Result:
    - everyone's local file "foo" has the content pointed to by the snapshot in their DMD's "foo" entry
    - A and C's DMDs each have the "foo" entry pointing at snapshot XA
    - B and D's DMDs each have the "foo" entry pointing at snapshot XB
    - A and C have a local file called foo.conflict-B,D with XB's content
    - B and D have a local file called foo.conflict-A,C with XA's content

Later:

    - Everyone ignores the conflict, and continue updating their local "foo". but slowly enough that there are no further conflicts, so that A and C remain in sync with eachother, and B and D remain in sync with eachother.

    - A and C's foo.conflict-B,D file continues to be updated with the latest version of the file B and D are working on, and vice-versa.

    - A and C edit the file at the same time again, causing a new conflict.

    - Local files are now:

    A: "foo", "foo.conflict-B,D", "foo.conflict-C"

    C: "foo", "foo.conflict-B,D", "foo.conflict-A"

    B and D: "foo", "foo.conflict-A", "foo.conflict-C"

    - Finally, D decides to look at "foo.conflict-A" and "foo.conflict-C", and they manually integrate (or decide to ignore) the differences into their own local file "foo".

    - D deletes their conflict files.

    - D's DMD now points to a snapshot that is a descendant of everyone else's current snapshot, resolving all conflicts.

    - The conflict files on A, B, and C disappear, and everyone's local file "foo" contains D's manually-merged content.


Daira: I think it is too complicated to include multiple nicknames in the .conflict files
(e.g. "foo.conflict-B,D"). It should be sufficient to have one file for each other client,
reflecting that client's latest version, regardless of who else it conflicts with.


Zooko's Design (as interpreted by Daira)
========================================

A version map is a mapping from client nickname to version number.

Definition: a version map M' strictly-follows a mapping M iff for every entry c->v
in M, there is an entry c->v' in M' such that v' > v.


Each client maintains a 'local version map' and a 'conflict version map' for each file
in its magic folder db.
If it has never written the file, then the entry for its own nickname in the local version
map is zero. The conflict version map only contains entries for nicknames B where
"$FILENAME.conflict-$B" exists.

When a client A uploads a file, it increments the version for its own nickname in its
local version map for the file, and includes that map as metadata with its upload.

A download by client A from client B is an overwrite iff the downloaded version map
strictly-follows A's local version map for that file; in this case A replaces its local
version map with the downloaded version map. Otherwise it is a conflict, and the
download is put into "$FILENAME.conflict-$B"; in this case A's
local version map remains unchanged, and the entry B->v taken from the downloaded
version map is added to its conflict version map.

If client A deletes or renames a conflict file "$FILENAME.conflict-$B", then A copies
the entry for B from its conflict version map to its local version map, deletes
the entry for B in its conflict version map, and performs another upload (with
incremented version number) of $FILENAME.


Example:
    A, B, C = (10, 20, 30) everyone agrees.
    A updates: (11, 20, 30)
    B updates: (10, 21, 30)

C will see either A or B first. Both would be an overwrite, if considered alone.



