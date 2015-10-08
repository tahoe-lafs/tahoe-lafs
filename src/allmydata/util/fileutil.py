"""
Futz with files like a pro.
"""

import sys, exceptions, os, stat, tempfile, time, binascii
from collections import namedtuple
from errno import ENOENT

if sys.platform == "win32":
    from ctypes import WINFUNCTYPE, WinError, windll, POINTER, byref, c_ulonglong, \
        create_unicode_buffer, get_last_error
    from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPWSTR, LPVOID, HANDLE

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

def write_atomically(target, contents, mode="b"):
    f = open(target+".tmp", "w"+mode)
    try:
        f.write(contents)
    finally:
        f.close()
    move_into_place(target+".tmp", target)

def write(path, data, mode="wb"):
    wf = open(path, mode)
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

def put_file(path, inf):
    precondition_abspath(path)

    # TODO: create temporary file and move into place?
    outf = open(path, "wb")
    try:
        while True:
            data = inf.read(32768)
            if not data:
                break
            outf.write(data)
    finally:
        outf.close()


def precondition_abspath(path):
    if not isinstance(path, unicode):
        raise AssertionError("an abspath must be a Unicode string")

    if sys.platform == "win32":
        # This intentionally doesn't view absolute paths starting with a drive specification, or
        # paths relative to the current drive, as acceptable.
        if not path.startswith("\\\\"):
            raise AssertionError("an abspath should be normalized using abspath_expanduser_unicode")
    else:
        # This intentionally doesn't view the path '~' or paths starting with '~/' as acceptable.
        if not os.path.isabs(path):
            raise AssertionError("an abspath should be normalized using abspath_expanduser_unicode")

# Work around <http://bugs.python.org/issue3426>. This code is adapted from
# <http://svn.python.org/view/python/trunk/Lib/ntpath.py?revision=78247&view=markup>
# with some simplifications.

_getfullpathname = None
try:
    from nt import _getfullpathname
except ImportError:
    pass

def abspath_expanduser_unicode(path, base=None):
    """
    Return the absolute version of a path. If 'base' is given and 'path' is relative,
    the path will be expanded relative to 'base'.
    'path' must be a Unicode string. 'base', if given, must be a Unicode string
    corresponding to an absolute path as returned by a previous call to
    abspath_expanduser_unicode.
    """
    if not isinstance(path, unicode):
        raise AssertionError("paths must be Unicode strings")
    if base is not None:
        precondition_abspath(base)

    path = expanduser(path)

    if _getfullpathname:
        # On Windows, os.path.isabs will incorrectly return True
        # for paths without a drive letter (that are not UNC paths),
        # e.g. "\\". See <http://bugs.python.org/issue1669539>.
        try:
            if base is None:
                path = _getfullpathname(path or u".")
            else:
                path = _getfullpathname(os.path.join(base, path))
        except OSError:
            pass

    if not os.path.isabs(path):
        if base is None:
            path = os.path.join(os.getcwdu(), path)
        else:
            path = os.path.join(base, path)

    # We won't hit <http://bugs.python.org/issue5827> because
    # there is always at least one Unicode path component.
    path = os.path.normpath(path)

    if sys.platform == "win32":
        path = to_windows_long_path(path)

    return path

def to_windows_long_path(path):
    # '/' is normally a perfectly valid path component separator in Windows.
    # However, when using the "\\?\" syntax it is not recognized, so we
    # replace it with '\' here.
    path = path.replace(u"/", u"\\")

    # Note that other normalizations such as removing '.' and '..' should
    # be done outside this function.

    if path.startswith(u"\\\\?\\") or path.startswith(u"\\\\.\\"):
        return path
    elif path.startswith(u"\\\\"):
        return u"\\\\?\\UNC\\" + path[2 :]
    else:
        return u"\\\\?\\" + path


