# coding=utf-8

TEST_FILENAMES = (
  u'Ärtonwall.mp3',
  u'test_file',
  u'Blah blah.txt',
)

# The following main helps to generate a test class for other operating
# systems.

if __name__ == "__main__":
    import sys, os
    import tempfile
    import shutil
    import platform
    
    if len(sys.argv) != 2:
        print "Usage: %s lumière" % sys.argv[0]
        sys.exit(1)
    
    print
    print "class MyWeirdOS(StringUtils, unittest.TestCase):"
    print "    uname = '%s'" % ' '.join(platform.uname())
    print "    argv = %s" % repr(sys.argv[1])
    print "    platform = '%s'" % sys.platform
    print "    filesystemencoding = '%s'" % sys.getfilesystemencoding()
    print "    stdoutencoding = '%s'" % sys.stdout.encoding

    try:
        tmpdir = tempfile.mkdtemp()
        for fname in TEST_FILENAMES:
            open(os.path.join(tmpdir, fname), 'w').close() 

        # Use Unicode API under Windows or MacOS X
        if sys.platform in ('win32', 'darwin'):
            dirlist = os.listdir(unicode(tmpdir))
        else:
            dirlist = os.listdir(tmpdir)

        print "    dirlist = %s" % repr(dirlist)
    except:
        print "    # Oops, I cannot write filenames containing non-ascii characters"
    print

    shutil.rmtree(tmpdir)
    sys.exit(0)

from twisted.trial import unittest
from mock import patch
import sys

from allmydata.util.stringutils import argv_to_unicode, unicode_to_url, \
    unicode_to_stdout, unicode_platform, listdir_unicode, open_unicode, \
    FilenameEncodingError, get_term_encoding
from twisted.python import usage

class StringUtilsErrors(unittest.TestCase):
    @patch('sys.stdout')
    def test_get_term_encoding(self, mock):
        mock.encoding = None
        
        self.failUnlessEqual(get_term_encoding(), 'ascii')

    @patch('sys.stdout')
    def test_argv_to_unicode(self, mock):
        mock.encoding = 'utf-8'

        self.failUnlessRaises(usage.UsageError,
                              argv_to_unicode,
                              u'lumière'.encode('latin1'))

    def test_unicode_to_url(self):
        pass

    @patch('sys.stdout')
    def test_unicode_to_stdout(self, mock):
        # Encoding koi8-r cannot represent 'è'
        mock.encoding = 'koi8-r'
        self.failUnlessEqual(unicode_to_stdout(u'lumière'), 'lumi?re')

    @patch('os.listdir')
    def test_unicode_normalization(self, mock):
        # Pretend to run on an Unicode platform such as Windows
        orig_platform = sys.platform
        sys.platform = 'win32'

        mock.return_value = [u'A\u0308rtonwall.mp3']
        self.failUnlessEqual(listdir_unicode(u'/dummy'), [u'\xc4rtonwall.mp3'])

        sys.platform = orig_platform

# The following tests applies only to platforms which don't store filenames as
# Unicode entities on the filesystem.
class StringUtilsNonUnicodePlatform(unittest.TestCase):
    def setUp(self):
        # Mock sys.platform because unicode_platform() uses it
        self.original_platform = sys.platform
        sys.platform = 'linux'

    def tearDown(self):
        sys.platform = self.original_platform

    @patch('sys.getfilesystemencoding')
    @patch('os.listdir')
    def test_listdir_unicode(self, mock_listdir, mock_getfilesystemencoding):
        # What happen if a latin1-encoded filenames is encountered on an UTF-8
        # filesystem?
        mock_listdir.return_value = [
            u'lumière'.encode('utf-8'),
            u'lumière'.encode('latin1')]

        mock_getfilesystemencoding.return_value = 'utf-8'
       
        self.failUnlessRaises(FilenameEncodingError,
                              listdir_unicode,
                              u'/dummy')
        
        # We're trying to list a directory whose name cannot be represented in
        # the filesystem encoding.  This should fail.
        mock_getfilesystemencoding.return_value = 'ascii'
        self.failUnlessRaises(FilenameEncodingError,
                              listdir_unicode,
                              u'/lumière')

    @patch('sys.getfilesystemencoding')
    def test_open_unicode(self, mock):
        mock.return_value = 'ascii'

        self.failUnlessRaises(FilenameEncodingError,
                              open_unicode,
                              u'lumière')

