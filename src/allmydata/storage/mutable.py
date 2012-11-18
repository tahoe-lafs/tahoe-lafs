import os, struct

from twisted.python.filepath import FilePath

from allmydata.interfaces import BadWriteEnablerError
from allmydata.util import idlib, log
from allmydata.util.assertutil import precondition
from allmydata.util.hashutil import constant_time_compare
from allmydata.util.fileutil import get_used_space
from allmydata.storage.common import UnknownMutableContainerVersionError, \
     DataTooLargeError
from allmydata.mutable.layout import MAX_MUTABLE_SHARE_SIZE


# the MutableShareFile is like the ShareFile, but used for mutable data. It
# has a different layout. See docs/mutable.txt for more details.

# #   offset    size    name
# 1   0         32      magic verstr "tahoe mutable container v1" plus binary
# 2   32        20      write enabler's nodeid
# 3   52        32      write enabler
# 4   84        8       data size (actual share data present) (a)
# 5   92        8       offset of (8) count of extra leases (after data)
# 6   100       368     four leases, 92 bytes each
#                        0    4   ownerid (0 means "no lease here")
#                        4    4   expiration timestamp
#                        8   32   renewal token
#                        40  32   cancel token
#                        72  20   nodeid which accepted the tokens
# 7   468       (a)     data
# 8   ??        4       count of extra leases
# 9   ??        n*92    extra leases


# The struct module doc says that L's are 4 bytes in size., and that Q's are
# 8 bytes in size. Since compatibility depends upon this, double-check it.
assert struct.calcsize(">L") == 4, struct.calcsize(">L")
assert struct.calcsize(">Q") == 8, struct.calcsize(">Q")

