"""
Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2, bytes_to_native_str
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os, stat, struct, time

from collections_extended import RangeMap

from foolscap.api import Referenceable

from zope.interface import implementer
from allmydata.interfaces import (
    RIBucketWriter, RIBucketReader, ConflictingWriteError,
    DataTooLargeError,
    NoSpace,
)
from allmydata.util import base32, fileutil, log
from allmydata.util.assertutil import precondition
from allmydata.storage.common import UnknownImmutableContainerVersionError

from .immutable_schema import (
    NEWEST_SCHEMA_VERSION,
    schema_from_version,
)


# each share file (in storage/shares/$SI/$SHNUM) contains lease information
# and share data. The share data is accessed by RIBucketWriter.write and
# RIBucketReader.read . The lease information is not accessible through these
# interfaces.

# The share file has the following layout:
#  0x00: share file version number, four bytes, current version is 2
#  0x04: share data length, four bytes big-endian = A # See Footnote 1 below.
#  0x08: number of leases, four bytes big-endian
#  0x0c: beginning of share data (see immutable.layout.WriteBucketProxy)
#  A+0x0c = B: first lease. Lease format is:
#   B+0x00: owner number, 4 bytes big-endian, 0 is reserved for no-owner
#   B+0x04: renew secret, 32 bytes (SHA256 + blake2b) # See Footnote 2 below.
#   B+0x24: cancel secret, 32 bytes (SHA256 + blake2b)
#   B+0x44: expiration time, 4 bytes big-endian seconds-since-epoch
#   B+0x48: next lease, or end of record

# Footnote 1: as of Tahoe v1.3.0 this field is not used by storage servers,
# but it is still filled in by storage servers in case the storage server
# software gets downgraded from >= Tahoe v1.3.0 to < Tahoe v1.3.0, or the
# share file is moved from one storage server to another. The value stored in
# this field is truncated, so if the actual share data length is >= 2**32,
# then the value stored in this field will be the actual share data length
# modulo 2**32.

# Footnote 2: The change between share file version number 1 and 2 is that
# storage of lease secrets is changed from plaintext to hashed.  This change
# protects the secrets from compromises of local storage on the server: if a
# plaintext cancel secret is somehow exfiltrated from the storage server, an
# attacker could use it to cancel that lease and potentially cause user data
# to be discarded before intended by the real owner.  As of this comment,
# lease cancellation is disabled because there have been at least two bugs
# which leak the persisted value of the cancellation secret.  If lease secrets
# were stored hashed instead of plaintext then neither of these bugs would
# have allowed an attacker to learn a usable cancel secret.
#
# Clients are free to construct these secrets however they like.  The
# Tahoe-LAFS client uses a SHA256-based construction.  The server then uses
# blake2b to hash these values for storage so that it retains no persistent
# copy of the original secret.
#

def _fix_lease_count_format(lease_count_format):
    """
    Turn a single character struct format string into a format string suitable
    for use in encoding and decoding the lease count value inside a share
    file, if possible.

    :param str lease_count_format: A single character format string like
        ``"B"`` or ``"L"``.

    :raise ValueError: If the given format string is not suitable for use
        encoding and decoding a lease count.

    :return str: A complete format string which can safely be used to encode
        and decode lease counts in a share file.
    """
    if len(lease_count_format) != 1:
        raise ValueError(
            "Cannot construct ShareFile with lease_count_format={!r}; "
            "format must accept a single value".format(
                lease_count_format,
            ),
        )
    # Make it big-endian with standard size so all platforms agree on the
    # result.
    fixed = ">" + lease_count_format
    if struct.calcsize(fixed) > 4:
        # There is only room for at most 4 bytes in the share file format so
        # we can't allow any larger formats.
        raise ValueError(
            "Cannot construct ShareFile with lease_count_format={!r}; "
            "size must be smaller than size of '>L'".format(
                lease_count_format,
            ),
        )
    return fixed


class ShareFile(object):
    """
    Support interaction with persistent storage of a share.

    :ivar str _lease_count_format: The format string which is used to encode
        and decode the lease count inside the share file.  As stated in the
        comment in this module there is room for at most 4 bytes in this part
        of the file.  A format string that works on fewer bytes is allowed to
        restrict the number of leases allowed in the share file to a smaller
        number than could be supported by using the full 4 bytes.  This is
        mostly of interest for testing.
    """
    LEASE_SIZE = struct.calcsize(">L32s32sL")
    sharetype = "immutable"

    @classmethod
    def is_valid_header(cls, header):
        # type: (bytes) -> bool
        """
        Determine if the given bytes constitute a valid header for this type of
        container.

        :param header: Some bytes from the beginning of a container.

        :return: ``True`` if the bytes could belong to this container,
            ``False`` otherwise.
        """
        (version,) = struct.unpack(">L", header[:4])
        return schema_from_version(version) is not None

    def __init__(
            self,
            filename,
            max_size=None,
            create=False,
            lease_count_format="L",
            schema=NEWEST_SCHEMA_VERSION,
    ):
        """
        Initialize a ``ShareFile``.

        :param Optional[int] max_size: If given, the maximum number of bytes
           that this ``ShareFile`` will accept to be stored.

        :param bool create: If ``True``, create the file (and fail if it
            exists already).  ``max_size`` must not be ``None`` in this case.
            If ``False``, open an existing file for reading.

        :param str lease_count_format: A format character to use to encode and
            decode the number of leases in the share file.  There are only 4
            bytes available in the file so the format must be 4 bytes or
            smaller.  If different formats are used at different times with
            the same share file, the result will likely be nonsense.

            This parameter is intended for the test suite to use to be able to
            exercise values near the maximum encodeable value without having
            to create billions of leases.

        :raise ValueError: If the encoding of ``lease_count_format`` is too
            large or if it is not a single format character.
        """

        precondition((max_size is not None) or (not create), max_size, create)

        self._lease_count_format = _fix_lease_count_format(lease_count_format)
        self._lease_count_size = struct.calcsize(self._lease_count_format)
        self.home = filename
        self._max_size = max_size
        if create:
            # touch the file, so later callers will see that we're working on
            # it. Also construct the metadata.
            assert not os.path.exists(self.home)
            fileutil.make_dirs(os.path.dirname(self.home))
            self._schema = schema
            with open(self.home, 'wb') as f:
                f.write(self._schema.header(max_size))
            self._lease_offset = max_size + 0x0c
            self._num_leases = 0
        else:
            with open(self.home, 'rb') as f:
                filesize = os.path.getsize(self.home)
                (version, unused, num_leases) = struct.unpack(">LLL", f.read(0xc))
            self._schema = schema_from_version(version)
            if self._schema is None:
                raise UnknownImmutableContainerVersionError(filename, version)
            self._num_leases = num_leases
            self._lease_offset = filesize - (num_leases * self.LEASE_SIZE)
            self._length = filesize - 0xc - (num_leases * self.LEASE_SIZE)

        self._data_offset = 0xc

    def get_length(self):
        """
        Return the length of the data in the share, if we're reading.
        """
        return self._length

    def unlink(self):
        os.unlink(self.home)

    def read_share_data(self, offset, length):
        precondition(offset >= 0)
        # reads beyond the end of the data are truncated. Reads that start
        # beyond the end of the data return an empty string.
        seekpos = self._data_offset+offset
        actuallength = max(0, min(length, self._lease_offset-seekpos))
        if actuallength == 0:
            return b""
        with open(self.home, 'rb') as f:
            f.seek(seekpos)
            return f.read(actuallength)

    def write_share_data(self, offset, data):
        length = len(data)
        precondition(offset >= 0, offset)
        if self._max_size is not None and offset+length > self._max_size:
            raise DataTooLargeError(self._max_size, offset, length)
        with open(self.home, 'rb+') as f:
            real_offset = self._data_offset+offset
            f.seek(real_offset)
            assert f.tell() == real_offset
            f.write(data)

    def _write_lease_record(self, f, lease_number, lease_info):
        offset = self._lease_offset + lease_number * self.LEASE_SIZE
        f.seek(offset)
        assert f.tell() == offset
        f.write(self._schema.lease_serializer.serialize(lease_info))

    def _read_num_leases(self, f):
        f.seek(0x08)
        (num_leases,) = struct.unpack(
            self._lease_count_format,
            f.read(self._lease_count_size),
        )
        return num_leases

    def _write_num_leases(self, f, num_leases):
        self._write_encoded_num_leases(
            f,
            struct.pack(self._lease_count_format, num_leases),
        )

    def _write_encoded_num_leases(self, f, encoded_num_leases):
        f.seek(0x08)
        f.write(encoded_num_leases)

    def _truncate_leases(self, f, num_leases):
        f.truncate(self._lease_offset + num_leases * self.LEASE_SIZE)

    def get_leases(self):
        """Yields a LeaseInfo instance for all leases."""
        with open(self.home, 'rb') as f:
            (version, unused, num_leases) = struct.unpack(">LLL", f.read(0xc))
            f.seek(self._lease_offset)
            for i in range(num_leases):
                data = f.read(self.LEASE_SIZE)
                if data:
                    yield self._schema.lease_serializer.unserialize(data)

    def add_lease(self, lease_info):
        with open(self.home, 'rb+') as f:
            num_leases = self._read_num_leases(f)
            # Before we write the new lease record, make sure we can encode
            # the new lease count.
            new_lease_count = struct.pack(self._lease_count_format, num_leases + 1)
            self._write_lease_record(f, num_leases, lease_info)
            self._write_encoded_num_leases(f, new_lease_count)

    def renew_lease(self, renew_secret, new_expire_time, allow_backdate=False):
        # type: (bytes, int, bool) -> None
        """
        Update the expiration time on an existing lease.

        :param allow_backdate: If ``True`` then allow the new expiration time
            to be before the current expiration time.  Otherwise, make no
            change when this is the case.

        :raise IndexError: If there is no lease matching the given renew
            secret.
        """
        for i,lease in enumerate(self.get_leases()):
            if lease.is_renew_secret(renew_secret):
                # yup. See if we need to update the owner time.
                if allow_backdate or new_expire_time > lease.get_expiration_time():
                    # yes
                    lease = lease.renew(new_expire_time)
                    with open(self.home, 'rb+') as f:
                        self._write_lease_record(f, i, lease)
                return
        raise IndexError("unable to renew non-existent lease")

    def add_or_renew_lease(self, available_space, lease_info):
        """
        Renew an existing lease if possible, otherwise allocate a new one.

        :param int available_space: The maximum number of bytes of storage to
            commit in this operation.  If more than this number of bytes is
            required, raise ``NoSpace`` instead.

        :param LeaseInfo lease_info: The details of the lease to renew or add.

        :raise NoSpace: If more than ``available_space`` bytes is required to
            complete the operation.  In this case, no lease is added.

        :return: ``None``
        """
        try:
            self.renew_lease(lease_info.renew_secret,
                             lease_info.get_expiration_time())
        except IndexError:
            if lease_info.immutable_size() > available_space:
                raise NoSpace()
            self.add_lease(lease_info)

    def cancel_lease(self, cancel_secret):
        """Remove a lease with the given cancel_secret. If the last lease is
        cancelled, the file will be removed. Return the number of bytes that
        were freed (by truncating the list of leases, and possibly by
        deleting the file. Raise IndexError if there was no lease with the
        given cancel_secret.
        """

        leases = list(self.get_leases())
        num_leases_removed = 0
        for i,lease in enumerate(leases):
            if lease.is_cancel_secret(cancel_secret):
                leases[i] = None
                num_leases_removed += 1
        if not num_leases_removed:
            raise IndexError("unable to find matching lease to cancel")
        if num_leases_removed:
            # pack and write out the remaining leases. We write these out in
            # the same order as they were added, so that if we crash while
            # doing this, we won't lose any non-cancelled leases.
            leases = [l for l in leases if l] # remove the cancelled leases
            with open(self.home, 'rb+') as f:
                for i, lease in enumerate(leases):
                    self._write_lease_record(f, i, lease)
                self._write_num_leases(f, len(leases))
                self._truncate_leases(f, len(leases))
        space_freed = self.LEASE_SIZE * num_leases_removed
        if not len(leases):
            space_freed += os.stat(self.home)[stat.ST_SIZE]
            self.unlink()
        return space_freed


class BucketWriter(object):
    """
    Keep track of the process of writing to a ShareFile.
    """

    def __init__(self, ss, incominghome, finalhome, max_size, lease_info, clock):
        self.ss = ss
        self.incominghome = incominghome
        self.finalhome = finalhome
        self._max_size = max_size # don't allow the client to write more than this
        self.closed = False
        self.throw_out_all_data = False
        self._sharefile = ShareFile(incominghome, create=True, max_size=max_size)
        # also, add our lease to the file now, so that other ones can be
        # added by simultaneous uploaders
        self._sharefile.add_lease(lease_info)
        self._already_written = RangeMap()
        self._clock = clock
        self._timeout = clock.callLater(30 * 60, self._abort_due_to_timeout)

    def required_ranges(self):  # type: () -> RangeMap
        """
        Return which ranges still need to be written.
        """
        result = RangeMap()
        result.set(True, 0, self._max_size)
        for start, end, _ in self._already_written.ranges():
            result.delete(start, end)
        return result

    def allocated_size(self):
        return self._max_size

    def write(self, offset, data):  # type: (int, bytes) -> bool
        """
        Write data at given offset, return whether the upload is complete.
        """
        # Delay the timeout, since we received data; if we get an
        # AlreadyCancelled error, that means there's a bug in the client and
        # write() was called after close().
        self._timeout.reset(30 * 60)
        start = self._clock.seconds()
        precondition(not self.closed)
        if self.throw_out_all_data:
            return False

        # Make sure we're not conflicting with existing data:
        end = offset + len(data)
        for (chunk_start, chunk_stop, _) in self._already_written.ranges(offset, end):
            chunk_len = chunk_stop - chunk_start
            actual_chunk = self._sharefile.read_share_data(chunk_start, chunk_len)
            writing_chunk = data[chunk_start - offset:chunk_stop - offset]
            if actual_chunk != writing_chunk:
                raise ConflictingWriteError(
                    "Chunk {}-{} doesn't match already written data.".format(chunk_start, chunk_stop)
                )
        self._sharefile.write_share_data(offset, data)

        self._already_written.set(True, offset, end)
        self.ss.add_latency("write", self._clock.seconds() - start)
        self.ss.count("write")
        return self._is_finished()

    def _is_finished(self):
        """
        Return whether the whole thing has been written.
        """
        return sum([mr.stop - mr.start for mr in self._already_written.ranges()]) == self._max_size

    def close(self):
        # This can't actually be enabled, because it's not backwards compatible
        # with old Foolscap clients.
        # assert self._is_finished()
        precondition(not self.closed)
        self._timeout.cancel()
        start = self._clock.seconds()

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

        filelen = os.stat(self.finalhome)[stat.ST_SIZE]
        self.ss.bucket_writer_closed(self, filelen)
        self.ss.add_latency("close", self._clock.seconds() - start)
        self.ss.count("close")

    def disconnected(self):
        if not self.closed:
            self.abort()

    def _abort_due_to_timeout(self):
        """
        Called if we run out of time.
        """
        log.msg("storage: aborting sharefile %s due to timeout" % self.incominghome,
                facility="tahoe.storage", level=log.UNUSUAL)
        self.abort()

    def abort(self):
        log.msg("storage: aborting sharefile %s" % self.incominghome,
                facility="tahoe.storage", level=log.UNUSUAL)
        self.ss.count("abort")
        if self.closed:
            return

        os.remove(self.incominghome)
        # if we were the last share to be moved, remove the incoming/
        # directory that was our parent
        parentdir = os.path.split(self.incominghome)[0]
        if not os.listdir(parentdir):
            os.rmdir(parentdir)
        self._sharefile = None

        # We are now considered closed for further writing. We must tell
        # the storage server about this so that it stops expecting us to
        # use the space it allocated for us earlier.
        self.closed = True
        self.ss.bucket_writer_closed(self, 0)

        # Cancel timeout if it wasn't already cancelled.
        if self._timeout.active():
            self._timeout.cancel()


@implementer(RIBucketWriter)
class FoolscapBucketWriter(Referenceable):  # type: ignore # warner/foolscap#78
    """
    Foolscap-specific BucketWriter.
    """
    def __init__(self, bucket_writer):
        self._bucket_writer = bucket_writer

    def remote_write(self, offset, data):
        self._bucket_writer.write(offset, data)

    def remote_close(self):
        return self._bucket_writer.close()

    def remote_abort(self):
        return self._bucket_writer.abort()


class BucketReader(object):
    """
    Manage the process for reading from a ``ShareFile``.
    """

    def __init__(self, ss, sharefname, storage_index=None, shnum=None):
        self.ss = ss
        self._share_file = ShareFile(sharefname)
        self.storage_index = storage_index
        self.shnum = shnum

    def __repr__(self):
        return "<%s %s %s>" % (self.__class__.__name__,
                               bytes_to_native_str(
                                   base32.b2a(self.storage_index[:8])[:12]
                               ),
                               self.shnum)

    def read(self, offset, length):
        start = time.time()
        data = self._share_file.read_share_data(offset, length)
        self.ss.add_latency("read", time.time() - start)
        self.ss.count("read")
        return data

    def advise_corrupt_share(self, reason):
        return self.ss.advise_corrupt_share(b"immutable",
                                            self.storage_index,
                                            self.shnum,
                                            reason)

    def get_length(self):
        """
        Return the length of the data in the share.
        """
        return self._share_file.get_length()


@implementer(RIBucketReader)
class FoolscapBucketReader(Referenceable):  # type: ignore # warner/foolscap#78
    """
    Foolscap wrapper for ``BucketReader``
    """

    def __init__(self, bucket_reader):
        self._bucket_reader = bucket_reader

    def remote_read(self, offset, length):
        return self._bucket_reader.read(offset, length)

    def remote_advise_corrupt_share(self, reason):
        return self._bucket_reader.advise_corrupt_share(reason)
