import os, re, weakref, stat, struct, time
from distutils.version import LooseVersion

from foolscap import Referenceable
from twisted.application import service
from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import RIStorageServer, RIBucketWriter, \
     RIBucketReader, IStorageBucketWriter, IStorageBucketReader, HASH_SIZE, \
     BadWriteEnablerError, IStatsProducer, FileTooLargeError
from allmydata.util import base32, fileutil, idlib, mathutil, log
from allmydata.util.assertutil import precondition, _assert
import allmydata # for __version__

class DataTooLargeError(Exception):
    pass

# storage/
# storage/shares/incoming
#   incoming/ holds temp dirs named $START/$STORAGEINDEX/$SHARENUM which will
#   be moved to storage/shares/$START/$STORAGEINDEX/$SHARENUM upon success
# storage/shares/$START/$STORAGEINDEX
# storage/shares/$START/$STORAGEINDEX/$SHARENUM

# Where "$START" denotes the first 10 bits worth of $STORAGEINDEX (that's 2
# base-32 chars).

# $SHARENUM matches this regex:
NUM_RE=re.compile("^[0-9]+$")

# each share file (in storage/shares/$SI/$SHNUM) contains lease information
# and share data. The share data is accessed by RIBucketWriter.write and
# RIBucketReader.read . The lease information is not accessible through these
# interfaces.

# The share file has the following layout:
#  0x00: share file version number, four bytes, current version is 1
#  0x04: share data length, four bytes big-endian = A
#  0x08: number of leases, four bytes big-endian
#  0x0c: beginning of share data (described below, at WriteBucketProxy)
#  A+0x0c = B: first lease. Lease format is:
#   B+0x00: owner number, 4 bytes big-endian, 0 is reserved for no-owner
#   B+0x04: renew secret, 32 bytes (SHA256)
#   B+0x24: cancel secret, 32 bytes (SHA256)
#   B+0x44: expiration time, 4 bytes big-endian seconds-since-epoch
#   B+0x48: next lease, or end of record

def si_b2a(storageindex):
    return base32.b2a(storageindex)

def si_a2b(ascii_storageindex):
    return base32.a2b(ascii_storageindex)

def storage_index_to_dir(storageindex):
    sia = si_b2a(storageindex)
    return os.path.join(sia[:2], sia)

class ShareFile:
    LEASE_SIZE = struct.calcsize(">L32s32sL")

    def __init__(self, filename):
        self.home = filename
        f = open(self.home, 'rb')
        (version, size, num_leases) = struct.unpack(">LLL", f.read(0xc))
        assert version == 1
        self._size = size
        self._num_leases = num_leases
        self._data_offset = 0xc
        self._lease_offset = 0xc + self._size

    def read_share_data(self, offset, length):
        precondition(offset >= 0)
        precondition(offset+length <= self._size)
        f = open(self.home, 'rb')
        f.seek(self._data_offset+offset)
        return f.read(length)

    def write_share_data(self, offset, data):
        length = len(data)
        precondition(offset >= 0)
        precondition(offset+length <= self._size)
        f = open(self.home, 'rb+')
        real_offset = self._data_offset+offset
        f.seek(real_offset)
        assert f.tell() == real_offset
        f.write(data)
        f.close()

    def _write_lease_record(self, f, lease_number, lease_info):
        (owner_num, renew_secret, cancel_secret, expiration_time) = lease_info
        offset = self._lease_offset + lease_number * self.LEASE_SIZE
        f.seek(offset)
        assert f.tell() == offset
        f.write(struct.pack(">L32s32sL",
                            owner_num, renew_secret, cancel_secret,
                            int(expiration_time)))

    def _read_num_leases(self, f):
        f.seek(0x08)
        (num_leases,) = struct.unpack(">L", f.read(4))
        return num_leases

    def _write_num_leases(self, f, num_leases):
        f.seek(0x08)
        f.write(struct.pack(">L", num_leases))

    def _truncate_leases(self, f, num_leases):
        f.truncate(self._lease_offset + num_leases * self.LEASE_SIZE)

    def iter_leases(self):
        """Yields (ownernum, renew_secret, cancel_secret, expiration_time)
        for all leases."""
        f = open(self.home, 'rb')
        (version, size, num_leases) = struct.unpack(">LLL", f.read(0xc))
        f.seek(self._lease_offset)
        for i in range(num_leases):
            data = f.read(self.LEASE_SIZE)
            if data:
                yield struct.unpack(">L32s32sL", data)

    def add_lease(self, lease_info):
        f = open(self.home, 'rb+')
        num_leases = self._read_num_leases(f)
        self._write_lease_record(f, num_leases, lease_info)
        self._write_num_leases(f, num_leases+1)
        f.close()

    def renew_lease(self, renew_secret, new_expire_time):
        for i,(on,rs,cs,et) in enumerate(self.iter_leases()):
            if rs == renew_secret:
                # yup. See if we need to update the owner time.
                if new_expire_time > et:
                    # yes
                    new_lease = (on,rs,cs,new_expire_time)
                    f = open(self.home, 'rb+')
                    self._write_lease_record(f, i, new_lease)
                    f.close()
                return
        raise IndexError("unable to renew non-existent lease")

    def add_or_renew_lease(self, lease_info):
        owner_num, renew_secret, cancel_secret, expire_time = lease_info
        try:
            self.renew_lease(renew_secret, expire_time)
        except IndexError:
            self.add_lease(lease_info)

    def cancel_lease(self, cancel_secret):
        """Remove a lease with the given cancel_secret. Return
        (num_remaining_leases, space_freed). Raise IndexError if there was no
        lease with the given cancel_secret."""

        leases = list(self.iter_leases())
        num_leases = len(leases)
        num_leases_removed = 0
        for i,lease_info in enumerate(leases[:]):
            (on,rs,cs,et) = lease_info
            if cs == cancel_secret:
                leases[i] = None
                num_leases_removed += 1
        if not num_leases_removed:
            raise IndexError("unable to find matching lease to cancel")
        if num_leases_removed:
            # pack and write out the remaining leases. We write these out in
            # the same order as they were added, so that if we crash while
            # doing this, we won't lose any non-cancelled leases.
            leases = [l for l in leases if l] # remove the cancelled leases
            f = open(self.home, 'rb+')
            for i,lease in enumerate(leases):
                self._write_lease_record(f, i, lease)
            self._write_num_leases(f, len(leases))
            self._truncate_leases(f, len(leases))
            f.close()
        return len(leases), self.LEASE_SIZE * num_leases_removed


