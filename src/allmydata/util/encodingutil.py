"""
Functions used to convert inputs from whatever encoding used in the system to
unicode and back.
"""

import sys
import re
from allmydata.util.assertutil import precondition
from twisted.python import usage
import locale
from allmydata.util import log


def _canonical_encoding(encoding):
    if encoding is None:
        log.msg("Warning: falling back to UTF-8 encoding.", level=log.WEIRD)
        encoding = 'utf-8'
    encoding = encoding.lower()
    if encoding == "cp65001":
        encoding = 'utf-8'
    elif encoding == "us-ascii" or encoding == "646" or encoding == "ansi_x3.4-1968":
        encoding = 'ascii'

    # sometimes Python returns an encoding name that it doesn't support for conversion
    # fail early if this happens
    try:
        u"test".encode(encoding)
    except (LookupError, AttributeError):
        raise AssertionError("The character encoding '%s' is not supported for conversion." % (encoding,))

    return encoding

filesystem_encoding = None
output_encoding = None
argv_encoding = None
is_unicode_platform = False

def _reload():
    global filesystem_encoding, output_encoding, argv_encoding, is_unicode_platform

    filesystem_encoding = _canonical_encoding(sys.getfilesystemencoding())

    outenc = None
    if hasattr(sys.stdout, 'encoding'):
        outenc = sys.stdout.encoding
    if outenc is None:
        try:
            outenc = locale.getpreferredencoding()
        except Exception:
            pass  # work around <http://bugs.python.org/issue1443504>
    output_encoding = _canonical_encoding(outenc)

    if sys.platform == 'win32':
        # Unicode arguments are not supported on Windows yet; see #565 and #1074.
        argv_encoding = 'ascii'
    else:
        argv_encoding = output_encoding
    is_unicode_platform = sys.platform in ["win32", "darwin"]

_reload()


def get_filesystem_encoding():
    """
    Returns expected encoding for local filenames.
    """
    return filesystem_encoding

def get_output_encoding():
    """
    Returns expected encoding for writing to stdout or stderr.
    """
    return output_encoding

def get_argv_encoding():
    """
    Returns expected encoding for command-line arguments.
    """
    return argv_encoding

def argv_to_unicode(s):
    """
    Decode given argv element to unicode. If this fails, raise a UsageError.
    """
    precondition(isinstance(s, str), s)

    try:
        return unicode(s, argv_encoding)
    except UnicodeDecodeError:
        raise usage.UsageError("Argument %s cannot be decoded as %s." %
                               (quote_output(s), argv_encoding))

def unicode_to_url(s):
    """
    Encode an unicode object used in an URL.
    """
    # According to RFC 2718, non-ascii characters in URLs must be UTF-8 encoded.

    # FIXME
    return to_str(s)
    #precondition(isinstance(s, unicode), s)
    #return s.encode('utf-8')

def to_str(s):
    if s is None or isinstance(s, str):
        return s
    return s.encode('utf-8')

def to_argv(s):
    if isinstance(s, str):
        return s
    return s.encode(argv_encoding)

PRINTABLE_ASCII = re.compile(r'^[ -~\n\r]*$', re.DOTALL)
PRINTABLE_8BIT = re.compile(r'^[ -&(-~\n\r\x80-\xFF]*$', re.DOTALL)

def is_printable_ascii(s):
    return PRINTABLE_ASCII.search(s) is not None

def unicode_to_output(s):
    """
    Encode an unicode object for representation on stdout or stderr.
    """
    precondition(isinstance(s, unicode), s)

    try:
        out = s.encode(output_encoding)
    except (UnicodeEncodeError, UnicodeDecodeError):
        raise UnicodeEncodeError(output_encoding, s, 0, 0,
                                 "A string could not be encoded as %s for output to the terminal:\n%r" %
                                 (output_encoding, repr(s)))

    if PRINTABLE_8BIT.search(out) is None:
        raise UnicodeEncodeError(output_encoding, s, 0, 0,
                                 "A string encoded as %s for output to the terminal contained unsafe bytes:\n%r" %
                                 (output_encoding, repr(s)))
    return out

def quote_output(s, quotemarks=True, encoding=None):
    """
    Encode either a Unicode string or a UTF-8-encoded bytestring for representation
    on stdout or stderr, tolerating errors. If 'quotemarks' is True, the string is
    always surrounded by single quotes; otherwise, it is quoted only if necessary to
    avoid ambiguity or control bytes in the output.
    """
    precondition(isinstance(s, (str, unicode)), s)

    if isinstance(s, str):
        try:
            s = s.decode('utf-8')
        except UnicodeDecodeError:
            return 'b' + repr(s)

    try:
        out = s.encode(encoding or output_encoding)
    except (UnicodeEncodeError, UnicodeDecodeError):
        return repr(s)

    if PRINTABLE_8BIT.search(out) is None:
        return repr(out)

    if quotemarks:
        return "'" + out.replace("\\", "\\\\").replace("'", "\'") + "'"
    else:
        return out

def quote_path(path, quotemarks=True):
    return quote_output("/".join(map(to_str, path)), quotemarks=quotemarks)


def unicode_platform():
    """
    Does the current platform handle Unicode filenames natively?
    """
    return is_unicode_platform