class StringUtils:
    def setUp(self):
        # Mock sys.platform because unicode_platform() uses it
        self.original_platform = sys.platform
        sys.platform = self.platform

    def tearDown(self):
        sys.platform = self.original_platform

    @patch('sys.stdout')
    def test_argv_to_unicode(self, mock):
        if 'argv' not in dir(self):
            raise unittest.SkipTest("There's no way to pass non-ASCII arguments in CLI on this (mocked) platform")

        mock.encoding = self.stdoutencoding

        argu = u'lumière'
        argv = self.argv

        self.failUnlessEqual(argv_to_unicode(argv), argu)

    def test_unicode_to_url(self):
        self.failUnless(unicode_to_url(u'lumière'), u'lumière'.encode('utf-8'))

    @patch('sys.stdout')
    def test_unicode_to_stdout(self, mock):
        if 'argv' not in dir(self):
            raise unittest.SkipTest("There's no way to pass non-ASCII arguments in CLI on this (mocked) platform")

        mock.encoding = self.stdoutencoding
        self.failUnlessEqual(unicode_to_stdout(u'lumière'), self.argv)

    def test_unicode_platform(self):
        matrix = {
          'linux2': False,
          'openbsd4': False,
          'win32':  True,
          'darwin': True,
        }

        self.failUnlessEqual(unicode_platform(), matrix[self.platform])
 
    @patch('sys.getfilesystemencoding')
    @patch('os.listdir')
    def test_listdir_unicode(self, mock_listdir, mock_getfilesystemencoding):
        if 'dirlist' not in dir(self):
            raise unittest.SkipTest("No way to write non-ASCII filenames on this system")

        mock_listdir.return_value = self.dirlist
        mock_getfilesystemencoding.return_value = self.filesystemencoding
       
        filenames = listdir_unicode(u'/dummy')

        for fname in TEST_FILENAMES:
            self.failUnless(isinstance(fname, unicode))

            if fname not in filenames:
                self.fail("Cannot find %r in %r" % (fname, filenames))

    @patch('sys.getfilesystemencoding')
    @patch('__builtin__.open')
    def test_open_unicode(self, mock_open, mock_getfilesystemencoding):
        mock_getfilesystemencoding.return_value = self.filesystemencoding

        fn = u'/dummy_directory/lumière.txt'

        try:
            open_unicode(fn)
        except FilenameEncodingError:
            raise unittest.SkipTest("Cannot represent test filename on this (mocked) platform")

        # Pass Unicode string to open() on Unicode platforms
        if unicode_platform():
            mock_open.assert_called_with(fn, 'r')

        # Pass correctly encoded bytestrings to open() on non-Unicode platforms
        else:
            fn_bytestring = fn.encode(self.filesystemencoding)
            mock_open.assert_called_with(fn_bytestring, 'r')

class UbuntuKarmicUTF8(StringUtils, unittest.TestCase):
    uname = 'Linux korn 2.6.31-14-generic #48-Ubuntu SMP Fri Oct 16 14:05:01 UTC 2009 x86_64'
    argv = 'lumi\xc3\xa8re'
    platform = 'linux2'
    filesystemencoding = 'UTF-8'
    stdoutencoding = 'UTF-8'
    dirlist = ['test_file', '\xc3\x84rtonwall.mp3', 'Blah blah.txt']


class UbuntuKarmicLatin1(StringUtils, unittest.TestCase):
    uname = 'Linux korn 2.6.31-14-generic #48-Ubuntu SMP Fri Oct 16 14:05:01 UTC 2009 x86_64'
    argv = 'lumi\xe8re'
    platform = 'linux2'
    filesystemencoding = 'ISO-8859-1'
    stdoutencoding = 'ISO-8859-1'
    dirlist = ['test_file', 'Blah blah.txt', '\xc4rtonwall.mp3']

class WindowsXP(StringUtils, unittest.TestCase):
    uname = 'Windows XP 5.1.2600 x86 x86 Family 15 Model 75 Step ping 2, AuthenticAMD'
    argv = 'lumi\xe8re'
    platform = 'win32'
    filesystemencoding = 'mbcs'
    stdoutencoding = 'cp850'
    dirlist = [u'Blah blah.txt', u'test_file', u'\xc4rtonwall.mp3']

    todo = "Unicode arguments on the command-line is not yet supported under Windows, see bug #565."

class WindowsXP_UTF8(StringUtils, unittest.TestCase):
    uname = 'Windows XP 5.1.2600 x86 x86 Family 15 Model 75 Step ping 2, AuthenticAMD'
    argv = 'lumi\xe8re'
    platform = 'win32'
    filesystemencoding = 'mbcs'
    stdoutencoding = 'cp65001'
    dirlist = [u'Blah blah.txt', u'test_file', u'\xc4rtonwall.mp3']

    todo = "Unicode arguments on the command-line is not yet supported under Windows, see bug #565."

class WindowsVista(StringUtils, unittest.TestCase):
    uname = 'Windows Vista 6.0.6000 x86 x86 Family 6 Model 15 Stepping 11, GenuineIntel'
    argv = 'lumi\xe8re'
    platform = 'win32'
    filesystemencoding = 'mbcs'
    stdoutencoding = 'cp850'
    dirlist = [u'Blah blah.txt', u'test_file', u'\xc4rtonwall.mp3']

    todo = "Unicode arguments on the command-line is not yet supported under Windows, see bug #565."

class MacOSXLeopard(StringUtils, unittest.TestCase):
    uname = 'Darwin g5.local 9.8.0 Darwin Kernel Version 9.8.0: Wed Jul 15 16:57:01 PDT 2009; root:xnu-1228.15.4~1/RELEASE_PPC Power Macintosh powerpc'
    argv = 'lumi\xc3\xa8re'
    platform = 'darwin'
    filesystemencoding = 'utf-8'
    stdoutencoding = 'UTF-8'
    dirlist = [u'A\u0308rtonwall.mp3', u'Blah blah.txt', u'test_file']

class MacOSXLeopard7bit(StringUtils, unittest.TestCase):
    uname = 'Darwin g5.local 9.8.0 Darwin Kernel Version 9.8.0: Wed Jul 15 16:57:01 PDT 2009; root:xnu-1228.15.4~1/RELEASE_PPC Power Macintosh powerpc'
    #argv = 'lumiere'
    platform = 'darwin'
    filesystemencoding = 'utf-8'
    stdoutencoding = 'US-ASCII'
    dirlist = [u'A\u0308rtonwall.mp3', u'Blah blah.txt', u'test_file']

class OpenBSD(StringUtils, unittest.TestCase):
    uname = 'OpenBSD 4.1 GENERIC#187 i386 Intel(R) Celeron(R) CPU 2.80GHz ("GenuineIntel" 686-class)'
    #argv = 'lumiere'
    platform = 'openbsd4'
    filesystemencoding = '646'
    stdoutencoding = '646'
    # Oops, I cannot write filenames containing non-ascii characters
