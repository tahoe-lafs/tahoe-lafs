import os, re, weakref, stat, struct, time

from foolscap import Referenceable
from twisted.application import service
from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import RIStorageServer, RIBucketWriter, \
     RIBucketReader, IStorageBucketWriter, IStorageBucketReader, HASH_SIZE
from allmydata.util import fileutil, idlib, mathutil
from allmydata.util.assertutil import precondition, _assert

# storage/
# storage/shares/incoming
#   incoming/ holds temp dirs named $STORAGEINDEX/$SHARENUM which will be
#   moved to storage/shares/$STORAGEINDEX/$SHARENUM upon success
# storage/shares/$STORAGEINDEX
# storage/shares/$STORAGEINDEX/$SHARENUM

# $SHARENUM matches this regex:
NUM_RE=re.compile("[0-9]*")

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

    def __init__(self, ss, incominghome, finalhome, size, lease_info):
        self.ss = ss
        self.incominghome = incominghome
        self.finalhome = finalhome
        self._size = size
        self.closed = False
        self.throw_out_all_data = False
        # touch the file, so later callers will see that we're working on it.
        # Also construct the metadata.
        assert not os.path.exists(self.incominghome)
        f = open(self.incominghome, 'wb')
        f.write(struct.pack(">LLL", 1, size, 0))
        f.close()
        self._sharefile = ShareFile(self.incominghome)
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
        fileutil.rename(self.incominghome, self.finalhome)
        self._sharefile = None
        self.closed = True

        filelen = os.stat(self.finalhome)[stat.ST_SIZE]
        self.ss.bucket_writer_closed(self, filelen)


class BucketReader(Referenceable):
    implements(RIBucketReader)

    def __init__(self, home):
        self._share_file = ShareFile(home)

    def remote_read(self, offset, length):
        return self._share_file.read_share_data(offset, length)

class StorageServer(service.MultiService, Referenceable):
    implements(RIStorageServer)
    name = 'storageserver'

    def __init__(self, storedir, sizelimit=None, no_storage=False):
        service.MultiService.__init__(self)
        self.storedir = storedir
        sharedir = os.path.join(storedir, "shares")
        fileutil.make_dirs(sharedir)
        self.sharedir = sharedir
        self.sizelimit = sizelimit
        self.no_storage = no_storage
        self.incomingdir = os.path.join(sharedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)
        self._active_writers = weakref.WeakKeyDictionary()

        self.measure_size()

    def _clean_incomplete(self):
        fileutil.rm_dir(self.incomingdir)

    def measure_size(self):
        self.consumed = fileutil.du(self.sharedir)

    def allocated_size(self):
        space = self.consumed
        for bw in self._active_writers:
            space += bw.allocated_size()
        return space

    def remote_allocate_buckets(self, storage_index,
                                renew_secret, cancel_secret,
                                sharenums, allocated_size,
                                canary, owner_num=0):
        # owner_num is not for clients to set, but rather it should be
        # curried into the PersonalStorageServer instance that is dedicated
        # to a particular owner.
        alreadygot = set()
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_s = idlib.b2a(storage_index)

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
        for shnum in sharenums:
            incominghome = os.path.join(self.incomingdir, si_s, "%d" % shnum)
            finalhome = os.path.join(self.sharedir, si_s, "%d" % shnum)
            if os.path.exists(incominghome) or os.path.exists(finalhome):
                alreadygot.add(shnum)
                # add a lease for the client whose upload was pre-empted
                if os.path.exists(incominghome):
                    # the lease gets added to the still-in-construction share
                    sf = ShareFile(incominghome)
                else:
                    sf = ShareFile(finalhome)
                sf.add_or_renew_lease(lease_info)
            elif no_limits or remaining_space >= space_per_bucket:
                fileutil.make_dirs(os.path.join(self.incomingdir, si_s))
                bw = BucketWriter(self, incominghome, finalhome,
                                  space_per_bucket, lease_info)
                if self.no_storage:
                    bw.throw_out_all_data = True
                bucketwriters[shnum] = bw
                self._active_writers[bw] = 1
                if yes_limits:
                    remaining_space -= space_per_bucket
            else:
                # not enough space to accept this bucket
                pass

        if bucketwriters:
            fileutil.make_dirs(os.path.join(self.sharedir, si_s))

        return alreadygot, bucketwriters

    def remote_renew_lease(self, storage_index, renew_secret):
        new_expire_time = time.time() + 31*24*60*60
        found_buckets = False
        for shnum, filename in self._get_bucket_shares(storage_index):
            found_buckets = True
            sf = ShareFile(filename)
            sf.renew_lease(renew_secret, new_expire_time)
        if not found_buckets:
            raise IndexError("no such lease to renew")

    def remote_cancel_lease(self, storage_index, cancel_secret):
        storagedir = os.path.join(self.sharedir, idlib.b2a(storage_index))

        remaining_files = 0
        total_space_freed = 0
        found_buckets = False
        for shnum, filename in self._get_bucket_shares(storage_index):
            # note: if we can't find a lease on one share, we won't bother
            # looking in the others. Unless something broke internally
            # (perhaps we ran out of disk space while adding a lease), the
            # leases on all shares will be identical.
            found_buckets = True
            sf = ShareFile(filename)
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
            os.rmdir(storagedir)
        self.consumed -= total_space_freed
        if not found_buckets:
            raise IndexError("no such lease to cancel")

    def bucket_writer_closed(self, bw, consumed_size):
        self.consumed += consumed_size
        del self._active_writers[bw]

    def _get_bucket_shares(self, storage_index):
        """Return a list of (shnum, pathname) tuples for files that hold
        shares for this storage_index. In each tuple, 'shnum' will always be
        the integer form of the last component of 'pathname'."""
        storagedir = os.path.join(self.sharedir, idlib.b2a(storage_index))
        try:
            for f in os.listdir(storagedir):
                if NUM_RE.match(f):
                    filename = os.path.join(storagedir, f)
                    yield (int(f), filename)
        except OSError:
            # Commonly caused by there being no buckets at all.
            pass

    def remote_get_buckets(self, storage_index):
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
                           uri_extension_size)
    uri_extension_starts_at = wbp._offsets['uri_extension']
    return uri_extension_starts_at + 4 + uri_extension_size

class WriteBucketProxy:
    implements(IStorageBucketWriter)
    def __init__(self, rref, data_size, segment_size, num_segments,
                 num_share_hashes, uri_extension_size):
        self._rref = rref
        self._data_size = data_size
        self._segment_size = segment_size
        self._num_segments = num_segments

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

class ReadBucketProxy:
    implements(IStorageBucketReader)
    def __init__(self, rref):
        self._rref = rref
        self._started = False

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
