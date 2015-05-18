
# Windows near-equivalent to twisted.internet.inotify
# This should only be imported on Windows.

import os, sys

from twisted.internet import defer, reactor
from twisted.internet.threads import deferToThread

from allmydata.util.fake_inotify import humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED
[humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED]

from allmydata.util.assertutil import _assert, precondition
from allmydata.util.deferredutil import eventually_callback, eventually_errback
from allmydata.util.encodingutil import quote_local_unicode_path
from allmydata.util import log, fileutil
from allmydata.util.pollmixin import PollMixin

from ctypes import WINFUNCTYPE, WinError, windll, POINTER, byref, \
    create_string_buffer, addressof, Structure, get_last_error
from ctypes.wintypes import BOOL, HANDLE, DWORD, LPCWSTR, LPVOID

# <https://msdn.microsoft.com/en-us/library/windows/desktop/gg258116%28v=vs.85%29.aspx>
FILE_LIST_DIRECTORY              = 1

# <https://msdn.microsoft.com/en-us/library/windows/desktop/aa363858%28v=vs.85%29.aspx>
CreateFileW = WINFUNCTYPE(
    HANDLE,
      LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE,
    use_last_error=True
  )(("CreateFileW", windll.kernel32))

FILE_SHARE_READ                  = 0x00000001
FILE_SHARE_WRITE                 = 0x00000002
FILE_SHARE_DELETE                = 0x00000004

OPEN_EXISTING                    = 3

FILE_FLAG_BACKUP_SEMANTICS       = 0x02000000
FILE_FLAG_OVERLAPPED             = 0x40000000

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms724211%28v=vs.85%29.aspx>
CloseHandle = WINFUNCTYPE(
    BOOL,
      HANDLE,
    use_last_error=True
  )(("CloseHandle", windll.kernel32))

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms684342%28v=vs.85%29.aspx>
class OVERLAPPED(Structure):
    _fields_ = [('Internal', LPVOID),
                ('InternalHigh', LPVOID),
                ('Offset', DWORD),
                ('OffsetHigh', DWORD),
                ('Pointer', LPVOID),
                ('hEvent', HANDLE),
               ]

# <https://msdn.microsoft.com/en-us/library/windows/desktop/aa365465%28v=vs.85%29.aspx>
ReadDirectoryChangesW = WINFUNCTYPE(
    BOOL,
      HANDLE, LPVOID, DWORD, BOOL, DWORD, POINTER(DWORD), POINTER(OVERLAPPED), LPVOID,
    use_last_error=True
  )(("ReadDirectoryChangesW", windll.kernel32))

FILE_NOTIFY_CHANGE_FILE_NAME     = 0x00000001
FILE_NOTIFY_CHANGE_DIR_NAME      = 0x00000002
FILE_NOTIFY_CHANGE_ATTRIBUTES    = 0x00000004
#FILE_NOTIFY_CHANGE_SIZE         = 0x00000008
FILE_NOTIFY_CHANGE_LAST_WRITE    = 0x00000010
FILE_NOTIFY_CHANGE_LAST_ACCESS   = 0x00000020
#FILE_NOTIFY_CHANGE_CREATION     = 0x00000040
FILE_NOTIFY_CHANGE_SECURITY      = 0x00000100

# <https://msdn.microsoft.com/en-us/library/windows/desktop/aa364391%28v=vs.85%29.aspx>
FILE_ACTION_ADDED                = 0x00000001
FILE_ACTION_REMOVED              = 0x00000002
FILE_ACTION_MODIFIED             = 0x00000003
FILE_ACTION_RENAMED_OLD_NAME     = 0x00000004
FILE_ACTION_RENAMED_NEW_NAME     = 0x00000005

_action_to_string = {
    FILE_ACTION_ADDED            : "FILE_ACTION_ADDED",
    FILE_ACTION_REMOVED          : "FILE_ACTION_REMOVED",
    FILE_ACTION_MODIFIED         : "FILE_ACTION_MODIFIED",
    FILE_ACTION_RENAMED_OLD_NAME : "FILE_ACTION_RENAMED_OLD_NAME",
    FILE_ACTION_RENAMED_NEW_NAME : "FILE_ACTION_RENAMED_NEW_NAME",
}

_action_to_inotify_mask = {
    FILE_ACTION_ADDED            : IN_CREATE,
    FILE_ACTION_REMOVED          : IN_DELETE,
    FILE_ACTION_MODIFIED         : IN_CHANGED,
    FILE_ACTION_RENAMED_OLD_NAME : IN_MOVED_FROM,
    FILE_ACTION_RENAMED_NEW_NAME : IN_MOVED_TO,
}

INVALID_HANDLE_VALUE             = 0xFFFFFFFF

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms682396%28v=vs.85%29.aspx>
CreateEventW = WINFUNCTYPE(
    HANDLE,
      LPVOID, BOOL, BOOL, LPCWSTR,
    use_last_error=True
  )(("CreateEventW", windll.kernel32))

