
import struct

from cStringIO import StringIO

from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import IShareForReading, IShareForWriting

from allmydata.util.assertutil import precondition, _assert
from allmydata.util.mathutil import div_ceil
from allmydata.storage.common import CorruptStoredShareError, UnknownImmutableContainerVersionError, \
     DataTooLargeError
from allmydata.storage.backends.cloud import cloud_common
from allmydata.storage.backends.cloud.cloud_common import get_chunk_key, \
     BackpressurePipeline, ChunkCache, CloudShareBase, CloudShareReaderMixin


# Each share file (stored in the chunks with keys 'shares/$PREFIX/$STORAGEINDEX/$SHNUM.$CHUNK')
# contains lease information [currently inaccessible] and share data. The share data is
# accessed by RIBucketWriter.write and RIBucketReader.read .

# The share file has the following layout:
#  0x00: share file version number, four bytes, current version is 1
#  0x04: always zero (was share data length prior to Tahoe-LAFS v1.3.0)
#  0x08: number of leases, four bytes big-endian
#  0x0c: beginning of share data (see immutable.layout.WriteBucketProxy)
#  data_length + 0x0c: first lease. Each lease record is 72 bytes. (not used)


class ImmutableCloudShareMixin:
    sharetype = "immutable"
    LEASE_SIZE = struct.calcsize(">L32s32sL")  # for compatibility
    HEADER = ">LLL"
    HEADER_SIZE = struct.calcsize(HEADER)
    DATA_OFFSET = HEADER_SIZE


class ImmutableCloudShareForWriting(CloudShareBase, ImmutableCloudShareMixin):
    implements(IShareForWriting)

    def __init__(self, container, storage_index, shnum, allocated_data_length, incomingset):
        """
        I won't allow more than allocated_data_length to be written to me.
        """
        precondition(isinstance(allocated_data_length, (int, long)), allocated_data_length)
        CloudShareBase.__init__(self, container, storage_index, shnum)

        self._chunksize = cloud_common.PREFERRED_CHUNK_SIZE
        self._allocated_data_length = allocated_data_length

        self._buf = StringIO()
        # The second field, which was the four-byte share data length in
        # Tahoe-LAFS versions prior to 1.3.0, is not used; we always write 0.
        # We also write 0 for the number of leases.
        self._buf.write(struct.pack(self.HEADER, 1, 0, 0) )
        self._set_size(self._buf.tell())
        self._current_chunknum = 0

        self._incomingset = incomingset
        self._incomingset.add( (storage_index, shnum) )

        self._pipeline = BackpressurePipeline(cloud_common.PIPELINE_DEPTH)

    def _set_size(self, size):
        self._total_size = size
        self._data_length = size - self.DATA_OFFSET  # no leases

    def get_allocated_data_length(self):
        return self._allocated_data_length

    def write_share_data(self, offset, data):
        """Write 'data' at position 'offset' past the end of the header."""
        seekpos = self.DATA_OFFSET + offset
        precondition(seekpos >= self._total_size, offset=offset, seekpos=seekpos, total_size=self._total_size)
        if offset + len(data) > self._allocated_data_length:
            raise DataTooLargeError(self._allocated_data_length, offset, len(data))

        self._set_size(self._total_size + len(data))
        return self._store_or_buffer( (seekpos, data, 0) )

    def close(self):
        chunkdata = self._buf.getvalue()
        self._discard()
        d = self._pipeline_store_next_chunk(chunkdata)
        d.addCallback(lambda ign: self._pipeline.close())
        return d

    def _store_or_buffer(self, (seekpos, b, b_offset) ):
        """
        Helper method that stores the next complete chunk to the container or buffers
        an incomplete chunk. The data still to be written is b[b_offset:], but we may
        only process part of it in this call.
        """
        chunknum = seekpos / self._chunksize
        offset_in_chunk = seekpos % self._chunksize

        _assert(chunknum >= self._current_chunknum, seekpos=seekpos, chunknum=chunknum,
                current_chunknum=self._current_chunknum)

        if chunknum > self._current_chunknum or offset_in_chunk + (len(b) - b_offset) >= self._chunksize:
            if chunknum > self._current_chunknum:
                # The write left a gap that spans a chunk boundary. Fill with zeroes to the end
                # of the current chunk and store it.
                # TODO: test this case
                self._buf.seek(self._chunksize - 1)
                self._buf.write("\x00")
            else:
                # Store a complete chunk.
                writelen = self._chunksize - offset_in_chunk
                self._buf.seek(offset_in_chunk)
                self._buf.write(b[b_offset : b_offset + writelen])
                seekpos += writelen
                b_offset += writelen

            chunkdata = self._buf.getvalue()
            self._buf = StringIO()
            _assert(len(chunkdata) == self._chunksize, len_chunkdata=len(chunkdata), chunksize=self._chunksize)

            d2 = self._pipeline_store_next_chunk(chunkdata)
            d2.addCallback(lambda ign: self._store_or_buffer( (seekpos, b, b_offset) ))
            return d2
        else:
            # Buffer an incomplete chunk.
            if b_offset > 0:
                b = b[b_offset :]
            self._buf.seek(offset_in_chunk)
            self._buf.write(b)
            return defer.succeed(None)

    def _pipeline_store_next_chunk(self, chunkdata):
        chunkkey = get_chunk_key(self._key, self._current_chunknum)
        self._current_chunknum += 1
        #print "STORING", chunkkey, len(chunkdata)

        # We'd like to stream writes, but the supported service containers
        # (and the IContainer interface) don't support that yet. For txaws, see
        # https://bugs.launchpad.net/txaws/+bug/767205 and
        # https://bugs.launchpad.net/txaws/+bug/783801
        return self._pipeline.add(1, self._container.put_object, chunkkey, chunkdata)

    def _discard(self):
        self._buf = None
        self._incomingset.discard( (self.get_storage_index(), self.get_shnum()) )


