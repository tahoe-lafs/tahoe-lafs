import os, re, weakref, stat, struct, time

from foolscap import Referenceable
from twisted.application import service

from zope.interface import implements
from allmydata.interfaces import RIStorageServer, RIBucketWriter, \
     RIBucketReader, BadWriteEnablerError, IStatsProducer
from allmydata.util import base32, fileutil, idlib, log, time_format
from allmydata.util.assertutil import precondition
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
#  0x04: share data length, four bytes big-endian = A # See Footnote 1 below.
#  0x08: number of leases, four bytes big-endian
#  0x0c: beginning of share data (see immutable.layout.WriteBucketProxy)
#  A+0x0c = B: first lease. Lease format is:
#   B+0x00: owner number, 4 bytes big-endian, 0 is reserved for no-owner
#   B+0x04: renew secret, 32 bytes (SHA256)
#   B+0x24: cancel secret, 32 bytes (SHA256)
#   B+0x44: expiration time, 4 bytes big-endian seconds-since-epoch
#   B+0x48: next lease, or end of record

# Footnote 1: as of Tahoe v1.3.0 this field is not used by storage servers, but it is still
# filled in by storage servers in case the storage server software gets downgraded from >= Tahoe
# v1.3.0 to < Tahoe v1.3.0, or the share file is moved from one storage server to another.  The
# value stored in this field is truncated, so If the actual share data length is >= 2**32, then
# the value stored in this field will be the actual share data length modulo 2**32.

def si_b2a(storageindex):
    return base32.b2a(storageindex)

def si_a2b(ascii_storageindex):
    return base32.a2b(ascii_storageindex)

def storage_index_to_dir(storageindex):
    sia = si_b2a(storageindex)
    return os.path.join(sia[:2], sia)

class LeaseInfo:
    def __init__(self, owner_num=None, renew_secret=None, cancel_secret=None,
                 expiration_time=None, nodeid=None):
        self.owner_num = owner_num
        self.renew_secret = renew_secret
        self.cancel_secret = cancel_secret
        self.expiration_time = expiration_time
        if nodeid is not None:
            assert isinstance(nodeid, str)
            assert len(nodeid) == 20
        self.nodeid = nodeid

    def from_immutable_data(self, data):
        (self.owner_num,
         self.renew_secret,
         self.cancel_secret,
         self.expiration_time) = struct.unpack(">L32s32sL", data)
        self.nodeid = None
        return self
    def to_immutable_data(self):
        return struct.pack(">L32s32sL",
                           self.owner_num,
                           self.renew_secret, self.cancel_secret,
                           int(self.expiration_time))

    def to_mutable_data(self):
        return struct.pack(">LL32s32s20s",
                           self.owner_num,
                           int(self.expiration_time),
                           self.renew_secret, self.cancel_secret,
                           self.nodeid)
    def from_mutable_data(self, data):
        (self.owner_num,
         self.expiration_time,
         self.renew_secret, self.cancel_secret,
         self.nodeid) = struct.unpack(">LL32s32s20s", data)
        return self


