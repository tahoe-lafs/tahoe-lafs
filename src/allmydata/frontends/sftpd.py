
import os, tempfile, heapq, binascii, traceback, array, stat, struct
from stat import S_IFREG, S_IFDIR
from time import time, strftime, localtime

from zope.interface import implements
from twisted.python import components
from twisted.application import service, strports
from twisted.conch.ssh import factory, keys, session
from twisted.conch.ssh.filetransfer import FileTransferServer, SFTPError, \
     FX_NO_SUCH_FILE, FX_OP_UNSUPPORTED, FX_PERMISSION_DENIED, FX_EOF, \
     FX_BAD_MESSAGE, FX_FAILURE
from twisted.conch.ssh.filetransfer import FXF_READ, FXF_WRITE, FXF_APPEND, \
     FXF_CREAT, FXF_TRUNC, FXF_EXCL
from twisted.conch.interfaces import ISFTPServer, ISFTPFile, IConchUser, ISession
from twisted.conch.avatar import ConchUser
from twisted.conch.openssh_compat import primes
from twisted.cred import portal
from twisted.internet.error import ProcessDone, ProcessTerminated

from twisted.internet import defer
from twisted.internet.interfaces import IFinishableConsumer
from foolscap.api import eventually
from allmydata.util import deferredutil

from allmydata.util.consumer import download_to_data
from allmydata.interfaces import IFileNode, IDirectoryNode, ExistingChildError, \
     NoSuchChildError
from allmydata.mutable.common import NotWriteableError
from allmydata.immutable.upload import FileHandle

from pycryptopp.cipher.aes import AES

# twisted.conch.ssh.filetransfer generates this warning, but not when it is imported,
# only on an error.
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning,
    message="BaseException.message has been deprecated as of Python 2.6",
    module=".*filetransfer", append=True)

noisy = True
use_foolscap_logging = True

from allmydata.util.log import NOISY, OPERATIONAL, SCARY

if use_foolscap_logging:
    from allmydata.util.log import msg as logmsg, err as logerr, PrefixingLogMixin
else:
    def logmsg(s, level=None):
        print s
    def logerr(s, level=None):
        print s
    class PrefixingLogMixin:
        def __init__(self, facility=None):
            pass
        def log(self, s, level=None):
            print s

if noisy:
    def eventually_callback(d):
        s = traceback.format_stack()
        def _cb(res):
            try:
                if noisy: logmsg("CALLBACK %r %r" % (d, res), level=NOISY)
                d.callback(res)
            except:  # pragma: no cover
                logerr("Failed to callback %r with %r\n"
                       "Original stack:\n!%s" %
                       (d, res, '!'.join(s)), level=SCARY)
                traceback.print_exc()
                raise
        return lambda res: eventually(_cb, res)

    def eventually_errback(d):
        s = traceback.format_stack()
        def _eb(err):
            try:
                if noisy: logmsg("ERRBACK %r %r" % (d, err), level=NOISY)
                d.errback(err)
            except:  # pragma: no cover
                logerr("Failed to errback %r with %r\n"
                       "Original stack:\n!%s" %
                       (d, err, '!'.join(s)), level=SCARY)
                traceback.print_exc()
                raise
        return lambda err: eventually(_eb, err)
else:
    def eventually_callback(d):
        return lambda res: eventually(d.callback, res)

    def eventually_errback(d):
        return lambda err: eventually(d.errback, err)


def _raise_error(err):
    if err is None:
        return None
    if noisy: logmsg("RAISE %r" % (err,), level=NOISY)
    #traceback.print_exc(err)

    # The message argument to SFTPError must not reveal information that
    # might compromise anonymity.

    if err.check(SFTPError):
        # original raiser of SFTPError has responsibility to ensure anonymity
        raise err
    if err.check(NoSuchChildError):
        childname = err.value.args[0].encode('utf-8')
        raise SFTPError(FX_NO_SUCH_FILE, childname)
    if err.check(ExistingChildError) or err.check(NotWriteableError):
        # later versions of SFTP define FX_FILE_ALREADY_EXISTS, but version 3 doesn't
        msg = err.value.args[0].encode('utf-8')
        raise SFTPError(FX_PERMISSION_DENIED, msg)
    if err.check(NotImplementedError):
        raise SFTPError(FX_OP_UNSUPPORTED, str(err.value))
    if err.check(EOFError):
        raise SFTPError(FX_EOF, "end of file reached")
    if err.check(defer.FirstError):
        _raise_error(err.value.subFailure)

    # We assume that the error message is not anonymity-sensitive.
    raise SFTPError(FX_FAILURE, str(err.value))

def _repr_flags(flags):
    return "|".join([f for f in
                     [(flags & FXF_READ) and "FXF_READ" or None,
                      (flags & FXF_WRITE) and "FXF_WRITE" or None,
                      (flags & FXF_APPEND) and "FXF_APPEND" or None,
                      (flags & FXF_CREAT) and "FXF_CREAT" or None,
                      (flags & FXF_TRUNC) and "FXF_TRUNC" or None,
                      (flags & FXF_EXCL) and "FXF_EXCL" or None,
                     ]
                     if f])

def _lsLine(name, attrs):
    st_uid = "tahoe"
    st_gid = "tahoe"
    st_mtime = attrs.get("mtime", 0)
    st_mode = attrs["permissions"]
    # TODO: check that clients are okay with this being a "?".
    # (They should be because the longname is intended for human
    # consumption.)
    st_size = attrs.get("size", "?")
    # We don't know how many links there really are to this object.
    st_nlink = 1

    # From <http://twistedmatrix.com/trac/browser/trunk/twisted/conch/ls.py?rev=25412>.
    # We can't call the version in Twisted because we might have a version earlier than
    # <http://twistedmatrix.com/trac/changeset/25412> (released in Twisted 8.2).

    mode = st_mode
    perms = array.array('c', '-'*10)
    ft = stat.S_IFMT(mode)
    if stat.S_ISDIR(ft): perms[0] = 'd'
    elif stat.S_ISCHR(ft): perms[0] = 'c'
    elif stat.S_ISBLK(ft): perms[0] = 'b'
    elif stat.S_ISREG(ft): perms[0] = '-'
    elif stat.S_ISFIFO(ft): perms[0] = 'f'
    elif stat.S_ISLNK(ft): perms[0] = 'l'
    elif stat.S_ISSOCK(ft): perms[0] = 's'
    else: perms[0] = '?'
    # user
    if mode&stat.S_IRUSR:perms[1] = 'r'
    if mode&stat.S_IWUSR:perms[2] = 'w'
    if mode&stat.S_IXUSR:perms[3] = 'x'
    # group
    if mode&stat.S_IRGRP:perms[4] = 'r'
    if mode&stat.S_IWGRP:perms[5] = 'w'
    if mode&stat.S_IXGRP:perms[6] = 'x'
    # other
    if mode&stat.S_IROTH:perms[7] = 'r'
    if mode&stat.S_IWOTH:perms[8] = 'w'
    if mode&stat.S_IXOTH:perms[9] = 'x'
    # suid/sgid never set

    l = perms.tostring()
    l += str(st_nlink).rjust(5) + ' '
    un = str(st_uid)
    l += un.ljust(9)
    gr = str(st_gid)
    l += gr.ljust(9)
    sz = str(st_size)
    l += sz.rjust(8)
    l += ' '
    sixmo = 60 * 60 * 24 * 7 * 26
    if st_mtime + sixmo < time(): # last edited more than 6mo ago
        l += strftime("%b %d  %Y ", localtime(st_mtime))
    else:
        l += strftime("%b %d %H:%M ", localtime(st_mtime))
    l += name
    return l

