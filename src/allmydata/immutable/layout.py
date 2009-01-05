import struct
from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IStorageBucketWriter, IStorageBucketReader, \
     FileTooLargeError, HASH_SIZE
from allmydata.util import log, mathutil, idlib, observer
from allmydata.util.assertutil import precondition
from allmydata import storage

class LayoutInvalid(Exception):
    """ There is something wrong with these bytes so they can't be interpreted as the kind of
    immutable file that I know how to download. """
    pass

class RidiculouslyLargeURIExtensionBlock(LayoutInvalid):
    """ When downloading a file, the length of the URI Extension Block was given as >= 2**32.
    This means the share data must have been corrupted, or else the original uploader of the
    file wrote a ridiculous value into the URI Extension Block length. """
    pass

class ShareVersionIncompatible(LayoutInvalid):
    """ When downloading a share, its format was not one of the formats we know how to
    parse. """
    pass

"""
Share data is written in a file. At the start of the file, there is a series of four-byte
big-endian offset values, which indicate where each section starts. Each offset is measured from
the beginning of the share data.

0x00: version number (=00 00 00 01)
0x04: block size # See Footnote 1 below.
0x08: share data size # See Footnote 1 below.
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
       followed by the 32-byte SHA-256 hash. We store only the hashes
       necessary to validate the share hash root
?   : start of uri_extension_length (four-byte big-endian value)
?   : start of uri_extension
"""

