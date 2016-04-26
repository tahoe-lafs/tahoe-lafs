
import os, struct

from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import IMutableShare, BadWriteEnablerError

from allmydata.util import fileutil, idlib, log
from allmydata.util.assertutil import precondition, _assert
from allmydata.util.hashutil import timing_safe_compare
from allmydata.storage.common import si_b2a, CorruptStoredShareError, UnknownMutableContainerVersionError, \
     DataTooLargeError
from allmydata.storage.backends.base import testv_compare
from allmydata.mutable.layout import MUTABLE_MAGIC, MAX_MUTABLE_SHARE_SIZE


# MutableDiskShare is like ImmutableDiskShare, but used for mutable data.
# Mutable shares have a different layout. See docs/mutable.rst for more details.

# #   offset    size    name
# 1   0         32      magic verstr "tahoe mutable container v1" plus binary
# 2   32        20      write enabler's nodeid
# 3   52        32      write enabler
# 4   84        8       data size (actual share data present) (a)
# 5   92        8       offset of (8) count of extra leases (after data)
# 6   100       368     four leases, 92 bytes each (ignored)
# 7   468       (a)     data
# 8   ??        4       count of extra leases
# 9   ??        n*92    extra leases (ignored)


# The struct module doc says that L's are 4 bytes in size, and that Q's are
# 8 bytes in size. Since compatibility depends upon this, double-check it.
assert struct.calcsize(">L") == 4, struct.calcsize(">L")
assert struct.calcsize(">Q") == 8, struct.calcsize(">Q")