class ShareFile:
    LEASE_SIZE = struct.calcsize(">L32s32sL")

    def __init__(self, filename, max_size=None, create=False):
        """ If max_size is not None then I won't allow more than max_size to be written to me. If create=True and max_size must not be None. """
        precondition((max_size is not None) or (not create), max_size, create)
        self.home = filename
        self._max_size = max_size
        if create:
            # touch the file, so later callers will see that we're working on it.
            # Also construct the metadata.
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
            self._lease_offset = max_size + 0x0c
            self._num_leases = 0
        else:
            f = open(self.home, 'rb')
            filesize = os.path.getsize(self.home)
            (version, unused, num_leases) = struct.unpack(">LLL", f.read(0xc))
            f.close()
            assert version == 1, version
            self._num_leases = num_leases
            self._lease_offset = filesize - (num_leases * self.LEASE_SIZE)
        self._data_offset = 0xc

    def unlink(self):
        os.unlink(self.home)

    def read_share_data(self, offset, length):
        precondition(offset >= 0)
        # reads beyond the end of the data are truncated. Reads that start beyond the end of the
        # data return an empty string.
        # I wonder why Python doesn't do the following computation for me?
        seekpos = self._data_offset+offset
        fsize = os.path.getsize(self.home)
        actuallength = max(0, min(length, fsize-seekpos))
        if actuallength == 0:
            return ""
        f = open(self.home, 'rb')
        f.seek(seekpos)
        return f.read(actuallength)

    def write_share_data(self, offset, data):
        length = len(data)
        precondition(offset >= 0, offset)
        if self._max_size is not None and offset+length > self._max_size:
            raise DataTooLargeError(self._max_size, offset, length)
        f = open(self.home, 'rb+')
        real_offset = self._data_offset+offset
        f.seek(real_offset)
        assert f.tell() == real_offset
        f.write(data)
        f.close()

    def _write_lease_record(self, f, lease_number, lease_info):
        offset = self._lease_offset + lease_number * self.LEASE_SIZE
        f.seek(offset)
        assert f.tell() == offset
        f.write(lease_info.to_immutable_data())

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
        (version, unused, num_leases) = struct.unpack(">LLL", f.read(0xc))
        f.seek(self._lease_offset)
        for i in range(num_leases):
            data = f.read(self.LEASE_SIZE)
            if data:
                yield LeaseInfo().from_immutable_data(data)

    def add_lease(self, lease_info):
        f = open(self.home, 'rb+')
        num_leases = self._read_num_leases(f)
        self._write_lease_record(f, num_leases, lease_info)
        self._write_num_leases(f, num_leases+1)
        f.close()

    def renew_lease(self, renew_secret, new_expire_time):
        for i,lease in enumerate(self.iter_leases()):
            if lease.renew_secret == renew_secret:
                # yup. See if we need to update the owner time.
                if new_expire_time > lease.expiration_time:
                    # yes
                    lease.expiration_time = new_expire_time
                    f = open(self.home, 'rb+')
                    self._write_lease_record(f, i, lease)
                    f.close()
                return
        raise IndexError("unable to renew non-existent lease")

    def add_or_renew_lease(self, lease_info):
        try:
            self.renew_lease(lease_info.renew_secret,
                             lease_info.expiration_time)
        except IndexError:
            self.add_lease(lease_info)


    def cancel_lease(self, cancel_secret):
        """Remove a lease with the given cancel_secret. If the last lease is
        cancelled, the file will be removed. Return the number of bytes that
        were freed (by truncating the list of leases, and possibly by
        deleting the file. Raise IndexError if there was no lease with the
        given cancel_secret.
        """

        leases = list(self.iter_leases())
        num_leases = len(leases)
        num_leases_removed = 0
        for i,lease in enumerate(leases[:]):
            if lease.cancel_secret == cancel_secret:
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
        space_freed = self.LEASE_SIZE * num_leases_removed
        if not len(leases):
            space_freed += os.stat(self.home)[stat.ST_SIZE]
            self.unlink()
        return space_freed


