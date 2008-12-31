import struct
from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IStorageBucketWriter, IStorageBucketReader, \
     FileTooLargeError, HASH_SIZE
from allmydata.util import mathutil, idlib
from allmydata.util.assertutil import _assert, precondition
from allmydata import storage


"""
Share data is written in a file. At the start of the file, there is a series of four-byte
big-endian offset values, which indicate where each section starts. Each offset is measured from
the beginning of the share data.

0x00: version number (=00 00 00 01)
0x04: segment size
0x08: data size
0x0c: offset of data (=00 00 00 24)
0x10: offset of plaintext_hash_tree UNUSED
0x14: offset of crypttext_hash_tree
0x18: offset of block_hashes
0x1c: offset of share_hashes
0x20: offset of uri_extension_length + uri_extension
0x24: start of data
?   : start of plaintext_hash_tree UNUSED
?   : start of crypttext_hash_tree
?   : start of block_hashes
?   : start of share_hashes
       each share_hash is written as a two-byte (big-endian) hashnum
       followed by the 32-byte SHA-256 hash. We only store the hashes
       necessary to validate the share hash root
?   : start of uri_extension_length (four-byte big-endian value)
?   : start of uri_extension
"""

"""
v2 shares: these use 8-byte offsets to remove two of the three ~12GiB size
limitations described in #346.

0x00: version number (=00 00 00 02)
0x04: segment size
0x0c: data size
0x14: offset of data (=00 00 00 00 00 00 00 44)
0x1c: offset of plaintext_hash_tree UNUSED
0x24: offset of crypttext_hash_tree
0x2c: offset of block_hashes
0x34: offset of share_hashes
0x3c: offset of uri_extension_length + uri_extension
0x44: start of data
    : rest of share is the same as v1, above
...   ...
?   : start of uri_extension_length (eight-byte big-endian value)
"""

def allocated_size(data_size, num_segments, num_share_hashes,
                   uri_extension_size):
    wbp = WriteBucketProxy(None, data_size, 0, num_segments, num_share_hashes,
                           uri_extension_size, None)
    uri_extension_starts_at = wbp._offsets['uri_extension']
    return uri_extension_starts_at + wbp.fieldsize + uri_extension_size

class WriteBucketProxy:
    implements(IStorageBucketWriter)
    fieldsize = 4
    fieldstruct = ">L"

    def __init__(self, rref, data_size, segment_size, num_segments,
                 num_share_hashes, uri_extension_size, nodeid):
        self._rref = rref
        self._data_size = data_size
        self._segment_size = segment_size
        self._num_segments = num_segments
        self._nodeid = nodeid

        effective_segments = mathutil.next_power_of_k(num_segments,2)
        self._segment_hash_size = (2*effective_segments - 1) * HASH_SIZE
        # how many share hashes are included in each share? This will be
        # about ln2(num_shares).
        self._share_hash_size = num_share_hashes * (2+HASH_SIZE)
        # we commit to not sending a uri extension larger than this
        self._uri_extension_size = uri_extension_size

        self._create_offsets(segment_size, data_size)

    def _create_offsets(self, segment_size, data_size):
        if segment_size >= 2**32 or data_size >= 2**32:
            raise FileTooLargeError("This file is too large to be uploaded (data_size).")

        offsets = self._offsets = {}
        x = 0x24
        offsets['data'] = x
        x += data_size
        offsets['plaintext_hash_tree'] = x # UNUSED
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
                                  offsets['plaintext_hash_tree'], # UNUSED
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
        length = struct.pack(self.fieldstruct, len(data))
        return self._write(offset, length+data)

    def _write(self, offset, data):
        # TODO: for small shares, buffer the writes and do just a single call
        return self._rref.callRemote("write", offset, data)

    def close(self):
        return self._rref.callRemote("close")

    def abort(self):
        return self._rref.callRemoteOnly("abort")

class WriteBucketProxy_v2(WriteBucketProxy):
    fieldsize = 8
    fieldstruct = ">Q"

    def _create_offsets(self, segment_size, data_size):
        if segment_size >= 2**64 or data_size >= 2**64:
            raise FileTooLargeError("This file is too large to be uploaded (data_size).")

        offsets = self._offsets = {}
        x = 0x44
        offsets['data'] = x
        x += data_size
        offsets['plaintext_hash_tree'] = x # UNUSED
        x += self._segment_hash_size
        offsets['crypttext_hash_tree'] = x
        x += self._segment_hash_size
        offsets['block_hashes'] = x
        x += self._segment_hash_size
        offsets['share_hashes'] = x
        x += self._share_hash_size
        offsets['uri_extension'] = x

        if x >= 2**64:
            raise FileTooLargeError("This file is too large to be uploaded (offsets).")

        offset_data = struct.pack(">LQQQQQQQQ",
                                  2, # version number
                                  segment_size,
                                  data_size,
                                  offsets['data'],
                                  offsets['plaintext_hash_tree'], # UNUSED
                                  offsets['crypttext_hash_tree'],
                                  offsets['block_hashes'],
                                  offsets['share_hashes'],
                                  offsets['uri_extension'],
                                  )
        assert len(offset_data) == 0x44, len(offset_data)
        self._offset_data = offset_data

class ReadBucketProxy:
    implements(IStorageBucketReader)
    def __init__(self, rref, peerid, storage_index):
        self._rref = rref
        self._peerid = peerid
        peer_id_s = idlib.shortnodeid_b2a(peerid)
        storage_index_s = storage.si_b2a(storage_index)
        self._reprstr = "<ReadBucketProxy to peer [%s] SI %s>" % (peer_id_s, storage_index_s)
        self._started = False

    def get_peerid(self):
        return self._peerid

    def __repr__(self):
        return self._reprstr

    def startIfNecessary(self):
        if self._started:
            return defer.succeed(self)
        d = self.start()
        d.addCallback(lambda res: self)
        return d

    def start(self):
        # TODO: for small shares, read the whole bucket in start()
        d = self._read(0, 0x44)
        d.addCallback(self._parse_offsets)
        def _started(res):
            self._started = True
            return res
        d.addCallback(_started)
        return d

    def _parse_offsets(self, data):
        precondition(len(data) >= 0x4)
        self._offsets = {}
        (version,) = struct.unpack(">L", data[0:4])
        _assert(version in (1,2))

        if version == 1:
            precondition(len(data) >= 0x24)
            x = 0x0c
            fieldsize = 0x4
            fieldstruct = ">L"
            (self._segment_size,
             self._data_size) = struct.unpack(">LL", data[0x4:0xc])
        else:
            precondition(len(data) >= 0x44)
            x = 0x14
            fieldsize = 0x8
            fieldstruct = ">Q"
            (self._segment_size,
             self._data_size) = struct.unpack(">QQ", data[0x4:0x14])

        self._version = version
        self._fieldsize = fieldsize
        self._fieldstruct = fieldstruct

        for field in ( 'data',
                       'plaintext_hash_tree', # UNUSED
                       'crypttext_hash_tree',
                       'block_hashes',
                       'share_hashes',
                       'uri_extension',
                       ):
            offset = struct.unpack(fieldstruct, data[x:x+fieldsize])[0]
            x += fieldsize
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
        d = self._read(offset, self._fieldsize)
        def _got_length(data):
            length = struct.unpack(self._fieldstruct, data)[0]
            return self._read(offset+self._fieldsize, length)
        d.addCallback(_got_length)
        return d

    def _read(self, offset, length):
        return self._rref.callRemote("read", offset, length)
