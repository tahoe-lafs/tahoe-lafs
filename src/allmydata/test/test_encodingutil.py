
lumiere_nfc = u"lumi\u00E8re"
Artonwall_nfc = u"\u00C4rtonwall.mp3"
Artonwall_nfd = u"A\u0308rtonwall.mp3"

TEST_FILENAMES = (
  Artonwall_nfc,
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
        print "Usage: %s lumi<e-grave>re" % sys.argv[0]
        sys.exit(1)
    
    print
    print "class MyWeirdOS(EncodingUtil, unittest.TestCase):"
    print "    uname = '%s'" % ' '.join(platform.uname())
    if sys.platform != "win32":
        print "    argv = %s" % repr(sys.argv[1])
    print "    platform = '%s'" % sys.platform
    print "    filesystem_encoding = '%s'" % sys.getfilesystemencoding()
    print "    output_encoding = '%s'" % sys.stdout.encoding
    print "    argv_encoding = '%s'" % (sys.platform == "win32" and 'ascii' or sys.stdout.encoding)

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
import os, sys, locale

from allmydata.test.common_util import ReallyEqualMixin
from allmydata.util.encodingutil import argv_to_unicode, unicode_to_url, \
    unicode_to_output, unicode_platform, listdir_unicode, FilenameEncodingError, \
    get_output_encoding, get_filesystem_encoding, _reload
from allmydata.dirnode import normalize

from twisted.python import usage

class EncodingUtilErrors(ReallyEqualMixin, unittest.TestCase):
    def tearDown(self):
        _reload()

    @patch('sys.stdout')
    def test_get_output_encoding(self, mock_stdout):
        mock_stdout.encoding = 'UTF-8'
        _reload()
        self.failUnlessReallyEqual(get_output_encoding(), 'utf-8')

        mock_stdout.encoding = 'cp65001'
        _reload()
        self.failUnlessReallyEqual(get_output_encoding(), 'utf-8')

        mock_stdout.encoding = 'koi8-r'
        _reload()
        self.failUnlessReallyEqual(get_output_encoding(), 'koi8-r')

        mock_stdout.encoding = 'nonexistent_encoding'
        self.failUnlessRaises(AssertionError, _reload)

    @patch('locale.getpreferredencoding')
    def test_get_output_encoding_not_from_stdout(self, mock_locale_getpreferredencoding):
        locale  # hush pyflakes
        mock_locale_getpreferredencoding.return_value = 'koi8-r'

        class DummyStdout:
            pass
        old_stdout = sys.stdout
        sys.stdout = DummyStdout()
        try:
            _reload()
            self.failUnlessReallyEqual(get_output_encoding(), 'koi8-r')

            sys.stdout.encoding = None
            _reload()
            self.failUnlessReallyEqual(get_output_encoding(), 'koi8-r')

            mock_locale_getpreferredencoding.return_value = None
            _reload()
            self.failUnlessReallyEqual(get_output_encoding(), 'utf-8')
        finally:
            sys.stdout = old_stdout

    @patch('sys.stdout')
    def test_argv_to_unicode(self, mock):
        mock.encoding = 'utf-8'
        _reload()

        self.failUnlessRaises(usage.UsageError,
                              argv_to_unicode,
                              lumiere_nfc.encode('latin1'))

    @patch('sys.stdout')
    def test_unicode_to_output(self, mock):
        # Encoding koi8-r cannot represent e-grave
        mock.encoding = 'koi8-r'
        _reload()
        self.failUnlessRaises(UnicodeEncodeError, unicode_to_output, lumiere_nfc)

    @patch('os.listdir')
    def test_no_unicode_normalization(self, mock):
        # Pretend to run on a Unicode platform.
        # We normalized to NFC in 1.7beta, but we now don't.
        orig_platform = sys.platform
        try:
            sys.platform = 'darwin'
            mock.return_value = [Artonwall_nfd]
            _reload()
            self.failUnlessReallyEqual(listdir_unicode(u'/dummy'), [Artonwall_nfd])
        finally:
            sys.platform = orig_platform

# The following tests apply only to platforms that don't store filenames as
# Unicode entities on the filesystem.
class EncodingUtilNonUnicodePlatform(unittest.TestCase):
    def setUp(self):
        # Mock sys.platform because unicode_platform() uses it
        self.original_platform = sys.platform
        sys.platform = 'linux'

    def tearDown(self):
        sys.platform = self.original_platform
        _reload()

    @patch('sys.getfilesystemencoding')
    @patch('os.listdir')
    def test_listdir_unicode(self, mock_listdir, mock_getfilesystemencoding):
        # What happens if latin1-encoded filenames are encountered on an UTF-8
        # filesystem?
        mock_listdir.return_value = [
            lumiere_nfc.encode('utf-8'),
            lumiere_nfc.encode('latin1')]

        mock_getfilesystemencoding.return_value = 'utf-8'
        _reload()
        self.failUnlessRaises(FilenameEncodingError,
                              listdir_unicode,
                              u'/dummy')
        
        # We're trying to list a directory whose name cannot be represented in
        # the filesystem encoding.  This should fail.
        mock_getfilesystemencoding.return_value = 'ascii'
        _reload()
        self.failUnlessRaises(FilenameEncodingError,
                              listdir_unicode,
                              u'/' + lumiere_nfc)

class EncodingUtil(ReallyEqualMixin):
    def setUp(self):
        # Mock sys.platform because unicode_platform() uses it
        self.original_platform = sys.platform
        sys.platform = self.platform

    def tearDown(self):
        sys.platform = self.original_platform
        _reload()

    @patch('sys.stdout')
    def test_argv_to_unicode(self, mock):
        if 'argv' not in dir(self):
            return

        mock.encoding = self.output_encoding
        argu = lumiere_nfc
        argv = self.argv
        _reload()
        self.failUnlessReallyEqual(argv_to_unicode(argv), argu)

    def test_unicode_to_url(self):
        self.failUnless(unicode_to_url(lumiere_nfc), "lumi\xc3\xa8re")

    @patch('sys.stdout')
    def test_unicode_to_output(self, mock):
        if 'output' not in dir(self):
            return

        mock.encoding = self.output_encoding
        _reload()
        self.failUnlessReallyEqual(unicode_to_output(lumiere_nfc), self.output)

    def test_unicode_platform(self):
        matrix = {
          'linux2': False,
          'openbsd4': False,
          'win32':  True,
          'darwin': True,
        }

        _reload()
        self.failUnlessReallyEqual(unicode_platform(), matrix[self.platform])
 
    @patch('sys.getfilesystemencoding')
    @patch('os.listdir')
    def test_listdir_unicode(self, mock_listdir, mock_getfilesystemencoding):
        if 'dirlist' not in dir(self):
            return

        try:
            u"test".encode(self.filesystem_encoding)
        except (LookupError, AttributeError):
            raise unittest.SkipTest("This platform does not support the '%s' filesystem encoding "
                                    "that we are testing for the benefit of a different platform."
                                    % (self.filesystem_encoding,))

        mock_listdir.return_value = self.dirlist
        mock_getfilesystemencoding.return_value = self.filesystem_encoding

        _reload()
        filenames = listdir_unicode(u'/dummy')

        self.failUnlessEqual(set([normalize(fname) for fname in filenames]),
                             set(TEST_FILENAMES))


class StdlibUnicode(unittest.TestCase):
    """This mainly tests that some of the stdlib functions support Unicode paths, but also that
    listdir_unicode works for valid filenames."""

    def skip_if_cannot_represent_filename(self, u):
        enc = get_filesystem_encoding()
        if not unicode_platform():
            try:
                u.encode(enc)
            except UnicodeEncodeError:
                raise unittest.SkipTest("A non-ASCII filename could not be encoded on this platform.")

    def test_mkdir_open_exists_abspath_listdir_expanduser(self):
        self.skip_if_cannot_represent_filename(lumiere_nfc)

        try:
            os.mkdir(lumiere_nfc)
        except EnvironmentError, e:
            raise unittest.SkipTest("%r\nIt is possible that the filesystem on which this test is being run "
                                    "does not support Unicode, even though the platform does." % (e,))

        fn = lumiere_nfc + '/' + lumiere_nfc + '.txt'
        open(fn, 'wb').close()
        self.failUnless(os.path.exists(fn))
        self.failUnless(os.path.exists(os.path.abspath(fn)))
        filenames = listdir_unicode(lumiere_nfc)

        # We only require that the listing includes a filename that is canonically equivalent
        # to lumiere_nfc (on Mac OS X, it will be the NFD equivalent).
        self.failUnlessIn(lumiere_nfc + ".txt", set([normalize(fname) for fname in filenames]))

        expanded = os.path.expanduser("~/" + lumiere_nfc)
        self.failIfIn("~", expanded)
        self.failUnless(expanded.endswith(lumiere_nfc), expanded)

    def test_open_unrepresentable(self):
        if unicode_platform():
            raise unittest.SkipTest("This test is not applicable to platforms that represent filenames as Unicode.")

        enc = get_filesystem_encoding()
        fn = u'\u2621.txt'
        try:
            fn.encode(enc)
            raise unittest.SkipTest("This test cannot be run unless we know a filename that is not representable.")
        except UnicodeEncodeError:
            self.failUnlessRaises(UnicodeEncodeError, open, fn, 'wb')


class UbuntuKarmicUTF8(EncodingUtil, unittest.TestCase):
    uname = 'Linux korn 2.6.31-14-generic #48-Ubuntu SMP Fri Oct 16 14:05:01 UTC 2009 x86_64'
    output = 'lumi\xc3\xa8re'
    argv = 'lumi\xc3\xa8re'
    platform = 'linux2'
    filesystem_encoding = 'UTF-8'
    output_encoding = 'UTF-8'
    argv_encoding = 'UTF-8'
    dirlist = ['test_file', '\xc3\x84rtonwall.mp3', 'Blah blah.txt']

class UbuntuKarmicLatin1(EncodingUtil, unittest.TestCase):
    uname = 'Linux korn 2.6.31-14-generic #48-Ubuntu SMP Fri Oct 16 14:05:01 UTC 2009 x86_64'
    output = 'lumi\xe8re'
    argv = 'lumi\xe8re'
    platform = 'linux2'
    filesystem_encoding = 'ISO-8859-1'
    output_encoding = 'ISO-8859-1'
    argv_encoding = 'ISO-8859-1'
    dirlist = ['test_file', 'Blah blah.txt', '\xc4rtonwall.mp3']

class WindowsXP(EncodingUtil, unittest.TestCase):
    uname = 'Windows XP 5.1.2600 x86 x86 Family 15 Model 75 Step ping 2, AuthenticAMD'
    output = 'lumi\x8are'
    platform = 'win32'
    filesystem_encoding = 'mbcs'
    output_encoding = 'cp850'
    argv_encoding = 'ascii'
    dirlist = [u'Blah blah.txt', u'test_file', u'\xc4rtonwall.mp3']

class WindowsXP_UTF8(EncodingUtil, unittest.TestCase):
    uname = 'Windows XP 5.1.2600 x86 x86 Family 15 Model 75 Step ping 2, AuthenticAMD'
    output = 'lumi\xc3\xa8re'
    platform = 'win32'
    filesystem_encoding = 'mbcs'
    output_encoding = 'cp65001'
    argv_encoding = 'ascii'
    dirlist = [u'Blah blah.txt', u'test_file', u'\xc4rtonwall.mp3']

class WindowsVista(EncodingUtil, unittest.TestCase):
    uname = 'Windows Vista 6.0.6000 x86 x86 Family 6 Model 15 Stepping 11, GenuineIntel'
    output = 'lumi\x8are'
    platform = 'win32'
    filesystem_encoding = 'mbcs'
    output_encoding = 'cp850'
    argv_encoding = 'ascii'
    dirlist = [u'Blah blah.txt', u'test_file', u'\xc4rtonwall.mp3']

class MacOSXLeopard(EncodingUtil, unittest.TestCase):
    uname = 'Darwin g5.local 9.8.0 Darwin Kernel Version 9.8.0: Wed Jul 15 16:57:01 PDT 2009; root:xnu-1228.15.4~1/RELEASE_PPC Power Macintosh powerpc'
    output = 'lumi\xc3\xa8re'
    argv = 'lumi\xc3\xa8re'
    platform = 'darwin'
    filesystem_encoding = 'utf-8'
    output_encoding = 'UTF-8'
    argv_encoding = 'UTF-8'
    dirlist = [u'A\u0308rtonwall.mp3', u'Blah blah.txt', u'test_file']

class MacOSXLeopard7bit(EncodingUtil, unittest.TestCase):
    uname = 'Darwin g5.local 9.8.0 Darwin Kernel Version 9.8.0: Wed Jul 15 16:57:01 PDT 2009; root:xnu-1228.15.4~1/RELEASE_PPC Power Macintosh powerpc'
    platform = 'darwin'
    filesystem_encoding = 'utf-8'
    output_encoding = 'US-ASCII'
    argv_encoding = 'US-ASCII'
    dirlist = [u'A\u0308rtonwall.mp3', u'Blah blah.txt', u'test_file']

class OpenBSD(EncodingUtil, unittest.TestCase):
    uname = 'OpenBSD 4.1 GENERIC#187 i386 Intel(R) Celeron(R) CPU 2.80GHz ("GenuineIntel" 686-class)'
    platform = 'openbsd4'
    filesystem_encoding = '646'
    output_encoding = '646'
    argv_encoding = '646'
    # Oops, I cannot write filenames containing non-ascii characters