class BucketWriter(Referenceable):
    implements(RIBucketWriter)

    def __init__(self, ss, incominghome, finalhome, size, lease_info, canary):
        self.ss = ss
        self.incominghome = incominghome
        self.finalhome = finalhome
        self._size = size
        self._canary = canary
        self._disconnect_marker = canary.notifyOnDisconnect(self._disconnected)
        self.closed = False
        self.throw_out_all_data = False
        # touch the file, so later callers will see that we're working on it.
        assert not os.path.exists(incominghome)
        fileutil.make_dirs(os.path.dirname(incominghome))
        # Also construct the metadata.
        f = open(incominghome, 'wb')
        f.write(struct.pack(">LLL", 1, size, 0))
        f.close()
        self._sharefile = ShareFile(incominghome)
        # also, add our lease to the file now, so that other ones can be
        # added by simultaneous uploaders
        self._sharefile.add_lease(lease_info)

    def allocated_size(self):
        return self._size

    def remote_write(self, offset, data):
        precondition(not self.closed)
        if self.throw_out_all_data:
            return
        self._sharefile.write_share_data(offset, data)

    def remote_close(self):
        precondition(not self.closed)

        fileutil.make_dirs(os.path.dirname(self.finalhome))
        fileutil.rename(self.incominghome, self.finalhome)
        try:
            os.rmdir(os.path.dirname(self.incominghome))
            os.rmdir(os.path.dirname(os.path.dirname(self.incominghome)))
            os.rmdir(os.path.dirname(os.path.dirname(os.path.dirname(self.incominghome))))
        except EnvironmentError:
            pass
        self._sharefile = None
        self.closed = True
        self._canary.dontNotifyOnDisconnect(self._disconnect_marker)

        filelen = os.stat(self.finalhome)[stat.ST_SIZE]
        self.ss.bucket_writer_closed(self, filelen)

    def _disconnected(self):
        if not self.closed:
            self._abort()

    def remote_abort(self):
        log.msg("storage: aborting sharefile %s" % self.incominghome,
                facility="tahoe.storage", level=log.UNUSUAL)
        if not self.closed:
            self._canary.dontNotifyOnDisconnect(self._disconnect_marker)
        self._abort()

    def _abort(self):
        if self.closed:
            return
        os.remove(self.incominghome)
        # if we were the last share to be moved, remove the incoming/
        # directory that was our parent
        parentdir = os.path.split(self.incominghome)[0]
        if not os.listdir(parentdir):
            os.rmdir(parentdir)



class BucketReader(Referenceable):
    implements(RIBucketReader)

    def __init__(self, sharefname):
        self._share_file = ShareFile(sharefname)

    def remote_read(self, offset, length):
        return self._share_file.read_share_data(offset, length)


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


assert struct.calcsize("L"), 4
assert struct.calcsize("Q"), 8

