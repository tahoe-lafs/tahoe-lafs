"""
Ported to Python 3.
"""

from __future__ import annotations

import struct
from io import BytesIO

from attrs import define, field
from zope.interface import implementer
from twisted.internet import defer
from allmydata.interfaces import IStorageBucketWriter, IStorageBucketReader, \
     FileTooLargeError, HASH_SIZE
from allmydata.util import mathutil, observer, log
from allmydata.util.assertutil import precondition
from allmydata.storage.server import si_b2a


class LayoutInvalid(Exception):
    """ There is something wrong with these bytes so they can't be
    interpreted as the kind of immutable file that I know how to download."""
    pass

class RidiculouslyLargeURIExtensionBlock(LayoutInvalid):
    """ When downloading a file, the length of the URI Extension Block was
    given as >= 2**32. This means the share data must have been corrupted, or
    else the original uploader of the file wrote a ridiculous value into the
    URI Extension Block length."""
    pass

class ShareVersionIncompatible(LayoutInvalid):
    """ When downloading a share, its format was not one of the formats we
    know how to parse."""
    pass

"""
Share data is written in a file. At the start of the file, there is a series
of four-byte big-endian offset values, which indicate where each section
starts. Each offset is measured from the beginning of the share data.

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
?   : start of uri_extension
"""

# Footnote 1: as of Tahoe v1.3.0 these fields are not used when reading, but
# they are still provided when writing so that older versions of Tahoe can
# read them.

FORCE_V2 = False # set briefly by unit tests to make small-sized V2 shares

def make_write_bucket_proxy(rref, server,
                            data_size, block_size, num_segments,
                            num_share_hashes, uri_extension_size):
    # Use layout v1 for small files, so they'll be readable by older versions
    # (<tahoe-1.3.0). Use layout v2 for large files; they'll only be readable
    # by tahoe-1.3.0 or later.
    try:
        if FORCE_V2:
            raise FileTooLargeError
        wbp = WriteBucketProxy(rref, server,
                               data_size, block_size, num_segments,
                               num_share_hashes, uri_extension_size)
    except FileTooLargeError:
        wbp = WriteBucketProxy_v2(rref, server,
                                  data_size, block_size, num_segments,
                                  num_share_hashes, uri_extension_size)
    return wbp


@define
class _WriteBuffer:
    """
    Queue up small writes to be written in a single batched larger write.
    """
    _batch_size: int
    _to_write : BytesIO = field(factory=BytesIO)
    _written_bytes : int = field(default=0)

    def queue_write(self, data: bytes) -> bool:
        """
        Queue a write.  If the result is ``False``, no further action is needed
        for now.  If the result is some ``True``, it's time to call ``flush()``
        and do a real write.
        """
        self._to_write.write(data)
        return self.get_queued_bytes() >= self._batch_size

    def flush(self) -> tuple[int, bytes]:
        """Return offset and data to be written."""
        offset = self._written_bytes
        data = self._to_write.getvalue()
        self._written_bytes += len(data)
        self._to_write = BytesIO()
        return (offset, data)

    def get_queued_bytes(self) -> int:
        """Return number of queued, unwritten bytes."""
        return self._to_write.tell()

    def get_total_bytes(self) -> int:
        """Return how many bytes were written or queued in total."""
        return self._written_bytes + self.get_queued_bytes()


