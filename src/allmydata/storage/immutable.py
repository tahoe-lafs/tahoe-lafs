import os, stat, struct, time

from foolscap import Referenceable

from zope.interface import implements
from allmydata.interfaces import RIBucketWriter, RIBucketReader
from allmydata.util import base32, fileutil, log
from allmydata.util.assertutil import precondition
from allmydata.storage.lease import LeaseInfo
from allmydata.storage.common import DataTooLargeError

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
