#! /usr/bin/python

"""
This tool estimates how much space would be consumed by a filetree into which
a native directory was copied.

One open question is how we should encode directories. One approach is to put
a block of data on a server, one per directory, which effectively contains a
dictionary that maps child names to targets (URIs for children which are
files, slotnames for children which are directories). To prevent the server
which hosts this data from either learning its contents or corrupting them,
we can add encryption and integrity checks to the data, at the cost of
storage overhead.

This program is intended to estimate the size of these data blocks using
real-world filenames and directories. You point it at a real directory, and
it does a recursive walk of the filesystem, adding up the size of the
filetree data structures that would be required to represent it.

MODES:

 A: no confidentiality or integrity checking. Directories are serialized
    plaintext dictionaries which map file/subdir names to targets (either
    URIs or slotnames). Each entry can be changed independently.
 B1: child names and targets are encrypted. No integrity checks, so the
     server can still corrupt the contents undetectably. Each entry can
     still be changed independently.
 B2: same security properties as B1, but the dictionary is serialized before
     encryption. This reduces overhead at the cost of preventing independent
     updates of entries (all entries must be updated at the same time, so
     test-and-set operations are required to avoid data-losing races)
 C1: like B1, but adding HMACs to each entry to guarantee data integrity
 C2: like B2, but adding a single block-wide HMAC for data integrity

"""

import sys, os.path

#URI:7jzbza6iwdsk5xbxsvdgjaugyrhetw64zpflp4gihmyh5krjblra====:a5qdejwbimu5b2wfke7xwexxlq======:gzeub5v42rjbgd7ccawnahu2evqd42lpdpzd447c6zkmdvjkpowq====:25:100:219889
# that's a printable representation of two 32-byte hashes (storage index, URI
# extension block hash) and a 16-byte AES read-capability key, and some
# share-count and size information
URI_SIZE = 164

#pb://xextf3eap44o3wi27mf7ehiur6wvhzr6@207.7.153.180:56677,127.0.0.1:56677/zilcw5uz2yyyo===
# that's a FURL which points at the slot. Modes that need to add a
# read-capability AES key will need more space.
SLOTNAME_SIZE = 90


def slotsize(mode, numfiles, numdirs):
    # URI_sizes is the total space taken up by the target (dict keys) strings
    # for all of the targets that are files, instead of directories
    target_sizes_for_files = numfiles * URI_SIZE
    slotname_size = SLOTNAME_SIZE
    if mode in ("B1", "B2", "C1", "C2"):
        slotname_size += 16
    # slotname_sizes is the total space taken up by the target strings for
    # all the targets that are directories, instead of files. These are
    # bigger when the read+write-cap slotname is larger than the store-cap,
    # which happens as soon as we seek to prevent the slot's host from
    # reading or corrupting it.
    target_sizes_for_subdirs = numdirs * slotname_size

    # now how much overhead is there for each entry?
    per_slot, per_entry = 0, 0
    if mode == "B1":
        per_entry = 16+12+12
    elif mode == "C1":
        per_entry = 16+12+12 + 32+32
    elif mode == "B2":
        per_slot = 12
    elif mode == "C2":
        per_slot = 12+32
    num_entries = numfiles + numdirs
    total = (target_sizes_for_files +
             target_sizes_for_subdirs +
             per_slot +
             per_entry * num_entries
             )
    return total

MODES = ("A", "B1", "B2", "C1", "C2")

def scan(root):
    total = dict([(mode,0) for mode in MODES])
    num_files = 0
    num_dirs = 0
    for absroot, dirs, files in os.walk(root):
        #print absroot
        #print " %d files" % len(files)
        #print " %d subdirs" % len(dirs)
        num_files += len(files)
        num_dirs += len(dirs)
        stringsize = len(''.join(files) + ''.join(dirs))
        for mode in MODES:
            total[mode] += slotsize(mode, len(files), len(dirs)) + stringsize

    print "%d directories" % num_dirs
    print "%d files" % num_files
    for mode in sorted(total.keys()):
        print "%s: %d bytes" % (mode, total[mode])


if __name__ == '__main__':
    scan(sys.argv[1])

"""
260:warner@monolith% ./count_dirs.py ~
70925 directories
457199 files
A: 90042361 bytes
B1: 112302121 bytes
B2: 92027061 bytes
C1: 146102057 bytes
C2: 94293461 bytes

"""