@implementer(IStorageBucketWriter)
class WriteBucketProxy(object):
    """
    Note: The various ``put_`` methods need to be called in the order in which the
    bytes will get written.
    """
    fieldsize = 4
    fieldstruct = ">L"

    def __init__(self, rref, server, data_size, block_size, num_segments,
                 num_share_hashes, uri_extension_size, batch_size=1_000_000):
        self._rref = rref
        self._server = server
        self._data_size = data_size
        self._block_size = block_size
        self._num_segments = num_segments

        effective_segments = mathutil.next_power_of_k(num_segments,2)
        self._segment_hash_size = (2*effective_segments - 1) * HASH_SIZE
        # how many share hashes are included in each share? This will be
        # about ln2(num_shares).
        self._share_hashtree_size = num_share_hashes * (2+HASH_SIZE)
        self._uri_extension_size = uri_extension_size

        self._create_offsets(block_size, data_size)

        # With a ~1MB batch size, max upload speed is 1MB/(round-trip latency)
        # assuming the writing code waits for writes to finish, so 20MB/sec if
        # latency is 50ms. In the US many people only have 1MB/sec upload speed
        # as of 2022 (standard Comcast). For further discussion of how one
        # might set batch sizes see
        # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3787#comment:1.
        self._write_buffer = _WriteBuffer(batch_size)

    def get_allocated_size(self):
        return (self._offsets['uri_extension'] + self.fieldsize +
                self._uri_extension_size)

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
        return "<WriteBucketProxy for node %r>" % self._server.get_name()

    def put_header(self):
        return self._queue_write(0, self._offset_data)

    def put_block(self, segmentnum, data):
        offset = self._offsets['data'] + segmentnum * self._block_size
        assert offset + len(data) <= self._offsets['uri_extension']
        assert isinstance(data, bytes)
        if segmentnum < self._num_segments-1:
            precondition(len(data) == self._block_size,
                         len(data), self._block_size)
        else:
            precondition(len(data) == (self._data_size -
                                       (self._block_size *
                                        (self._num_segments - 1))),
                         len(data), self._block_size)
        return self._queue_write(offset, data)

    def put_crypttext_hashes(self, hashes):
        # plaintext_hash_tree precedes crypttext_hash_tree. It is not used, and
        # so is not explicitly written, but we need to write everything, so
        # fill it in with nulls.
        d = self._queue_write(self._offsets['plaintext_hash_tree'], b"\x00" * self._segment_hash_size)
        d.addCallback(lambda _: self._really_put_crypttext_hashes(hashes))
        return d

    def _really_put_crypttext_hashes(self, hashes):
        offset = self._offsets['crypttext_hash_tree']
        assert isinstance(hashes, list)
        data = b"".join(hashes)
        precondition(len(data) == self._segment_hash_size,
                     len(data), self._segment_hash_size)
        precondition(offset + len(data) <= self._offsets['block_hashes'],
                     offset, len(data), offset+len(data),
                     self._offsets['block_hashes'])
        return self._queue_write(offset, data)

    def put_block_hashes(self, blockhashes):
        offset = self._offsets['block_hashes']
        assert isinstance(blockhashes, list)
        data = b"".join(blockhashes)
        precondition(len(data) == self._segment_hash_size,
                     len(data), self._segment_hash_size)
        precondition(offset + len(data) <= self._offsets['share_hashes'],
                     offset, len(data), offset+len(data),
                     self._offsets['share_hashes'])
        return self._queue_write(offset, data)

    def put_share_hashes(self, sharehashes):
        # sharehashes is a list of (index, hash) tuples, so they get stored
        # as 2+32=34 bytes each
        offset = self._offsets['share_hashes']
        assert isinstance(sharehashes, list)
        data = b"".join([struct.pack(">H", hashnum) + hashvalue
                        for hashnum,hashvalue in sharehashes])
        precondition(len(data) == self._share_hashtree_size,
                     len(data), self._share_hashtree_size)
        precondition(offset + len(data) <= self._offsets['uri_extension'],
                     offset, len(data), offset+len(data),
                     self._offsets['uri_extension'])
        return self._queue_write(offset, data)

    def put_uri_extension(self, data):
        offset = self._offsets['uri_extension']
        assert isinstance(data, bytes)
        precondition(len(data) == self._uri_extension_size)
        length = struct.pack(self.fieldstruct, len(data))
        return self._queue_write(offset, length+data)

    def _queue_write(self, offset, data):
        """
        This queues up small writes to be written in a single batched larger
        write.

        Callers of this function are expected to queue the data in order, with
        no holes.  As such, the offset is technically unnecessary, but is used
        to check the inputs.  Possibly we should get rid of it.
        """
        assert offset == self._write_buffer.get_total_bytes()
        if self._write_buffer.queue_write(data):
            return self._actually_write()
        else:
            return defer.succeed(False)

    def _actually_write(self):
        """Write data to the server."""
        offset, data = self._write_buffer.flush()
        return self._rref.callRemote("write", offset, data)

    def close(self):
        assert self._write_buffer.get_total_bytes() == self.get_allocated_size(), (
            f"{self._written_buffer.get_total_bytes_queued()} != {self.get_allocated_size()}"
        )
        if self._write_buffer.get_queued_bytes() > 0:
            d = self._actually_write()
        else:
            # No data queued, don't send empty string write.
            d = defer.succeed(True)
        d.addCallback(lambda _: self._rref.callRemote("close"))
        return d

    def abort(self):
        return self._rref.callRemote("abort").addErrback(log.err, "Error from remote call to abort an immutable write bucket")

    def get_servername(self):
        return self._server.get_name()
    def get_peerid(self):
        return self._server.get_serverid()

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