have_GetDiskFreeSpaceExW = False
if sys.platform == "win32":
    from ctypes import WINFUNCTYPE, windll, POINTER, byref, c_ulonglong, create_unicode_buffer, \
        get_last_error
    from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPWSTR

    # <http://msdn.microsoft.com/en-us/library/windows/desktop/ms683188%28v=vs.85%29.aspx>
    GetEnvironmentVariableW = WINFUNCTYPE(
        DWORD,
          LPCWSTR, LPWSTR, DWORD,
        use_last_error=True
      )(("GetEnvironmentVariableW", windll.kernel32))

    try:
        # <http://msdn.microsoft.com/en-us/library/aa383742%28v=VS.85%29.aspx>
        PULARGE_INTEGER = POINTER(c_ulonglong)

        # <http://msdn.microsoft.com/en-us/library/aa364937%28VS.85%29.aspx>
        GetDiskFreeSpaceExW = WINFUNCTYPE(
            BOOL,
              LPCWSTR, PULARGE_INTEGER, PULARGE_INTEGER, PULARGE_INTEGER,
            use_last_error=True
          )(("GetDiskFreeSpaceExW", windll.kernel32))

        have_GetDiskFreeSpaceExW = True
    except Exception:
        import traceback
        traceback.print_exc()

def expanduser(path):
    # os.path.expanduser is hopelessly broken for Unicode paths on Windows (ticket #1674).
    if sys.platform == "win32":
        return windows_expanduser(path)
    else:
        return os.path.expanduser(path)

def windows_expanduser(path):
    if not path.startswith('~'):
        return path

    home_dir = windows_getenv(u'USERPROFILE')
    if home_dir is None:
        home_drive = windows_getenv(u'HOMEDRIVE')
        home_path = windows_getenv(u'HOMEPATH')
        if home_drive is None or home_path is None:
            raise OSError("Could not find home directory: neither %USERPROFILE% nor (%HOMEDRIVE% and %HOMEPATH%) are set.")
        home_dir = os.path.join(home_drive, home_path)

    if path == '~':
        return home_dir
    elif path.startswith('~/') or path.startswith('~\\'):
        return os.path.join(home_dir, path[2 :])
    else:
        return path

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms681382%28v=vs.85%29.aspx>
ERROR_ENVVAR_NOT_FOUND = 203

def windows_getenv(name):
    # Based on <http://stackoverflow.com/questions/2608200/problems-with-umlauts-in-python-appdata-environvent-variable/2608368#2608368>,
    # with improved error handling. Returns None if there is no enivronment variable of the given name.
    if not isinstance(name, unicode):
        raise AssertionError("name must be Unicode")

    n = GetEnvironmentVariableW(name, None, 0)
    # GetEnvironmentVariableW returns DWORD, so n cannot be negative.
    if n == 0:
        err = get_last_error()
        if err == ERROR_ENVVAR_NOT_FOUND:
            return None
        raise OSError("Windows error %d attempting to read size of environment variable %r"
                      % (err, name))
    if n == 1:
        # Avoid an ambiguity between a zero-length string and an error in the return value of the
        # call to GetEnvironmentVariableW below.
        return u""

    buf = create_unicode_buffer(u'\0'*n)
    retval = GetEnvironmentVariableW(name, buf, n)
    if retval == 0:
        err = get_last_error()
        if err == ERROR_ENVVAR_NOT_FOUND:
            return None
        raise OSError("Windows error %d attempting to read environment variable %r"
                      % (err, name))
    if retval >= n:
        raise OSError("Unexpected result %d (expected less than %d) from GetEnvironmentVariableW attempting to read environment variable %r"
                      % (retval, n, name))

    return buf.value

