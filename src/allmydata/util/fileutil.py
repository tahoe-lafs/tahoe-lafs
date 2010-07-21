"""
Futz with files like a pro.
"""

import sys, exceptions, os, stat, tempfile, time, binascii

from twisted.python import log

from pycryptopp.cipher.aes import AES


def rename(src, dst, tries=4, basedelay=0.1):
    """ Here is a superkludge to workaround the fact that occasionally on
    Windows some other process (e.g. an anti-virus scanner, a local search
    engine, etc.) is looking at your file when you want to delete or move it,
    and hence you can't.  The horrible workaround is to sit and spin, trying
    to delete it, for a short time and then give up.

    With the default values of tries and basedelay this can block for less
    than a second.

    @param tries: number of tries -- each time after the first we wait twice
    as long as the previous wait
    @param basedelay: how long to wait before the second try
    """
    for i in range(tries-1):
        try:
            return os.rename(src, dst)
        except EnvironmentError, le:
            # XXX Tighten this to check if this is a permission denied error (possibly due to another Windows process having the file open and execute the superkludge only in this case.
            log.msg("XXX KLUDGE Attempting to move file %s => %s; got %s; sleeping %s seconds" % (src, dst, le, basedelay,))
            time.sleep(basedelay)
            basedelay *= 2
    return os.rename(src, dst) # The last try.