def _populate_attrs(childnode, metadata, writeable, size=None):
    attrs = {}

    # see webapi.txt for what these times mean
    if metadata:
        if "linkmotime" in metadata.get("tahoe", {}):
            attrs["mtime"] = int(metadata["tahoe"]["linkmotime"])
        elif "mtime" in metadata:
            attrs["mtime"] = int(metadata["mtime"])

        if "linkcrtime" in metadata.get("tahoe", {}):
            attrs["createtime"] = int(metadata["tahoe"]["linkcrtime"])

        if "ctime" in metadata:
            attrs["ctime"] = int(metadata["ctime"])

        # We would prefer to omit atime, but SFTP version 3 can only
        # accept mtime if atime is also set.
        attrs["atime"] = attrs["mtime"]

    # The permissions must have the extra bits (040000 or 0100000),
    # otherwise the client will not call openDirectory.

    # Directories and unknown nodes have no size, and SFTP doesn't
    # require us to make one up.
    # childnode might be None, meaning that the file doesn't exist yet,
    # but we're going to write it later.

    if childnode and childnode.is_unknown():
        perms = 0
    elif childnode and IDirectoryNode.providedBy(childnode):
        perms = S_IFDIR | 0770 
    else:
        # For files, omit the size if we don't immediately know it.
        if childnode and size is None:
            size = childnode.get_size()
        if size is not None:
            assert isinstance(size, (int, long)), repr(size)
            attrs["size"] = size
        perms = S_IFREG | 0660

    if not writeable:
        perms &= S_IFDIR | S_IFREG | 0555  # clear 'w' bits

    attrs["permissions"] = perms

    # We could set the SSH_FILEXFER_ATTR_FLAGS here:
    # ENCRYPTED would always be true ("The file is stored on disk
    # using file-system level transparent encryption.")
    # SYSTEM, HIDDEN, ARCHIVE and SYNC would always be false.
    # READONLY and IMMUTABLE would be set according to
    # childnode.is_readonly() and childnode.is_immutable()
    # for known nodes.
    # However, twisted.conch.ssh.filetransfer only implements
    # SFTP version 3, which doesn't include these flags.

    return attrs

class EncryptedTemporaryFile(PrefixingLogMixin):
    # not implemented: next, readline, readlines, xreadlines, writelines

    def __init__(self):
        PrefixingLogMixin.__init__(self, facility="tahoe.sftp")
        self.file = tempfile.TemporaryFile()
        self.key = os.urandom(16)  # AES-128

    def _crypt(self, offset, data):
        # FIXME: use random-access AES (pycryptopp ticket #18)
        offset_big = offset // 16
        offset_small = offset % 16
        iv = binascii.unhexlify("%032x" % offset_big)
        cipher = AES(self.key, iv=iv)
        cipher.process("\x00"*offset_small)
        return cipher.process(data)

    def close(self):
        self.file.close()

    def flush(self):
        self.file.flush()

    def seek(self, offset, whence=os.SEEK_SET):
        if noisy: self.log(".seek(%r, %r)" % (offset, whence), level=NOISY)
        self.file.seek(offset, whence)

    def tell(self):
        offset = self.file.tell()
        if noisy: self.log(".tell() = %r" % (offset,), level=NOISY)
        return offset

    def read(self, size=-1):
        if noisy: self.log(".read(%r)" % (size,), level=NOISY)
        index = self.file.tell()
        ciphertext = self.file.read(size)
        plaintext = self._crypt(index, ciphertext)
        return plaintext

    def write(self, plaintext):
        if noisy: self.log(".write(<data of length %r>)" % (len(plaintext),), level=NOISY)
        index = self.file.tell()
        ciphertext = self._crypt(index, plaintext)
        self.file.write(ciphertext)

    def truncate(self, newsize):
        if noisy: self.log(".truncate(%r)" % (newsize,), level=NOISY)
        self.file.truncate(newsize)


