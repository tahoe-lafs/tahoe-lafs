from __future__ import print_function

from future.utils import PY3
from past.builtins import unicode

# This code isn't loadable or sensible except on Windows.  Importers all know
# this and are careful.  Normally I would just let an import error from ctypes
# explain any mistakes but Mypy also needs some help here.  This assert
# explains to it that this module is Windows-only.  This prevents errors about
# ctypes.windll and such which only exist when running on Windows.
#
# Beware of the limitations of the Mypy AST analyzer.  The check needs to take
# exactly this form or it may not be recognized.
#
# https://mypy.readthedocs.io/en/stable/common_issues.html?highlight=platform#python-version-and-system-platform-checks
import sys
assert sys.platform == "win32"

import codecs
from functools import partial

from ctypes import WINFUNCTYPE, windll, POINTER, c_int, WinError, byref, get_last_error
from ctypes.wintypes import BOOL, HANDLE, DWORD, LPWSTR, LPCWSTR, LPVOID

# <https://msdn.microsoft.com/en-us/library/ms680621%28VS.85%29.aspx>
from win32api import (
    STD_OUTPUT_HANDLE,
    STD_ERROR_HANDLE,
    SetErrorMode,

    # <https://msdn.microsoft.com/en-us/library/ms683231(VS.85).aspx>
    # HANDLE WINAPI GetStdHandle(DWORD nStdHandle);
    # returns INVALID_HANDLE_VALUE, NULL, or a valid handle
    GetStdHandle,
)
from win32con import (
    SEM_FAILCRITICALERRORS,
    SEM_NOOPENFILEERRORBOX,
)

from win32file import (
    INVALID_HANDLE_VALUE,
    FILE_TYPE_CHAR,

    # <https://msdn.microsoft.com/en-us/library/aa364960(VS.85).aspx>
    # DWORD WINAPI GetFileType(DWORD hFile);
    GetFileType,
)

from allmydata.util import (
    log,
)

# Keep track of whether `initialize` has run so we don't do any of the
# initialization more than once.
_done = False

#
# pywin32 for Python 2.7 does not bind any of these *W variants so we do it
# ourselves.
#

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms687401%28v=vs.85%29.aspx>
# BOOL WINAPI WriteConsoleW(HANDLE hOutput, LPWSTR lpBuffer, DWORD nChars,
#                           LPDWORD lpCharsWritten, LPVOID lpReserved);
WriteConsoleW = WINFUNCTYPE(
    BOOL,  HANDLE, LPWSTR, DWORD, POINTER(DWORD), LPVOID,
    use_last_error=True
)(("WriteConsoleW", windll.kernel32))

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms683156%28v=vs.85%29.aspx>
GetCommandLineW = WINFUNCTYPE(
    LPWSTR,
    use_last_error=True
)(("GetCommandLineW", windll.kernel32))

# <https://msdn.microsoft.com/en-us/library/windows/desktop/bb776391%28v=vs.85%29.aspx>
CommandLineToArgvW = WINFUNCTYPE(
    POINTER(LPWSTR),  LPCWSTR, POINTER(c_int),
    use_last_error=True
)(("CommandLineToArgvW", windll.shell32))

# <https://msdn.microsoft.com/en-us/library/ms683167(VS.85).aspx>
# BOOL WINAPI GetConsoleMode(HANDLE hConsole, LPDWORD lpMode);
GetConsoleMode = WINFUNCTYPE(
    BOOL,  HANDLE, POINTER(DWORD),
    use_last_error=True
)(("GetConsoleMode", windll.kernel32))


STDOUT_FILENO = 1
STDERR_FILENO = 2

def get_argv():
    """
    :return [unicode]: The argument list this process was invoked with, as
        unicode.

        Python 2 does not do a good job exposing this information in
        ``sys.argv`` on Windows so this code re-retrieves the underlying
        information using Windows API calls and massages it into the right
        shape.
    """
    command_line = GetCommandLineW()
    argc = c_int(0)
    argv_unicode = CommandLineToArgvW(command_line, byref(argc))
    if argv_unicode is None:
        raise WinError(get_last_error())

    # Convert it to a normal Python list
    return list(
        argv_unicode[i]
        for i
        in range(argc.value)
    )


