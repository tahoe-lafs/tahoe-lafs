"""
Functions used to convert inputs from whatever encoding used in the system to
unicode and back.
"""

import sys
import os
import re
import unicodedata
from allmydata.util.assertutil import precondition
from twisted.python import usage
import locale


def _canonical_encoding(encoding):
    if encoding is None:
        encoding = 'utf-8'
    encoding = encoding.lower()
    if encoding == "cp65001":
        encoding = 'utf-8'
    elif encoding == "us-ascii" or encoding == "646":
        encoding = 'ascii'

    # sometimes Python returns an encoding name that it doesn't support for conversion
    # fail early if this happens
    try:
        u"test".encode(encoding)
    except LookupError:
        raise AssertionError("The character encoding '%s' is not supported for conversion." % (encoding,))

    return encoding

filesystem_encoding = None
output_encoding = None
argv_encoding = None
is_unicode_platform = False

def _reload():
    global filesystem_encoding, output_encoding, argv_encoding, is_unicode_platform

    filesystem_encoding = _canonical_encoding(sys.getfilesystemencoding())
    output_encoding = _canonical_encoding(sys.stdout.encoding or locale.getpreferredencoding())
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
    except UnicodeEncodeError:
        raise UnicodeEncodeError(output_encoding, s, 0, 0,
                                 "A string could not be encoded as %s for output to the terminal:\n%r" %
                                 (output_encoding, repr(s)))

    if PRINTABLE_8BIT.search(out) is None:
        raise UnicodeEncodeError(output_encoding, s, 0, 0,
                                 "A string encoded as %s for output to the terminal contained unsafe bytes:\n%r" %
                                 (output_encoding, repr(s)))
    return out

def quote_output(s, quotemarks=True):
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
        out = s.encode(output_encoding)
    except UnicodeEncodeError:
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

class FilenameEncodingError(Exception):
    """
    Filename cannot be encoded using the current encoding of your filesystem
    (%s). Please configure your locale correctly or rename this file.
    """
    pass

def listdir_unicode_fallback(path):
    """
    This function emulates a fallback Unicode API similar to one available
    under Windows or MacOS X.

    If badly encoded filenames are encountered, an exception is raised.
    """
    precondition(isinstance(path, unicode), path)

    try:
        byte_path = path.encode(filesystem_encoding)
    except UnicodeEncodeError:
        raise FilenameEncodingError(path)

    try:
        return [unicode(fn, filesystem_encoding) for fn in os.listdir(byte_path)]
    except UnicodeDecodeError:
        raise FilenameEncodingError(fn)

def listdir_unicode(path):
    """
    Wrapper around listdir() which provides safe access to the convenient
    Unicode API even under platforms that don't provide one natively.
    """
    precondition(isinstance(path, unicode), path)

    # On Windows and MacOS X, the Unicode API is used
    # On other platforms (ie. Unix systems), the byte-level API is used

    if is_unicode_platform:
        dirlist = os.listdir(path)
    else:
        dirlist = listdir_unicode_fallback(path)

    # Normalize the resulting unicode filenames
    #
    # This prevents different OSes from generating non-equal unicode strings for
    # the same filename representation
    return [unicodedata.normalize('NFC', fname) for fname in dirlist]

def open_unicode(path, mode):
    """
    Wrapper around open() which provides safe access to the convenient Unicode
    API even under Unix.
    """
    precondition(isinstance(path, unicode), path)

    if is_unicode_platform:
        return open(os.path.expanduser(path), mode)
    else:
        try:
            return open(os.path.expanduser(path.encode(filesystem_encoding)), mode)
        except UnicodeEncodeError:
            raise FilenameEncodingError(path)

def abspath_expanduser_unicode(path):
    precondition(isinstance(path, unicode), path)

    if is_unicode_platform:
        return os.path.abspath(os.path.expanduser(path))
    else:
        try:
            pathstr = path.encode(filesystem_encoding)
            return os.path.abspath(os.path.expanduser(pathstr)).decode(filesystem_encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            raise FilenameEncodingError(path)