class OverwriteableFileConsumer(PrefixingLogMixin):
    implements(IFinishableConsumer)
    """I act both as a consumer for the download of the original file contents, and as a
    wrapper for a temporary file that records the downloaded data and any overwrites.
    I use a priority queue to keep track of which regions of the file have been overwritten
    but not yet downloaded, so that the download does not clobber overwritten data.
    I use another priority queue to record milestones at which to make callbacks
    indicating that a given number of bytes have been downloaded.

    The temporary file reflects the contents of the file that I represent, except that:
     - regions that have neither been downloaded nor overwritten, if present,
       contain zeroes.
     - the temporary file may be shorter than the represented file (it is never longer).
       The latter's current size is stored in self.current_size.

    This abstraction is mostly independent of SFTP. Consider moving it, if it is found
    useful for other frontends."""

    def __init__(self, check_abort, download_size, tempfile_maker):
        PrefixingLogMixin.__init__(self, facility="tahoe.sftp")
        if noisy: self.log(".__init__(%r, %r, %r)" % (check_abort, download_size, tempfile_maker), level=NOISY)
        self.check_abort = check_abort
        self.download_size = download_size
        self.current_size = download_size
        self.f = tempfile_maker()
        self.downloaded = 0
        self.milestones = []  # empty heap of (offset, d)
        self.overwrites = []  # empty heap of (start, end)
        self.done = self.when_reached(download_size)  # adds a milestone
        self.is_done = False
        def _signal_done(ign):
            if noisy: self.log("DONE", level=NOISY)
            self.is_done = True
        self.done.addCallback(_signal_done)
        self.producer = None

    def get_file(self):
        return self.f

    def get_current_size(self):
        return self.current_size

    def set_current_size(self, size):
        if noisy: self.log(".set_current_size(%r), current_size = %r, downloaded = %r" %
                           (size, self.current_size, self.downloaded), level=NOISY)
        if size < self.current_size or size < self.downloaded:
            self.f.truncate(size)
        self.current_size = size
        if size < self.download_size:
            self.download_size = size
        if self.downloaded >= self.download_size:
            self.finish()

    def registerProducer(self, p, streaming):
        if noisy: self.log(".registerProducer(%r, streaming=%r)" % (p, streaming), level=NOISY)
        self.producer = p
        if streaming:
            # call resumeProducing once to start things off
            p.resumeProducing()
        else:
            def _iterate():
                if not self.is_done:
                    p.resumeProducing()
                    eventually(_iterate)
            _iterate()

    def write(self, data):
        if noisy: self.log(".write(<data of length %r>)" % (len(data),), level=NOISY)
        if self.check_abort():
            self.close()
            return

        if self.downloaded >= self.download_size:
            return

        next_downloaded = self.downloaded + len(data)
        if next_downloaded > self.download_size:
            data = data[:(self.download_size - self.downloaded)]

        while len(self.overwrites) > 0:
            (start, end) = self.overwrites[0]
            if start >= next_downloaded:
                # This and all remaining overwrites are after the data we just downloaded.
                break
            if start > self.downloaded:
                # The data we just downloaded has been partially overwritten.
                # Write the prefix of it that precedes the overwritten region.
                self.f.seek(self.downloaded)
                self.f.write(data[:(start - self.downloaded)])

            # This merges consecutive overwrites if possible, which allows us to detect the
            # case where the download can be stopped early because the remaining region
            # to download has already been fully overwritten.
            heapq.heappop(self.overwrites)
            while len(self.overwrites) > 0:
                (start1, end1) = self.overwrites[0]
                if start1 > end:
                    break
                end = end1
                heapq.heappop(self.overwrites)

            if end >= next_downloaded:
                # This overwrite extends past the downloaded data, so there is no
                # more data to consider on this call.
                heapq.heappush(self.overwrites, (next_downloaded, end))
                self._update_downloaded(next_downloaded)
                return
            elif end >= self.downloaded:
                data = data[(end - self.downloaded):]
                self._update_downloaded(end)

        self.f.seek(self.downloaded)
        self.f.write(data)
        self._update_downloaded(next_downloaded)

    def _update_downloaded(self, new_downloaded):
        self.downloaded = new_downloaded
        milestone = new_downloaded
        if len(self.overwrites) > 0:
            (start, end) = self.overwrites[0]
            if start <= new_downloaded and end > milestone:
                milestone = end

        while len(self.milestones) > 0:
            (next, d) = self.milestones[0]
            if next > milestone:
                return
            if noisy: self.log("MILESTONE %r %r" % (next, d), level=NOISY)
            heapq.heappop(self.milestones)
            eventually_callback(d)(None)

        if milestone >= self.download_size:
            self.finish()

    def overwrite(self, offset, data):
        if noisy: self.log(".overwrite(%r, %r)" % (offset, data), level=NOISY)
        if offset > self.download_size and offset > self.current_size:
            # Normally writing at an offset beyond the current end-of-file
            # would leave a hole that appears filled with zeroes. However, an
            # EncryptedTemporaryFile doesn't behave like that (if there is a
            # hole in the file on disk, the zeroes that are read back will be
            # XORed with the keystream). So we must explicitly write zeroes in
            # the gap between the current EOF and the offset.

            self.f.seek(self.current_size)
            self.f.write("\x00" * (offset - self.current_size))            
        else:
            self.f.seek(offset)
        self.f.write(data)
        end = offset + len(data)
        self.current_size = max(self.current_size, end)
        if end > self.downloaded:
            heapq.heappush(self.overwrites, (offset, end))

    def read(self, offset, length):
        """When the data has been read, callback the Deferred that we return with this data.
        Otherwise errback the Deferred that we return.
        The caller must perform no more overwrites until the Deferred has fired."""

        if noisy: self.log(".read(%r, %r), current_size = %r" % (offset, length, self.current_size), level=NOISY)
        if offset >= self.current_size:
            def _eof(): raise EOFError("read past end of file")
            return defer.execute(_eof)

        if offset + length > self.current_size:
            length = self.current_size - offset
            if noisy: self.log("truncating read to %r bytes" % (length,), level=NOISY)

        needed = min(offset + length, self.download_size)
        d = self.when_reached(needed)
        def _reached(ign):
            # It is not necessarily the case that self.downloaded >= needed, because
            # the file might have been truncated (thus truncating the download) and
            # then extended.

            assert self.current_size >= offset + length, (self.current_size, offset, length)
            if noisy: self.log("self.f = %r" % (self.f,), level=NOISY)
            self.f.seek(offset)
            return self.f.read(length)
        d.addCallback(_reached)
        return d

    def when_reached(self, index):
        if noisy: self.log(".when_reached(%r)" % (index,), level=NOISY)
        if index <= self.downloaded:  # already reached
            if noisy: self.log("already reached %r" % (index,), level=NOISY)
            return defer.succeed(None)
        d = defer.Deferred()
        def _reached(ign):
            if noisy: self.log("reached %r" % (index,), level=NOISY)
            return ign
        d.addCallback(_reached)
        heapq.heappush(self.milestones, (index, d))
        return d

    def when_done(self):
        return self.done

    def finish(self):
        while len(self.milestones) > 0:
            (next, d) = self.milestones[0]
            if noisy: self.log("MILESTONE FINISH %r %r" % (next, d), level=NOISY)
            heapq.heappop(self.milestones)
            # The callback means that the milestone has been reached if
            # it is ever going to be. Note that the file may have been
            # truncated to before the milestone.
            eventually_callback(d)(None)

        # FIXME: causes spurious failures
        #self.unregisterProducer()

    def close(self):
        self.finish()
        self.f.close()

    def unregisterProducer(self):
        if self.producer:
            self.producer.stopProducing()
            self.producer = None


SIZE_THRESHOLD = 1000

def _make_sftp_file(check_abort, flags, convergence, parent=None, childname=None, filenode=None, metadata=None):
    if noisy: logmsg("_make_sftp_file(%r, %r, <convergence censored>, parent=%r, childname=%r, filenode=%r, metadata=%r" %
                      (check_abort, flags, parent, childname, filenode, metadata), level=NOISY)

    if not (flags & (FXF_WRITE | FXF_CREAT)) and (flags & FXF_READ) and filenode and \
       not filenode.is_mutable() and filenode.get_size() <= SIZE_THRESHOLD:
        return ShortReadOnlySFTPFile(filenode, metadata)
    else:
        return GeneralSFTPFile(check_abort, flags, convergence,
                               parent=parent, childname=childname, filenode=filenode, metadata=metadata)


