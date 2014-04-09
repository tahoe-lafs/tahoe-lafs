
import os, os.path, struct

from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import IShareForReading, IShareForWriting

from allmydata.util import fileutil
from allmydata.util.assertutil import precondition, _assert
from allmydata.storage.common import si_b2a, CorruptStoredShareError, UnknownImmutableContainerVersionError, \
     DataTooLargeError


# Each share file (in storage/shares/$PREFIX/$STORAGEINDEX/$SHNUM) contains
# share data that can be accessed by RIBucketWriter.write and RIBucketReader.read .

# The share file has the following layout:
#  0x00: share file version number, four bytes, current version is 1
#  0x04: share data length, four bytes big-endian     # Footnote 1
#  0x08: number of leases, four bytes big-endian = N  # Footnote 2
#  0x0c: beginning of share data (see immutable.layout.WriteBucketProxy)
#  filesize - 72*N: leases (ignored). Each lease is 72 bytes.

# Footnote 1: as of Tahoe v1.3.0 this field is not used by storage servers.

# Footnote 2: as of Tahoe v1.11.0 this field is not used by storage servers.
# New shares will have a 0 here. Old shares will have whatever value was left
# over when the server was upgraded. All lease information is now kept in the
# leasedb.

class ImmutableDiskShare(object):
    implements(IShareForReading, IShareForWriting)

    sharetype = "immutable"
    LEASE_SIZE = struct.calcsize(">L32s32sL")
    HEADER = ">LLL"
    HEADER_SIZE = struct.calcsize(HEADER)
    DATA_OFFSET = HEADER_SIZE

    def __init__(self, home, storage_index, shnum, finalhome=None, allocated_data_length=None):
        """
        If allocated_data_length is not None then I won't allow more than allocated_data_length
        to be written to me.
        If finalhome is not None (meaning that we are creating the share) then allocated_data_length
        must not be None.

        Clients should use the load_immutable_disk_share and create_immutable_disk_share
        factory functions rather than creating instances directly.
        """
        precondition((allocated_data_length is not None) or (finalhome is None),
                     allocated_data_length=allocated_data_length, finalhome=finalhome)
        self._storage_index = storage_index
        self._allocated_data_length = allocated_data_length

        # If we are creating the share, _finalhome refers to the final path and
        # _home to the incoming path. Otherwise, _finalhome is None.
        self._finalhome = finalhome
        self._home = home
        self._shnum = shnum

        if self._finalhome is not None:
            # Touch the file, so later callers will see that we're working on
            # it. Also construct the metadata.
            _assert(not os.path.exists(self._finalhome), finalhome=self._finalhome)
            fileutil.make_dirs(os.path.dirname(self._home))
            # The second field -- the four-byte share data length -- is no
            # longer used as of Tahoe v1.3.0, but we continue to write it in
            # there in case someone downgrades a storage server from >=
            # Tahoe-1.3.0 to < Tahoe-1.3.0, or moves a share file from one
            # server to another, etc. We do saturation -- a share data length
            # larger than 2**32-1 (what can fit into the field) is marked as
            # the largest length that can fit into the field. That way, even
            # if this does happen, the old < v1.3.0 server will still allow
            # clients to read the first part of the share.
            fileutil.write(self._home, struct.pack(self.HEADER, 1, min(2**32-1, allocated_data_length), 0))
            self._data_length = allocated_data_length
        else:
            f = open(self._home, 'rb')
            try:
                (version, unused, num_leases) = struct.unpack(self.HEADER, f.read(self.HEADER_SIZE))
            except struct.error, e:
                raise CorruptStoredShareError(shnum, "invalid immutable share header for shnum %d: %s" % (shnum, e))
            finally:
                f.close()
            if version != 1:
                msg = "sharefile %r had version %d but we wanted 1" % (self._home, version)
                raise UnknownImmutableContainerVersionError(shnum, msg)

            filesize = os.stat(self._home).st_size
            self._data_length = filesize - self.DATA_OFFSET - (num_leases * self.LEASE_SIZE)

        if self._data_length < 0:
            raise CorruptStoredShareError("calculated data length for shnum %d is %d" % (shnum, self._data_length))

    def __repr__(self):
        return ("<ImmutableDiskShare %s:%r at %r>"
                % (si_b2a(self._storage_index or ""), self._shnum, self._home))

    def close(self):
        fileutil.make_dirs(os.path.dirname(self._finalhome))
        fileutil.move_into_place(self._home, self._finalhome)

        # self._home is like storage/shares/incoming/ab/abcde/4 .
        # We try to delete the parent (.../ab/abcde) to avoid leaving
        # these directories lying around forever, but the delete might
        # fail if we're working on another share for the same storage
        # index (like ab/abcde/5). The alternative approach would be to
        # use a hierarchy of objects (PrefixHolder, BucketHolder,
        # ShareWriter), each of which is responsible for a single
        # directory on disk, and have them use reference counting of
        # their children to know when they should do the rmdir. This
        # approach is simpler, but relies on os.rmdir (used by
        # rmdir_if_empty) refusing to delete a non-empty directory.
        # Do *not* use fileutil.remove() here!
        parent = os.path.dirname(self._home)
        fileutil.rmdir_if_empty(parent)

        # we also delete the grandparent (prefix) directory, .../ab ,
        # again to avoid leaving directories lying around. This might
        # fail if there is another bucket open that shares a prefix (like
        # ab/abfff).
        fileutil.rmdir_if_empty(os.path.dirname(parent))

        # we leave the great-grandparent (incoming/) directory in place.

        self._home = self._finalhome
        self._finalhome = None
        return defer.succeed(None)

    def get_used_space(self):
        return (fileutil.get_used_space(self._finalhome) +
                fileutil.get_used_space(self._home))

    def get_storage_index(self):
        return self._storage_index

    def get_storage_index_string(self):
        return si_b2a(self._storage_index)

    def get_shnum(self):
        return self._shnum

    def unlink(self):
        fileutil.remove(self._home)
        return defer.succeed(None)

    def get_allocated_data_length(self):
        return self._allocated_data_length

    def get_size(self):
        return os.stat(self._home).st_size

    def get_data_length(self):
        return self._data_length

    def readv(self, readv):
        datav = []
        f = open(self._home, 'rb')
        try:
            for (offset, length) in readv:
                datav.append(self._read_share_data(f, offset, length))
        finally:
            f.close()
        return defer.succeed(datav)

    def _get_path(self):
        return self._home

    def _read_share_data(self, f, offset, length):
        precondition(offset >= 0)

        # Reads beyond the end of the data are truncated. Reads that start
        # beyond the end of the data return an empty string.
        seekpos = self.DATA_OFFSET + offset
        actuallength = max(0, min(length, self._data_length - offset))
        if actuallength == 0:
            return ""
        f.seek(seekpos)
        return f.read(actuallength)

    def read_share_data(self, offset, length):
        f = open(self._home, 'rb')
        try:
            return defer.succeed(self._read_share_data(f, offset, length))
        finally:
            f.close()

    def write_share_data(self, offset, data):
        length = len(data)
        precondition(offset >= 0, offset)
        if self._allocated_data_length is not None and offset+length > self._allocated_data_length:
            raise DataTooLargeError(self._allocated_data_length, offset, length)
        f = open(self._home, 'rb+')
        try:
            real_offset = self.DATA_OFFSET + offset
            f.seek(real_offset)
            _assert(f.tell() == real_offset)
            f.write(data)
            return defer.succeed(None)
        finally:
            f.close()


def load_immutable_disk_share(home, storage_index=None, shnum=None):
    return ImmutableDiskShare(home, storage_index=storage_index, shnum=shnum)

def create_immutable_disk_share(home, finalhome, allocated_data_length, storage_index=None, shnum=None):
    return ImmutableDiskShare(home, finalhome=finalhome, allocated_data_length=allocated_data_length,
                              storage_index=storage_index, shnum=shnum)
