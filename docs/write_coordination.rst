==================================
Avoiding Write Collisions in Tahoe
==================================

Tahoe does not provide locking of mutable files and directories.
If there is more than one simultaneous attempt to change a mutable file
or directory, then an ``UncoordinatedWriteError`` may result.
This might, in rare cases, cause the file or directory contents to be
accidentally deleted.  The user is expected to ensure that there is at
most one outstanding write or update request for a given file or
directory at a time.  One convenient way to accomplish this is to make
a different file or directory for each person or process that wants to
write.

If mutable parts of a filesystem are accessed via sshfs, only a single
sshfs mount should be used. There may be data loss if mutable files or
directories are accessed via two sshfs mounts, or written both via sshfs
and from other clients.