class ShortReadOnlySFTPFile(PrefixingLogMixin):
    implements(ISFTPFile)
    """I represent a file handle to a particular file on an SFTP connection.
    I am used only for short immutable files opened in read-only mode.
    The file contents are downloaded to memory when I am created."""

    def __init__(self, filenode, metadata):
        PrefixingLogMixin.__init__(self, facility="tahoe.sftp")
        if noisy: self.log(".__init__(%r, %r)" % (filenode, metadata), level=NOISY)

        assert IFileNode.providedBy(filenode), filenode
        self.filenode = filenode
        self.metadata = metadata
        self.async = download_to_data(filenode)
        self.closed = False

    def readChunk(self, offset, length):
        self.log(".readChunk(%r, %r)" % (offset, length), level=OPERATIONAL)

        if self.closed:
            def _closed(): raise SFTPError(FX_BAD_MESSAGE, "cannot read from a closed file handle")
            return defer.execute(_closed)

        d = defer.Deferred()
        def _read(data):
            if noisy: self.log("_read(%r) in readChunk(%r, %r)" % (data, offset, length), level=NOISY)

            # "In response to this request, the server will read as many bytes as it
            #  can from the file (up to 'len'), and return them in a SSH_FXP_DATA
            #  message.  If an error occurs or EOF is encountered before reading any
            #  data, the server will respond with SSH_FXP_STATUS.  For normal disk
            #  files, it is guaranteed that this will read the specified number of
            #  bytes, or up to end of file."
            #
            # i.e. we respond with an EOF error iff offset is already at EOF.

            if offset >= len(data):
                eventually_errback(d)(SFTPError(FX_EOF, "read at or past end of file"))
            else:
                eventually_callback(d)(data[offset:min(offset+length, len(data))])
            return data
        self.async.addCallbacks(_read, eventually_errback(d))
        return d

    def writeChunk(self, offset, data):
        self.log(".writeChunk(%r, <data of length %r>)" % (offset, len(data)), level=OPERATIONAL)

        def _denied(): raise SFTPError(FX_PERMISSION_DENIED, "file handle was not opened for writing")
        return defer.execute(_denied)

    def close(self):
        self.log(".close()", level=OPERATIONAL)

        self.closed = True
        return defer.succeed(None)

    def getAttrs(self):
        self.log(".getAttrs()", level=OPERATIONAL)

        if self.closed:
            def _closed(): raise SFTPError(FX_BAD_MESSAGE, "cannot get attributes for a closed file handle")
            return defer.execute(_closed)

        return defer.succeed(_populate_attrs(self.filenode, self.metadata, False))

    def setAttrs(self, attrs):
        self.log(".setAttrs(%r)" % (attrs,), level=OPERATIONAL)
        def _denied(): raise SFTPError(FX_PERMISSION_DENIED, "file handle was not opened for writing")
        return defer.execute(_denied)


