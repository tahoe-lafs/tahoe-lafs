
import struct
from collections import deque

from twisted.internet import defer
from allmydata.util.deferredutil import gatherResults, async_iterate

from zope.interface import implements

from allmydata.interfaces import IMutableShare, BadWriteEnablerError
from allmydata.util import idlib, log
from allmydata.util.assertutil import precondition, _assert
from allmydata.util.mathutil import div_ceil
from allmydata.util.hashutil import timing_safe_compare
from allmydata.storage.common import UnknownMutableContainerVersionError, DataTooLargeError
from allmydata.storage.backends.base import testv_compare
from allmydata.mutable.layout import MUTABLE_MAGIC, MAX_MUTABLE_SHARE_SIZE
from allmydata.storage.backends.cloud import cloud_common
from allmydata.storage.backends.cloud.cloud_common import get_chunk_key, get_zero_chunkdata, \
     delete_chunks, BackpressurePipeline, ChunkCache, CloudShareBase, CloudShareReaderMixin


# Mutable shares have a different layout to immutable shares. See docs/mutable.rst
# for more details.

# #   offset    size    name
# 1   0         32      magic verstr "tahoe mutable container v1" plus binary
# 2   32        20      write enabler's nodeid
# 3   52        32      write enabler
# 4   84        8       data size (actual share data present) (a)
# 5   92        8       offset of (8) count of extra leases (after data)
# 6   100       368     four leases, 92 bytes each, unused
# 7   468       (a)     data
# 8   ??        4       count of extra leases
# 9   ??        n*92    extra leases


# The struct module doc says that L's are 4 bytes in size, and that Q's are
# 8 bytes in size. Since compatibility depends upon this, double-check it.
assert struct.calcsize(">L") == 4, struct.calcsize(">L")
assert struct.calcsize(">Q") == 8, struct.calcsize(">Q")


class Namespace(object):
    pass


