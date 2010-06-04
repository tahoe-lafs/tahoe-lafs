"""
Functions used to convert inputs from whatever encoding used in the system to
unicode and back.
"""

import sys
import os
import unicodedata
from allmydata.util.assertutil import precondition
from twisted.python import usage
import locale

def get_term_encoding():
    """
    Returns expected encoding for writing to the terminal and reading
    arguments from the command-line.
    """

    if sys.stdout.encoding:
        return sys.stdout.encoding
    else:
        return locale.getpreferredencoding()

def argv_to_unicode(s):
    """
    Decode given argv element to unicode.
    """
    # Try to decode the command-line argument with the encoding returned by
    # get_term_encoding(), if this fails print an error message to the user.

    precondition(isinstance(s, str), s)

    try:
        return unicode(s, get_term_encoding())
    except UnicodeDecodeError:
        raise usage.UsageError("Argument '%s' cannot be decoded as %s." %
                               (s, get_term_encoding()))

def unicode_to_url(s):
    """
    Encode an unicode object used in an URL.
    """
    # According to RFC 2718, non-ascii characters in url's must be UTF-8 encoded.

    precondition(isinstance(s, unicode), s)
    return s.encode('utf-8')

def unicode_to_stdout(s):
    """
    Encode an unicode object for representation on stdout.
    """

    precondition(isinstance(s, unicode), s)
    return s.encode(get_term_encoding(), 'replace')

def unicode_platform():
    """
    Does the current platform handle Unicode filenames natively ?
    """

    return sys.platform in ('win32', 'darwin')

class FilenameEncodingError(Exception):
    """
    Filename cannot be encoded using the current encoding of your filesystem
    (%s). Please configure your locale correctly or rename this file.
    """

    pass

def listdir_unicode_unix(path):
    """
    This function emulates an Unicode API under Unix similar to one available
    under Windows or MacOS X.

    If badly encoded filenames are encountered, an exception is raised.
    """
    precondition(isinstance(path, unicode), path)

    encoding = sys.getfilesystemencoding()
    try:
        byte_path = path.encode(encoding)
    except UnicodeEncodeError:
        raise FilenameEncodingError(path)

    try:
        return [unicode(fn, encoding) for fn in os.listdir(byte_path)]
    except UnicodeDecodeError:
        raise FilenameEncodingError(fn)

def listdir_unicode(path, encoding = None):
    """
    Wrapper around listdir() which provides safe access to the convenient
    Unicode API even under Unix.
    """

    precondition(isinstance(path, unicode), path)

    # On Windows and MacOS X, the Unicode API is used
    if unicode_platform():
        dirlist = os.listdir(path)

    # On other platforms (ie. Unix systems), the byte-level API is used
    else:
        dirlist = listdir_unicode_unix(path)

    # Normalize the resulting unicode filenames
    #
    # This prevents different OS from generating non-equal unicode strings for
    # the same filename representation
    return [unicodedata.normalize('NFC', fname) for fname in dirlist]

def open_unicode(path, mode='r'):
    """
    Wrapper around open() which provides safe access to the convenient Unicode
    API even under Unix.
    """

    precondition(isinstance(path, unicode), path)

    if unicode_platform():
        return open(path, mode)
    else:
        encoding = sys.getfilesystemencoding()

        try:
            return open(path.encode(encoding), mode)
        except UnicodeEncodeError:
            raise FilenameEncodingError(path)