"""
v2 shares: these use 8-byte offsets to remove two of the three ~12GiB size
limitations described in #346.

0x00: version number (=00 00 00 02)
0x04: block size # See Footnote 1 below.
0x0c: share data size # See Footnote 1 below.
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

# Footnote 1: as of Tahoe v1.3.0 these fields are not used when reading, but they are still
# provided when writing so that older versions of Tahoe can read them.

def allocated_size(data_size, num_segments, num_share_hashes,
                   uri_extension_size_max):
    wbp = WriteBucketProxy(None, data_size, 0, num_segments, num_share_hashes,
                           uri_extension_size_max, None)
    uri_extension_starts_at = wbp._offsets['uri_extension']
    return uri_extension_starts_at + wbp.fieldsize + uri_extension_size_max

class WriteBucketProxy:
    implements(IStorageBucketWriter)
    fieldsize = 4
    fieldstruct = ">L"

    def __init__(self, rref, data_size, block_size, num_segments,
                 num_share_hashes, uri_extension_size_max, nodeid):
        self._rref = rref
        self._data_size = data_size
        self._block_size = block_size
        self._num_segments = num_segments
        self._nodeid = nodeid

        effective_segments = mathutil.next_power_of_k(num_segments,2)
        self._segment_hash_size = (2*effective_segments - 1) * HASH_SIZE
        # how many share hashes are included in each share? This will be
        # about ln2(num_shares).
        self._share_hashtree_size = num_share_hashes * (2+HASH_SIZE)
        # we commit to not sending a uri extension larger than this
        self._uri_extension_size_max = uri_extension_size_max

        self._create_offsets(block_size, data_size)

    def _create_offsets(self, block_size, data_size):
        if block_size >= 2**32 or data_size >= 2**32:
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
        x += self._share_hashtree_size
        offsets['uri_extension'] = x

        if x >= 2**32:
            raise FileTooLargeError("This file is too large to be uploaded (offsets).")

        offset_data = struct.pack(">LLLLLLLLL",
                                  1, # version number
                                  block_size,
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

    def put_header(self):
        return self._write(0, self._offset_data)

    def put_block(self, segmentnum, data):
        offset = self._offsets['data'] + segmentnum * self._block_size
        assert offset + len(data) <= self._offsets['uri_extension']
        assert isinstance(data, str)
        if segmentnum < self._num_segments-1:
            precondition(len(data) == self._block_size,
                         len(data), self._block_size)
        else:
            precondition(len(data) == (self._data_size -
                                       (self._block_size *
                                        (self._num_segments - 1))),
                         len(data), self._block_size)
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
        precondition(len(data) == self._share_hashtree_size,
                     len(data), self._share_hashtree_size)
        precondition(offset + len(data) <= self._offsets['uri_extension'],
                     offset, len(data), offset+len(data),
                     self._offsets['uri_extension'])
        return self._write(offset, data)

    def put_uri_extension(self, data):
        offset = self._offsets['uri_extension']
        assert isinstance(data, str)
        precondition(len(data) <= self._uri_extension_size_max,
                     len(data), self._uri_extension_size_max)
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

    def _create_offsets(self, block_size, data_size):
        if block_size >= 2**64 or data_size >= 2**64:
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
        x += self._share_hashtree_size
        offsets['uri_extension'] = x

        if x >= 2**64:
            raise FileTooLargeError("This file is too large to be uploaded (offsets).")

        offset_data = struct.pack(">LQQQQQQQQ",
                                  2, # version number
                                  block_size,
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

    MAX_UEB_SIZE = 2000 # actual size is closer to 419, but varies by a few bytes

    def __init__(self, rref, peerid, storage_index):
        self._rref = rref
        self._peerid = peerid
        peer_id_s = idlib.shortnodeid_b2a(peerid)
        storage_index_s = storage.si_b2a(storage_index)
        self._reprstr = "<ReadBucketProxy %s to peer [%s] SI %s>" % (id(self), peer_id_s, storage_index_s)
        self._started = False # sent request to server
        self._ready = observer.OneShotObserverList() # got response from server

    def get_peerid(self):
        return self._peerid

    def __repr__(self):
        return self._reprstr

    def _start_if_needed(self):
        """ Returns a deferred that will be fired when I'm ready to return data, or errbacks if
        the starting (header reading and parsing) process fails."""
        if not self._started:
            self._start()
        return self._ready.when_fired()

    def _start(self):
        self._started = True
        # TODO: for small shares, read the whole bucket in _start()
        d = self._fetch_header()
        d.addCallback(self._parse_offsets)
        # XXX The following two callbacks implement a slightly faster/nicer way to get the ueb
        # and sharehashtree, but it requires that the storage server be >= v1.3.0.
        # d.addCallback(self._fetch_sharehashtree_and_ueb)
        # d.addCallback(self._parse_sharehashtree_and_ueb)
        def _fail_waiters(f):
            self._ready.fire(f)
        def _notify_waiters(result):
            self._ready.fire(result)
        d.addCallbacks(_notify_waiters, _fail_waiters)
        return d

    def _fetch_header(self):
        return self._read(0, 0x44)

    def _parse_offsets(self, data):
        precondition(len(data) >= 0x4)
        self._offsets = {}
        (version,) = struct.unpack(">L", data[0:4])
        if version != 1 and version != 2:
            raise ShareVersionIncompatible(version)

        if version == 1:
            precondition(len(data) >= 0x24)
            x = 0x0c
            fieldsize = 0x4
            fieldstruct = ">L"
        else:
            precondition(len(data) >= 0x44)
            x = 0x14
            fieldsize = 0x8
            fieldstruct = ">Q"

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

    def _fetch_sharehashtree_and_ueb(self, offsets):
        sharehashtree_size = offsets['uri_extension'] - offsets['share_hashes']
        return self._read(offsets['share_hashes'], self.MAX_UEB_SIZE+sharehashtree_size)

    def _parse_sharehashtree_and_ueb(self, data):
        sharehashtree_size = self._offsets['uri_extension'] - self._offsets['share_hashes']
        if len(data) < sharehashtree_size:
            raise LayoutInvalid("share hash tree truncated -- should have at least %d bytes -- not %d" % (sharehashtree_size, len(data)))
        if sharehashtree_size % (2+HASH_SIZE) != 0:
            raise LayoutInvalid("share hash tree malformed -- should have an even multiple of %d bytes -- not %d" % (2+HASH_SIZE, sharehashtree_size))
        self._share_hashes = []
        for i in range(0, sharehashtree_size, 2+HASH_SIZE):
            hashnum = struct.unpack(">H", data[i:i+2])[0]
            hashvalue = data[i+2:i+2+HASH_SIZE]
            self._share_hashes.append( (hashnum, hashvalue) )

        i = self._offsets['uri_extension']-self._offsets['share_hashes']
        if len(data) < i+self._fieldsize:
            raise LayoutInvalid("not enough bytes to encode URI length -- should be at least %d bytes long, not %d " % (i+self._fieldsize, len(data),))
        length = struct.unpack(self._fieldstruct, data[i:i+self._fieldsize])[0]
        self._ueb_data = data[i+self._fieldsize:i+self._fieldsize+length]

    def _get_block_data(self, unused, blocknum, blocksize, thisblocksize):
        offset = self._offsets['data'] + blocknum * blocksize
        return self._read(offset, thisblocksize)

    def get_block_data(self, blocknum, blocksize, thisblocksize):
        d = self._start_if_needed()
        d.addCallback(self._get_block_data, blocknum, blocksize, thisblocksize)
        return d

    def _str2l(self, s):
        """ split string (pulled from storage) into a list of blockids """
        return [ s[i:i+HASH_SIZE]
                 for i in range(0, len(s), HASH_SIZE) ]

    def _get_crypttext_hashes(self, unused=None):
        offset = self._offsets['crypttext_hash_tree']
        size = self._offsets['block_hashes'] - offset
        d = self._read(offset, size)
        d.addCallback(self._str2l)
        return d

    def get_crypttext_hashes(self):
        d = self._start_if_needed()
        d.addCallback(self._get_crypttext_hashes)
        return d

    def _get_block_hashes(self, unused=None, at_least_these=()):
        # TODO: fetch only at_least_these instead of all of them.
        offset = self._offsets['block_hashes']
        size = self._offsets['share_hashes'] - offset
        d = self._read(offset, size)
        d.addCallback(self._str2l)
        return d

    def get_block_hashes(self, at_least_these=()):
        if at_least_these:
            d = self._start_if_needed()
            d.addCallback(self._get_block_hashes, at_least_these)
            return d
        else:
            return defer.succeed([])

    def _get_share_hashes(self, unused=None):
        if hasattr(self, '_share_hashes'):
            return self._share_hashes
        else:
            return self._get_share_hashes_the_old_way()
        return self._share_hashes

    def get_share_hashes(self):
        d = self._start_if_needed()
        d.addCallback(self._get_share_hashes)
        return d

    def _get_share_hashes_the_old_way(self):
        """ Tahoe storage servers < v1.3.0 would return an error if you tried to read past the
        end of the share, so we need to use the offset and read just that much."""
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

    def _get_uri_extension_the_old_way(self, unused=None):
        """ Tahoe storage servers < v1.3.0 would return an error if you tried to read past the
        end of the share, so we need to fetch the UEB size and then read just that much."""
        offset = self._offsets['uri_extension']
        d = self._read(offset, self._fieldsize)
        def _got_length(data):
            if len(data) != self._fieldsize:
                raise LayoutInvalid("not enough bytes to encode URI length -- should be %d bytes long, not %d " % (self._fieldsize, len(data),))
            length = struct.unpack(self._fieldstruct, data)[0]
            if length >= 2**31:
                # URI extension blocks are around 419 bytes long, so this must be corrupted.
                # Anyway, the foolscap interface schema for "read" will not allow >= 2**31 bytes
                # length.
                raise RidiculouslyLargeURIExtensionBlock(length)

            return self._read(offset+self._fieldsize, length)
        d.addCallback(_got_length)
        return d

    def _get_uri_extension(self, unused=None):
        if hasattr(self, '_ueb_data'):
            return self._ueb_data
        else:
            return self._get_uri_extension_the_old_way()

    def get_uri_extension(self):
        d = self._start_if_needed()
        d.addCallback(self._get_uri_extension)
        return d

    def _read(self, offset, length):
        return self._rref.callRemote("read", offset, length)