class GeneralSFTPFile(PrefixingLogMixin):
    implements(ISFTPFile)
    """I represent a file handle to a particular file on an SFTP connection.
    I wrap an instance of OverwriteableFileConsumer, which is responsible for
    storing the file contents. In order to allow write requests to be satisfied
    immediately, there is effectively a FIFO queue between requests made to this
    file handle, and requests to my OverwriteableFileConsumer. This queue is
    implemented by the callback chain of self.async."""

    def __init__(self, check_abort, flags, convergence, parent=None, childname=None, filenode=None, metadata=None):
        PrefixingLogMixin.__init__(self, facility="tahoe.sftp")
        if noisy: self.log(".__init__(%r, %r, <convergence censored>, parent=%r, childname=%r, filenode=%r, metadata=%r)" %
                           (check_abort, flags, parent, childname, filenode, metadata), level=NOISY)

        self.check_abort = check_abort
        self.flags = flags
        self.convergence = convergence
        self.parent = parent
        self.childname = childname
        self.filenode = filenode
        self.metadata = metadata
        self.async = defer.succeed(None)
        self.closed = False
        
        # self.consumer should only be relied on in callbacks for self.async, since it might
        # not be set before then.
        self.consumer = None
        tempfile_maker = EncryptedTemporaryFile

        if (flags & FXF_TRUNC) or not filenode:
            # We're either truncating or creating the file, so we don't need the old contents.
            assert flags & FXF_CREAT, flags
            self.consumer = OverwriteableFileConsumer(self.check_abort, 0, tempfile_maker)
            self.consumer.finish()
        else:
            assert IFileNode.providedBy(filenode), filenode

            # TODO: use download interface described in #993 when implemented.
            if filenode.is_mutable():
                self.async.addCallback(lambda ign: filenode.download_best_version())
                def _downloaded(data):
                    self.consumer = OverwriteableFileConsumer(self.check_abort, len(data), tempfile_maker)
                    self.consumer.write(data)
                    self.consumer.finish()
                    return None
                self.async.addCallback(_downloaded)
            else:
                download_size = filenode.get_size()
                assert download_size is not None
                self.consumer = OverwriteableFileConsumer(self.check_abort, download_size, tempfile_maker)
                def _read(ign):
                    if noisy: self.log("_read immutable", level=NOISY)
                    filenode.read(self.consumer, 0, None)
                self.async.addCallback(_read)

    def readChunk(self, offset, length):
        self.log(".readChunk(%r, %r)" % (offset, length), level=OPERATIONAL)

        if not (self.flags & FXF_READ):
            def _denied(): raise SFTPError(FX_PERMISSION_DENIED, "file handle was not opened for reading")
            return defer.execute(_denied)

        if self.closed:
            def _closed(): raise SFTPError(FX_BAD_MESSAGE, "cannot read from a closed file handle")
            return defer.execute(_closed)

        d = defer.Deferred()
        def _read(ign):
            if noisy: self.log("_read in readChunk(%r, %r)" % (offset, length), level=NOISY)
            d2 = self.consumer.read(offset, length)
            d2.addErrback(_raise_error)
            d2.addCallbacks(eventually_callback(d), eventually_errback(d))
            # It is correct to drop d2 here.
            return None
        self.async.addCallbacks(_read, eventually_errback(d))
        return d

    def writeChunk(self, offset, data):
        self.log(".writeChunk(%r, <data of length %r>)" % (offset, len(data)), level=OPERATIONAL)

        if not (self.flags & FXF_WRITE):
            def _denied(): raise SFTPError(FX_PERMISSION_DENIED, "file handle was not opened for writing")
            return defer.execute(_denied)

        if self.closed:
            def _closed(): raise SFTPError(FX_BAD_MESSAGE, "cannot write to a closed file handle")
            return defer.execute(_closed)

        # Note that we return without waiting for the write to occur. Reads and
        # close wait for prior writes, and will fail if any prior operation failed.
        # This is ok because SFTP makes no guarantee that the request completes
        # before the write. In fact it explicitly allows write errors to be delayed
        # until close:
        #   "One should note that on some server platforms even a close can fail.
        #    This can happen e.g. if the server operating system caches writes,
        #    and an error occurs while flushing cached writes during the close."

        def _write(ign):
            if noisy: self.log("_write in .writeChunk(%r, <data of length %r>), current_size = %r" %
                               (offset, len(data), self.consumer.get_current_size()), level=NOISY)
            # FXF_APPEND means that we should always write at the current end of file.
            write_offset = offset
            if self.flags & FXF_APPEND:
                write_offset = self.consumer.get_current_size()

            self.consumer.overwrite(write_offset, data)
            return None
        self.async.addCallback(_write)
        # don't addErrback to self.async, just allow subsequent async ops to fail.
        return defer.succeed(None)

    def close(self):
        self.log(".close()", level=OPERATIONAL)

        if self.closed:
            return defer.succeed(None)

        # This means that close has been called, not that the close has succeeded.
        self.closed = True

        if not (self.flags & (FXF_WRITE | FXF_CREAT)):
            return defer.execute(self.consumer.close)

        def _close(ign):
            d2 = self.consumer.when_done()
            if self.filenode and self.filenode.is_mutable():
                d2.addCallback(lambda ign: self.consumer.get_current_size())
                d2.addCallback(lambda size: self.consumer.read(0, size))
                d2.addCallback(lambda new_contents: self.filenode.overwrite(new_contents))
            #elif (self.flags & FXF_EXCL) and self.consumer.get_current_size() == 0:
            #    # The file will already have been written by the open call, so we can
            #    # optimize out the extra directory write (useful for zero-length lockfiles).
            #    pass
            else:
                def _add_file(ign):
                    self.log("_add_file childname=%r" % (self.childname,), level=OPERATIONAL)
                    u = FileHandle(self.consumer.get_file(), self.convergence)
                    return self.parent.add_file(self.childname, u)
                d2.addCallback(_add_file)

            d2.addCallback(lambda ign: self.consumer.close())
            return d2
        self.async.addCallback(_close)

        d = defer.Deferred()
        self.async.addCallbacks(eventually_callback(d), eventually_errback(d))
        return d

    def getAttrs(self):
        self.log(".getAttrs()", level=OPERATIONAL)

        if self.closed:
            def _closed(): raise SFTPError(FX_BAD_MESSAGE, "cannot get attributes for a closed file handle")
            return defer.execute(_closed)

        # Optimization for read-only handles, when we already know the metadata.
        if not(self.flags & (FXF_WRITE | FXF_CREAT)) and self.metadata and self.filenode and not self.filenode.is_mutable():
            return defer.succeed(_populate_attrs(self.filenode, self.metadata, False))

        d = defer.Deferred()
        def _get(ign):
            # FIXME: pass correct value for writeable
            # self.filenode might be None, but that's ok.
            attrs = _populate_attrs(self.filenode, self.metadata, False,
                                    size=self.consumer.get_current_size())
            eventually_callback(d)(attrs)
            return None
        self.async.addCallbacks(_get, eventually_errback(d))
        return d

    def setAttrs(self, attrs):
        self.log(".setAttrs(attrs) %r" % (attrs,), level=OPERATIONAL)

        if not (self.flags & FXF_WRITE):
            def _denied(): raise SFTPError(FX_PERMISSION_DENIED, "file handle was not opened for writing")
            return defer.execute(_denied)

        if self.closed:
            def _closed(): raise SFTPError(FX_BAD_MESSAGE, "cannot set attributes for a closed file handle")
            return defer.execute(_closed)

        if not "size" in attrs:
            return defer.succeed(None)

        size = attrs["size"]
        if not isinstance(size, (int, long)) or size < 0:
            def _bad(): raise SFTPError(FX_BAD_MESSAGE, "new size is not a valid nonnegative integer")
            return defer.execute(_bad)

        d = defer.Deferred()
        def _resize(ign):
            self.consumer.set_current_size(size)
            eventually_callback(d)(None)
            return None
        self.async.addCallbacks(_resize, eventually_errback(d))
        return d


class StoppableList:
    def __init__(self, items):
        self.items = items
    def __iter__(self):
        for i in self.items:
            yield i
    def close(self):
        pass


class Reason:
    def __init__(self, value):
        self.value = value