class BucketWriter(Referenceable):
    implements(RIBucketWriter)

    def __init__(self, ss, incominghome, finalhome, max_size, lease_info, canary):
        self.ss = ss
        self.incominghome = incominghome
        self.finalhome = finalhome
        self._max_size = max_size # don't allow the client to write more than this
        self._canary = canary
        self._disconnect_marker = canary.notifyOnDisconnect(self._disconnected)
        self.closed = False
        self.throw_out_all_data = False
        self._sharefile = ShareFile(incominghome, create=True, max_size=max_size)
        # also, add our lease to the file now, so that other ones can be
        # added by simultaneous uploaders
        self._sharefile.add_lease(lease_info)

    def allocated_size(self):
        return self._max_size

    def remote_write(self, offset, data):
        start = time.time()
        precondition(not self.closed)
        if self.throw_out_all_data:
            return
        self._sharefile.write_share_data(offset, data)
        self.ss.add_latency("write", time.time() - start)
        self.ss.count("write")

    def remote_close(self):
        precondition(not self.closed)
        start = time.time()

        fileutil.make_dirs(os.path.dirname(self.finalhome))
        fileutil.rename(self.incominghome, self.finalhome)
        try:
            # self.incominghome is like storage/shares/incoming/ab/abcde/4 .
            # We try to delete the parent (.../ab/abcde) to avoid leaving
            # these directories lying around forever, but the delete might
            # fail if we're working on another share for the same storage
            # index (like ab/abcde/5). The alternative approach would be to
            # use a hierarchy of objects (PrefixHolder, BucketHolder,
            # ShareWriter), each of which is responsible for a single
            # directory on disk, and have them use reference counting of
            # their children to know when they should do the rmdir. This
            # approach is simpler, but relies on os.rmdir refusing to delete
            # a non-empty directory. Do *not* use fileutil.rm_dir() here!
            os.rmdir(os.path.dirname(self.incominghome))
            # we also delete the grandparent (prefix) directory, .../ab ,
            # again to avoid leaving directories lying around. This might
            # fail if there is another bucket open that shares a prefix (like
            # ab/abfff).
            os.rmdir(os.path.dirname(os.path.dirname(self.incominghome)))
            # we leave the great-grandparent (incoming/) directory in place.
        except EnvironmentError:
            # ignore the "can't rmdir because the directory is not empty"
            # exceptions, those are normal consequences of the
            # above-mentioned conditions.
            pass
        self._sharefile = None
        self.closed = True
        self._canary.dontNotifyOnDisconnect(self._disconnect_marker)

        filelen = os.stat(self.finalhome)[stat.ST_SIZE]
        self.ss.bucket_writer_closed(self, filelen)
        self.ss.add_latency("close", time.time() - start)
        self.ss.count("close")

    def _disconnected(self):
        if not self.closed:
            self._abort()

    def remote_abort(self):
        log.msg("storage: aborting sharefile %s" % self.incominghome,
                facility="tahoe.storage", level=log.UNUSUAL)
        if not self.closed:
            self._canary.dontNotifyOnDisconnect(self._disconnect_marker)
        self._abort()
        self.ss.count("abort")

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

    def __init__(self, ss, sharefname, storage_index=None, shnum=None):
        self.ss = ss
        self._share_file = ShareFile(sharefname)
        self.storage_index = storage_index
        self.shnum = shnum

    def __repr__(self):
        return "<%s %s %s>" % (self.__class__.__name__, base32.b2a_l(self.storage_index[:8], 60), self.shnum)

    def remote_read(self, offset, length):
        start = time.time()
        data = self._share_file.read_share_data(offset, length)
        self.ss.add_latency("read", time.time() - start)
        self.ss.count("read")
        return data

    def remote_advise_corrupt_share(self, reason):
        return self.ss.remote_advise_corrupt_share("immutable",
                                                   self.storage_index,
                                                   self.shnum,
                                                   reason)

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