def get_disk_stats(whichdir, reserved_space=0):
    """Return disk statistics for the storage disk, in the form of a dict
    with the following fields.
      total:            total bytes on disk
      free_for_root:    bytes actually free on disk
      free_for_nonroot: bytes free for "a non-privileged user" [Unix] or
                          the current user [Windows]; might take into
                          account quotas depending on platform
      used:             bytes used on disk
      avail:            bytes available excluding reserved space
    An AttributeError can occur if the OS has no API to get disk information.
    An EnvironmentError can occur if the OS call fails.

    whichdir is a directory on the filesystem in question -- the
    answer is about the filesystem, not about the directory, so the
    directory is used only to specify which filesystem.

    reserved_space is how many bytes to subtract from the answer, so
    you can pass how many bytes you would like to leave unused on this
    filesystem as reserved_space.
    """

    if have_GetDiskFreeSpaceExW:
        # If this is a Windows system and GetDiskFreeSpaceExW is available, use it.
        # (This might put up an error dialog unless
        # SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX) has been called,
        # which we do in allmydata.windows.fixups.initialize().)

        n_free_for_nonroot = c_ulonglong(0)
        n_total            = c_ulonglong(0)
        n_free_for_root    = c_ulonglong(0)
        retval = GetDiskFreeSpaceExW(whichdir, byref(n_free_for_nonroot),
                                               byref(n_total),
                                               byref(n_free_for_root))
        if retval == 0:
            raise OSError("Windows error %d attempting to get disk statistics for %r"
                          % (get_last_error(), whichdir))
        free_for_nonroot = n_free_for_nonroot.value
        total            = n_total.value
        free_for_root    = n_free_for_root.value
    else:
        # For Unix-like systems.
        # <http://docs.python.org/library/os.html#os.statvfs>
        # <http://opengroup.org/onlinepubs/7990989799/xsh/fstatvfs.html>
        # <http://opengroup.org/onlinepubs/7990989799/xsh/sysstatvfs.h.html>
        s = os.statvfs(whichdir)

        # on my mac laptop:
        #  statvfs(2) is a wrapper around statfs(2).
        #    statvfs.f_frsize = statfs.f_bsize :
        #     "minimum unit of allocation" (statvfs)
        #     "fundamental file system block size" (statfs)
        #    statvfs.f_bsize = statfs.f_iosize = stat.st_blocks : preferred IO size
        # on an encrypted home directory ("FileVault"), it gets f_blocks
        # wrong, and s.f_blocks*s.f_frsize is twice the size of my disk,
        # but s.f_bavail*s.f_frsize is correct

        total = s.f_frsize * s.f_blocks
        free_for_root = s.f_frsize * s.f_bfree
        free_for_nonroot = s.f_frsize * s.f_bavail

    # valid for all platforms:
    used = total - free_for_root
    avail = max(free_for_nonroot - reserved_space, 0)

    return { 'total': total,
             'free_for_root': free_for_root,
             'free_for_nonroot': free_for_nonroot,
             'used': used,
             'avail': avail,
           }

def get_available_space(whichdir, reserved_space):
    """Returns available space for share storage in bytes, or None if no
    API to get this information is available.

    whichdir is a directory on the filesystem in question -- the
    answer is about the filesystem, not about the directory, so the
    directory is used only to specify which filesystem.

    reserved_space is how many bytes to subtract from the answer, so
    you can pass how many bytes you would like to leave unused on this
    filesystem as reserved_space.
    """
    try:
        return get_disk_stats(whichdir, reserved_space)['avail']
    except AttributeError:
        return None
    except EnvironmentError:
        log.msg("OS call to get disk statistics failed")
        return 0


if sys.platform == "win32":
    # <http://msdn.microsoft.com/en-us/library/aa363858%28v=vs.85%29.aspx>
    CreateFileW = WINFUNCTYPE(HANDLE, LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE) \
                      (("CreateFileW", windll.kernel32))

    GENERIC_WRITE        = 0x40000000
    FILE_SHARE_READ      = 0x00000001
    FILE_SHARE_WRITE     = 0x00000002
    OPEN_EXISTING        = 3
    INVALID_HANDLE_VALUE = 0xFFFFFFFF

    # <http://msdn.microsoft.com/en-us/library/aa364439%28v=vs.85%29.aspx>
    FlushFileBuffers = WINFUNCTYPE(BOOL, HANDLE)(("FlushFileBuffers", windll.kernel32))

    # <http://msdn.microsoft.com/en-us/library/ms724211%28v=vs.85%29.aspx>
    CloseHandle = WINFUNCTYPE(BOOL, HANDLE)(("CloseHandle", windll.kernel32))

    # <http://social.msdn.microsoft.com/forums/en-US/netfxbcl/thread/4465cafb-f4ed-434f-89d8-c85ced6ffaa8/>
    def flush_volume(path):
        drive = os.path.splitdrive(os.path.realpath(path))[0]

        hVolume = CreateFileW(u"\\\\.\\" + drive,
                              GENERIC_WRITE,
                              FILE_SHARE_READ | FILE_SHARE_WRITE,
                              None,
                              OPEN_EXISTING,
                              0,
                              None
                             )
        if hVolume == INVALID_HANDLE_VALUE:
            raise WinError()

        if FlushFileBuffers(hVolume) == 0:
            raise WinError()

        CloseHandle(hVolume)
