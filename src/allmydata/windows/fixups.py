
done = False

def initialize():
    global done
    import sys
    if sys.platform != "win32" or done:
        return True
    done = True

    original_stderr = sys.stderr
    import codecs, re
    from ctypes import WINFUNCTYPE, windll, POINTER, byref, c_int
    from ctypes.wintypes import BOOL, HANDLE, DWORD, LPWSTR, LPCWSTR, LPVOID
    from allmydata.util import log
    from allmydata.util.encodingutil import canonical_encoding

    # Work around <http://bugs.python.org/issue6058>.
    codecs.register(lambda name: name == 'cp65001' and codecs.lookup('utf-8') or None)

    # Make Unicode console output work independently of the current code page.
    # This also fixes <http://bugs.python.org/issue1602>.
    # Credit to Michael Kaplan <http://blogs.msdn.com/b/michkap/archive/2010/04/07/9989346.aspx>
    # and TZOmegaTZIOY
    # <http://stackoverflow.com/questions/878972/windows-cmd-encoding-change-causes-python-crash/1432462#1432462>.
    try:
        # <http://msdn.microsoft.com/en-us/library/ms683231(VS.85).aspx>
        # HANDLE WINAPI GetStdHandle(DWORD nStdHandle);
        # returns INVALID_HANDLE_VALUE, NULL, or a valid handle
        #
        # <http://msdn.microsoft.com/en-us/library/aa364960(VS.85).aspx>
        # DWORD WINAPI GetFileType(DWORD hFile);
        #
        # <http://msdn.microsoft.com/en-us/library/ms683167(VS.85).aspx>
        # BOOL WINAPI GetConsoleMode(HANDLE hConsole, LPDWORD lpMode);

        GetStdHandle = WINFUNCTYPE(HANDLE, DWORD)(("GetStdHandle", windll.kernel32))
        STD_OUTPUT_HANDLE = DWORD(-11)
        STD_ERROR_HANDLE  = DWORD(-12)
        GetFileType = WINFUNCTYPE(DWORD, DWORD)(("GetFileType", windll.kernel32))
        FILE_TYPE_CHAR   = 0x0002
        FILE_TYPE_REMOTE = 0x8000
        GetConsoleMode = WINFUNCTYPE(BOOL, HANDLE, POINTER(DWORD))(("GetConsoleMode", windll.kernel32))
        INVALID_HANDLE_VALUE = DWORD(-1).value

        def not_a_console(handle):
            if handle == INVALID_HANDLE_VALUE or handle is None:
                return True
            return ((GetFileType(handle) & ~FILE_TYPE_REMOTE) != FILE_TYPE_CHAR
                    or GetConsoleMode(handle, byref(DWORD())) == 0)

        old_stdout_fileno = None
        old_stderr_fileno = None
        if hasattr(sys.stdout, 'fileno'):
            old_stdout_fileno = sys.stdout.fileno()
        if hasattr(sys.stderr, 'fileno'):
            old_stderr_fileno = sys.stderr.fileno()

        STDOUT_FILENO = 1
        STDERR_FILENO = 2
        real_stdout = (old_stdout_fileno == STDOUT_FILENO)
        real_stderr = (old_stderr_fileno == STDERR_FILENO)

        if real_stdout:
            hStdout = GetStdHandle(STD_OUTPUT_HANDLE)
            if not_a_console(hStdout):
                real_stdout = False

        if real_stderr:
            hStderr = GetStdHandle(STD_ERROR_HANDLE)
            if not_a_console(hStderr):
                real_stderr = False

        if real_stdout or real_stderr:
            # BOOL WINAPI WriteConsoleW(HANDLE hOutput, LPWSTR lpBuffer, DWORD nChars,
            #                           LPDWORD lpCharsWritten, LPVOID lpReserved);

            WriteConsoleW = WINFUNCTYPE(BOOL, HANDLE, LPWSTR, DWORD, POINTER(DWORD), LPVOID) \
                                (("WriteConsoleW", windll.kernel32))

            # If any exception occurs in this code, we'll probably try to print it on stderr,
            # which makes for frustrating debugging if stderr is directed to this code.
            # So be paranoid about catching errors and reporting them to original_stderr,
            # so that we can at least see them.

            class UnicodeOutput:
                def __init__(self, hConsole, stream, fileno, name):
                    self._hConsole = hConsole
                    self._stream = stream
                    self._fileno = fileno
                    self.closed = False
                    self.softspace = False
                    self.mode = 'w'
                    self.encoding = 'utf-8'
                    self.name = name
                    if hasattr(stream, 'encoding') and canonical_encoding(stream.encoding) != 'utf-8':
                        log.msg("%s (%r) had encoding %r, but we're going to write UTF-8 to it" %
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
                        except Exception, e:
                            print >>original_stderr, repr(e)
                            raise

                def write(self, text):
                    try:
                        if self._hConsole is None:
                            if isinstance(text, unicode):
                                text = text.encode('utf-8')
                            self._stream.write(text)
                        else:
                            if not isinstance(text, unicode):
                                text = str(text).decode('utf-8')
                            remaining = len(text)
                            while remaining > 0:
                                n = DWORD(0)
                                retval = WriteConsoleW(self._hConsole, text, remaining, byref(n), None)
                                if retval == 0 or n.value == 0:
                                    raise IOError("could not write to %s [WriteConsoleW returned %r, n.value = %r]"
                                                  % (self.name, retval, n.value))
                                remaining -= n.value
                                if remaining == 0: break
                                text = text[n.value:]
                    except Exception, e:
                        print >>original_stderr, repr(e)
                        raise

                def writelines(self, lines):
                    try:
                        for line in lines:
                            self.write(line)
                    except Exception, e:
                        print >>original_stderr, repr(e)
                        raise

            if real_stdout:
                sys.stdout = UnicodeOutput(hStdout, None, STDOUT_FILENO, '<Unicode console stdout>')
            else:
                sys.stdout = UnicodeOutput(None, sys.stdout, old_stdout_fileno, '<Unicode redirected stdout>')

            if real_stderr:
                sys.stderr = UnicodeOutput(hStderr, None, STDERR_FILENO, '<Unicode console stderr>')
            else:
                sys.stderr = UnicodeOutput(None, sys.stderr, old_stderr_fileno, '<Unicode redirected stdout>')
    except Exception, e:
        print >>original_stderr, "exception %r while fixing up sys.stdout and sys.stderr" % (e,)
        log.msg("exception %r while fixing up sys.stdout and sys.stderr" % (e,), log.WEIRD)

    # Unmangle command-line arguments.
    GetCommandLineW = WINFUNCTYPE(LPWSTR)(("GetCommandLineW", windll.kernel32))
    CommandLineToArgvW = WINFUNCTYPE(POINTER(LPWSTR), LPCWSTR, POINTER(c_int)) \
                            (("CommandLineToArgvW", windll.shell32))

    argc = c_int(0)
    argv_unicode = CommandLineToArgvW(GetCommandLineW(), byref(argc))

    def unmangle(s):
        return re.sub(ur'\x7f[0-9a-fA-F]*\;', lambda m: unichr(int(m.group(0)[1:-1], 16)), s)

    try:
        sys.argv = [unmangle(argv_unicode[i]).encode('utf-8') for i in xrange(1, argc.value)]
    except Exception, e:
        print >>sys.stderr, "%s:  could not unmangle Unicode arguments" % (sys.argv[0],)
        print >>sys.stderr, [argv_unicode[i] for i in xrange(1, argc.value)]
        raise

    if sys.argv[0].endswith('.pyscript'):
        sys.argv[0] = sys.argv[0][:-9]