class MutableDiskShare(object):
    implements(IMutableShare)

    sharetype = "mutable"
    DATA_LENGTH_OFFSET = struct.calcsize(">32s20s32s")
    EXTRA_LEASE_COUNT_OFFSET = DATA_LENGTH_OFFSET + 8
    HEADER = ">32s20s32sQQ"
    HEADER_SIZE = struct.calcsize(HEADER) # doesn't include leases
    LEASE_SIZE = struct.calcsize(">LL32s32s20s")
    assert LEASE_SIZE == 92, LEASE_SIZE
    DATA_OFFSET = HEADER_SIZE + 4*LEASE_SIZE
    assert DATA_OFFSET == 468, DATA_OFFSET

    MAGIC = MUTABLE_MAGIC
    assert len(MAGIC) == 32
    MAX_SIZE = MAX_MUTABLE_SHARE_SIZE

    def __init__(self, home, storage_index, shnum, parent=None):
        """
        Clients should use the load_mutable_disk_share and create_mutable_disk_share
        factory functions rather than creating instances directly.
        """
        self._storage_index = storage_index
        self._shnum = shnum
        self._home = home
        if os.path.exists(self._home):
            # we don't cache anything, just check the magic
            f = open(self._home, 'rb')
            try:
                data = f.read(self.HEADER_SIZE)
                (magic,
                 _write_enabler_nodeid, _write_enabler,
                 _data_length, _extra_lease_count_offset) = struct.unpack(self.HEADER, data)
                if magic != self.MAGIC:
                    msg = "sharefile %r had magic '%r' but we wanted '%r'" % \
                          (self._home, magic, self.MAGIC)
                    raise UnknownMutableContainerVersionError(shnum, msg)
            except struct.error, e:
                raise CorruptStoredShareError(shnum, "invalid mutable share header for shnum %d: %s" % (shnum, e))
            finally:
                f.close()
        self.parent = parent # for logging

    def log(self, *args, **kwargs):
        if self.parent:
            return self.parent.log(*args, **kwargs)

    def create(self, serverid, write_enabler):
        _assert(not os.path.exists(self._home), "%r already exists and should not" % (self._home,))
        data_length = 0
        extra_lease_count_offset = (self.HEADER_SIZE
                                    + 4 * self.LEASE_SIZE
                                    + data_length)
        assert extra_lease_count_offset == self.DATA_OFFSET # true at creation
        num_extra_leases = 0
        f = open(self._home, 'wb')
        try:
            header = struct.pack(self.HEADER,
                                 self.MAGIC, serverid, write_enabler,
                                 data_length, extra_lease_count_offset,
                                 )
            leases = ("\x00"*self.LEASE_SIZE) * 4
            f.write(header + leases)
            # data goes here, empty after creation
            f.write(struct.pack(">L", num_extra_leases))
            # extra leases go here, none at creation
        finally:
            f.close()
        return self

    def __repr__(self):
        return ("<MutableDiskShare %s:%r at %r>"
                % (si_b2a(self._storage_index or ""), self._shnum, self._home))

    def get_size(self):
        return os.stat(self._home).st_size

    def get_data_length(self):
        f = open(self._home, 'rb')
        try:
            data_length = self._read_data_length(f)
        finally:
            f.close()
        return data_length

    def get_used_space(self):
        return fileutil.get_used_space(self._home)

    def get_storage_index(self):
        return self._storage_index

    def get_storage_index_string(self):
        return si_b2a(self._storage_index)

    def get_shnum(self):
        return self._shnum

    def unlink(self):
        fileutil.remove(self._home)
        return defer.succeed(None)

    def _get_path(self):
        return self._home

    @classmethod
    def _read_data_length(cls, f):
        f.seek(cls.DATA_LENGTH_OFFSET)
        (data_length,) = struct.unpack(">Q", f.read(8))
        return data_length

    @classmethod
    def _read_container_size(cls, f):
        f.seek(cls.EXTRA_LEASE_COUNT_OFFSET)
        (extra_lease_count_offset,) = struct.unpack(">Q", f.read(8))
        return extra_lease_count_offset - cls.DATA_OFFSET

    @classmethod
    def _write_data_length(cls, f, data_length):
        extra_lease_count_offset = cls.DATA_OFFSET + data_length
        f.seek(cls.DATA_LENGTH_OFFSET)
        f.write(struct.pack(">QQ", data_length, extra_lease_count_offset))
        f.seek(extra_lease_count_offset)
        f.write(struct.pack(">L", 0))

    def _read_share_data(self, f, offset, length):
        precondition(offset >= 0, offset=offset)
        data_length = self._read_data_length(f)
        if offset + length > data_length:
            # reads beyond the end of the data are truncated. Reads that
            # start beyond the end of the data return an empty string.
            length = max(0, data_length - offset)
        if length == 0:
            return ""
        precondition(offset + length <= data_length)
        f.seek(self.DATA_OFFSET+offset)
        data = f.read(length)
        return data

    def _write_share_data(self, f, offset, data):
        length = len(data)
        precondition(offset >= 0, offset=offset)
        if offset + length > self.MAX_SIZE:
            raise DataTooLargeError(self._shnum, self.MAX_SIZE, offset, length)

        data_length = self._read_data_length(f)

        if offset+length >= data_length:
            # They are expanding their data size. We must write
            # their new data and modify the recorded data size.

            # Fill any newly exposed empty space with 0's.
            if offset > data_length:
                f.seek(self.DATA_OFFSET + data_length)
                f.write('\x00'*(offset - data_length))
                f.flush()

            new_data_length = offset + length
            self._write_data_length(f, new_data_length)
            # an interrupt here will result in a corrupted share

        # now all that's left to do is write out their data
        f.seek(self.DATA_OFFSET + offset)
        f.write(data)
        return

    @classmethod
    def _read_write_enabler_and_nodeid(cls, f):
        f.seek(0)
        data = f.read(cls.HEADER_SIZE)
        (magic,
         write_enabler_nodeid, write_enabler,
         _data_length, _extra_lease_count_offset) = struct.unpack(cls.HEADER, data)
        assert magic == cls.MAGIC
        return (write_enabler, write_enabler_nodeid)

    def readv(self, readv):
        datav = []
        f = open(self._home, 'rb')
        try:
            for (offset, length) in readv:
                datav.append(self._read_share_data(f, offset, length))
        finally:
            f.close()
        return defer.succeed(datav)

    def check_write_enabler(self, write_enabler):
        f = open(self._home, 'rb+')
        try:
            (real_write_enabler, write_enabler_nodeid) = self._read_write_enabler_and_nodeid(f)
        finally:
            f.close()
        # avoid a timing attack
        if not timing_safe_compare(write_enabler, real_write_enabler):
            # accomodate share migration by reporting the nodeid used for the
            # old write enabler.
            def _bad_write_enabler():
                nodeid_s = idlib.nodeid_b2a(write_enabler_nodeid)
                self.log(format="bad write enabler on SI %(si)s,"
                         " recorded by nodeid %(nodeid)s",
                         facility="tahoe.storage",
                         level=log.WEIRD, umid="cE1eBQ",
                         si=self.get_storage_index_string(),
                         nodeid=nodeid_s)
                raise BadWriteEnablerError("The write enabler was recorded by nodeid '%s'."
                                           % (nodeid_s,))
            return defer.execute(_bad_write_enabler)
        return defer.succeed(None)

    def check_testv(self, testv):
        test_good = True
        f = open(self._home, 'rb+')
        try:
            for (offset, length, operator, specimen) in testv:
                data = self._read_share_data(f, offset, length)
                if not testv_compare(data, operator, specimen):
                    test_good = False
                    break
        finally:
            f.close()
        return defer.succeed(test_good)

    def writev(self, datav, new_length):
        precondition(new_length is None or new_length >= 0, new_length=new_length)

        for (offset, data) in datav:
            precondition(offset >= 0, offset=offset)
            if offset + len(data) > self.MAX_SIZE:
                raise DataTooLargeError(self._shnum, self.MAX_SIZE, offset, len(data))

        f = open(self._home, 'rb+')
        try:
            for (offset, data) in datav:
                self._write_share_data(f, offset, data)
            if new_length is not None:
                cur_length = self._read_data_length(f)
                if new_length < cur_length:
                    self._write_data_length(f, new_length)
                    # TODO: shrink the share file.
        finally:
            f.close()
        return defer.succeed(None)

    def close(self):
        return defer.succeed(None)


def load_mutable_disk_share(home, storage_index=None, shnum=None, parent=None):
    return MutableDiskShare(home, storage_index, shnum, parent)

def create_mutable_disk_share(home, serverid, write_enabler, storage_index=None, shnum=None, parent=None):
    ms = MutableDiskShare(home, storage_index, shnum, parent)
    return ms.create(serverid, write_enabler)