class SFTPUserHandler(ConchUser, PrefixingLogMixin):
    implements(ISFTPServer)
    def __init__(self, client, rootnode, username):
        ConchUser.__init__(self)
        PrefixingLogMixin.__init__(self, facility="tahoe.sftp")
        if noisy: self.log(".__init__(%r, %r, %r)" % (client, rootnode, username), level=NOISY)

        self.channelLookup["session"] = session.SSHSession
        self.subsystemLookup["sftp"] = FileTransferServer

        self._client = client
        self._root = rootnode
        self._username = username
        self._convergence = client.convergence
        self._logged_out = False

    def logout(self):
        self._logged_out = True

    def check_abort(self):
        return self._logged_out

    def gotVersion(self, otherVersion, extData):
        self.log(".gotVersion(%r, %r)" % (otherVersion, extData), level=OPERATIONAL)
        return {}

    def openFile(self, pathstring, flags, attrs):
        self.log(".openFile(%r, %r = %r, %r)" % (pathstring, flags, _repr_flags(flags), attrs), level=OPERATIONAL)

        # This is used for both reading and writing.
        # First exclude invalid combinations of flags.

        # /usr/bin/sftp 'get' gives us FXF_READ, while 'put' on a new file
        # gives FXF_WRITE | FXF_CREAT | FXF_TRUNC. I'm guessing that 'put' on an
        # existing file gives the same.

        if not (flags & (FXF_READ | FXF_WRITE)):
            raise SFTPError(FX_BAD_MESSAGE,
                            "invalid file open flags: at least one of FXF_READ and FXF_WRITE must be set")

        if not (flags & FXF_CREAT):
            if flags & FXF_TRUNC:
                raise SFTPError(FX_BAD_MESSAGE,
                                "invalid file open flags: FXF_TRUNC cannot be set without FXF_CREAT")
            if flags & FXF_EXCL:
                raise SFTPError(FX_BAD_MESSAGE,
                                "invalid file open flags: FXF_EXCL cannot be set without FXF_CREAT")

        path = self._path_from_string(pathstring)
        if not path:
            raise SFTPError(FX_NO_SUCH_FILE, "path cannot be empty")

        # The combination of flags is potentially valid. Now there are two major cases:
        #
        #  1. The path is specified as /uri/FILECAP, with no parent directory.
        #     If the FILECAP is mutable and writeable, then we can open it in write-only
        #     or read/write mode (non-exclusively), otherwise we can only open it in
        #     read-only mode. The open should succeed immediately as long as FILECAP is
        #     a valid known filecap that grants the required permission.
        #
        #  2. The path is specified relative to a parent. We find the parent dirnode and
        #     get the child's URI and metadata if it exists. There are four subcases:
        #       a. the child does not exist: FXF_CREAT must be set, and we must be able
        #          to write to the parent directory.
        #       b. the child exists but is not a valid known filecap: fail
        #       c. the child is mutable: if we are trying to open it write-only or
        #          read/write, then we must be able to write to the file.
        #       d. the child is immutable: if we are trying to open it write-only or
        #          read/write, then we must be able to write to the parent directory.
        #
        # To reduce latency, open succeeds as soon as these conditions are met, even
        # though there might be a failure in downloading the existing file or uploading
        # a new one.
        #
        # Note that the permission checks below are for more precise error reporting on
        # the open call; later operations would fail even if we did not make these checks.

        stash = {'parent': None}
        d = self._get_root(path)
        def _got_root((root, path)):
            if root.is_unknown():
                raise SFTPError(FX_PERMISSION_DENIED,
                                "cannot open an unknown cap (or child of an unknown directory). "
                                "Upgrading the gateway to a later Tahoe-LAFS version may help")
            if not path:
                # case 1
                if noisy: self.log("case 1: root = %r, path[:-1] = %r" % (root, path[:-1]), level=NOISY)
                if not IFileNode.providedBy(root):
                    raise SFTPError(FX_PERMISSION_DENIED,
                                    "cannot open a directory cap")
                if (flags & FXF_WRITE) and root.is_readonly():
                    raise SFTPError(FX_PERMISSION_DENIED,
                                    "cannot write to a non-writeable filecap without a parent directory")
                if flags & FXF_EXCL:
                    raise SFTPError(FX_PERMISSION_DENIED,
                                    "cannot create a file exclusively when it already exists")

                return _make_sftp_file(self.check_abort, flags, self._convergence, filenode=root)
            else:
                # case 2
                childname = path[-1]
                if noisy: self.log("case 2: root = %r, childname = %r, path[:-1] = %r" %
                                   (root, childname, path[:-1]), level=NOISY)
                d2 = root.get_child_at_path(path[:-1])
                def _got_parent(parent):
                    if noisy: self.log("_got_parent(%r)" % (parent,), level=NOISY)
                    stash['parent'] = parent

                    if flags & FXF_EXCL:
                        # FXF_EXCL means that the link to the file (not the file itself) must
                        # be created atomically wrt updates by this storage client.
                        # That is, we need to create the link before returning success to the
                        # SFTP open request (and not just on close, as would normally be the
                        # case). We make the link initially point to a zero-length LIT file,
                        # which is consistent with what might happen on a POSIX filesystem.

                        if parent.is_readonly():
                            raise SFTPError(FX_PERMISSION_DENIED,
                                            "cannot create a file exclusively when the parent directory is read-only")

                        # 'overwrite=False' ensures failure if the link already exists.
                        # FIXME: should use a single call to set_uri and return (child, metadata) (#1035)
                        zero_length_lit = "URI:LIT:"
                        d3 = parent.set_uri(childname, None, zero_length_lit, overwrite=False)
                        def _seturi_done(child):
                            stash['child'] = child
                            return parent.get_metadata_for(childname)
                        d3.addCallback(_seturi_done)
                        d3.addCallback(lambda metadata: (stash['child'], metadata))
                        return d3
                    else:
                        if noisy: self.log("get_child_and_metadata(%r)" % (childname,), level=NOISY)
                        return parent.get_child_and_metadata(childname)
                d2.addCallback(_got_parent)

                def _got_child( (filenode, metadata) ):
                    if noisy: self.log("_got_child( (%r, %r) )" % (filenode, metadata), level=NOISY)
                    parent = stash['parent']
                    if filenode.is_unknown():
                        raise SFTPError(FX_PERMISSION_DENIED,
                                        "cannot open an unknown cap. Upgrading the gateway "
                                        "to a later Tahoe-LAFS version may help")
                    if not IFileNode.providedBy(filenode):
                        raise SFTPError(FX_PERMISSION_DENIED,
                                        "cannot open a directory as if it were a file")
                    if (flags & FXF_WRITE) and filenode.is_mutable() and filenode.is_readonly():
                        raise SFTPError(FX_PERMISSION_DENIED,
                                        "cannot open a read-only mutable file for writing")
                    if (flags & FXF_WRITE) and parent.is_readonly():
                        raise SFTPError(FX_PERMISSION_DENIED,
                                        "cannot open a file for writing when the parent directory is read-only")

                    return _make_sftp_file(self.check_abort, flags, self._convergence, parent=parent,
                                           childname=childname, filenode=filenode, metadata=metadata)
                def _no_child(f):
                    if noisy: self.log("_no_child(%r)" % (f,), level=NOISY)
                    f.trap(NoSuchChildError)
                    parent = stash['parent']
                    if parent is None:
                        return f
                    if not (flags & FXF_CREAT):
                        raise SFTPError(FX_NO_SUCH_FILE,
                                        "the file does not exist, and was not opened with the creation (CREAT) flag")
                    if parent.is_readonly():
                        raise SFTPError(FX_PERMISSION_DENIED,
                                        "cannot create a file when the parent directory is read-only")

                    return _make_sftp_file(self.check_abort, flags, self._convergence, parent=parent,
                                           childname=childname)
                d2.addCallbacks(_got_child, _no_child)
                return d2
        d.addCallback(_got_root)
        d.addErrback(_raise_error)
        return d

    def removeFile(self, pathstring):
        self.log(".removeFile(%r)" % (pathstring,), level=OPERATIONAL)

        path = self._path_from_string(pathstring)
        return self._remove_object(path, must_be_file=True)

    def renameFile(self, oldpathstring, newpathstring):
        self.log(".renameFile(%r, %r)" % (oldpathstring, newpathstring), level=OPERATIONAL)

        fromPath = self._path_from_string(oldpathstring)
        toPath = self._path_from_string(newpathstring)

        # the target directory must already exist
        d = deferredutil.gatherResults([self._get_parent(fromPath),
                                        self._get_parent(toPath)])
        def _got( (fromPair, toPair) ):
            if noisy: self.log("_got( (%r, %r) ) in .renameFile(%r, %r)" %
                               (fromPair, toPair, oldpathstring, newpathstring), level=NOISY)
            (fromParent, fromChildname) = fromPair
            (toParent, toChildname) = toPair

            # <http://tools.ietf.org/html/draft-ietf-secsh-filexfer-02#section-6.5>
            # "It is an error if there already exists a file with the name specified
            #  by newpath."
            # FIXME: use move_child_to_path to avoid possible data loss due to #943
            d = fromParent.move_child_to(fromChildname, toParent, toChildname, overwrite=False)
            #d = parent.move_child_to_path(fromChildname, toRoot, toPath[:-1],
            #                              toPath[-1], overwrite=False)
            return d
        d.addCallback(_got)
        d.addErrback(_raise_error)
        return d

    def makeDirectory(self, pathstring, attrs):
        self.log(".makeDirectory(%r, %r)" % (pathstring, attrs), level=OPERATIONAL)

        path = self._path_from_string(pathstring)
        metadata = self._attrs_to_metadata(attrs)
        d = self._get_root(path)
        d.addCallback(lambda (root, path):
                      self._get_or_create_directories(root, path, metadata))
        d.addErrback(_raise_error)
        return d

    def _get_or_create_directories(self, node, path, metadata):
        if not IDirectoryNode.providedBy(node):
            # unfortunately it is too late to provide the name of the
            # blocking file in the error message.
            raise SFTPError(FX_PERMISSION_DENIED,
                            "cannot create directory because there "
                            "is a file in the way") # close enough
        if not path:
            return defer.succeed(node)
        d = node.get(path[0])
        def _maybe_create(f):
            f.trap(NoSuchChildError)
            return node.create_subdirectory(path[0])
        d.addErrback(_maybe_create)
        d.addCallback(self._get_or_create_directories, path[1:], metadata)
        d.addErrback(_raise_error)
        return d

    def removeDirectory(self, pathstring):
        self.log(".removeDirectory(%r)" % (pathstring,), level=OPERATIONAL)

        path = self._path_from_string(pathstring)
        return self._remove_object(path, must_be_directory=True)

    def _remove_object(self, path, must_be_directory=False, must_be_file=False):
        d = defer.maybeDeferred(self._get_parent, path)
        def _got_parent( (parent, childname) ):
            d2 = parent.get(childname)
            def _got_child(child):
                # Unknown children can be removed by either removeFile or removeDirectory.
                if must_be_directory and IFileNode.providedBy(child):
                    raise SFTPError(FX_PERMISSION_DENIED, "rmdir called on a file")
                if must_be_file and IDirectoryNode.providedBy(child):
                    raise SFTPError(FX_PERMISSION_DENIED, "rmfile called on a directory")
                return parent.delete(childname)
            d2.addCallback(_got_child)
            return d2
        d.addCallback(_got_parent)
        d.addErrback(_raise_error)
        return d

    def openDirectory(self, pathstring):
        self.log(".openDirectory(%r)" % (pathstring,), level=OPERATIONAL)

        path = self._path_from_string(pathstring)
        d = self._get_node_and_metadata_for_path(path)
        def _list( (dirnode, metadata) ):
            if dirnode.is_unknown():
                raise SFTPError(FX_PERMISSION_DENIED,
                                "cannot list an unknown cap as a directory. Upgrading the gateway "
                                "to a later Tahoe-LAFS version may help")
            if not IDirectoryNode.providedBy(dirnode):
                raise SFTPError(FX_PERMISSION_DENIED,
                                "cannot list a file as if it were a directory")
            d2 = dirnode.list()
            def _render(children):
                parent_writeable = not dirnode.is_readonly()
                results = []
                for filename, (node, metadata) in children.iteritems():
                    # The file size may be cached or absent.
                    writeable = parent_writeable and (node.is_unknown() or
                                                      not (node.is_mutable() and node.is_readonly()))
                    attrs = _populate_attrs(node, metadata, writeable)
                    filename_utf8 = filename.encode('utf-8')
                    longname = _lsLine(filename_utf8, attrs)
                    results.append( (filename_utf8, longname, attrs) )
                return StoppableList(results)
            d2.addCallback(_render)
            return d2
        d.addCallback(_list)
        d.addErrback(_raise_error)
        return d

    def getAttrs(self, pathstring, followLinks):
        self.log(".getAttrs(%r, followLinks=%r)" % (pathstring, followLinks), level=OPERATIONAL)

        d = self._get_node_and_metadata_for_path(self._path_from_string(pathstring))
        def _render( (node, metadata) ):
            # When asked about a specific file, report its current size.
            # TODO: the modification time for a mutable file should be
            # reported as the update time of the best version. But that
            # information isn't currently stored in mutable shares, I think.
            d2 = node.get_current_size()
            def _got_size(size):
                # FIXME: pass correct value for writeable
                attrs = _populate_attrs(node, metadata, False, size=size)
                return attrs
            d2.addCallback(_got_size)
            return d2
        d.addCallback(_render)
        d.addErrback(_raise_error)
        return d

    def setAttrs(self, pathstring, attrs):
        self.log(".setAttrs(%r, %r)" % (pathstring, attrs), level=OPERATIONAL)

        if "size" in attrs:
            # this would require us to download and re-upload the truncated/extended
            # file contents
            raise SFTPError(FX_OP_UNSUPPORTED, "setAttrs wth size attribute")
        return None

    def readLink(self, pathstring):
        self.log(".readLink(%r)" % (pathstring,), level=OPERATIONAL)

        raise SFTPError(FX_OP_UNSUPPORTED, "readLink")

    def makeLink(self, linkPathstring, targetPathstring):
        self.log(".makeLink(%r, %r)" % (linkPathstring, targetPathstring), level=OPERATIONAL)

        raise SFTPError(FX_OP_UNSUPPORTED, "makeLink")

    def extendedRequest(self, extendedName, extendedData):
        self.log(".extendedRequest(%r, %r)" % (extendedName, extendedData), level=OPERATIONAL)

        if extendedName == 'statvfs@openssh.com' or extendedName == 'fstatvfs@openssh.com':
            # <http://dev.libssh.org/ticket/11>
            return struct.pack('>11Q',
                1024,         # uint64  f_bsize     /* file system block size */
                1024,         # uint64  f_frsize    /* fundamental fs block size */
                628318530,    # uint64  f_blocks    /* number of blocks (unit f_frsize) */
                314159265,    # uint64  f_bfree     /* free blocks in file system */
                314159265,    # uint64  f_bavail    /* free blocks for non-root */
                200000000,    # uint64  f_files     /* total file inodes */
                100000000,    # uint64  f_ffree     /* free file inodes */
                100000000,    # uint64  f_favail    /* free file inodes for non-root */
                0x1AF5,       # uint64  f_fsid      /* file system id */
                2,            # uint64  f_flag      /* bit mask = ST_NOSUID; not ST_RDONLY */
                65535,        # uint64  f_namemax   /* maximum filename length */
                )

        raise SFTPError(FX_OP_UNSUPPORTED, "extendedRequest %r" % extendedName)

    def realPath(self, pathstring):
        self.log(".realPath(%r)" % (pathstring,), level=OPERATIONAL)

        path_utf8 = [p.encode('utf-8') for p in self._path_from_string(pathstring)]
        return "/" + "/".join(path_utf8)

    def _path_from_string(self, pathstring):
        if noisy: self.log("CONVERT %r" % (pathstring,), level=NOISY)

        # The home directory is the root directory.
        pathstring = pathstring.strip("/")
        if pathstring == "" or pathstring == ".":
            path_utf8 = []
        else:
            path_utf8 = pathstring.split("/")

        # <http://tools.ietf.org/html/draft-ietf-secsh-filexfer-02#section-6.2>
        # "Servers SHOULD interpret a path name component ".." as referring to
        #  the parent directory, and "." as referring to the current directory."
        path = []
        for p_utf8 in path_utf8:
            if p_utf8 == "..":
                # ignore excess .. components at the root
                if len(path) > 0:
                    path = path[:-1]
            elif p_utf8 != ".":
                try:
                    p = p_utf8.decode('utf-8', 'strict')
                except UnicodeError:
                    raise SFTPError(FX_NO_SUCH_FILE, "path could not be decoded as UTF-8")
                path.append(p)

        if noisy: self.log(" PATH %r" % (path,), level=NOISY)
        return path

    def _get_node_and_metadata_for_path(self, path):
        d = self._get_root(path)
        def _got_root( (root, path) ):
            if noisy: self.log("_got_root( (%r, %r) )" % (root, path), level=NOISY)
            if path:
                return root.get_child_and_metadata_at_path(path)
            else:
                return (root, {})
        d.addCallback(_got_root)
        return d

    def _get_root(self, path):
        # return (root, remaining_path)
        if path and path[0] == u"uri":
            d = defer.maybeDeferred(self._client.create_node_from_uri, path[1].encode('utf-8'))
            d.addCallback(lambda root: (root, path[2:]))
        else:
            d = defer.succeed((self._root, path))
        return d

    def _get_parent(self, path):
        # fire with (parentnode, childname)
        if not path:
            def _nosuch(): raise SFTPError(FX_NO_SUCH_FILE, "path does not exist")
            return defer.execute(_nosuch)

        childname = path[-1]
        assert isinstance(childname, unicode), repr(childname)
        d = self._get_root(path)
        def _got_root( (root, path) ):
            if not path:
                raise SFTPError(FX_NO_SUCH_FILE, "path does not exist")
            return root.get_child_at_path(path[:-1])
        d.addCallback(_got_root)
        def _got_parent(parent):
            return (parent, childname)
        d.addCallback(_got_parent)
        return d

    def _attrs_to_metadata(self, attrs):
        metadata = {}

        for key in attrs:
            if key == "mtime" or key == "ctime" or key == "createtime":
                metadata[key] = long(attrs[key])
            elif key.startswith("ext_"):
                metadata[key] = str(attrs[key])

        return metadata