def initialize():
    global _done
    import sys
    if sys.platform != "win32" or _done:
        return True
    _done = True

    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX)

    if PY3:
        # The rest of this appears to be Python 2-specific
        return

    original_stderr = sys.stderr

    # If any exception occurs in this code, we'll probably try to print it on stderr,
    # which makes for frustrating debugging if stderr is directed to our wrapper.
    # So be paranoid about catching errors and reporting them to original_stderr,
    # so that we can at least see them.
    def _complain(output_file, message):
        print(isinstance(message, str) and message or repr(message), file=output_file)
        log.msg(message, level=log.WEIRD)

    _complain = partial(_complain, original_stderr)

    # Work around <http://bugs.python.org/issue6058>.
    codecs.register(lambda name: name == 'cp65001' and codecs.lookup('utf-8') or None)

    # Make Unicode console output work independently of the current code page.
    # This also fixes <http://bugs.python.org/issue1602>.
    # Credit to Michael Kaplan <https://blogs.msdn.com/b/michkap/archive/2010/04/07/9989346.aspx>
    # and TZOmegaTZIOY
    # <http://stackoverflow.com/questions/878972/windows-cmd-encoding-change-causes-python-crash/1432462#1432462>.
    try:
        old_stdout_fileno = None
        old_stderr_fileno = None
        if hasattr(sys.stdout, 'fileno'):
            old_stdout_fileno = sys.stdout.fileno()
        if hasattr(sys.stderr, 'fileno'):
            old_stderr_fileno = sys.stderr.fileno()

        real_stdout = (old_stdout_fileno == STDOUT_FILENO)
        real_stderr = (old_stderr_fileno == STDERR_FILENO)

        if real_stdout:
            hStdout = GetStdHandle(STD_OUTPUT_HANDLE)
            if not a_console(hStdout):
                real_stdout = False

        if real_stderr:
            hStderr = GetStdHandle(STD_ERROR_HANDLE)
            if not a_console(hStderr):
                real_stderr = False

        if real_stdout:
            sys.stdout = UnicodeOutput(hStdout, None, STDOUT_FILENO, '<Unicode console stdout>', _complain)
        else:
            sys.stdout = UnicodeOutput(None, sys.stdout, old_stdout_fileno, '<Unicode redirected stdout>', _complain)

        if real_stderr:
            sys.stderr = UnicodeOutput(hStderr, None, STDERR_FILENO, '<Unicode console stderr>', _complain)
        else:
            sys.stderr = UnicodeOutput(None, sys.stderr, old_stderr_fileno, '<Unicode redirected stderr>', _complain)
    except Exception as e:
        _complain("exception %r while fixing up sys.stdout and sys.stderr" % (e,))

    argv = list(arg.encode("utf-8") for arg in get_argv())

    # Take only the suffix with the same number of arguments as sys.argv.
    # This accounts for anything that can cause initial arguments to be stripped,
    # for example, the Python interpreter or any options passed to it, or runner
    # scripts such as 'coverage run'. It works even if there are no such arguments,
    # as in the case of a frozen executable created by bb-freeze or similar.
    #
    # Also, modify sys.argv in place.  If any code has already taken a
    # reference to the original argument list object then this ensures that
    # code sees the new values.  This reliance on mutation of shared state is,
    # of course, awful.  Why does this function even modify sys.argv?  Why not
    # have a function that *returns* the properly initialized argv as a new
    # list?  I don't know.
    #
    # At least Python 3 gets sys.argv correct so before very much longer we
    # should be able to fix this bad design by deleting it.
    sys.argv[:] = argv[-len(sys.argv):]


def a_console(handle):
    """
    :return: ``True`` if ``handle`` refers to a console, ``False`` otherwise.
    """
    if handle == INVALID_HANDLE_VALUE:
        return False
    return (
        # It's a character file (eg a printer or a console)
        GetFileType(handle) == FILE_TYPE_CHAR and
        # Checking the console mode doesn't fail (thus it's a console)
        GetConsoleMode(handle, byref(DWORD())) != 0
    )


class UnicodeOutput(object):
    """
    ``UnicodeOutput`` is a file-like object that encodes unicode to UTF-8 and
    writes it to another file or writes unicode natively to the Windows
    console.
    """
    def __init__(self, hConsole, stream, fileno, name, _complain):
        """
        :param hConsole: ``None`` or a handle on the console to which to write
            unicode.  Mutually exclusive with ``stream``.

        :param stream: ``None`` or a file-like object to which to write bytes.

        :param fileno: A result to hand back from method of the same name.

        :param name: A human-friendly identifier for this output object.

        :param _complain: A one-argument callable which accepts bytes to be
            written when there's a problem.  Care should be taken to not make
            this do a write on this object.
        """
        self._hConsole = hConsole
        self._stream = stream
        self._fileno = fileno
        self.closed = False
        self.softspace = False
        self.mode = 'w'
        self.encoding = 'utf-8'
        self.name = name

        self._complain = _complain

        from allmydata.util.encodingutil import canonical_encoding
        from allmydata.util import log
        if hasattr(stream, 'encoding') and canonical_encoding(stream.encoding) != 'utf-8':
            log.msg("%s: %r had encoding %r, but we're going to write UTF-8 to it" %
                    (name, stream, stream.encoding), level=log.CURIOUS)
        self.flush()

    def isatty(self):
        return False
    def close(self):
        # don't really close the handle, that would only cause problems
        self.closed = True
    def fileno(self):
        return self._fileno
    def flush(self):
        if self._hConsole is None:
            try:
                self._stream.flush()
            except Exception as e:
                self._complain("%s.flush: %r from %r" % (self.name, e, self._stream))
                raise

    def write(self, text):
        try:
            if self._hConsole is None:
                # There is no Windows console available.  That means we are
                # responsible for encoding the unicode to a byte string to
                # write it to a Python file object.
                if isinstance(text, unicode):
                    text = text.encode('utf-8')
                self._stream.write(text)
            else:
                # There is a Windows console available.  That means Windows is
                # responsible for dealing with the unicode itself.
                if not isinstance(text, unicode):
                    text = str(text).decode('utf-8')
                remaining = len(text)
                while remaining > 0:
                    n = DWORD(0)
                    # There is a shorter-than-documented limitation on the
                    # length of the string passed to WriteConsoleW (see
                    # #1232).
                    retval = WriteConsoleW(self._hConsole, text, min(remaining, 10000), byref(n), None)
                    if retval == 0:
                        raise IOError("WriteConsoleW failed with WinError: %s" % (WinError(get_last_error()),))
                    if n.value == 0:
                        raise IOError("WriteConsoleW returned %r, n.value = 0" % (retval,))
                    remaining -= n.value
                    if remaining == 0: break
                    text = text[n.value:]
        except Exception as e:
            self._complain("%s.write: %r" % (self.name, e))
            raise

    def writelines(self, lines):
        try:
            for line in lines:
                self.write(line)
        except Exception as e:
            self._complain("%s.writelines: %r" % (self.name, e))
            raise
