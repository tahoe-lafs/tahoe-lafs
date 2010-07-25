
done = False

def initialize():
    global done
    import sys
    if sys.platform != "win32" or done:
        return True
    done = True

    import codecs, re
    from ctypes import WINFUNCTYPE, windll, CFUNCTYPE, cdll, POINTER, byref, \
        c_wchar_p, c_char_p, c_void_p, c_int, c_size_t
    from allmydata.util import log
    from allmydata.util.encodingutil import canonical_encoding

    # Work around <http://bugs.python.org/issue6058>.
    codecs.register(lambda name: name == 'cp65001' and codecs.lookup('utf-8') or None)

    # Make Unicode console output work independently of the current code page.
    # This also fixes <http://bugs.python.org/issue1602>.
    # Credit to Michael Kaplan <http://blogs.msdn.com/b/michkap/archive/2008/03/18/8306597.aspx>
    # and TZOmegaTZIOY
    # <http://stackoverflow.com/questions/878972/windows-cmd-encoding-change-causes-python-crash/1432462#1432462>.
    try:
        STDOUT_FILENO = 1
        STDERR_FILENO = 2
        real_stdout = hasattr(sys.stdout, 'fileno') and sys.stdout.fileno() == STDOUT_FILENO
        real_stderr = hasattr(sys.stderr, 'fileno') and sys.stderr.fileno() == STDERR_FILENO

        def force_utf8(stream, name):
            if hasattr(stream, 'encoding') and canonical_encoding(stream.encoding) != 'utf-8':
                log.msg("%s (%r) had encoding %r, but we're going to write UTF-8 to it" %
                        (name, stream, stream.encoding), level=log.CURIOUS)
            stream.encoding = 'utf-8'

        if not real_stdout:
            force_utf8(sys.stdout, "sys.stdout")

        if not real_stderr:
            force_utf8(sys.stderr, "sys.stderr")

        if real_stdout or real_stderr:
            # FILE * _fdopen(int fd, const char *mode);
            # #define _IOLBF 0x0040
            # int setvbuf(FILE *stream, char *buffer, int mode, size_t size);
            # #define _O_U8TEXT 0x40000
            # int _setmode(int fd, int mode);
            # int fputws(const wchar_t *ws, FILE *stream);
            # int fflush(FILE *stream);

            c_runtime = cdll.msvcrt
            NULL = None
            _fdopen = CFUNCTYPE(c_void_p, c_int, c_char_p)(("_fdopen", c_runtime))
            _IOLBF = 0x0040
            setvbuf = CFUNCTYPE(c_int, c_void_p, c_char_p, c_int, c_size_t)(("setvbuf", c_runtime))
            _O_U8TEXT = 0x40000
            _setmode = CFUNCTYPE(c_int, c_int, c_int)(("_setmode", c_runtime))
            fputws = CFUNCTYPE(c_int, c_wchar_p, c_void_p)(("fputws", c_runtime));
            fflush = CFUNCTYPE(c_int, c_void_p)(("fflush", c_runtime));

            buffer_chars = 1024

            class UnicodeOutput:
                def __init__(self, fileno, name):
                    self._stream = _fdopen(fileno, "w")
                    assert self._stream is not NULL

                    # Deep magic. MSVCRT supports writing wide-oriented output to stdout/stderr
                    # to the console using the Unicode APIs, but it does the conversion in the
                    # stdio buffer, so you need that buffer to be as large as the maximum amount
                    # you're going to write in a single call (in bytes, not characters).
                    setvbuf(self._stream, NULL, _IOLBF, buffer_chars*4 + 100)
                    _setmode(fileno, _O_U8TEXT)

                    self._fileno = fileno
                    self.closed = False
                    self.softspace = False
                    self.mode = 'w'
                    self.encoding = 'utf-8'
                    self.name = name

                def isatty(self):
                    return False
                def close(self):
                    self.closed = True
                    self.flush()
                def fileno(self):
                    return self._fileno
                def flush(self):
                    fflush(self._stream)

                def write(self, text):
                    if not isinstance(text, unicode):
                        text = str(text).decode('utf-8')
                    for i in xrange(0, len(text), buffer_chars):
                        fputws(text[i:(i+buffer_chars)], self._stream)
                        fflush(self._stream)

                def writelines(self, lines):
                    for line in lines:
                        self.write(line)

            if real_stdout:
                sys.stdout = UnicodeOutput(STDOUT_FILENO, '<Unicode stdout>')

            if real_stderr:
                sys.stderr = UnicodeOutput(STDERR_FILENO, '<Unicode stderr>')
    except Exception, e:
        log.msg("exception %r while fixing up sys.stdout and sys.stderr" % (e,), level=log.WEIRD)

    # Unmangle command-line arguments.
    GetCommandLineW = WINFUNCTYPE(c_wchar_p)(("GetCommandLineW", windll.kernel32))
    CommandLineToArgvW = WINFUNCTYPE(POINTER(c_wchar_p), c_wchar_p, POINTER(c_int)) \
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