class SFTPUser(ConchUser, PrefixingLogMixin):
    implements(ISession)
    def __init__(self, check_abort, client, rootnode, username, convergence):
        ConchUser.__init__(self)
        PrefixingLogMixin.__init__(self, facility="tahoe.sftp")

        self.channelLookup["session"] = session.SSHSession
        self.subsystemLookup["sftp"] = FileTransferServer

        self.check_abort = check_abort
        self.client = client
        self.root = rootnode
        self.username = username
        self.convergence = convergence

    def getPty(self, terminal, windowSize, attrs):
        self.log(".getPty(%r, %r, %r)" % (terminal, windowSize, attrs), level=OPERATIONAL)
        raise NotImplementedError

    def openShell(self, protocol):
        self.log(".openShell(%r)" % (protocol,), level=OPERATIONAL)
        raise NotImplementedError

    def execCommand(self, protocol, cmd):
        self.log(".execCommand(%r, %r)" % (protocol, cmd), level=OPERATIONAL)
        raise NotImplementedError

    def windowChanged(self, newWindowSize):
        self.log(".windowChanged(%r)" % (newWindowSize,), level=OPERATIONAL)

    def eofReceived():
        self.log(".eofReceived()", level=OPERATIONAL)

    def closed(self):
        self.log(".closed()", level=OPERATIONAL)


