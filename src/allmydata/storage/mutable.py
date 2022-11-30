"""
Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os, stat, struct

from allmydata.interfaces import (
    BadWriteEnablerError,
    NoSpace,
)
from allmydata.util import idlib, log
from allmydata.util.assertutil import precondition
from allmydata.util.hashutil import timing_safe_compare
from allmydata.storage.lease import LeaseInfo
from allmydata.storage.common import UnknownMutableContainerVersionError, \
     DataTooLargeError
from allmydata.mutable.layout import MAX_MUTABLE_SHARE_SIZE
from .mutable_schema import (
    NEWEST_SCHEMA_VERSION,
    schema_from_header,
)

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

class MutableShareFile(object):

    sharetype = "mutable"
    DATA_LENGTH_OFFSET = struct.calcsize(">32s20s32s")
    EXTRA_LEASE_OFFSET = DATA_LENGTH_OFFSET + 8
    HEADER_SIZE = struct.calcsize(">32s20s32sQQ") # doesn't include leases
    LEASE_SIZE = struct.calcsize(">LL32s32s20s")
    assert LEASE_SIZE == 92
    DATA_OFFSET = HEADER_SIZE + 4*LEASE_SIZE
    assert DATA_OFFSET == 468, DATA_OFFSET
    # our sharefiles share with a recognizable string, plus some random
    # binary data to reduce the chance that a regular text file will look
    # like a sharefile.
    MAX_SIZE = MAX_MUTABLE_SHARE_SIZE
    # TODO: decide upon a policy for max share size

    @classmethod
    def is_valid_header(cls, header):
        # type: (bytes) -> bool
        """
        Determine if the given bytes constitute a valid header for this type of
        container.

        :param header: Some bytes from the beginning of a container.

        :return: ``True`` if the bytes could belong to this container,
            ``False`` otherwise.
        """
        return schema_from_header(header) is not None

    def __init__(self, filename, parent=None, schema=NEWEST_SCHEMA_VERSION):
        self.home = filename
        if os.path.exists(self.home):
            # we don't cache anything, just check the magic
            with open(self.home, 'rb') as f:
                header = f.read(self.HEADER_SIZE)
            self._schema = schema_from_header(header)
            if self._schema is None:
                raise UnknownMutableContainerVersionError(filename, header)
        else:
            self._schema = schema
        self.parent = parent # for logging

    def log(self, *args, **kwargs):
        return self.parent.log(*args, **kwargs)

    def create(self, my_nodeid, write_enabler):
        assert not os.path.exists(self.home)
        with open(self.home, 'wb') as f:
            f.write(self._schema.header(my_nodeid, write_enabler))

    def unlink(self):
        os.unlink(self.home)

    def _read_data_length(self, f):
        f.seek(self.DATA_LENGTH_OFFSET)
        (data_length,) = struct.unpack(">Q", f.read(8))
        return data_length

    def _write_data_length(self, f, data_length):
        f.seek(self.DATA_LENGTH_OFFSET)
        f.write(struct.pack(">Q", data_length))

    def _read_share_data(self, f, offset, length):
        precondition(offset >= 0)
        precondition(length >= 0)
        data_length = self._read_data_length(f)
        if offset+length > data_length:
            # reads beyond the end of the data are truncated. Reads that
            # start beyond the end of the data return an empty string.
            length = max(0, data_length-offset)
        if length == 0:
            return b""
        precondition(offset+length <= data_length)
        f.seek(self.DATA_OFFSET+offset)
        data = f.read(length)
        return data

    def _read_extra_lease_offset(self, f):
        f.seek(self.EXTRA_LEASE_OFFSET)
        (extra_lease_offset,) = struct.unpack(">Q", f.read(8))
        return extra_lease_offset

    def _write_extra_lease_offset(self, f, offset):
        f.seek(self.EXTRA_LEASE_OFFSET)
        f.write(struct.pack(">Q", offset))

    def _read_num_extra_leases(self, f):
        offset = self._read_extra_lease_offset(f)
        f.seek(offset)
        (num_extra_leases,) = struct.unpack(">L", f.read(4))
        return num_extra_leases

    def _write_num_extra_leases(self, f, num_leases):
        extra_lease_offset = self._read_extra_lease_offset(f)
        f.seek(extra_lease_offset)
        f.write(struct.pack(">L", num_leases))

    def _change_container_size(self, f, new_container_size):
        if new_container_size > self.MAX_SIZE:
            raise DataTooLargeError()
        old_extra_lease_offset = self._read_extra_lease_offset(f)
        new_extra_lease_offset = self.DATA_OFFSET + new_container_size
        if new_extra_lease_offset < old_extra_lease_offset:
            # TODO: allow containers to shrink. For now they remain large.
            return
        num_extra_leases = self._read_num_extra_leases(f)
        f.seek(old_extra_lease_offset)
        leases_size = 4 + num_extra_leases * self.LEASE_SIZE
        extra_lease_data = f.read(leases_size)

        # Zero out the old lease info (in order to minimize the chance that
        # it could accidentally be exposed to a reader later, re #1528).
        f.seek(old_extra_lease_offset)
        f.write(b'\x00' * leases_size)
        f.flush()

        # An interrupt here will corrupt the leases.

        f.seek(new_extra_lease_offset)
        f.write(extra_lease_data)
        self._write_extra_lease_offset(f, new_extra_lease_offset)

    def _write_share_data(self, f, offset, data):
        length = len(data)
        precondition(offset >= 0)
        data_length = self._read_data_length(f)
        extra_lease_offset = self._read_extra_lease_offset(f)

        if offset+length >= data_length:
            # They are expanding their data size.

            if self.DATA_OFFSET+offset+length > extra_lease_offset:
                # TODO: allow containers to shrink. For now, they remain
                # large.

                # Their new data won't fit in the current container, so we
                # have to move the leases. With luck, they're expanding it
                # more than the size of the extra lease block, which will
                # minimize the corrupt-the-share window
                self._change_container_size(f, offset+length)
                extra_lease_offset = self._read_extra_lease_offset(f)

                # an interrupt here is ok.. the container has been enlarged
                # but the data remains untouched

            assert self.DATA_OFFSET+offset+length <= extra_lease_offset
            # Their data now fits in the current container. We must write
            # their new data and modify the recorded data size.

            # Fill any newly exposed empty space with 0's.
            if offset > data_length:
                f.seek(self.DATA_OFFSET+data_length)
                f.write(b'\x00'*(offset - data_length))
                f.flush()

            new_data_length = offset+length
            self._write_data_length(f, new_data_length)
            # an interrupt here will result in a corrupted share

        # now all that's left to do is write out their data
        f.seek(self.DATA_OFFSET+offset)
        f.write(data)
        return

    def _write_lease_record(self, f, lease_number, lease_info):
        extra_lease_offset = self._read_extra_lease_offset(f)
        num_extra_leases = self._read_num_extra_leases(f)
        if lease_number < 4:
            offset = self.HEADER_SIZE + lease_number * self.LEASE_SIZE
        elif (lease_number-4) < num_extra_leases:
            offset = (extra_lease_offset
                      + 4
                      + (lease_number-4)*self.LEASE_SIZE)
        else:
            # must add an extra lease record
            self._write_num_extra_leases(f, num_extra_leases+1)
            offset = (extra_lease_offset
                      + 4
                      + (lease_number-4)*self.LEASE_SIZE)
        f.seek(offset)
        assert f.tell() == offset
        f.write(self._schema.lease_serializer.serialize(lease_info))

    def _read_lease_record(self, f, lease_number):
        # returns a LeaseInfo instance, or None
        extra_lease_offset = self._read_extra_lease_offset(f)
        num_extra_leases = self._read_num_extra_leases(f)
        if lease_number < 4:
            offset = self.HEADER_SIZE + lease_number * self.LEASE_SIZE
        elif (lease_number-4) < num_extra_leases:
            offset = (extra_lease_offset
                      + 4
                      + (lease_number-4)*self.LEASE_SIZE)
        else:
            raise IndexError("No such lease number %d" % lease_number)
        f.seek(offset)
        assert f.tell() == offset
        data = f.read(self.LEASE_SIZE)
        lease_info = self._schema.lease_serializer.unserialize(data)
        if lease_info.owner_num == 0:
            return None
        return lease_info

    def _get_num_lease_slots(self, f):
        # how many places do we have allocated for leases? Not all of them
        # are filled.
        num_extra_leases = self._read_num_extra_leases(f)
        return 4+num_extra_leases

    def _get_first_empty_lease_slot(self, f):
        # return an int with the index of an empty slot, or None if we do not
        # currently have an empty slot

        for i in range(self._get_num_lease_slots(f)):
            if self._read_lease_record(f, i) is None:
                return i
        return None

    def get_leases(self):
        """Yields a LeaseInfo instance for all leases."""
        with open(self.home, 'rb') as f:
            for i, lease in self._enumerate_leases(f):
                yield lease

    def _enumerate_leases(self, f):
        for i in range(self._get_num_lease_slots(f)):
            try:
                data = self._read_lease_record(f, i)
                if data is not None:
                    yield i,data
            except IndexError:
                return

    def add_lease(self, available_space, lease_info):
        """
        Add a new lease to this share.

        :param int available_space: The maximum number of bytes of storage to
            commit in this operation.  If more than this number of bytes is
            required, raise ``NoSpace`` instead.

        :raise NoSpace: If more than ``available_space`` bytes is required to
            complete the operation.  In this case, no lease is added.

        :return: ``None``
        """
        precondition(lease_info.owner_num != 0) # 0 means "no lease here"
        with open(self.home, 'rb+') as f:
            num_lease_slots = self._get_num_lease_slots(f)
            empty_slot = self._get_first_empty_lease_slot(f)
            if empty_slot is not None:
                self._write_lease_record(f, empty_slot, lease_info)
            else:
                if lease_info.mutable_size() > available_space:
                    raise NoSpace()
                self._write_lease_record(f, num_lease_slots, lease_info)

    def renew_lease(self, renew_secret, new_expire_time, allow_backdate=False):
        # type: (bytes, int, bool) -> None
        """
        Update the expiration time on an existing lease.

        :param allow_backdate: If ``True`` then allow the new expiration time
            to be before the current expiration time.  Otherwise, make no
            change when this is the case.

        :raise IndexError: If there is no lease matching the given renew
            secret.
        """
        accepting_nodeids = set()
        with open(self.home, 'rb+') as f:
            for (leasenum,lease) in self._enumerate_leases(f):
                if lease.is_renew_secret(renew_secret):
                    # yup. See if we need to update the owner time.
                    if allow_backdate or new_expire_time > lease.get_expiration_time():
                        # yes
                        lease = lease.renew(new_expire_time)
                        self._write_lease_record(f, leasenum, lease)
                    return
                accepting_nodeids.add(lease.nodeid)
        # Return the accepting_nodeids set, to give the client a chance to
        # update the leases on a share which has been migrated from its
        # original server to a new one.
        msg = ("Unable to renew non-existent lease. I have leases accepted by"
               " nodeids: ")
        msg += ",".join([("'%s'" % idlib.nodeid_b2a(anid))
                         for anid in accepting_nodeids])
        msg += " ."
        raise IndexError(msg)

    def add_or_renew_lease(self, available_space, lease_info):
        precondition(lease_info.owner_num != 0) # 0 means "no lease here"
        try:
            self.renew_lease(lease_info.renew_secret,
                             lease_info.get_expiration_time())
        except IndexError:
            self.add_lease(available_space, lease_info)

    def cancel_lease(self, cancel_secret):
        """Remove any leases with the given cancel_secret. If the last lease
        is cancelled, the file will be removed. Return the number of bytes
        that were freed (by truncating the list of leases, and possibly by
        deleting the file. Raise IndexError if there was no lease with the
        given cancel_secret."""

        accepting_nodeids = set()
        modified = 0
        remaining = 0
        blank_lease = LeaseInfo(owner_num=0,
                                renew_secret=b"\x00"*32,
                                cancel_secret=b"\x00"*32,
                                expiration_time=0,
                                nodeid=b"\x00"*20)
        with open(self.home, 'rb+') as f:
            for (leasenum,lease) in self._enumerate_leases(f):
                accepting_nodeids.add(lease.nodeid)
                if lease.is_cancel_secret(cancel_secret):
                    self._write_lease_record(f, leasenum, blank_lease)
                    modified += 1
                else:
                    remaining += 1
            if modified:
                freed_space = self._pack_leases(f)
                f.close()
                if not remaining:
                    freed_space += os.stat(self.home)[stat.ST_SIZE]
                    self.unlink()
                return freed_space

        msg = ("Unable to cancel non-existent lease. I have leases "
               "accepted by nodeids: ")
        msg += ",".join([("'%s'" % idlib.nodeid_b2a(anid))
                         for anid in accepting_nodeids])
        msg += " ."
        raise IndexError(msg)

    def _pack_leases(self, f):
        # TODO: reclaim space from cancelled leases
        return 0

    def _read_write_enabler_and_nodeid(self, f):
        f.seek(0)
        data = f.read(self.HEADER_SIZE)
        (magic,
         write_enabler_nodeid, write_enabler,
         data_length, extra_least_offset) = \
         struct.unpack(">32s20s32sQQ", data)
        assert self.is_valid_header(data)
        return (write_enabler, write_enabler_nodeid)

    def readv(self, readv):
        datav = []
        with open(self.home, 'rb') as f:
            for (offset, length) in readv:
                datav.append(self._read_share_data(f, offset, length))
        return datav

    def get_length(self):
        """
        Return the length of the data in the share.
        """
        f = open(self.home, 'rb')
        data_length = self._read_data_length(f)
        f.close()
        return data_length

    def check_write_enabler(self, write_enabler, si_s):
        with open(self.home, 'rb+') as f:
            (real_write_enabler, write_enabler_nodeid) = \
                                 self._read_write_enabler_and_nodeid(f)
        # avoid a timing attack
        #if write_enabler != real_write_enabler:
        if not timing_safe_compare(write_enabler, real_write_enabler):
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
        with open(self.home, 'rb+') as f:
            for (offset, length, operator, specimen) in testv:
                data = self._read_share_data(f, offset, length)
                if not testv_compare(data, operator, specimen):
                    test_good = False
                    break
        return test_good

    def writev(self, datav, new_length):
        with open(self.home, 'rb+') as f:
            for (offset, data) in datav:
                self._write_share_data(f, offset, data)
            if new_length is not None:
                cur_length = self._read_data_length(f)
                if new_length < cur_length:
                    self._write_data_length(f, new_length)
                    # TODO: if we're going to shrink the share file when the
                    # share data has shrunk, then call
                    # self._change_container_size() here.

def testv_compare(a, op, b):
    assert op == b"eq"
    return a == b


class EmptyShare(object):

    def check_testv(self, testv):
        test_good = True
        for (offset, length, operator, specimen) in testv:
            data = b""
            if not testv_compare(data, operator, specimen):
                test_good = False
                break
        return test_good

def create_mutable_sharefile(filename, my_nodeid, write_enabler, parent):
    ms = MutableShareFile(filename, parent)
    ms.create(my_nodeid, write_enabler)
    del ms
    return MutableShareFile(filename, parent)