# <https://msdn.microsoft.com/en-us/library/windows/desktop/aa363792%28v=vs.85%29.aspx>
CancelIoEx = WINFUNCTYPE(
    BOOL,
      HANDLE, POINTER(OVERLAPPED),
    use_last_error=True
  )(("CancelIoEx", windll.kernel32))

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms683209%28v=vs.85%29.aspx>
GetOverlappedResult = WINFUNCTYPE(
    BOOL,
      HANDLE, POINTER(OVERLAPPED), POINTER(DWORD), BOOL,
    use_last_error=True
  )(("GetOverlappedResult", windll.kernel32))

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms681388%28v=vs.85%29.aspx>
ERROR_OPERATION_ABORTED = 995

# Use these rather than False and True for Windows BOOL.
FALSE = 0
TRUE  = 1


class StoppedException(Exception):
    """The notifier has been stopped."""
    pass


class Notification(object):
    """
    * action:   a FILE_ACTION_* constant (not a bit mask)
    * filename: a Unicode string, giving the name relative to the watched directory
    """
    def __init__(self, action, filename):
        self.action = action
        self.filename = filename

    def __repr__(self):
        return "Notification(%r, %r)" % (_action_to_string.get(self.action, self.action), self.filename)


# WARNING: ROCKET SCIENCE!
# ReadDirectoryChangesW is one of the most difficult APIs in Windows.
# The documentation is incomplete and misleading, and many of the possible
# ways of using it do not work in practice. In particular, robustly
# cancelling a call in order to stop monitoring the directory is
# ridiculously hard.
#
# Attempting to use it via ctypes is therefore pure foolishness :-p
# Do not change this without first reading, carefully, both parts of
# <http://qualapps.blogspot.co.uk/2010/05/understanding-readdirectorychangesw.html>.
# Then ask Daira to review your changes.

class FileNotifier(object):
    """
    I represent a buffer containing FILE_NOTIFY_INFORMATION structures,
    associated with a particular directory handle. I can iterate over those
    structures, decoding them into Notification objects.
    """

    def __init__(self, path_u, filter, recursive=False, size=1024):
        self._hDirectory = self._open_directory(path_u)
        self._filter = filter
        self._recursive = recursive
        self._size = size
        self._buffer = create_string_buffer(size)
        address = addressof(self._buffer)
        _assert(address & 3 == 0, "address 0x%X returned by create_string_buffer is not DWORD-aligned"
                                  % (address,))

        self._hNotified = self._create_event()
        self._overlapped = OVERLAPPED()
        self._overlapped.Internal = None
        self._overlapped.InternalHigh = None
        self._overlapped.Offset = 0
        self._overlapped.OffsetHigh = 0
        self._overlapped.Pointer = None
        self._overlapped.hEvent = self._hNotified

        self._interrupted = False

    @staticmethod
    def _create_event():
        # no security descriptor, manual reset, unsignalled, anonymous
        hEvent = CreateEventW(None, FALSE, FALSE, None)
        if hEvent is None:
            raise WinError(get_last_error())
        return hEvent

    @staticmethod
    def _open_directory(path_u):
        hDirectory = CreateFileW(path_u,
								 FILE_LIST_DIRECTORY,         # access rights
								 FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
															  # don't prevent other processes from accessing
								 None,                        # no security descriptor
								 OPEN_EXISTING,               # directory must already exist
								 FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED,
															  # necessary to open a directory with overlapped I/O
								 None                         # no template file
								)
        if hDirectory == INVALID_HANDLE_VALUE:
            e = WinError(get_last_error())
            raise OSError("Opening directory %s gave Windows error %r: %s" %
                          (quote_local_unicode_path(path_u), e.args[0], e.args[1]))
        return hDirectory

    def __del__(self):
        if hasattr(self, '_hDirectory'):
            CloseHandle(self._hDirectory)
        if hasattr(self, '_hNotified'):
            CloseHandle(self._hNotified)

    def interrupt(self):
        # This must be repeated until the thread that calls get_notifications()
        # is confirmed to be stopped.
        self._interrupted = True
        CancelIoEx(self._hDirectory, None)

    def read_notifications(self):
        """This does not block."""
        if self._interrupted:
            raise StoppedException()

        bytes_returned = DWORD(0)
        print "here"
        r = ReadDirectoryChangesW(self._hDirectory,
                                  byref(self._buffer),
                                  self._size,
                                  TRUE if self._recursive else FALSE,
                                  self._filter,
                                  byref(bytes_returned),
                                  self._overlapped,
                                  None
                                 )
        print "there"
        if r == 0:
            raise WinError(get_last_error())

    def get_notifications(self):
        """This blocks and then iterates over the notifications."""
        if self._interrupted:
            raise StoppedException()

        print "hereq1"
        bytes_read = DWORD()
        r = GetOverlappedResult(self._hDirectory,
                                self._overlapped,
                                byref(bytes_read),
                                TRUE)
        if r == 0:
            err = get_last_error()
            if err == ERROR_OPERATION_ABORTED:
                raise StoppedException()
            raise WinError(err)
        print "hereq2"
        
        data = self._buffer.raw[:bytes_returned.value]
        print data

        pos = 0
        while True:
            bytes = _read_dword(data, pos+8)
            try:
                path_u = data[pos+12 : pos+12+bytes].decode('utf-16-le')
            except UnicodeDecodeError as e:
                log.err(e)
            else:
                s = Notification(_read_dword(data, pos+4), path_u)
                print s
                yield s

            next_entry_offset = _read_dword(data, pos)
            if next_entry_offset == 0:
                break
            pos = pos + next_entry_offset

    @staticmethod
    def _read_dword(data, i):
        # little-endian
        return ( ord(data[i])          |
                (ord(data[i+1]) <<  8) |
                (ord(data[i+2]) << 16) |
                (ord(data[i+3]) << 24))