@implementer(IStorageBucketReader)
class ReadBucketProxy(object):

    def __init__(self, rref, server, storage_index):
        self._rref = rref
        self._server = server
        self._storage_index = storage_index
        self._started = False # sent request to server
        self._ready = observer.OneShotObserverList() # got response from server

    def get_peerid(self):
        return self._server.get_serverid()

    def __repr__(self):
        return "<ReadBucketProxy %r to peer [%r] SI %r>" % \
               (id(self), self._server.get_name(), si_b2a(self._storage_index))

    def _start_if_needed(self):
        """ Returns a deferred that will be fired when I'm ready to return
        data, or errbacks if the starting (header reading and parsing)
        process fails."""
        if not self._started:
            self._start()
        return self._ready.when_fired()

    def _start(self):
        self._started = True
        # TODO: for small shares, read the whole bucket in _start()
        d = self._fetch_header()
        d.addCallback(self._parse_offsets)
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

        for field_name in ( 'data',
                            'plaintext_hash_tree', # UNUSED
                            'crypttext_hash_tree',
                            'block_hashes',
                            'share_hashes',
                            'uri_extension',
                           ):
            offset = struct.unpack(fieldstruct, data[x:x+fieldsize])[0]
            x += fieldsize
            self._offsets[field_name] = offset
        return self._offsets

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

    def get_share_hashes(self):
        d = self._start_if_needed()
        d.addCallback(self._get_share_hashes)
        return d

    def _get_share_hashes(self, _ignore):
        """ Tahoe storage servers < v1.3.0 would return an error if you tried
        to read past the end of the share, so we need to use the offset and
        read just that much.

        HTTP-based storage protocol also doesn't like reading past the end.
        """
        offset = self._offsets['share_hashes']
        size = self._offsets['uri_extension'] - offset
        if size % (2+HASH_SIZE) != 0:
            raise LayoutInvalid("share hash tree corrupted -- should occupy a multiple of %d bytes, not %d bytes" % ((2+HASH_SIZE), size))
        d = self._read(offset, size)
        def _unpack_share_hashes(data):
            if len(data) != size:
                raise LayoutInvalid("share hash tree corrupted -- got a short read of the share data -- should have gotten %d, not %d bytes" % (size, len(data)))
            hashes = []
            for i in range(0, size, 2+HASH_SIZE):
                hashnum = struct.unpack(">H", data[i:i+2])[0]
                hashvalue = data[i+2:i+2+HASH_SIZE]
                hashes.append( (hashnum, hashvalue) )
            return hashes
        d.addCallback(_unpack_share_hashes)
        return d

    def _get_uri_extension(self, unused=None):
        """ Tahoe storage servers < v1.3.0 would return an error if you tried
        to read past the end of the share, so we need to fetch the UEB size
        and then read just that much.

        HTTP-based storage protocol also doesn't like reading past the end.
        """
        offset = self._offsets['uri_extension']
        d = self._read(offset, self._fieldsize)
        def _got_length(data):
            if len(data) != self._fieldsize:
                raise LayoutInvalid("not enough bytes to encode URI length -- should be %d bytes long, not %d " % (self._fieldsize, len(data),))
            length = struct.unpack(self._fieldstruct, data)[0]
            if length >= 2000:
                # URI extension blocks are around 419 bytes long; in previous
                # versions of the code 1000 was used as a default catchall. So
                # 2000 or more must be corrupted.
                raise RidiculouslyLargeURIExtensionBlock(length)

            return self._read(offset+self._fieldsize, length)
        d.addCallback(_got_length)
        return d

    def get_uri_extension(self):
        d = self._start_if_needed()
        d.addCallback(self._get_uri_extension)
        return d

    def _read(self, offset, length):
        return self._rref.callRemote("read", offset, length)
