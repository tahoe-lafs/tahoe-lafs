
import os, struct

from allmydata.util import fileutil
from allmydata.util.fileutil import get_used_space
from allmydata.util.assertutil import precondition
from allmydata.storage.common import UnknownImmutableContainerVersionError, \
     DataTooLargeError


# Each share file (in storage/shares/$SI/$SHNUM) contains share data that
# can be accessed by RIBucketWriter.write and RIBucketReader.read .

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


class ShareFile:
    sharetype = "immutable"
    LEASE_SIZE = struct.calcsize(">L32s32sL")
    HEADER = ">LLL"
    HEADER_SIZE = struct.calcsize(HEADER)
    DATA_OFFSET = HEADER_SIZE

    def __init__(self, filename, max_size=None, create=False):
        """ If max_size is not None then I won't allow more than max_size to be written to me. If create=True and max_size must not be None. """
        precondition((max_size is not None) or (not create), max_size, create)
        self.home = filename
        self._max_size = max_size
        if create:
            # touch the file, so later callers will see that we're working on
            # it. Also construct the metadata.
            assert not os.path.exists(self.home)
            fileutil.make_dirs(os.path.dirname(self.home))
            f = open(self.home, 'wb')
            # The second field -- the four-byte share data length -- is no
            # longer used as of Tahoe v1.3.0, but we continue to write it in
            # there in case someone downgrades a storage server from >=
            # Tahoe-1.3.0 to < Tahoe-1.3.0, or moves a share file from one
            # server to another, etc. We do saturation -- a share data length
            # larger than 2**32-1 (what can fit into the field) is marked as
            # the largest length that can fit into the field. That way, even
            # if this does happen, the old < v1.3.0 server will still allow
            # clients to read the first part of the share.
            f.write(struct.pack(">LLL", 1, min(2**32-1, max_size), 0))
            f.close()
            self._data_length = max_size
        else:
            f = open(self.home, 'rb')
            try:
                (version, unused, num_leases) = struct.unpack(self.HEADER, f.read(self.HEADER_SIZE))
            finally:
                f.close()
            if version != 1:
                msg = "sharefile %s had version %d but we wanted 1" % \
                      (filename, version)
                raise UnknownImmutableContainerVersionError(msg)

            filesize = os.stat(self.home).st_size
            self._data_length = filesize - self.DATA_OFFSET - (num_leases * self.LEASE_SIZE)

        # TODO: raise a better exception.
        assert self._data_length >= 0, self._data_length

    def get_used_space(self):
        return get_used_space(self.home)

    def unlink(self):
        os.unlink(self.home)

    def get_size(self):
        return os.stat(self.home).st_size

    def read_share_data(self, offset, length):
        precondition(offset >= 0)

        # Reads beyond the end of the data are truncated. Reads that start
        # beyond the end of the data return an empty string.
        seekpos = self.DATA_OFFSET + offset
        actuallength = max(0, min(length, self._data_length - offset))
        if actuallength == 0:
            return ""
        f = open(self.home, 'rb')
        try:
            f.seek(seekpos)
            return f.read(actuallength)
        finally:
            f.close()

    def write_share_data(self, offset, data):
        length = len(data)
        precondition(offset >= 0, offset)
        if self._max_size is not None and offset+length > self._max_size:
            raise DataTooLargeError(self._max_size, offset, length)
        f = open(self.home, 'rb+')
        try:
            real_offset = self.DATA_OFFSET + offset
            f.seek(real_offset)
            assert f.tell() == real_offset
            f.write(data)
        finally:
            f.close()