def remove(f, tries=4, basedelay=0.1):
    """ Here is a superkludge to workaround the fact that occasionally on
    Windows some other process (e.g. an anti-virus scanner, a local search
    engine, etc.) is looking at your file when you want to delete or move it,
    and hence you can't.  The horrible workaround is to sit and spin, trying
    to delete it, for a short time and then give up.

    With the default values of tries and basedelay this can block for less
    than a second.

    @param tries: number of tries -- each time after the first we wait twice
    as long as the previous wait
    @param basedelay: how long to wait before the second try
    """
    try:
        os.chmod(f, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
    except:
        pass
    for i in range(tries-1):
        try:
            return os.remove(f)
        except EnvironmentError, le:
            # XXX Tighten this to check if this is a permission denied error (possibly due to another Windows process having the file open and execute the superkludge only in this case.
            if not os.path.exists(f):
                return
            log.msg("XXX KLUDGE Attempting to remove file %s; got %s; sleeping %s seconds" % (f, le, basedelay,))
            time.sleep(basedelay)
            basedelay *= 2
    return os.remove(f) # The last try.

class ReopenableNamedTemporaryFile:
    """
    This uses tempfile.mkstemp() to generate a secure temp file.  It then closes
    the file, leaving a zero-length file as a placeholder.  You can get the
    filename with ReopenableNamedTemporaryFile.name.  When the
    ReopenableNamedTemporaryFile instance is garbage collected or its shutdown()
    method is called, it deletes the file.
    """
    def __init__(self, *args, **kwargs):
        fd, self.name = tempfile.mkstemp(*args, **kwargs)
        os.close(fd)

    def __repr__(self):
        return "<%s instance at %x %s>" % (self.__class__.__name__, id(self), self.name)

    def __str__(self):
        return self.__repr__()

    def __del__(self):
        self.shutdown()

    def shutdown(self):
        remove(self.name)

class NamedTemporaryDirectory:
    """
    This calls tempfile.mkdtemp(), stores the name of the dir in
    self.name, and rmrf's the dir when it gets garbage collected or
    "shutdown()".
    """
    def __init__(self, cleanup=True, *args, **kwargs):
        """ If cleanup, then the directory will be rmrf'ed when the object is shutdown. """
        self.cleanup = cleanup
        self.name = tempfile.mkdtemp(*args, **kwargs)

    def __repr__(self):
        return "<%s instance at %x %s>" % (self.__class__.__name__, id(self), self.name)

    def __str__(self):
        return self.__repr__()

    def __del__(self):
        try:
            self.shutdown()
        except:
            import traceback
            traceback.print_exc()

    def shutdown(self):
        if self.cleanup and hasattr(self, 'name'):
            rm_dir(self.name)

class EncryptedTemporaryFile:
    # not implemented: next, readline, readlines, xreadlines, writelines

    def __init__(self):
        self.file = tempfile.TemporaryFile()
        self.key = os.urandom(16)  # AES-128

    def _crypt(self, offset, data):
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

    def seek(self, offset, whence=0):  # 0 = SEEK_SET
        self.file.seek(offset, whence)

    def tell(self):
        offset = self.file.tell()
        return offset

    def read(self, size=-1):
        """A read must not follow a write, or vice-versa, without an intervening seek."""
        index = self.file.tell()
        ciphertext = self.file.read(size)
        plaintext = self._crypt(index, ciphertext)
        return plaintext

    def write(self, plaintext):
        """A read must not follow a write, or vice-versa, without an intervening seek.
        If seeking and then writing causes a 'hole' in the file, the contents of the
        hole are unspecified."""
        index = self.file.tell()
        ciphertext = self._crypt(index, plaintext)
        self.file.write(ciphertext)

    def truncate(self, newsize):
        """Truncate or extend the file to 'newsize'. If it is extended, the contents after the
        old end-of-file are unspecified. The file position after this operation is unspecified."""
        self.file.truncate(newsize)


def make_dirs(dirname, mode=0777):
    """
    An idempotent version of os.makedirs().  If the dir already exists, do
    nothing and return without raising an exception.  If this call creates the
    dir, return without raising an exception.  If there is an error that
    prevents creation or if the directory gets deleted after make_dirs() creates
    it and before make_dirs() checks that it exists, raise an exception.
    """
    tx = None
    try:
        os.makedirs(dirname, mode)
    except OSError, x:
        tx = x

    if not os.path.isdir(dirname):
        if tx:
            raise tx
        raise exceptions.IOError, "unknown error prevented creation of directory, or deleted the directory immediately after creation: %s" % dirname # careful not to construct an IOError with a 2-tuple, as that has a special meaning...

def rm_dir(dirname):
    """
    A threadsafe and idempotent version of shutil.rmtree().  If the dir is
    already gone, do nothing and return without raising an exception.  If this
    call removes the dir, return without raising an exception.  If there is an
    error that prevents deletion or if the directory gets created again after
    rm_dir() deletes it and before rm_dir() checks that it is gone, raise an
    exception.
    """
    excs = []
    try:
        os.chmod(dirname, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
        for f in os.listdir(dirname):
            fullname = os.path.join(dirname, f)
            if os.path.isdir(fullname):
                rm_dir(fullname)
            else:
                remove(fullname)
        os.rmdir(dirname)
    except Exception, le:
        # Ignore "No such file or directory"
        if (not isinstance(le, OSError)) or le.args[0] != 2:
            excs.append(le)

    # Okay, now we've recursively removed everything, ignoring any "No
    # such file or directory" errors, and collecting any other errors.

    if os.path.exists(dirname):
        if len(excs) == 1:
            raise excs[0]
        if len(excs) == 0:
            raise OSError, "Failed to remove dir for unknown reason."
        raise OSError, excs


def remove_if_possible(f):
    try:
        remove(f)
    except:
        pass

def open_or_create(fname, binarymode=True):
    try:
        return open(fname, binarymode and "r+b" or "r+")
    except EnvironmentError:
        return open(fname, binarymode and "w+b" or "w+")

def du(basedir):
    size = 0

    for root, dirs, files in os.walk(basedir):
        for f in files:
            fn = os.path.join(root, f)
            size += os.path.getsize(fn)

    return size

def move_into_place(source, dest):
    """Atomically replace a file, or as near to it as the platform allows.
    The dest file may or may not exist."""
    if "win32" in sys.platform.lower():
        remove_if_possible(dest)
    os.rename(source, dest)

def write(path, data):
    wf = open(path, "wb")
    try:
        wf.write(data)
    finally:
        wf.close()

def read(path):
    rf = open(path, "rb")
    try:
        return rf.read()
    finally:
        rf.close()

def put_file(pathname, inf):
    # TODO: create temporary file and move into place?
    outf = open(os.path.expanduser(pathname), "wb")
    try:
        while True:
            data = inf.read(32768)
            if not data:
                break
            outf.write(data)
    finally:
        outf.close()


# Work around <http://bugs.python.org/issue3426>. This code is adapted from
# <http://svn.python.org/view/python/trunk/Lib/ntpath.py?revision=78247&view=markup>
# with some simplifications.

_getfullpathname = None
try:
    from nt import _getfullpathname
except ImportError:
    pass

def abspath_expanduser_unicode(path):
    """Return the absolute version of a path."""
    assert isinstance(path, unicode), path

    path = os.path.expanduser(path)

    if _getfullpathname:
        # On Windows, os.path.isabs will return True for paths without a drive letter,
        # e.g. "\\". See <http://bugs.python.org/issue1669539>.
        try:
            path = _getfullpathname(path or u".")
        except WindowsError:
            pass

    if not os.path.isabs(path):
        path = os.path.join(os.getcwdu(), path)

    # We won't hit <http://bugs.python.org/issue5827> because
    # there is always at least one Unicode path component.
    return os.path.normpath(path)
