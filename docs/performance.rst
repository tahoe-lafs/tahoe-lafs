============================================
Performance costs for some common operations
============================================

1.  `Publishing an A-byte immutable file`_
2.  `Publishing an A-byte mutable file`_
3.  `Downloading B bytes of an A-byte immutable file`_
4.  `Downloading B bytes of an A-byte mutable file`_
5.  `Modifying B bytes of an A-byte mutable file`_
6.  `Inserting/Removing B bytes in an A-byte mutable file`_
7.  `Adding an entry to an A-entry directory`_
8.  `Listing an A entry directory`_
9.  `Performing a file-check on an A-byte file`_
10. `Performing a file-verify on an A-byte file`_
11. `Repairing an A-byte file (mutable or immutable)`_

``K`` indicates the number of shares required to reconstruct the file
(default: 3)

``N`` indicates the total number of shares produced (default: 10)

``S`` indicates the segment size (default: 128 KiB)

``A`` indicates the number of bytes in a file

``B`` indicates the number of bytes of a file which are being read or
written

``G`` indicates the number of storage servers on your grid

Publishing an ``A``-byte immutable file
=======================================

when the file is already uploaded
---------------------------------

If the file is already uploaded with the exact same contents, same
erasure coding parameters (K, N), and same added convergence secret,
then it reads the whole file from disk one time while hashing it to
compute the storage index, then contacts about N servers to ask each
one to store a share. All of the servers reply that they already have
a copy of that share, and the upload is done.

disk: A

cpu: ~A

network: ~N

memory footprint: S

when the file is not already uploaded
-------------------------------------

If the file is not already uploaded with the exact same contents, same
erasure coding parameters (K, N), and same added convergence secret,
then it reads the whole file from disk one time while hashing it to
compute the storage index, then contacts about N servers to ask each
one to store a share. Then it uploads each share to a storage server.

disk: 2*A

cpu: 2*~A

network: N/K*A

memory footprint: N/K*S

Publishing an ``A``-byte mutable file
=====================================

cpu: ~A + a large constant for RSA keypair generation

network: A

memory footprint: N/K*A

notes: Tahoe-LAFS generates a new RSA keypair for each mutable file that it
publishes to a grid. This takes up to 1 or 2 seconds on a typical desktop PC.

Part of the process of encrypting, encoding, and uploading a mutable file to a
Tahoe-LAFS grid requires that the entire file be in memory at once. For larger
files, this may cause Tahoe-LAFS to have an unacceptably large memory footprint
(at least when uploading a mutable file).

Downloading ``B`` bytes of an ``A``-byte immutable file
=======================================================

cpu: ~B

network: B

notes: When Tahoe-LAFS 1.8.0 or later is asked to read an arbitrary
range of an immutable file, only the S-byte segments that overlap the
requested range will be downloaded.

(Earlier versions would download from the beginning of the file up
until the end of the requested range, and then continue to download
the rest of the file even after the request was satisfied.)

Downloading ``B`` bytes of an ``A``-byte mutable file
=====================================================

cpu: ~A

network: A

memory footprint: A

notes: As currently implemented, mutable files must be downloaded in
their entirety before any part of them can be read. We are
exploring fixes for this; see ticket #393 for more information.

Modifying ``B`` bytes of an ``A``-byte mutable file
===================================================

cpu: ~A

network: A

memory footprint: N/K*A

notes: If you upload a changed version of a mutable file that you
earlier put onto your grid with, say, 'tahoe put --mutable',
Tahoe-LAFS will replace the old file with the new file on the
grid, rather than attempting to modify only those portions of the
file that have changed. Modifying a file in this manner is
essentially uploading the file over again, except that it re-uses
the existing RSA keypair instead of generating a new one.

Inserting/Removing ``B`` bytes in an ``A``-byte mutable file
============================================================

cpu: ~A

network: A

memory footprint: N/K*A

notes: Modifying any part of a mutable file in Tahoe-LAFS requires that
the entire file be downloaded, modified, held in memory while it is
encrypted and encoded, and then re-uploaded. A future version of the
mutable file layout ("LDMF") may provide efficient inserts and
deletes. Note that this sort of modification is mostly used internally
for directories, and isn't something that the WUI, CLI, or other
interfaces will do -- instead, they will simply overwrite the file to
be modified, as described in "Modifying B bytes of an A-byte mutable
file".

Adding an entry to an ``A``-entry directory
===========================================

cpu: ~A

network: ~A

memory footprint: N/K*~A

notes: In Tahoe-LAFS, directories are implemented as specialized mutable
files. So adding an entry to a directory is essentially adding B
(actually, 300-330) bytes somewhere in an existing mutable file.

Listing an ``A`` entry directory
================================

cpu: ~A

network: ~A

memory footprint: N/K*~A

notes: Listing a directory requires that the mutable file storing the
directory be downloaded from the grid. So listing an A entry
directory requires downloading a (roughly) 330 * A byte mutable
file, since each directory entry is about 300-330 bytes in size.

Performing a file-check on an ``A``-byte file
=============================================

cpu: ~G

network: ~G

memory footprint: negligible

notes: To check a file, Tahoe-LAFS queries all the servers that it knows
about. Note that neither of these values directly depend on the size
of the file. This is relatively inexpensive, compared to the verify
and repair operations.

Performing a file-verify on an ``A``-byte file
==============================================

cpu: ~N/K*A

network: N/K*A

memory footprint: N/K*S

notes: To verify a file, Tahoe-LAFS downloads all of the ciphertext
shares that were originally uploaded to the grid and integrity checks
them. This is (for well-behaved grids) more expensive than downloading
an A-byte file, since only a fraction of these shares are necessary to
recover the file.

Repairing an ``A``-byte file (mutable or immutable)
===================================================

cpu: variable, between ~A and ~N/K*A

network: variable; between A and N/K*A

memory footprint: (1+N/K)*S

notes: To repair a file, Tahoe-LAFS downloads the file, and
generates/uploads missing shares in the same way as when it initially
uploads the file.  So, depending on how many shares are missing, this
can cost as little as a download or as much as a download followed by
a full upload.