class ImmutableCloudShareForReading(CloudShareBase, ImmutableCloudShareMixin, CloudShareReaderMixin):
    implements(IShareForReading)

    def __init__(self, container, storage_index, shnum, total_size, first_chunkdata):
        CloudShareBase.__init__(self, container, storage_index, shnum)

        precondition(isinstance(total_size, (int, long)), total_size=total_size)
        precondition(isinstance(first_chunkdata, str), type(first_chunkdata))
        precondition(len(first_chunkdata) <= total_size, len_first_chunkdata=len(first_chunkdata), total_size=total_size)

        chunksize = len(first_chunkdata)
        if chunksize < self.HEADER_SIZE:
            msg = "%r had incomplete header (%d bytes)" % (self, chunksize)
            raise UnknownImmutableContainerVersionError(shnum, msg)

        self._total_size = total_size
        self._chunksize = chunksize
        nchunks = div_ceil(total_size, chunksize)
        initial_cachemap = {0: defer.succeed(first_chunkdata)}
        self._cache = ChunkCache(container, self._key, chunksize, nchunks, initial_cachemap)
        #print "ImmutableCloudShareForReading", total_size, chunksize, self._key

        header = first_chunkdata[:self.HEADER_SIZE]
        try:
            (version, unused, num_leases) = struct.unpack(self.HEADER, header)
        except struct.error, e:
            raise CorruptStoredShareError(shnum, "invalid immutable share header for shnum %d: %s" % (shnum, e))

        if version != 1:
            msg = "%r had version %d but we wanted 1" % (self, version)
            raise UnknownImmutableContainerVersionError(shnum, msg)

        # We cannot write leases in share files, but allow them to be present
        # in case a share file is copied from a disk backend, or in case we
        # need them in future.
        self._data_length = total_size - self.DATA_OFFSET - (num_leases * self.LEASE_SIZE)

        if self._data_length < 0:
            raise CorruptStoredShareError("calculated data length for shnum %d is %d" % (shnum, self._data_length))

    # Boilerplate is in CloudShareBase, read implementation is in CloudShareReaderMixin.
    # So nothing to implement here. Yay!

    def _discard(self):
        pass