def simple_test():
    path_u = u"test"
    filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE
    recursive = False

    notifier = FileNotifier(path_u, filter, recursive)
    while True:
        notifier.read_notifications()
        print "Waiting..."
        for info in notifier.get_notifications():
            print info


class INotify(PollMixin):
    def __init__(self):
        self._called_startReading = False
        self._called_stopReading = False
        self._started_d = defer.Deferred()
        self._stopped = False

        self._callbacks = None
        self._notifier = None
        self._path = None
        self._pending = set()
        self._pending_delay = 1.0

    def set_pending_delay(self, delay):
        self._pending_delay = delay

    def startReading(self):
        # Twisted's version of this is synchronous.
        precondition(not self._called_startReading, "startReading() called more than once")
        self._called_startReading = True
        deferToThread(self._thread)
        return self._started_d

    def stopReading(self):
        # Twisted's version of this is synchronous.
        precondition(self._called_startReading, "stopReading() called before startReading()")
        precondition(not self._called_stopReading, "stopReading() called more than once")
        self._called_stopReading = True

        # This is tricky. We don't know where the notifier thread is in its loop,
        # so it could block in get_notifications *after* any pending I/O has been
        # cancelled. Therefore, we need to continue interrupting until we see
        # that the thread has actually stopped.
        def _try_to_stop():
            if self._stopped:
                return True
            self._notifier.interrupt()
            return False
        return self.poll(_try_to_stop)

    def watch(self, path, mask=IN_WATCH_MASK, autoAdd=False, callbacks=None, recursive=False):
        precondition(not self._started_reading, "watch() can only be called before startReading()")
        precondition(self._notifier is None, "only one watch is supported")
        precondition(isinstance(autoAdd, bool), autoAdd=autoAdd)
        precondition(isinstance(recursive, bool), recursive=recursive)
        precondition(not autoAdd, "autoAdd not supported")

        self._path = path
        path_u = path.path
        if not isinstance(path_u, unicode):
            path_u = path_u.decode(sys.getfilesystemencoding())
            _assert(isinstance(path_u, unicode), path_u=path_u)

        filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE

        if mask & (IN_ACCESS | IN_CLOSE_NOWRITE | IN_OPEN):
            filter |= FILE_NOTIFY_CHANGE_LAST_ACCESS
        if mask & IN_ATTRIB:
            filter |= FILE_NOTIFY_CHANGE_ATTRIBUTES | FILE_NOTIFY_CHANGE_SECURITY

        self._callbacks = callbacks or []
        self._notifier = FileNotifier(path_u, filter, recursive)

    def _thread(self):
        started = False
        try:
            _assert(self._notifier is not None, "no watch set")

            # To call Twisted or Tahoe APIs, use reactor.callFromThread as described in
            # <http://twistedmatrix.com/documents/current/core/howto/threading.html>.

            while True:
                # We must set _started to True *after* calling read_notifications, so that
                # the caller of startReading() can tell when we've actually started reading.

                self._notifier.read_notifications()
                if not started:
                    reactor.callFromThread(self._started_d.callback, None)
                    started = True

                for info in self._notifier.get_notifications():
                    print info

                    path = self._path.preauthChild(info.filename)  # FilePath with Unicode path
                    #mask = _action_to_inotify_mask.get(info.action, IN_CHANGED)

                    def _maybe_notify(path):
                        if path not in self._pending:
                            self._pending.add(path)
                            def _do_callbacks():
                                self._pending.remove(path)
                                for cb in self._callbacks:
                                    try:
                                        cb(None, path, IN_CHANGED)
                                    except Exception as e:
                                        log.err(e)
                            reactor.callLater(self._pending_delay, _do_callbacks)
                    reactor.callFromThread(_maybe_notify, path)
        except Exception as e:
            if not isinstance(e, StoppedException):
                log.err(e)
            if not started:
                # startReading() should fail in this case.
                reactor.callFromThread(self._started_d.errback, Failure())
        finally:
            self._callbacks = []
            self._stopped = True