class MutableCloudShare(CloudShareBase, CloudShareReaderMixin):
    implements(IMutableShare)

    sharetype = "mutable"
    DATA_LENGTH_OFFSET = struct.calcsize(">32s20s32s")
    EXTRA_LEASE_OFFSET = DATA_LENGTH_OFFSET + 8
    HEADER = ">32s20s32sQQ"
    HEADER_SIZE = struct.calcsize(HEADER) # doesn't include leases
    LEASE_SIZE = struct.calcsize(">LL32s32s20s")
    assert LEASE_SIZE == 92, LEASE_SIZE
    DATA_OFFSET = HEADER_SIZE + 4*LEASE_SIZE
    assert DATA_OFFSET == 468, DATA_OFFSET
    NUM_EXTRA_LEASES_SIZE = struct.calcsize(">L")

    MAGIC = MUTABLE_MAGIC
    assert len(MAGIC) == 32
    MAX_SIZE = MAX_MUTABLE_SHARE_SIZE

    def __init__(self, container, storage_index, shnum, total_size, first_chunkdata, parent=None):
        CloudShareBase.__init__(self, container, storage_index, shnum)

        precondition(isinstance(total_size, (int, long)), total_size=total_size)
        precondition(isinstance(first_chunkdata, str), type(first_chunkdata))
        precondition(len(first_chunkdata) <= total_size, "total size is smaller than first chunk",
                     len_first_chunkdata=len(first_chunkdata), total_size=total_size)

        if len(first_chunkdata) < self.HEADER_SIZE:
            msg = "%r had incomplete header (%d bytes)" % (self, len(first_chunkdata))
            raise UnknownMutableContainerVersionError(msg)

        header = first_chunkdata[:self.HEADER_SIZE]
        (magic, write_enabler_nodeid, real_write_enabler,
         data_length, extra_lease_offset) = struct.unpack(self.HEADER, header)

        if magic != self.MAGIC:
            msg = "%r had magic %r but we wanted %r" % (self, magic, self.MAGIC)
            raise UnknownMutableContainerVersionError(msg)

        self._write_enabler_nodeid = write_enabler_nodeid
        self._real_write_enabler = real_write_enabler

        # We want to support changing PREFERRED_CHUNK_SIZE without breaking compatibility,
        # but without "rechunking" any existing shares. Also, existing shares created by
        # the pre-chunking code should be handled correctly.

        # If there is more than one chunk, the chunksize must be equal to the size of the
        # first chunk, to avoid rechunking.
        self._chunksize = len(first_chunkdata)
        if self._chunksize == total_size:
            # There is only one chunk, so we are at liberty to make the chunksize larger
            # than that chunk, but not smaller.
            self._chunksize = max(self._chunksize, cloud_common.PREFERRED_CHUNK_SIZE)

        self._zero_chunkdata = get_zero_chunkdata(self._chunksize)

        initial_cachemap = {0: defer.succeed(first_chunkdata)}
        self._cache = ChunkCache(container, self._key, self._chunksize, initial_cachemap=initial_cachemap)
        #print "CONSTRUCT %s with %r" % (object.__repr__(self), self._cache)
        self._data_length = data_length
        self._set_total_size(self.DATA_OFFSET + data_length + self.NUM_EXTRA_LEASES_SIZE)

        # The initial total size may not be less than the size of header + data + extra lease count.
        # TODO: raise a better exception.
        _assert(total_size >= self._total_size, share=repr(self),
                total_size=total_size, self_total_size=self._total_size, data_length=data_length)
        self._is_oversize = total_size > self._total_size

        self._pipeline = BackpressurePipeline(cloud_common.PIPELINE_DEPTH)

        self.parent = parent # for logging

    def _set_total_size(self, total_size):
        self._total_size = total_size
        self._nchunks = div_ceil(self._total_size, self._chunksize)
        self._cache.set_nchunks(self._nchunks)

    def log(self, *args, **kwargs):
        if self.parent:
            return self.parent.log(*args, **kwargs)

    @classmethod
    def create_empty_share(cls, container, serverid, write_enabler, storage_index=None, shnum=None, parent=None):
        # Unlike the disk backend, we don't check that the cloud object does not exist;
        # we assume that it does not because create was used, and no-one else should be
        # writing to the bucket.

        # There are no extra leases, but for compatibility, the offset they would have
        # still needs to be stored in the header.
        data_length = 0
        extra_lease_offset = cls.DATA_OFFSET + data_length
        header = struct.pack(cls.HEADER, cls.MAGIC, serverid, write_enabler,
                             data_length, extra_lease_offset)
        leases = "\x00"*(cls.LEASE_SIZE * 4)
        extra_lease_count = struct.pack(">L", 0)
        first_chunkdata = header + leases + extra_lease_count

        share = cls(container, storage_index, shnum, len(first_chunkdata), first_chunkdata, parent=parent)

        d = share._raw_writev(deque([(0, first_chunkdata)]), 0, 0)
        d.addCallback(lambda ign: share)
        return d

    def _discard(self):
        # TODO: discard read cache
        pass

    def check_write_enabler(self, write_enabler):
        # avoid a timing attack
        if not timing_safe_compare(write_enabler, self._real_write_enabler):
            # accomodate share migration by reporting the nodeid used for the
            # old write enabler.
            self.log(format="bad write enabler on SI %(si)s,"
                     " recorded by nodeid %(nodeid)s",
                     facility="tahoe.storage",
                     level=log.WEIRD, umid="DF2fCR",
                     si=self.get_storage_index_string(),
                     nodeid=idlib.nodeid_b2a(self._write_enabler_nodeid))
            msg = "The write enabler was recorded by nodeid '%s'." % \
                  (idlib.nodeid_b2a(self._write_enabler_nodeid),)
            raise BadWriteEnablerError(msg)
        return defer.succeed(None)

    def check_testv(self, testv):
        def _test( (offset, length, operator, specimen) ):
            d = self.read_share_data(offset, length)
            d.addCallback(lambda data: testv_compare(data, operator, specimen))
            return d
        return async_iterate(_test, sorted(testv))

    def writev(self, datav, new_length):
        precondition(new_length is None or new_length >= 0, new_length=new_length)

        raw_datav, preserved_size, new_data_length = self._prepare_writev(datav, new_length)
        return self._raw_writev(raw_datav, preserved_size, new_data_length)

    def _prepare_writev(self, datav, new_length):
        # Translate the client's write vector and 'new_length' into a "raw" write vector
        # and new total size. This has no side effects to make it easier to test.

        preserved_size = self.DATA_OFFSET + self._data_length

        # chunk containing the byte after the current end-of-data
        endofdata_chunknum = preserved_size / self._chunksize

        # Whether we need to add a dummy write to zero-extend the end-of-data chunk.
        ns = Namespace()
        ns.need_zeroextend_write = preserved_size % self._chunksize != 0

        raw_datav = deque()
        def _add_write(seekpos, data):
            #print "seekpos =", seekpos
            raw_datav.append( (seekpos, data) )

            lastpos = seekpos + len(data) - 1
            start_chunknum = seekpos / self._chunksize
            last_chunknum  = lastpos / self._chunksize
            if start_chunknum <= endofdata_chunknum and endofdata_chunknum <= last_chunknum:
                # If any of the client's writes overlaps the end-of-data chunk, we should not
                # add the zero-extending dummy write.
                ns.need_zeroextend_write = False

        #print "need_zeroextend_write =", ns.need_zeroextend_write
        new_data_length = self._data_length

        # Validate the write vector and translate its offsets into seek positions from
        # the start of the share.
        for (offset, data) in datav:
            length = len(data)
            precondition(offset >= 0, offset=offset)
            if offset + length > self.MAX_SIZE:
                raise DataTooLargeError()

            if new_length is not None and new_length < offset + length:
                length = max(0, new_length - offset)
                data = data[: length]

            new_data_length = max(new_data_length, offset + length)
            if length > 0:
                _add_write(self.DATA_OFFSET + offset, data)

        # new_length can only be used to truncate, not extend.
        if new_length is not None:
            new_data_length = min(new_length, new_data_length)

        # If the data length has changed, include additional raw writes to the data length
        # field in the header, and to the extra lease count field after the data.
        #
        # Also do this if there were extra leases (e.g. if this was a share copied from a
        # disk backend), so that they will be deleted. If the size hasn't changed and there
        # are no extra leases, we don't bother to ensure that the extra lease count field is
        # zero; it is ignored anyway.
        if new_data_length != self._data_length or self._is_oversize:
            extra_lease_offset = self.DATA_OFFSET + new_data_length

            # Don't preserve old data past the new end-of-data.
            preserved_size = min(preserved_size, extra_lease_offset)

            # These are disjoint with any ranges already in raw_datav.
            _add_write(self.DATA_LENGTH_OFFSET, struct.pack(">Q", new_data_length))
            _add_write(extra_lease_offset, struct.pack(">L", 0))

        #print "need_zeroextend_write =", ns.need_zeroextend_write
        # If the data length is being increased and there are no other writes to the
        # current end-of-data chunk (including the two we just added), add a dummy write
        # of one zero byte at the end of that chunk. This will cause that chunk to be
        # zero-extended to the full chunk size, which would not otherwise happen.
        if new_data_length > self._data_length and ns.need_zeroextend_write:
            _add_write((endofdata_chunknum + 1)*self._chunksize - 1, "\x00")

        # Sorting the writes simplifies things (and we need all the simplification we can get :-)
        raw_datav = deque(sorted(raw_datav, key=lambda (offset, data): offset))

        # Complain if write vector elements overlap, that's too hard in general.
        (last_seekpos, last_data) = (0, "")
        have_duplicates = False
        for (i, (seekpos, data)) in enumerate(raw_datav):
            # The MDMF publisher in 1.9.0 and 1.9.1 produces duplicated writes to the MDMF header.
            # If this is an exactly duplicated write, skip it.
            if seekpos == last_seekpos and data == last_data:
                raw_datav[i] = None
                have_duplicates = True
            else:
                last_endpos = last_seekpos + len(last_data)
                _assert(seekpos >= last_endpos, "overlapping write vector elements",
                        seekpos=seekpos, last_seekpos=last_seekpos, last_endpos=last_endpos)
            (last_seekpos, last_data) = (seekpos, data)

        if have_duplicates:
            raw_datav.remove(None)

        # Return a vector of writes to ranges in the share, the size of previous contents to
        # be preserved, and the final data length.
        return (raw_datav, preserved_size, new_data_length)

    def _raw_writev(self, raw_datav, preserved_size, new_data_length):
        #print "%r._raw_writev(%r, %r, %r)" % (self, raw_datav, preserved_size, new_data_length)

        old_nchunks = self._nchunks

        # The _total_size and _nchunks attributes are updated as each write is applied.
        self._set_total_size(preserved_size)

        final_size = self.DATA_OFFSET + new_data_length + self.NUM_EXTRA_LEASES_SIZE

        d = self._raw_write_share_data(None, raw_datav, final_size)

        def _resize(ign):
            self._data_length = new_data_length
            self._set_total_size(final_size)

            if self._nchunks < old_nchunks or self._is_oversize:
                self._is_oversize = False
                #print "DELETING chunks from", self._nchunks
                return delete_chunks(self._container, self._key, from_chunknum=self._nchunks)
        d.addCallback(_resize)

        d.addCallback(lambda ign: self._pipeline.flush())
        return d

    def _raw_write_share_data(self, ign, raw_datav, final_size):
        """
        raw_datav:  (deque of (integer, str)) the remaining raw write vector
        final_size: (integer) the size the file will be after all writes in the writev
        """
        #print "%r._raw_write_share_data(%r, %r)" % (self, (seekpos, data), final_size)

        precondition(final_size >= 0, final_size=final_size)

        d = defer.succeed(None)
        if not raw_datav:
            return d

        (seekpos, data) = raw_datav.popleft()
        _assert(seekpos >= 0 and len(data) > 0, seekpos=seekpos, len_data=len(data),
                len_raw_datav=len(raw_datav), final_size=final_size)

        # We *may* need to read the start chunk and/or last chunk before rewriting them.
        # (If they are the same chunk, that's fine, the cache will ensure we don't
        # read the cloud object twice.)
        lastpos = seekpos + len(data) - 1
        _assert(lastpos > 0, seekpos=seekpos, len_data=len(data), lastpos=lastpos)
        start_chunknum = seekpos / self._chunksize
        start_chunkpos = start_chunknum*self._chunksize
        start_offset   = seekpos % self._chunksize
        last_chunknum  = lastpos / self._chunksize
        last_chunkpos  = last_chunknum*self._chunksize
        last_offset    = lastpos % self._chunksize
        _assert(start_chunknum <= last_chunknum, start_chunknum=start_chunknum, last_chunknum=last_chunknum)

        #print "lastpos         =", lastpos
        #print "len(data)       =", len(data)
        #print "start_chunknum  =", start_chunknum
        #print "start_offset    =", start_offset
        #print "last_chunknum   =", last_chunknum
        #print "last_offset     =", last_offset
        #print "_total_size     =", self._total_size
        #print "_chunksize      =", self._chunksize
        #print "_nchunks        =", self._nchunks

        start_chunkdata_d = defer.Deferred()
        last_chunkdata_d = defer.Deferred()

        # Is the first byte of the start chunk preserved?
        if start_chunknum*self._chunksize < self._total_size and start_offset > 0:
            # Yes, so we need to read it first.
            d.addCallback(lambda ign: self._cache.get(start_chunknum, start_chunkdata_d))
        else:
            start_chunkdata_d.callback("")

        # Is any byte of the last chunk preserved?
        if last_chunkpos < self._total_size and lastpos < min(self._total_size, last_chunkpos + self._chunksize) - 1:
            # Yes, so we need to read it first.
            d.addCallback(lambda ign: self._cache.get(last_chunknum, last_chunkdata_d))
        else:
            last_chunkdata_d.callback("")

        d.addCallback(lambda ign: gatherResults( (start_chunkdata_d, last_chunkdata_d) ))
        def _got( (start_chunkdata, last_chunkdata) ):
            #print "start_chunkdata =", len(start_chunkdata), repr(start_chunkdata)
            #print "last_chunkdata  =", len(last_chunkdata),  repr(last_chunkdata)
            d2 = defer.succeed(None)

            # Zero any chunks from self._nchunks (i.e. after the last currently valid chunk)
            # to before the start chunk of the write.
            for zero_chunknum in xrange(self._nchunks, start_chunknum):
                d2.addCallback(self._pipeline_store_chunk, zero_chunknum, self._zero_chunkdata)

            # start_chunkdata and last_chunkdata may need to be truncated and/or zero-extended.
            start_preserved = max(0, min(len(start_chunkdata), self._total_size - start_chunkpos, start_offset))
            last_preserved  = max(0, min(len(last_chunkdata), self._total_size - last_chunkpos))

            start_chunkdata = (start_chunkdata[: start_preserved] +
                               self._zero_chunkdata[: max(0, start_offset - start_preserved)] +
                               data[: self._chunksize - start_offset])

            # last_slice_len = len(last_chunkdata[last_offset + 1 : last_preserved])
            last_slice_len  = max(0, last_preserved - (last_offset + 1))
            last_chunksize  = min(final_size - last_chunkpos, self._chunksize)
            last_chunkdata  = (last_chunkdata[last_offset + 1 : last_preserved] +
                               self._zero_chunkdata[: max(0, last_chunksize - (last_offset + 1) - last_slice_len)])

            # This loop eliminates redundant reads and writes, by merging the contents of writes
            # after this one into last_chunkdata as far as possible. It ensures that we never need
            # to read a chunk twice in the same writev (which is needed for correctness; see below).
            while raw_datav:
                # Does the next write start in the same chunk as this write ends (last_chunknum)?
                (next_seekpos, next_chunkdata) = raw_datav[0]
                next_start_chunknum = next_seekpos / self._chunksize
                next_start_offset   = next_seekpos % self._chunksize
                next_lastpos        = next_seekpos + len(next_chunkdata) - 1

                if next_start_chunknum != last_chunknum:
                    break

                _assert(next_start_offset > last_offset,
                        next_start_offset=next_start_offset, last_offset=last_offset)

                # Cut next_chunkdata at the end of next_start_chunknum.
                next_cutpos = (next_start_chunknum + 1)*self._chunksize
                last_chunkdata = (last_chunkdata[: next_start_offset - (last_offset + 1)] +
                                  next_chunkdata[: next_cutpos - next_seekpos] +
                                  last_chunkdata[next_lastpos - lastpos :])

                # Does the next write extend beyond that chunk?
                if next_lastpos >= next_cutpos:
                    # The part after the cut will be processed in the next call to _raw_write_share_data.
                    raw_datav[0] = (next_cutpos, next_chunkdata[next_cutpos - next_seekpos :])
                    break
                else:
                    # Discard the write that has already been processed.
                    raw_datav.popleft()

            # start_chunknum and last_chunknum are going to be written, so need to be flushed
            # from the read cache in case the new contents are needed by a subsequent readv
            # or writev. (Due to the 'while raw_datav' loop above, we won't need to read them
            # again in *this* writev. That property is needed for correctness because we don't
            # flush the write pipeline until the end of the writev.)

            d2.addCallback(lambda ign: self._cache.flush_chunk(start_chunkdata))
            d2.addCallback(lambda ign: self._cache.flush_chunk(last_chunkdata))

            # Now do the current write.
            if last_chunknum == start_chunknum:
                d2.addCallback(self._pipeline_store_chunk, start_chunknum,
                               start_chunkdata + last_chunkdata)
            else:
                d2.addCallback(self._pipeline_store_chunk, start_chunknum,
                               start_chunkdata)

                for middle_chunknum in xrange(start_chunknum + 1, last_chunknum):
                    d2.addCallback(self._pipeline_store_chunk, middle_chunknum,
                                   data[middle_chunknum*self._chunksize - seekpos
                                         : (middle_chunknum + 1)*self._chunksize - seekpos])

                d2.addCallback(self._pipeline_store_chunk, last_chunknum,
                               data[last_chunkpos - seekpos :] + last_chunkdata)
            return d2
        d.addCallback(_got)
        d.addCallback(self._raw_write_share_data, raw_datav, final_size)  # continue the iteration
        return d

    def _pipeline_store_chunk(self, ign, chunknum, chunkdata):
        precondition(len(chunkdata) <= self._chunksize, len_chunkdata=len(chunkdata), chunksize=self._chunksize)

        chunkkey = get_chunk_key(self._key, chunknum)
        #print "STORING", chunkkey, len(chunkdata), repr(chunkdata)

        endpos = chunknum*self._chunksize + len(chunkdata)
        if endpos > self._total_size:
            self._set_total_size(endpos)

        # We'd like to stream writes, but the supported service containers
        # (and the IContainer interface) don't support that yet. For txaws, see
        # https://bugs.launchpad.net/txaws/+bug/767205 and
        # https://bugs.launchpad.net/txaws/+bug/783801
        return self._pipeline.add(1, self._container.put_object, chunkkey, chunkdata)

    def close(self):
        # FIXME: 'close' doesn't exist in IMutableShare
        self._discard()
        d = self._pipeline.close()
        d.addCallback(lambda ign: self._cache.close())
        return d