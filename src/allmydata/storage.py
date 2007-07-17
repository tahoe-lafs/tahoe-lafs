import os, re, weakref, stat, struct

from foolscap import Referenceable
from twisted.application import service
from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import RIStorageServer, RIBucketWriter, \
     RIBucketReader, IStorageBucketWriter, IStorageBucketReader, HASH_SIZE
from allmydata.util import fileutil, idlib, mathutil
from allmydata.util.assertutil import precondition

# store/
# store/incoming # temp dirs named $STORAGEINDEX/$SHARENUM which will be moved to store/$STORAGEINDEX/$SHARENUM on success
# store/$STORAGEINDEX
# store/$STORAGEINDEX/$SHARENUM
# store/$STORAGEINDEX/$SHARENUM/blocksize
# store/$STORAGEINDEX/$SHARENUM/data
# store/$STORAGEINDEX/$SHARENUM/blockhashes
# store/$STORAGEINDEX/$SHARENUM/sharehashtree

# $SHARENUM matches this regex:
NUM_RE=re.compile("[0-9]*")

class BucketWriter(Referenceable):
    implements(RIBucketWriter)

    def __init__(self, ss, incominghome, finalhome, size):
        self.ss = ss
        self.incominghome = incominghome
        self.finalhome = finalhome
        self._size = size
        self.closed = False
        self.throw_out_all_data = False
        # touch the file, so later callers will see that we're working on it
        f = open(self.incominghome, 'ab')
        f.close()

    def allocated_size(self):
        return self._size

    def remote_write(self, offset, data):
        precondition(not self.closed)
        precondition(offset >= 0)
        precondition(offset+len(data) <= self._size)
        if self.throw_out_all_data:
            return
        f = open(self.incominghome, 'ab')
        f.seek(offset)
        f.write(data)
        f.close()

    def remote_close(self):
        precondition(not self.closed)
        fileutil.rename(self.incominghome, self.finalhome)
        self.closed = True
        filelen = os.stat(self.finalhome)[stat.ST_SIZE]
        self.ss.bucket_writer_closed(self, filelen)


class BucketReader(Referenceable):
    implements(RIBucketReader)

    def __init__(self, home):
        self.home = home

    def remote_read(self, offset, length):
        f = open(self.home, 'rb')
        f.seek(offset)
        return f.read(length)

class StorageServer(service.MultiService, Referenceable):
    implements(RIStorageServer)
    name = 'storageserver'

    def __init__(self, storedir, sizelimit=None, no_storage=False):
        service.MultiService.__init__(self)
        fileutil.make_dirs(storedir)
        self.storedir = storedir
        self.sizelimit = sizelimit
        self.no_storage = no_storage
        self.incomingdir = os.path.join(storedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)
        self._active_writers = weakref.WeakKeyDictionary()

        self.measure_size()

    def _clean_incomplete(self):
        fileutil.rm_dir(self.incomingdir)

    def measure_size(self):
        self.consumed = fileutil.du(self.storedir)

    def allocated_size(self):
        space = self.consumed
        for bw in self._active_writers:
            space += bw.allocated_size()
        return space

    def remote_allocate_buckets(self, storage_index, sharenums, allocated_size,
                                canary):
        alreadygot = set()
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_s = idlib.b2a(storage_index)
        space_per_bucket = allocated_size
        no_limits = self.sizelimit is None
        yes_limits = not no_limits
        if yes_limits:
            remaining_space = self.sizelimit - self.allocated_size()
        for shnum in sharenums:
            incominghome = os.path.join(self.incomingdir, si_s, "%d" % shnum)
            finalhome = os.path.join(self.storedir, si_s, "%d" % shnum)
            if os.path.exists(incominghome) or os.path.exists(finalhome):
                alreadygot.add(shnum)
            elif no_limits or remaining_space >= space_per_bucket:
                fileutil.make_dirs(os.path.join(self.incomingdir, si_s))
                bw = BucketWriter(self, incominghome, finalhome,
                                  space_per_bucket)
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
            fileutil.make_dirs(os.path.join(self.storedir, si_s))

        return alreadygot, bucketwriters

    def bucket_writer_closed(self, bw, consumed_size):
        self.consumed += consumed_size
        del self._active_writers[bw]

    def remote_get_buckets(self, storage_index):
        bucketreaders = {} # k: sharenum, v: BucketReader
        storagedir = os.path.join(self.storedir, idlib.b2a(storage_index))
        try:
            for f in os.listdir(storagedir):
                if NUM_RE.match(f):
                    br = BucketReader(os.path.join(storagedir, f))
                    bucketreaders[int(f)] = br
        except OSError:
            # Commonly caused by there being no buckets at all.
            pass

        return bucketreaders

"""
Share data is written into a single file. At the start of the file, there is
a series of four-byte big-endian offset values, which indicate where each
section starts. Each offset is measured from the beginning of the file.

0x00: segment size
0x04: data size
0x08: offset of data (=00 00 00 1c)
0x0c: offset of plaintext_hash_tree
0x10: offset of crypttext_hash_tree
0x14: offset of block_hashes
0x18: offset of share_hashes
0x1c: offset of uri_extension_length + uri_extension
0x20: start of data
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
        x = 0x20
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

        offset_data = struct.pack(">LLLLLLLL",
                                  segment_size,
                                  data_size,
                                  offsets['data'],
                                  offsets['plaintext_hash_tree'],
                                  offsets['crypttext_hash_tree'],
                                  offsets['block_hashes'],
                                  offsets['share_hashes'],
                                  offsets['uri_extension'],
                                  )
        assert len(offset_data) == 8*4
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
        d = self._read(0, 8*4)
        d.addCallback(self._parse_offsets)
        return d

    def _parse_offsets(self, data):
        precondition(len(data) == 8*4)
        self._offsets = {}
        self._segment_size = struct.unpack(">L", data[0:4])[0]
        self._data_size = struct.unpack(">L", data[4:8])[0]
        x = 0x08
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