else:
    def flush_volume(path):
        # use sync()?
        pass


class ConflictError(Exception):
    pass

class UnableToUnlinkReplacementError(Exception):
    pass

def reraise(wrapper):
    _, exc, tb = sys.exc_info()
    wrapper_exc = wrapper("%s: %s" % (exc.__class__.__name__, exc))
    raise wrapper_exc.__class__, wrapper_exc, tb

if sys.platform == "win32":
    from ctypes import WINFUNCTYPE, windll, WinError, get_last_error
    from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPVOID

    # <https://msdn.microsoft.com/en-us/library/windows/desktop/aa365512%28v=vs.85%29.aspx>
    ReplaceFileW = WINFUNCTYPE(
        BOOL,
          LPCWSTR, LPCWSTR, LPCWSTR, DWORD, LPVOID, LPVOID,
        use_last_error=True
      )(("ReplaceFileW", windll.kernel32))

    REPLACEFILE_IGNORE_MERGE_ERRORS = 0x00000002

    def rename_no_overwrite(source_path, dest_path):
        os.rename(source_path, dest_path)

    def replace_file(replaced_path, replacement_path, backup_path):
        precondition_abspath(replaced_path)
        precondition_abspath(replacement_path)
        precondition_abspath(backup_path)

        r = ReplaceFileW(replaced_path, replacement_path, backup_path,
                         REPLACEFILE_IGNORE_MERGE_ERRORS, None, None)
        if r == 0:
            # The UnableToUnlinkReplacementError case does not happen on Windows;
            # all errors should be treated as signalling a conflict.
            err = get_last_error()
            raise ConflictError("WinError: %s" % (WinError(err)))
else:
    def rename_no_overwrite(source_path, dest_path):
        # link will fail with EEXIST if there is already something at dest_path.
        os.link(source_path, dest_path)
        try:
            os.unlink(source_path)
        except EnvironmentError:
            reraise(UnableToUnlinkReplacementError)

    def replace_file(replaced_path, replacement_path, backup_path):
        precondition_abspath(replaced_path)
        precondition_abspath(replacement_path)
        precondition_abspath(backup_path)

        if not os.path.exists(replacement_path):
            raise ConflictError("Replacement file not found: %r" % (replacement_path,))

        try:
            os.rename(replaced_path, backup_path)
        except OSError as e:
            if e.errno != ENOENT:
                raise
        try:
            rename_no_overwrite(replacement_path, replaced_path)
        except EnvironmentError:
            reraise(ConflictError)

PathInfo = namedtuple('PathInfo', 'isdir isfile islink exists size mtime ctime')

def get_pathinfo(path_u, now=None):
    try:
        statinfo = os.lstat(path_u)
        mode = statinfo.st_mode
        return PathInfo(isdir =stat.S_ISDIR(mode),
                        isfile=stat.S_ISREG(mode),
                        islink=stat.S_ISLNK(mode),
                        exists=True,
                        size  =statinfo.st_size,
                        mtime =statinfo.st_mtime,
                        ctime =statinfo.st_ctime,
                       )
    except OSError as e:
        if e.errno == ENOENT:
            if now is None:
                now = time.time()
            return PathInfo(isdir =False,
                            isfile=False,
                            islink=False,
                            exists=False,
                            size  =None,
                            mtime =now,
                            ctime =now,
                           )
        raise