assert struct.calcsize("L"), 4 # The struct module doc says that L's are 4 bytes in size.
assert struct.calcsize("Q"), 8 # The struct module doc says that Q's are 8 bytes in size (at least with big-endian ordering).

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
        f.write(lease_info.to_mutable_data())

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
        lease_info = LeaseInfo().from_mutable_data(data)
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
        precondition(lease_info.owner_num != 0) # 0 means "no lease here"
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
        for (leasenum,lease) in self._enumerate_leases(f):
            if lease.renew_secret == renew_secret:
                # yup. See if we need to update the owner time.
                if new_expire_time > lease.expiration_time:
                    # yes
                    lease.expiration_time = new_expire_time
                    self._write_lease_record(f, leasenum, lease)
                f.close()
                return
            accepting_nodeids.add(lease.nodeid)
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
        precondition(lease_info.owner_num != 0) # 0 means "no lease here"
        try:
            self.renew_lease(lease_info.renew_secret,
                             lease_info.expiration_time)
        except IndexError:
            self.add_lease(lease_info)

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
                                renew_secret="\x00"*32,
                                cancel_secret="\x00"*32,
                                expiration_time=0,
                                nodeid="\x00"*20)
        f = open(self.home, 'rb+')
        for (leasenum,lease) in self._enumerate_leases(f):
            accepting_nodeids.add(lease.nodeid)
            if lease.cancel_secret == cancel_secret:
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

    def __init__(self, storedir, reserved_space=0,
                 discard_storage=False, readonly_storage=False,
                 stats_provider=None):
        service.MultiService.__init__(self)
        self.storedir = storedir
        sharedir = os.path.join(storedir, "shares")
        fileutil.make_dirs(sharedir)
        self.sharedir = sharedir
        # we don't actually create the corruption-advisory dir until necessary
        self.corruption_advisory_dir = os.path.join(storedir,
                                                    "corruption-advisories")
        self.reserved_space = int(reserved_space)
        self.no_storage = discard_storage
        self.readonly_storage = readonly_storage
        self.stats_provider = stats_provider
        if self.stats_provider:
            self.stats_provider.register_producer(self)
        self.incomingdir = os.path.join(sharedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)
        self._active_writers = weakref.WeakKeyDictionary()
        lp = log.msg("StorageServer created", facility="tahoe.storage")

        if reserved_space:
            if self.get_available_space() is None:
                log.msg("warning: [storage]reserved_space= is set, but this platform does not support statvfs(2), so this reservation cannot be honored",
                        umin="0wZ27w", level=log.UNUSUAL)

        self.latencies = {"allocate": [], # immutable
                          "write": [],
                          "close": [],
                          "read": [],
                          "get": [],
                          "writev": [], # mutable
                          "readv": [],
                          "add-lease": [], # both
                          "renew": [],
                          "cancel": [],
                          }

    def count(self, name, delta=1):
        if self.stats_provider:
            self.stats_provider.count("storage_server." + name, delta)

    def add_latency(self, category, latency):
        a = self.latencies[category]
        a.append(latency)
        if len(a) > 1000:
            self.latencies[category] = a[-1000:]

    def get_latencies(self):
        """Return a dict, indexed by category, that contains a dict of
        latency numbers for each category. Each dict will contain the
        following keys: mean, 01_0_percentile, 10_0_percentile,
        50_0_percentile (median), 90_0_percentile, 95_0_percentile,
        99_0_percentile, 99_9_percentile. If no samples have been collected
        for the given category, then that category name will not be present
        in the return value."""
        # note that Amazon's Dynamo paper says they use 99.9% percentile.
        output = {}
        for category in self.latencies:
            if not self.latencies[category]:
                continue
            stats = {}
            samples = self.latencies[category][:]
            samples.sort()
            count = len(samples)
            stats["mean"] = sum(samples) / count
            stats["01_0_percentile"] = samples[int(0.01 * count)]
            stats["10_0_percentile"] = samples[int(0.1 * count)]
            stats["50_0_percentile"] = samples[int(0.5 * count)]
            stats["90_0_percentile"] = samples[int(0.9 * count)]
            stats["95_0_percentile"] = samples[int(0.95 * count)]
            stats["99_0_percentile"] = samples[int(0.99 * count)]
            stats["99_9_percentile"] = samples[int(0.999 * count)]
            output[category] = stats
        return output

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
        # remember: RIStatsProvider requires that our return dict
        # contains numeric values.
        stats = { 'storage_server.allocated': self.allocated_size(), }
        for category,ld in self.get_latencies().items():
            for name,v in ld.items():
                stats['storage_server.latencies.%s.%s' % (category, name)] = v
        writeable = True
        if self.readonly_storage:
            writeable = False
        try:
            s = os.statvfs(self.storedir)
            disk_total = s.f_bsize * s.f_blocks
            disk_used = s.f_bsize * (s.f_blocks - s.f_bfree)
            # spacetime predictors should look at the slope of disk_used.
            disk_avail = s.f_bsize * s.f_bavail # available to non-root users
            # include our local policy here: if we stop accepting shares when
            # the available space drops below 1GB, then include that fact in
            # disk_avail.
            disk_avail -= self.reserved_space
            disk_avail = max(disk_avail, 0)
            if self.readonly_storage:
                disk_avail = 0
            if disk_avail == 0:
                writeable = False

            # spacetime predictors should use disk_avail / (d(disk_used)/dt)
            stats["storage_server.disk_total"] = disk_total
            stats["storage_server.disk_used"] = disk_used
            stats["storage_server.disk_avail"] = disk_avail
        except AttributeError:
            # os.statvfs is available only on unix
            pass
        stats["storage_server.accepting_immutable_shares"] = int(writeable)
        return stats


    def stat_disk(self, d):
        s = os.statvfs(d)
        # s.f_bavail: available to non-root users
        disk_avail = s.f_bsize * s.f_bavail
        return disk_avail

    def get_available_space(self):
        # returns None if it cannot be measured (windows)
        try:
            disk_avail = self.stat_disk(self.storedir)
            disk_avail -= self.reserved_space
        except AttributeError:
            disk_avail = None
        if self.readonly_storage:
            disk_avail = 0
        return disk_avail

    def allocated_size(self):
        space = 0
        for bw in self._active_writers:
            space += bw.allocated_size()
        return space

    def remote_get_version(self):
        remaining_space = self.get_available_space()
        if remaining_space is None:
            # we're on a platform that doesn't have 'df', so make a vague
            # guess.
            remaining_space = 2**64
        version = { "http://allmydata.org/tahoe/protocols/storage/v1" :
                    { "maximum-immutable-share-size": remaining_space,
                      "tolerates-immutable-read-overrun": True,
                      "delete-mutable-shares-with-zero-length-writev": True,
                      },
                    "application-version": str(allmydata.__version__),
                    }
        return version

    def remote_allocate_buckets(self, storage_index,
                                renew_secret, cancel_secret,
                                sharenums, allocated_size,
                                canary, owner_num=0):
        # owner_num is not for clients to set, but rather it should be
        # curried into the PersonalStorageServer instance that is dedicated
        # to a particular owner.
        start = time.time()
        self.count("allocate")
        alreadygot = set()
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_dir = storage_index_to_dir(storage_index)
        si_s = si_b2a(storage_index)

        log.msg("storage: allocate_buckets %s" % si_s)

        # in this implementation, the lease information (including secrets)
        # goes into the share files themselves. It could also be put into a
        # separate database. Note that the lease should not be added until
        # the BucketWriter has been closed.
        expire_time = time.time() + 31*24*60*60
        lease_info = LeaseInfo(owner_num,
                               renew_secret, cancel_secret,
                               expire_time, self.my_nodeid)

        max_space_per_bucket = allocated_size

        remaining_space = self.get_available_space()
        limited = remaining_space is not None
        if limited:
            # this is a bit conservative, since some of this allocated_size()
            # has already been written to disk, where it will show up in
            # get_available_space.
            remaining_space -= self.allocated_size()

        # fill alreadygot with all shares that we have, not just the ones
        # they asked about: this will save them a lot of work. Add or update
        # leases for all of them: if they want us to hold shares for this
        # file, they'll want us to hold leases for this file.
        for (shnum, fn) in self._get_bucket_shares(storage_index):
            alreadygot.add(shnum)
            sf = ShareFile(fn)
            sf.add_or_renew_lease(lease_info)

        # self.readonly_storage causes remaining_space=0

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
            elif (not limited) or (remaining_space >= max_space_per_bucket):
                # ok! we need to create the new share file.
                bw = BucketWriter(self, incominghome, finalhome,
                                  max_space_per_bucket, lease_info, canary)
                if self.no_storage:
                    bw.throw_out_all_data = True
                bucketwriters[shnum] = bw
                self._active_writers[bw] = 1
                if limited:
                    remaining_space -= max_space_per_bucket
            else:
                # bummer! not enough space to accept this bucket
                pass

        if bucketwriters:
            fileutil.make_dirs(os.path.join(self.sharedir, si_dir))

        self.add_latency("allocate", time.time() - start)
        return alreadygot, bucketwriters

    def _iter_share_files(self, storage_index):
        for shnum, filename in self._get_bucket_shares(storage_index):
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
                continue # non-sharefile
            yield sf

    def remote_add_lease(self, storage_index, renew_secret, cancel_secret,
                         owner_num=1):
        start = time.time()
        self.count("add-lease")
        new_expire_time = time.time() + 31*24*60*60
        lease_info = LeaseInfo(owner_num,
                               renew_secret, cancel_secret,
                               new_expire_time, self.my_nodeid)
        found_buckets = False
        for sf in self._iter_share_files(storage_index):
            found_buckets = True
            # note: if the share has been migrated, the renew_lease()
            # call will throw an exception, with information to help the
            # client update the lease.
            sf.add_or_renew_lease(lease_info)
        self.add_latency("add-lease", time.time() - start)
        if not found_buckets:
            raise IndexError("no such storage index to do add-lease")


    def remote_renew_lease(self, storage_index, renew_secret):
        start = time.time()
        self.count("renew")
        new_expire_time = time.time() + 31*24*60*60
        found_buckets = False
        for sf in self._iter_share_files(storage_index):
            found_buckets = True
            sf.renew_lease(renew_secret, new_expire_time)
        self.add_latency("renew", time.time() - start)
        if not found_buckets:
            raise IndexError("no such lease to renew")

    def remote_cancel_lease(self, storage_index, cancel_secret):
        start = time.time()
        self.count("cancel")

        total_space_freed = 0
        found_buckets = False
        for sf in self._iter_share_files(storage_index):
            # note: if we can't find a lease on one share, we won't bother
            # looking in the others. Unless something broke internally
            # (perhaps we ran out of disk space while adding a lease), the
            # leases on all shares will be identical.
            found_buckets = True
            # this raises IndexError if the lease wasn't present XXXX
            total_space_freed += sf.cancel_lease(cancel_secret)

        if found_buckets:
            storagedir = os.path.join(self.sharedir,
                                      storage_index_to_dir(storage_index))
            if not os.listdir(storagedir):
                os.rmdir(storagedir)

        if self.stats_provider:
            self.stats_provider.count('storage_server.bytes_freed',
                                      total_space_freed)
        self.add_latency("cancel", time.time() - start)
        if not found_buckets:
            raise IndexError("no such storage index")

    def bucket_writer_closed(self, bw, consumed_size):
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

    def remote_get_buckets(self, storage_index):
        start = time.time()
        self.count("get")
        si_s = si_b2a(storage_index)
        log.msg("storage: get_buckets %s" % si_s)
        bucketreaders = {} # k: sharenum, v: BucketReader
        for shnum, filename in self._get_bucket_shares(storage_index):
            bucketreaders[shnum] = BucketReader(self, filename,
                                                storage_index, shnum)
        self.add_latency("get", time.time() - start)
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
        start = time.time()
        self.count("writev")
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

        ownerid = 1 # TODO
        expire_time = time.time() + 31*24*60*60   # one month
        lease_info = LeaseInfo(ownerid,
                               renew_secret, cancel_secret,
                               expire_time, self.my_nodeid)

        if testv_is_good:
            # now apply the write vectors
            for sharenum in test_and_write_vectors:
                (testv, datav, new_length) = test_and_write_vectors[sharenum]
                if new_length == 0:
                    if sharenum in shares:
                        shares[sharenum].unlink()
                else:
                    if sharenum not in shares:
                        # allocate a new share
                        allocated_size = 2000 # arbitrary, really
                        share = self._allocate_slot_share(bucketdir, secrets,
                                                          sharenum,
                                                          allocated_size,
                                                          owner_num=0)
                        shares[sharenum] = share
                    shares[sharenum].writev(datav, new_length)
                    # and update the lease
                    shares[sharenum].add_or_renew_lease(lease_info)

            if new_length == 0:
                # delete empty bucket directories
                if not os.listdir(bucketdir):
                    os.rmdir(bucketdir)


        # all done
        self.add_latency("writev", time.time() - start)
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
        start = time.time()
        self.count("readv")
        si_s = si_b2a(storage_index)
        lp = log.msg("storage: slot_readv %s %s" % (si_s, shares),
                     facility="tahoe.storage", level=log.OPERATIONAL)
        si_dir = storage_index_to_dir(storage_index)
        # shares exist if there is a file for them
        bucketdir = os.path.join(self.sharedir, si_dir)
        if not os.path.isdir(bucketdir):
            self.add_latency("readv", time.time() - start)
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
        self.add_latency("readv", time.time() - start)
        return datavs

    def remote_advise_corrupt_share(self, share_type, storage_index, shnum,
                                    reason):
        fileutil.make_dirs(self.corruption_advisory_dir)
        now = time_format.iso_utc(sep="T")
        si_s = base32.b2a(storage_index)
        # windows can't handle colons in the filename
        fn = os.path.join(self.corruption_advisory_dir,
                          "%s--%s-%d" % (now, si_s, shnum)).replace(":","")
        f = open(fn, "w")
        f.write("report: Share Corruption\n")
        f.write("type: %s\n" % share_type)
        f.write("storage_index: %s\n" % si_s)
        f.write("share_number: %d\n" % shnum)
        f.write("\n")
        f.write(reason)
        f.write("\n")
        f.close()
        log.msg(format=("client claims corruption in (%(share_type)s) " +
                        "%(si)s-%(shnum)d: %(reason)s"),
                share_type=share_type, si=si_s, shnum=shnum, reason=reason,
                level=log.SCARY, umid="SGx2fA")
        return None
