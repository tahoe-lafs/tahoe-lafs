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
mutable directory.
Instead, we create one mutable Tahoe-LAFS directory per client. The
contents of the Magic Folder will be represented by the union of these
directories. Each client polls the other directories in order to detect
remote changes.

*Conflict detection*

there are several kinds of dragon

earth dragons: alice changes 'foo' locally while alice's gateway is writing 'foo'.

alice's gateway
* writes a temporary file foo.tmp
* if 'foo' is clean, i.e. there are no pending notifications, it renames foo.tmp over foo

there is a race condition where the local write notification occurs concurrently with the rename, in which case we may clobber the local write.
it is impossible to detect this (even after the fact) because we can't distinguish whether the notification was for the rename or for the local write.
(assertion: the type of event doesn't help, because the local write may also be a rename --in fact should be for a maximally well-behaved app--
and a rename event doesn't include the from filename. also Windows which doesn't support atomic rename-onto.)
this race has a small window (milliseconds or less)

OR: alice's gateway
* writes a temporary file foo.new
* if 'foo' is clean, i.e. there are no pending notifications, it renames foo to foo.old and then foo.new to foo

(this would work on Windows; note that the rename to foo.old will fail if the file is locked for writing)


did the notification event for the local change precede the write?


air dragons: alice sees a change by bob to 'foo' and needs to know whether that change is an overwrite or a conflict
i.e. is it "based on" the version that alice already had

for the definition of "based on", we build on the solution to the earth dragon
when any client uploads a file, it includes Tahoe-side metadata giving the URI of the last remote version that it saved
before the notification of the local write that caused the upload
the metadata also includes the length of time between the last save and the notification; if this is very short,
then we are uncertain about whether the writing app took into account the last save (and we can use that information
to be conservative about treating changes as conflicts).

so, when alice sees bob's change, it can compare the URI in the metadata for the downloaded file, with the URI that
is alice's magic folder db.
(if alice had that version but had not recorded the URI, we count that as a conflict.
this is justified because bob could not have learnt an URI matching alice's version unless [alice created that version
and had uploaded it] or [someone else created that version and alice had downloaded it])

alice does this comparison only when it is about to write bob's change. if it is a conflict, then it just creates a
new file for the conflicted copy (and doesn't update its own copy at the bare filename, nor does it change its
magic folder db)

filesystem notifications for filenames that match the conflicted pattern are ignored


fire dragons: resolving conflict loops

suppose that we've detected a remote write to file 'foo' that conflicts with a local write
(alice is the local user that has detected the conflict, and bob is the user who did the remote write)
alice's gateway creates a 'foo.conflict_by_bob_at_timestamp' file
alice-the-human at some point notices the conflict and updates hir copy of 'foo' to take into account bob's writes
but, there is no way to know whether that update actually took into account 'foo.conflict_by_bob_at_timestamp' or not
alice could have failed to notice 'foo.conflict_by_bob_at_timestamp' at all, and just saved hir copy of 'foo' again
so, when there is another remote write, how do we know whether it should be treated as a conflict or not?
well, alice could delete or rename 'foo.conflict_by_bob_at_timestamp' in order to indicate that ze'd taken it into account. but I'm not sure about the usability properties of that
the issue is whether, after 'foo.conflict_by_bob_at_timestamp' has been written, alice's magic folder db should be updated to indicate (for the purpose of conflict detection) that ze has seen bob's version of 'foo'
so, I think that alice's magic folder db should *not* be updated to indicate ze has seen bob's version of 'foo'. in that case, when ze updates hir local copy of 'foo' (with no suffix), the metadata of the copy of 'foo' that hir client uploads will indicate only that it was based on the previous version of 'foo'. then when bob gets that copy, it will be treated as a conflict and called 'foo.conflict_by_alice_at_timestamp2'
which I think is the desired behaviour
oh, but then how do alice and bob exit the conflict loop? that's the usability issue I was worried about [...]
but if alice's client does update hir magic folder db, then bob will see hir update as an overwrite
even though ze didn't necessarily take into account bob's changes
which seems wrong :-(
(bob's changes haven't been lost completely; they are still on alice's filesystem. but they have been overwritten in bob's filesystem!)
so maybe we need alice to delete 'foo.conflict_by_bob_at_timestamp', and use that as the signal that ze has seen bob's changes and to break the conflict loop
(or rename it; actually any change to that file is sufficient to indicate that alice has seen it)


water dragons:

we can't read a file atomically. therefore, when we read a file in order to upload it, we may read an inconsistent version.
the magic folder is still eventually consistent, but inconsistent versions may be visible to other users' clients,
and may interact with conflict/overwrite detection for those users
the queuing of notification events helps because it means that if files are written more quickly than the
pending delay and less frequently than the pending delay, we shouldn't encounter this dragon at all.
also, a well-behaved app will give us enough information to detect this case (in principle), because if we get a notification
of a rename-to while we're reading the file but before we commit the write to the Tahoe directory, then we can abort that
write and re-upload

we have implemented the pending delay but we will not implement the abort/re-upload for the OTF grant




other design issues:
* choice of conflicted filenames (e.g. foo.by_bob_at_YYYYMMDD_HHMMSS[v].type)
* Tahoe-side representation of per-user folders