class MutableShareFile:

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
    MAGIC = "Tahoe mutable container v1\n" + "\x75\x09\x44\x03\x8e"
    assert len(MAGIC) == 32
    MAX_SIZE = 2*1000*1000*1000 # 2GB, kind of arbitrary
    # TODO: decide upon a policy for max share size

    def __init__(self, filename, parent=None):
        self.home = filename
        if os.path.exists(self.home):
            # we don't cache anything, just check the magic
            f = open(self.home, 'rb')
            data = f.read(self.HEADER_SIZE)
            (magic,
             write_enabler_nodeid, write_enabler,
             data_length, extra_least_offset) = \
             struct.unpack(">32s20s32sQQ", data)
            assert magic == self.MAGIC
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

    def _read_data_length(self, f):
        f.seek(self.DATA_LENGTH_OFFSET)
        (data_length,) = struct.unpack(">Q", f.read(8))
        return data_length

    def _write_data_length(self, f, data_length):
        f.seek(self.DATA_LENGTH_OFFSET)
        f.write(struct.pack(">Q", data_length))

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
        extra_lease_data = f.read(4 + num_extra_leases * self.LEASE_SIZE)
        f.seek(new_extra_lease_offset)
        f.write(extra_lease_data)
        # an interrupt here will corrupt the leases, iff the move caused the
        # extra leases to overlap.
        self._write_extra_lease_offset(f, new_extra_lease_offset)

    def _write_share_data(self, f, offset, data):
        length = len(data)
        precondition(offset >= 0)
        data_length = self._read_data_length(f)
        extra_lease_offset = self._read_extra_lease_offset(f)

        if offset+length >= data_length:
            # They are expanding their data size.
            if self.DATA_OFFSET+offset+length > extra_lease_offset:
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
            new_data_length = offset+length
            self._write_data_length(f, new_data_length)
            # an interrupt here will result in a corrupted share

        # now all that's left to do is write out their data
        f.seek(self.DATA_OFFSET+offset)
        f.write(data)
        return

    def _write_lease_record(self, f, lease_number, lease_info):
        (ownerid, expiration_time,
         renew_secret, cancel_secret, nodeid) = lease_info
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
        f.write(struct.pack(">LL32s32s20s",
                            ownerid, int(expiration_time),
                            renew_secret, cancel_secret, nodeid))

    def _read_lease_record(self, f, lease_number):
        # returns a 5-tuple of lease info, or None
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
        lease_info = struct.unpack(">LL32s32s20s", data)
        (ownerid, expiration_time,
         renew_secret, cancel_secret, nodeid) = lease_info
        if ownerid == 0:
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

    def _enumerate_leases(self, f):
        """Yields (leasenum, (ownerid, expiration_time, renew_secret,
        cancel_secret, accepting_nodeid)) for all leases."""
        for i in range(self._get_num_lease_slots(f)):
            try:
                data = self._read_lease_record(f, i)
                if data is not None:
                    yield (i,data)
            except IndexError:
                return

    def debug_get_leases(self):
        f = open(self.home, 'rb')
        leases = list(self._enumerate_leases(f))
        f.close()
        return leases

    def add_lease(self, lease_info):
        f = open(self.home, 'rb+')
        num_lease_slots = self._get_num_lease_slots(f)
        empty_slot = self._get_first_empty_lease_slot(f)
        if empty_slot is not None:
            self._write_lease_record(f, empty_slot, lease_info)
        else:
            self._write_lease_record(f, num_lease_slots, lease_info)
        f.close()

    def renew_lease(self, renew_secret, new_expire_time):
        accepting_nodeids = set()
        f = open(self.home, 'rb+')
        for (leasenum,(oid,et,rs,cs,anid)) in self._enumerate_leases(f):
            if rs == renew_secret:
                # yup. See if we need to update the owner time.
                if new_expire_time > et:
                    # yes
                    new_lease = (oid,new_expire_time,rs,cs,anid)
                    self._write_lease_record(f, leasenum, new_lease)
                f.close()
                return
            accepting_nodeids.add(anid)
        f.close()
        # Return the accepting_nodeids set, to give the client a chance to
        # update the leases on a share which has been migrated from its
        # original server to a new one.
        msg = ("Unable to renew non-existent lease. I have leases accepted by"
               " nodeids: ")
        msg += ",".join([("'%s'" % idlib.nodeid_b2a(anid))
                         for anid in accepting_nodeids])
        msg += " ."
        raise IndexError(msg)

    def add_or_renew_lease(self, lease_info):
        ownerid, expire_time, renew_secret, cancel_secret, anid = lease_info
        try:
            self.renew_lease(renew_secret, expire_time)
        except IndexError:
            self.add_lease(lease_info)

    def cancel_lease(self, cancel_secret):
        """Remove any leases with the given cancel_secret. Return
        (num_remaining_leases, space_freed). Raise IndexError if there was no
        lease with the given cancel_secret."""

        accepting_nodeids = set()
        modified = 0
        remaining = 0
        blank_lease = (0, 0, "\x00"*32, "\x00"*32, "\x00"*20)
        f = open(self.home, 'rb+')
        for (leasenum,(oid,et,rs,cs,anid)) in self._enumerate_leases(f):
            accepting_nodeids.add(anid)
            if cs == cancel_secret:
                self._write_lease_record(f, leasenum, blank_lease)
                modified += 1
            else:
                remaining += 1
        if modified:
            freed_space = self._pack_leases(f)
            f.close()
            return (remaining, freed_space)
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
        assert magic == self.MAGIC
        return (write_enabler, write_enabler_nodeid)

    def readv(self, readv):
        datav = []
        f = open(self.home, 'rb')
        for (offset, length) in readv:
            datav.append(self._read_share_data(f, offset, length))
        f.close()
        return datav