class MutableShareFile:

    sharetype = "mutable"
    DATA_LENGTH_OFFSET = struct.calcsize(">32s20s32s")
    HEADER_SIZE = struct.calcsize(">32s20s32sQQ") # doesn't include leases
    LEASE_SIZE = struct.calcsize(">LL32s32s20s")
    assert LEASE_SIZE == 92
    DATA_OFFSET = HEADER_SIZE + 4*LEASE_SIZE
    assert DATA_OFFSET == 468, DATA_OFFSET
    # our sharefiles share with a recognizable string, plus some random
    # binary data to reduce the chance that a regular text file will look
    # like a sharefile.
    MAGIC = "Tahoe mutable container v1\n" + "\x75\x09\x44\x03\x8e"
    assert len(MAGIC) == 32
    MAX_SIZE = MAX_MUTABLE_SHARE_SIZE
    # TODO: decide upon a policy for max share size

    def __init__(self, filename, parent=None):
        self.home = filename
        if os.path.exists(self.home):
            # we don't cache anything, just check the magic
            f = open(self.home, 'rb')
            data = f.read(self.HEADER_SIZE)
            (magic,
             write_enabler_nodeid, write_enabler,
             data_length, extra_lease_offset) = \
             struct.unpack(">32s20s32sQQ", data)
            if magic != self.MAGIC:
                msg = "sharefile %s had magic '%r' but we wanted '%r'" % \
                      (filename, magic, self.MAGIC)
                raise UnknownMutableContainerVersionError(msg)
        self.parent = parent # for logging

    def log(self, *args, **kwargs):
        return self.parent.log(*args, **kwargs)

    def create(self, my_nodeid, write_enabler):
        assert not os.path.exists(self.home)
        data_length = 0
        extra_lease_offset = (self.HEADER_SIZE
                              + 4 * self.LEASE_SIZE
                              + data_length)
        assert extra_lease_offset == self.DATA_OFFSET # true at creation
        num_extra_leases = 0
        f = open(self.home, 'wb')
        header = struct.pack(">32s20s32sQQ",
                             self.MAGIC, my_nodeid, write_enabler,
                             data_length, extra_lease_offset,
                             )
        leases = ("\x00"*self.LEASE_SIZE) * 4
        f.write(header + leases)
        # data goes here, empty after creation
        f.write(struct.pack(">L", num_extra_leases))
        # extra leases go here, none at creation
        f.close()

    def get_used_space(self):
        return get_used_space(FilePath(self.home))

    def unlink(self):
        os.unlink(self.home)

    def get_size(self):
        return os.stat(self.home).st_size

    def _read_data_length(self, f):
        f.seek(self.DATA_LENGTH_OFFSET)
        (data_length,) = struct.unpack(">Q", f.read(8))
        return data_length

    def _read_container_size(self, f):
        f.seek(self.DATA_LENGTH_OFFSET + 8)
        (extra_lease_offset,) = struct.unpack(">Q", f.read(8))
        return extra_lease_offset - self.DATA_OFFSET

    def _write_data_length(self, f, data_length):
        extra_lease_offset = self.DATA_OFFSET + data_length
        f.seek(self.DATA_LENGTH_OFFSET)
        f.write(struct.pack(">QQ", data_length, extra_lease_offset))
        f.seek(extra_lease_offset)
        f.write(struct.pack(">L", 0))

    def _read_share_data(self, f, offset, length):
        precondition(offset >= 0)
        data_length = self._read_data_length(f)
        if offset+length > data_length:
            # reads beyond the end of the data are truncated. Reads that
            # start beyond the end of the data return an empty string.
            length = max(0, data_length-offset)
        if length == 0:
            return ""
        precondition(offset+length <= data_length)
        f.seek(self.DATA_OFFSET+offset)
        data = f.read(length)
        return data

    def _write_share_data(self, f, offset, data):
        length = len(data)
        precondition(offset >= 0)
        data_length = self._read_data_length(f)

        if offset+length >= data_length:
            # They are expanding their data size.

            if offset+length > self.MAX_SIZE:
                raise DataTooLargeError()

            # Their data now fits in the current container. We must write
            # their new data and modify the recorded data size.

            # Fill any newly exposed empty space with 0's.
            if offset > data_length:
                f.seek(self.DATA_OFFSET+data_length)
                f.write('\x00'*(offset - data_length))
                f.flush()

            new_data_length = offset+length
            self._write_data_length(f, new_data_length)
            # an interrupt here will result in a corrupted share

        # now all that's left to do is write out their data
        f.seek(self.DATA_OFFSET+offset)
        f.write(data)
        return

    def _read_write_enabler_and_nodeid(self, f):
        f.seek(0)
        data = f.read(self.HEADER_SIZE)
        (magic,
         write_enabler_nodeid, write_enabler,
         data_length, extra_least_offset) = \
         struct.unpack(">32s20s32sQQ", data)
        assert magic == self.MAGIC
        return (write_enabler, write_enabler_nodeid)

    def readv(self, readv):
        datav = []
        f = open(self.home, 'rb')
        for (offset, length) in readv:
            datav.append(self._read_share_data(f, offset, length))
        f.close()
        return datav

    def check_write_enabler(self, write_enabler, si_s):
        f = open(self.home, 'rb+')
        (real_write_enabler, write_enabler_nodeid) = \
                             self._read_write_enabler_and_nodeid(f)
        f.close()
        # avoid a timing attack
        #if write_enabler != real_write_enabler:
        if not constant_time_compare(write_enabler, real_write_enabler):
            # accomodate share migration by reporting the nodeid used for the
            # old write enabler.
            self.log(format="bad write enabler on SI %(si)s,"
                     " recorded by nodeid %(nodeid)s",
                     facility="tahoe.storage",
                     level=log.WEIRD, umid="cE1eBQ",
                     si=si_s, nodeid=idlib.nodeid_b2a(write_enabler_nodeid))
            msg = "The write enabler was recorded by nodeid '%s'." % \
                  (idlib.nodeid_b2a(write_enabler_nodeid),)
            raise BadWriteEnablerError(msg)

    def check_testv(self, testv):
        test_good = True
        f = open(self.home, 'rb+')
        for (offset, length, operator, specimen) in testv:
            data = self._read_share_data(f, offset, length)
            if not testv_compare(data, operator, specimen):
                test_good = False
                break
        f.close()
        return test_good

    def writev(self, datav, new_length):
        f = open(self.home, 'rb+')
        for (offset, data) in datav:
            self._write_share_data(f, offset, data)
        if new_length is not None:
            cur_length = self._read_data_length(f)
            if new_length < cur_length:
                self._write_data_length(f, new_length)
                # TODO: shrink the share file.
        f.close()

def testv_compare(a, op, b):
    assert op in ("lt", "le", "eq", "ne", "ge", "gt")
    if op == "lt":
        return a < b
    if op == "le":
        return a <= b
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if op == "ge":
        return a >= b
    if op == "gt":
        return a > b
    # never reached

class EmptyShare:

    def check_testv(self, testv):
        test_good = True
        for (offset, length, operator, specimen) in testv:
            data = ""
            if not testv_compare(data, operator, specimen):
                test_good = False
                break
        return test_good

def create_mutable_sharefile(filename, my_nodeid, write_enabler, parent):
    ms = MutableShareFile(filename, parent)
    ms.create(my_nodeid, write_enabler)
    del ms
    return MutableShareFile(filename, parent)

