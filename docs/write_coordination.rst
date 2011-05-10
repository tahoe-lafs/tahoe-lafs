==================================
Avoiding Write Collisions in Tahoe
==================================

Tahoe does not provide locking of the mutable files and directories.  
If there is more than one simultaneous attempt to change a mutable file 
or directory, then an <cite>UncoordinatedWriteError</p> will result.  
This might, in rare cases, cause the file or directory contents to be 
accidentally deleted.  The user is expected to ensure that there is at 
most one outstanding write or update request for a given file or 
directory at a time.  One convenient way to accomplish this is to make 
a different file or directory for each person or process which wants to 
write.