#    def remote_get_length(self):
#        f = open(self.home, 'rb')
#        data_length = self._read_data_length(f)
#        f.close()
#        return data_length

    def check_write_enabler(self, write_enabler, si_s):
        f = open(self.home, 'rb+')
        (real_write_enabler, write_enabler_nodeid) = \
                             self._read_write_enabler_and_nodeid(f)
        f.close()
        if write_enabler != real_write_enabler:
            # accomodate share migration by reporting the nodeid used for the
            # old write enabler.
            self.log(format="bad write enabler on SI %(si)s,"
                     " recorded by nodeid %(nodeid)s",
                     facility="tahoe.storage",
                     level=log.WEIRD,
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
            self._change_container_size(f, new_length)
            f.seek(self.DATA_LENGTH_OFFSET)
            f.write(struct.pack(">Q", new_length))
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


class StorageServer(service.MultiService, Referenceable):
    implements(RIStorageServer, IStatsProducer)
    name = 'storage'

    OLDEST_SUPPORTED_VERSION = LooseVersion("0.8.0")

    def __init__(self, storedir, sizelimit=None,
                 discard_storage=False, readonly_storage=False,
                 stats_provider=None):
        service.MultiService.__init__(self)
        self.storedir = storedir
        sharedir = os.path.join(storedir, "shares")
        fileutil.make_dirs(sharedir)
        self.sharedir = sharedir
        self.sizelimit = sizelimit
        self.no_storage = discard_storage
        self.readonly_storage = readonly_storage
        self.stats_provider = stats_provider
        if self.stats_provider:
            self.stats_provider.register_producer(self)
        self.incomingdir = os.path.join(sharedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)
        self._active_writers = weakref.WeakKeyDictionary()
        lp = log.msg("StorageServer created, now measuring space..",
                     facility="tahoe.storage")
        self.consumed = None
        if self.sizelimit:
            self.consumed = fileutil.du(self.sharedir)
            log.msg(format="space measurement done, consumed=%(consumed)d bytes",
                    consumed=self.consumed,
                    parent=lp, facility="tahoe.storage")

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.storage"
        return log.msg(*args, **kwargs)

    def setNodeID(self, nodeid):
        # somebody must set this before any slots can be created or leases
        # added
        self.my_nodeid = nodeid

    def startService(self):
        service.MultiService.startService(self)
        if self.parent:
            nodeid = self.parent.nodeid # 20 bytes, binary
            assert len(nodeid) == 20
            self.setNodeID(nodeid)

    def _clean_incomplete(self):
        fileutil.rm_dir(self.incomingdir)

    def get_stats(self):
        stats = { 'storage_server.allocated': self.allocated_size(), }
        if self.consumed is not None:
            stats['storage_server.consumed'] = self.consumed
        return stats

    def allocated_size(self):
        space = self.consumed or 0
        for bw in self._active_writers:
            space += bw.allocated_size()
        return space

    def remote_get_versions(self):
        return (str(allmydata.__version__), str(self.OLDEST_SUPPORTED_VERSION))

    def remote_allocate_buckets(self, storage_index,
                                renew_secret, cancel_secret,
                                sharenums, allocated_size,
                                canary, owner_num=0):
        # owner_num is not for clients to set, but rather it should be
        # curried into the PersonalStorageServer instance that is dedicated
        # to a particular owner.
        alreadygot = set()
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_dir = storage_index_to_dir(storage_index)
        si_s = si_b2a(storage_index)

        log.msg("storage: allocate_buckets %s" % si_s)

        # in this implementation, the lease information (including secrets)
        # goes into the share files themselves. It could also be put into a
        # separate database. Note that the lease should not be added until
        # the BucketWrite has been closed.
        expire_time = time.time() + 31*24*60*60
        lease_info = (owner_num, renew_secret, cancel_secret, expire_time)

        space_per_bucket = allocated_size
        no_limits = self.sizelimit is None
        yes_limits = not no_limits
        if yes_limits:
            remaining_space = self.sizelimit - self.allocated_size()

        # fill alreadygot with all shares that we have, not just the ones
        # they asked about: this will save them a lot of work. Add or update
        # leases for all of them: if they want us to hold shares for this
        # file, they'll want us to hold leases for this file.
        for (shnum, fn) in self._get_bucket_shares(storage_index):
            alreadygot.add(shnum)
            sf = ShareFile(fn)
            sf.add_or_renew_lease(lease_info)

        if self.readonly_storage:
            # we won't accept new shares
            return alreadygot, bucketwriters

        for shnum in sharenums:
            incominghome = os.path.join(self.incomingdir, si_dir, "%d" % shnum)
            finalhome = os.path.join(self.sharedir, si_dir, "%d" % shnum)
            if os.path.exists(finalhome):
                # great! we already have it. easy.
                pass
            elif os.path.exists(incominghome):
                # Note that we don't create BucketWriters for shnums that
                # have a partial share (in incoming/), so if a second upload
                # occurs while the first is still in progress, the second
                # uploader will use different storage servers.
                pass
            elif no_limits or remaining_space >= space_per_bucket:
                # ok! we need to create the new share file.
                bw = BucketWriter(self, incominghome, finalhome,
                                  space_per_bucket, lease_info, canary)
                if self.no_storage:
                    bw.throw_out_all_data = True
                bucketwriters[shnum] = bw
                self._active_writers[bw] = 1
                if yes_limits:
                    remaining_space -= space_per_bucket
            else:
                # bummer! not enough space to accept this bucket
                pass

        if bucketwriters:
            fileutil.make_dirs(os.path.join(self.sharedir, si_dir))

        return alreadygot, bucketwriters

    def remote_renew_lease(self, storage_index, renew_secret):
        new_expire_time = time.time() + 31*24*60*60
        found_buckets = False
        for shnum, filename in self._get_bucket_shares(storage_index):
            found_buckets = True
            f = open(filename, 'rb')
            header = f.read(32)
            f.close()
            if header[:32] == MutableShareFile.MAGIC:
                sf = MutableShareFile(filename, self)
                # note: if the share has been migrated, the renew_lease()
                # call will throw an exception, with information to help the
                # client update the lease.
            elif header[:4] == struct.pack(">L", 1):
                sf = ShareFile(filename)
            else:
                pass # non-sharefile
            sf.renew_lease(renew_secret, new_expire_time)
        if not found_buckets:
            raise IndexError("no such lease to renew")

    def remote_cancel_lease(self, storage_index, cancel_secret):
        storagedir = os.path.join(self.sharedir, storage_index_to_dir(storage_index))

        remaining_files = 0
        total_space_freed = 0
        found_buckets = False
        for shnum, filename in self._get_bucket_shares(storage_index):
            # note: if we can't find a lease on one share, we won't bother
            # looking in the others. Unless something broke internally
            # (perhaps we ran out of disk space while adding a lease), the
            # leases on all shares will be identical.
            found_buckets = True
            f = open(filename, 'rb')
            header = f.read(32)
            f.close()
            if header[:32] == MutableShareFile.MAGIC:
                sf = MutableShareFile(filename, self)
                # note: if the share has been migrated, the renew_lease()
                # call will throw an exception, with information to help the
                # client update the lease.
            elif header[:4] == struct.pack(">L", 1):
                sf = ShareFile(filename)
            else:
                pass # non-sharefile
            # this raises IndexError if the lease wasn't present
            remaining_leases, space_freed = sf.cancel_lease(cancel_secret)
            total_space_freed += space_freed
            if remaining_leases:
                remaining_files += 1
            else:
                # now remove the sharefile. We'll almost certainly be
                # removing the entire directory soon.
                filelen = os.stat(filename)[stat.ST_SIZE]
                os.unlink(filename)
                total_space_freed += filelen
        if not remaining_files:
            fileutil.rm_dir(storagedir)
        if self.consumed is not None:
            self.consumed -= total_space_freed
        if self.stats_provider:
            self.stats_provider.count('storage_server.bytes_freed', total_space_freed)
        if not found_buckets:
            raise IndexError("no such lease to cancel")

    def bucket_writer_closed(self, bw, consumed_size):
        if self.consumed is not None:
            self.consumed += consumed_size
        if self.stats_provider:
            self.stats_provider.count('storage_server.bytes_added', consumed_size)
        del self._active_writers[bw]

    def _get_bucket_shares(self, storage_index):
        """Return a list of (shnum, pathname) tuples for files that hold
        shares for this storage_index. In each tuple, 'shnum' will always be
        the integer form of the last component of 'pathname'."""
        storagedir = os.path.join(self.sharedir, storage_index_to_dir(storage_index))
        try:
            for f in os.listdir(storagedir):
                if NUM_RE.match(f):
                    filename = os.path.join(storagedir, f)
                    yield (int(f), filename)
        except OSError:
            # Commonly caused by there being no buckets at all.
            pass

    def _get_incoming_shares(self, storage_index):
        incomingdir = os.path.join(self.incomingdir, storage_index_to_dir(storage_index))
        try:
            for f in os.listdir(incomingdir):
                if NUM_RE.match(f):
                    filename = os.path.join(incomingdir, f)
                    yield (int(f), filename)
        except OSError:
            pass

    def remote_get_buckets(self, storage_index):
        si_s = si_b2a(storage_index)
        log.msg("storage: get_buckets %s" % si_s)
        bucketreaders = {} # k: sharenum, v: BucketReader
        for shnum, filename in self._get_bucket_shares(storage_index):
            bucketreaders[shnum] = BucketReader(filename)
        return bucketreaders

    def get_leases(self, storage_index):
        """Provide an iterator that yields all of the leases attached to this
        bucket. Each lease is returned as a tuple of (owner_num,
        renew_secret, cancel_secret, expiration_time).

        This method is not for client use.
        """

        # since all shares get the same lease data, we just grab the leases
        # from the first share
        try:
            shnum, filename = self._get_bucket_shares(storage_index).next()
            sf = ShareFile(filename)
            return sf.iter_leases()
        except StopIteration:
            return iter([])

    def remote_slot_testv_and_readv_and_writev(self, storage_index,
                                               secrets,
                                               test_and_write_vectors,
                                               read_vector):
        si_s = si_b2a(storage_index)
        lp = log.msg("storage: slot_writev %s" % si_s)
        si_dir = storage_index_to_dir(storage_index)
        (write_enabler, renew_secret, cancel_secret) = secrets
        # shares exist if there is a file for them
        bucketdir = os.path.join(self.sharedir, si_dir)
        shares = {}
        if os.path.isdir(bucketdir):
            for sharenum_s in os.listdir(bucketdir):
                try:
                    sharenum = int(sharenum_s)
                except ValueError:
                    continue
                filename = os.path.join(bucketdir, sharenum_s)
                msf = MutableShareFile(filename, self)
                msf.check_write_enabler(write_enabler, si_s)
                shares[sharenum] = msf
        # write_enabler is good for all existing shares.

        # Now evaluate test vectors.
        testv_is_good = True
        for sharenum in test_and_write_vectors:
            (testv, datav, new_length) = test_and_write_vectors[sharenum]
            if sharenum in shares:
                if not shares[sharenum].check_testv(testv):
                    self.log("testv failed: [%d]: %r" % (sharenum, testv))
                    testv_is_good = False
                    break
            else:
                # compare the vectors against an empty share, in which all
                # reads return empty strings.
                if not EmptyShare().check_testv(testv):
                    self.log("testv failed (empty): [%d] %r" % (sharenum,
                                                                testv))
                    testv_is_good = False
                    break

        # now gather the read vectors, before we do any writes
        read_data = {}
        for sharenum, share in shares.items():
            read_data[sharenum] = share.readv(read_vector)

        if testv_is_good:
            # now apply the write vectors
            for sharenum in test_and_write_vectors:
                (testv, datav, new_length) = test_and_write_vectors[sharenum]
                if sharenum not in shares:
                    # allocate a new share
                    allocated_size = 2000 # arbitrary, really
                    share = self._allocate_slot_share(bucketdir, secrets,
                                                      sharenum,
                                                      allocated_size,
                                                      owner_num=0)
                    shares[sharenum] = share
                shares[sharenum].writev(datav, new_length)
            # and update the leases on all shares
            ownerid = 1 # TODO
            expire_time = time.time() + 31*24*60*60   # one month
            my_nodeid = self.my_nodeid
            anid = my_nodeid
            lease_info = (ownerid, expire_time, renew_secret, cancel_secret,
                          anid)
            for share in shares.values():
                share.add_or_renew_lease(lease_info)

        # all done
        return (testv_is_good, read_data)

    def _allocate_slot_share(self, bucketdir, secrets, sharenum,
                             allocated_size, owner_num=0):
        (write_enabler, renew_secret, cancel_secret) = secrets
        my_nodeid = self.my_nodeid
        fileutil.make_dirs(bucketdir)
        filename = os.path.join(bucketdir, "%d" % sharenum)
        share = create_mutable_sharefile(filename, my_nodeid, write_enabler,
                                         self)
        return share

    def remote_slot_readv(self, storage_index, shares, readv):
        si_s = si_b2a(storage_index)
        lp = log.msg("storage: slot_readv %s %s" % (si_s, shares),
                     facility="tahoe.storage", level=log.OPERATIONAL)
        si_dir = storage_index_to_dir(storage_index)
        # shares exist if there is a file for them
        bucketdir = os.path.join(self.sharedir, si_dir)
        if not os.path.isdir(bucketdir):
            return {}
        datavs = {}
        for sharenum_s in os.listdir(bucketdir):
            try:
                sharenum = int(sharenum_s)
            except ValueError:
                continue
            if sharenum in shares or not shares:
                filename = os.path.join(bucketdir, sharenum_s)
                msf = MutableShareFile(filename, self)
                datavs[sharenum] = msf.readv(readv)
        log.msg("returning shares %s" % (datavs.keys(),),
                facility="tahoe.storage", level=log.NOISY, parent=lp)
        return datavs



# the code before here runs on the storage server, not the client
# the code beyond here runs on the client, not the storage server

"""
Share data is written into a single file. At the start of the file, there is
a series of four-byte big-endian offset values, which indicate where each
section starts. Each offset is measured from the beginning of the file.

0x00: version number (=00 00 00 01)
0x04: segment size
0x08: data size
0x0c: offset of data (=00 00 00 24)
0x10: offset of plaintext_hash_tree
0x14: offset of crypttext_hash_tree
0x18: offset of block_hashes
0x1c: offset of share_hashes
0x20: offset of uri_extension_length + uri_extension
0x24: start of data
?   : start of plaintext_hash_tree
?   : start of crypttext_hash_tree
?   : start of block_hashes
?   : start of share_hashes
       each share_hash is written as a two-byte (big-endian) hashnum
       followed by the 32-byte SHA-256 hash. We only store the hashes
       necessary to validate the share hash root
?   : start of uri_extension_length (four-byte big-endian value)
?   : start of uri_extension
"""

def allocated_size(data_size, num_segments, num_share_hashes,
                   uri_extension_size):
    wbp = WriteBucketProxy(None, data_size, 0, num_segments, num_share_hashes,
                           uri_extension_size, None)
    uri_extension_starts_at = wbp._offsets['uri_extension']
    return uri_extension_starts_at + 4 + uri_extension_size

class WriteBucketProxy:
    implements(IStorageBucketWriter)
    def __init__(self, rref, data_size, segment_size, num_segments,
                 num_share_hashes, uri_extension_size, nodeid):
        self._rref = rref
        self._data_size = data_size
        self._segment_size = segment_size
        self._num_segments = num_segments
        self._nodeid = nodeid

        if segment_size >= 2**32 or data_size >= 2**32:
            raise FileTooLargeError("This file is too large to be uploaded (data_size).")

        effective_segments = mathutil.next_power_of_k(num_segments,2)
        self._segment_hash_size = (2*effective_segments - 1) * HASH_SIZE
        # how many share hashes are included in each share? This will be
        # about ln2(num_shares).
        self._share_hash_size = num_share_hashes * (2+HASH_SIZE)
        # we commit to not sending a uri extension larger than this
        self._uri_extension_size = uri_extension_size

        offsets = self._offsets = {}
        x = 0x24
        offsets['data'] = x
        x += data_size
        offsets['plaintext_hash_tree'] = x
        x += self._segment_hash_size
        offsets['crypttext_hash_tree'] = x
        x += self._segment_hash_size
        offsets['block_hashes'] = x
        x += self._segment_hash_size
        offsets['share_hashes'] = x
        x += self._share_hash_size
        offsets['uri_extension'] = x

        if x >= 2**32:
            raise FileTooLargeError("This file is too large to be uploaded (offsets).")

        offset_data = struct.pack(">LLLLLLLLL",
                                  1, # version number
                                  segment_size,
                                  data_size,
                                  offsets['data'],
                                  offsets['plaintext_hash_tree'],
                                  offsets['crypttext_hash_tree'],
                                  offsets['block_hashes'],
                                  offsets['share_hashes'],
                                  offsets['uri_extension'],
                                  )
        assert len(offset_data) == 0x24
        self._offset_data = offset_data

    def __repr__(self):
        if self._nodeid:
            nodeid_s = idlib.nodeid_b2a(self._nodeid)
        else:
            nodeid_s = "[None]"
        return "<allmydata.storage.WriteBucketProxy for node %s>" % nodeid_s

    def start(self):
        return self._write(0, self._offset_data)

    def put_block(self, segmentnum, data):
        offset = self._offsets['data'] + segmentnum * self._segment_size
        assert offset + len(data) <= self._offsets['uri_extension']
        assert isinstance(data, str)
        if segmentnum < self._num_segments-1:
            precondition(len(data) == self._segment_size,
                         len(data), self._segment_size)
        else:
            precondition(len(data) == (self._data_size -
                                       (self._segment_size *
                                        (self._num_segments - 1))),
                         len(data), self._segment_size)
        return self._write(offset, data)

    def put_plaintext_hashes(self, hashes):
        offset = self._offsets['plaintext_hash_tree']
        assert isinstance(hashes, list)
        data = "".join(hashes)
        precondition(len(data) == self._segment_hash_size,
                     len(data), self._segment_hash_size)
        precondition(offset+len(data) <= self._offsets['crypttext_hash_tree'],
                     offset, len(data), offset+len(data),
                     self._offsets['crypttext_hash_tree'])
        return self._write(offset, data)

    def put_crypttext_hashes(self, hashes):
        offset = self._offsets['crypttext_hash_tree']
        assert isinstance(hashes, list)
        data = "".join(hashes)
        precondition(len(data) == self._segment_hash_size,
                     len(data), self._segment_hash_size)
        precondition(offset + len(data) <= self._offsets['block_hashes'],
                     offset, len(data), offset+len(data),
                     self._offsets['block_hashes'])
        return self._write(offset, data)

    def put_block_hashes(self, blockhashes):
        offset = self._offsets['block_hashes']
        assert isinstance(blockhashes, list)
        data = "".join(blockhashes)
        precondition(len(data) == self._segment_hash_size,
                     len(data), self._segment_hash_size)
        precondition(offset + len(data) <= self._offsets['share_hashes'],
                     offset, len(data), offset+len(data),
                     self._offsets['share_hashes'])
        return self._write(offset, data)

    def put_share_hashes(self, sharehashes):
        # sharehashes is a list of (index, hash) tuples, so they get stored
        # as 2+32=34 bytes each
        offset = self._offsets['share_hashes']
        assert isinstance(sharehashes, list)
        data = "".join([struct.pack(">H", hashnum) + hashvalue
                        for hashnum,hashvalue in sharehashes])
        precondition(len(data) == self._share_hash_size,
                     len(data), self._share_hash_size)
        precondition(offset + len(data) <= self._offsets['uri_extension'],
                     offset, len(data), offset+len(data),
                     self._offsets['uri_extension'])
        return self._write(offset, data)

    def put_uri_extension(self, data):
        offset = self._offsets['uri_extension']
        assert isinstance(data, str)
        precondition(len(data) <= self._uri_extension_size,
                     len(data), self._uri_extension_size)
        length = struct.pack(">L", len(data))
        return self._write(offset, length+data)

    def _write(self, offset, data):
        # TODO: for small shares, buffer the writes and do just a single call
        return self._rref.callRemote("write", offset, data)

    def close(self):
        return self._rref.callRemote("close")

    def abort(self):
        return self._rref.callRemoteOnly("abort")

class ReadBucketProxy:
    implements(IStorageBucketReader)
    def __init__(self, rref, peerid=None, storage_index_s=None):
        self._rref = rref
        self._peerid = peerid
        self._si_s = storage_index_s
        self._started = False

    def get_peerid(self):
        return self._peerid

    def __repr__(self):
        peerid_s = idlib.shortnodeid_b2a(self._peerid)
        return "<ReadBucketProxy to peer [%s] SI %s>" % (peerid_s,
                                                         self._si_s)

    def startIfNecessary(self):
        if self._started:
            return defer.succeed(self)
        d = self.start()
        d.addCallback(lambda res: self)
        return d

    def start(self):
        # TODO: for small shares, read the whole bucket in start()
        d = self._read(0, 0x24)
        d.addCallback(self._parse_offsets)
        return d

    def _parse_offsets(self, data):
        precondition(len(data) == 0x24)
        self._offsets = {}
        (version, self._segment_size, self._data_size) = \
                  struct.unpack(">LLL", data[0:0xc])
        _assert(version == 1)
        x = 0x0c
        for field in ( 'data',
                       'plaintext_hash_tree',
                       'crypttext_hash_tree',
                       'block_hashes',
                       'share_hashes',
                       'uri_extension',
                       ):
            offset = struct.unpack(">L", data[x:x+4])[0]
            x += 4
            self._offsets[field] = offset
        return self._offsets

    def get_block(self, blocknum):
        num_segments = mathutil.div_ceil(self._data_size, self._segment_size)
        if blocknum < num_segments-1:
            size = self._segment_size
        else:
            size = self._data_size % self._segment_size
            if size == 0:
                size = self._segment_size
        offset = self._offsets['data'] + blocknum * self._segment_size
        return self._read(offset, size)

    def _str2l(self, s):
        """ split string (pulled from storage) into a list of blockids """
        return [ s[i:i+HASH_SIZE]
                 for i in range(0, len(s), HASH_SIZE) ]

    def get_plaintext_hashes(self):
        offset = self._offsets['plaintext_hash_tree']
        size = self._offsets['crypttext_hash_tree'] - offset
        d = self._read(offset, size)
        d.addCallback(self._str2l)
        return d

    def get_crypttext_hashes(self):
        offset = self._offsets['crypttext_hash_tree']
        size = self._offsets['block_hashes'] - offset
        d = self._read(offset, size)
        d.addCallback(self._str2l)
        return d

    def get_block_hashes(self):
        offset = self._offsets['block_hashes']
        size = self._offsets['share_hashes'] - offset
        d = self._read(offset, size)
        d.addCallback(self._str2l)
        return d

    def get_share_hashes(self):
        offset = self._offsets['share_hashes']
        size = self._offsets['uri_extension'] - offset
        assert size % (2+HASH_SIZE) == 0
        d = self._read(offset, size)
        def _unpack_share_hashes(data):
            assert len(data) == size
            hashes = []
            for i in range(0, size, 2+HASH_SIZE):
                hashnum = struct.unpack(">H", data[i:i+2])[0]
                hashvalue = data[i+2:i+2+HASH_SIZE]
                hashes.append( (hashnum, hashvalue) )
            return hashes
        d.addCallback(_unpack_share_hashes)
        return d

    def get_uri_extension(self):
        offset = self._offsets['uri_extension']
        d = self._read(offset, 4)
        def _got_length(data):
            length = struct.unpack(">L", data)[0]
            return self._read(offset+4, length)
        d.addCallback(_got_length)
        return d

    def _read(self, offset, length):
        return self._rref.callRemote("read", offset, length)