# if you have an SFTPUser, and you want something that provides ISFTPServer,
# then you get SFTPHandler(user)
components.registerAdapter(SFTPHandler, SFTPUser, ISFTPServer)

from auth import AccountURLChecker, AccountFileChecker, NeedRootcapLookupScheme

class Dispatcher:
    implements(portal.IRealm)
    def __init__(self, client):
        self._client = client

    def requestAvatar(self, avatarID, mind, interface):
        assert interface == IConchUser
        rootnode = self._client.create_node_from_uri(avatarID.rootcap)
        handler = SFTPUserHandler(self._client, rootnode, avatarID.username)
        return (interface, handler, handler.logout)


class SFTPServer(service.MultiService):
    def __init__(self, client, accountfile, accounturl,
                 sftp_portstr, pubkey_file, privkey_file):
        service.MultiService.__init__(self)

        r = Dispatcher(client)
        p = portal.Portal(r)

        if accountfile:
            c = AccountFileChecker(self, accountfile)
            p.registerChecker(c)
        if accounturl:
            c = AccountURLChecker(self, accounturl)
            p.registerChecker(c)
        if not accountfile and not accounturl:
            # we could leave this anonymous, with just the /uri/CAP form
            raise NeedRootcapLookupScheme("must provide an account file or URL")

        pubkey = keys.Key.fromFile(pubkey_file)
        privkey = keys.Key.fromFile(privkey_file)
        class SSHFactory(factory.SSHFactory):
            publicKeys = {pubkey.sshType(): pubkey}
            privateKeys = {privkey.sshType(): privkey}
            def getPrimes(self):
                try:
                    # if present, this enables diffie-hellman-group-exchange
                    return primes.parseModuliFile("/etc/ssh/moduli")
                except IOError:
                    return None

        f = SSHFactory()
        f.portal = p

        s = strports.service(sftp_portstr, f)
        s.setServiceParent(self)
