"""
Ported to Python3.
"""

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    # open is not here because we want to use native strings on Py2
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
import six
import os, time, sys
import yaml
import json
from threading import current_thread

from twisted.trial import unittest
from foolscap.api import Violation, RemoteException

from allmydata.util import idlib, mathutil
from allmydata.util import fileutil
from allmydata.util import jsonbytes
from allmydata.util import pollmixin
from allmydata.util import yamlutil
from allmydata.util import rrefutil
from allmydata.util.fileutil import EncryptedTemporaryFile
from allmydata.util.cputhreadpool import defer_to_thread, disable_thread_pool_for_test
from allmydata.test.common_util import ReallyEqualMixin
from .no_network import fireNow, LocalWrapper

if six.PY3:
    long = int


class IDLib(unittest.TestCase):
    def test_nodeid_b2a(self):
        result = idlib.nodeid_b2a(b"\x00"*20)
        self.assertEqual(result, "a"*32)
        self.assertIsInstance(result, str)


class MyList(list):
    pass

class Math(unittest.TestCase):
    def test_round_sigfigs(self):
        f = mathutil.round_sigfigs
        self.failUnlessEqual(f(22.0/3, 4), 7.3330000000000002)


class FileUtil(ReallyEqualMixin, unittest.TestCase):
    def mkdir(self, basedir, path, mode=0o777):
        fn = os.path.join(basedir, path)
        fileutil.make_dirs(fn, mode)

    def touch(self, basedir, path, mode=None, data="touch\n"):
        fn = os.path.join(basedir, path)
        f = open(fn, "w")
        f.write(data)
        f.close()
        if mode is not None:
            os.chmod(fn, mode)

    def test_rm_dir(self):
        basedir = "util/FileUtil/test_rm_dir"
        fileutil.make_dirs(basedir)
        # create it again to test idempotency
        fileutil.make_dirs(basedir)
        d = os.path.join(basedir, "doomed")
        self.mkdir(d, "a/b")
        self.touch(d, "a/b/1.txt")
        self.touch(d, "a/b/2.txt", 0o444)
        self.touch(d, "a/b/3.txt", 0)
        self.mkdir(d, "a/c")
        self.touch(d, "a/c/1.txt")
        self.touch(d, "a/c/2.txt", 0o444)
        self.touch(d, "a/c/3.txt", 0)
        os.chmod(os.path.join(d, "a/c"), 0o444)
        self.mkdir(d, "a/d")
        self.touch(d, "a/d/1.txt")
        self.touch(d, "a/d/2.txt", 0o444)
        self.touch(d, "a/d/3.txt", 0)
        os.chmod(os.path.join(d, "a/d"), 0)

        fileutil.rm_dir(d)
        self.failIf(os.path.exists(d))
        # remove it again to test idempotency
        fileutil.rm_dir(d)

    def test_remove_if_possible(self):
        basedir = "util/FileUtil/test_remove_if_possible"
        fileutil.make_dirs(basedir)
        self.touch(basedir, "here")
        fn = os.path.join(basedir, "here")
        fileutil.remove_if_possible(fn)
        self.failIf(os.path.exists(fn))
        fileutil.remove_if_possible(fn) # should be idempotent
        fileutil.rm_dir(basedir)
        fileutil.remove_if_possible(fn) # should survive errors

    def test_write_atomically(self):
        basedir = "util/FileUtil/test_write_atomically"
        fileutil.make_dirs(basedir)
        fn = os.path.join(basedir, "here")
        fileutil.write_atomically(fn, b"one", "b")
        self.failUnlessEqual(fileutil.read(fn), b"one")
        fileutil.write_atomically(fn, u"two", mode="") # non-binary
        self.failUnlessEqual(fileutil.read(fn), b"two")

    def test_rename(self):
        basedir = "util/FileUtil/test_rename"
        fileutil.make_dirs(basedir)
        self.touch(basedir, "here")
        fn = os.path.join(basedir, "here")
        fn2 = os.path.join(basedir, "there")
        fileutil.rename(fn, fn2)
        self.failIf(os.path.exists(fn))
        self.failUnless(os.path.exists(fn2))

    def test_rename_no_overwrite(self):
        workdir = fileutil.abspath_expanduser_unicode(u"test_rename_no_overwrite")
        fileutil.make_dirs(workdir)

        source_path = os.path.join(workdir, "source")
        dest_path   = os.path.join(workdir, "dest")

        # when neither file exists
        self.failUnlessRaises(OSError, fileutil.rename_no_overwrite, source_path, dest_path)

        # when only dest exists
        fileutil.write(dest_path,   b"dest")
        self.failUnlessRaises(OSError, fileutil.rename_no_overwrite, source_path, dest_path)
        self.failUnlessEqual(fileutil.read(dest_path),   b"dest")

        # when both exist
        fileutil.write(source_path, b"source")
        self.failUnlessRaises(OSError, fileutil.rename_no_overwrite, source_path, dest_path)
        self.failUnlessEqual(fileutil.read(source_path), b"source")
        self.failUnlessEqual(fileutil.read(dest_path),   b"dest")

        # when only source exists
        os.remove(dest_path)
        fileutil.rename_no_overwrite(source_path, dest_path)
        self.failUnlessEqual(fileutil.read(dest_path), b"source")
        self.failIf(os.path.exists(source_path))

    def test_replace_file(self):
        workdir = fileutil.abspath_expanduser_unicode(u"test_replace_file")
        fileutil.make_dirs(workdir)

        replaced_path    = os.path.join(workdir, "replaced")
        replacement_path = os.path.join(workdir, "replacement")

        # when none of the files exist
        self.failUnlessRaises(fileutil.ConflictError, fileutil.replace_file, replaced_path, replacement_path)

        # when only replaced exists
        fileutil.write(replaced_path,   b"foo")
        self.failUnlessRaises(fileutil.ConflictError, fileutil.replace_file, replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), b"foo")

        # when both replaced and replacement exist
        fileutil.write(replacement_path, b"bar")
        fileutil.replace_file(replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), b"bar")
        self.failIf(os.path.exists(replacement_path))

        # when only replacement exists
        os.remove(replaced_path)
        fileutil.write(replacement_path, b"bar")
        fileutil.replace_file(replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), b"bar")
        self.failIf(os.path.exists(replacement_path))

    def test_du(self):
        basedir = "util/FileUtil/test_du"
        fileutil.make_dirs(basedir)
        d = os.path.join(basedir, "space-consuming")
        self.mkdir(d, "a/b")
        self.touch(d, "a/b/1.txt", data="a"*10)
        self.touch(d, "a/b/2.txt", data="b"*11)
        self.mkdir(d, "a/c")
        self.touch(d, "a/c/1.txt", data="c"*12)
        self.touch(d, "a/c/2.txt", data="d"*13)

        used = fileutil.du(basedir)
        self.failUnlessEqual(10+11+12+13, used)

    def test_abspath_expanduser_unicode(self):
        self.failUnlessRaises(AssertionError, fileutil.abspath_expanduser_unicode, b"bytestring")

        saved_cwd = os.path.normpath(os.getcwd())
        if PY2:
            saved_cwd = saved_cwd.decode("utf8")
        abspath_cwd = fileutil.abspath_expanduser_unicode(u".")
        abspath_cwd_notlong = fileutil.abspath_expanduser_unicode(u".", long_path=False)
        self.failUnless(isinstance(saved_cwd, str), saved_cwd)
        self.failUnless(isinstance(abspath_cwd, str), abspath_cwd)
        if sys.platform == "win32":
            self.failUnlessReallyEqual(abspath_cwd, fileutil.to_windows_long_path(saved_cwd))
        else:
            self.failUnlessReallyEqual(abspath_cwd, saved_cwd)
        self.failUnlessReallyEqual(abspath_cwd_notlong, saved_cwd)

        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"\\\\?\\foo"), u"\\\\?\\foo")
        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"\\\\.\\foo"), u"\\\\.\\foo")
        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"\\\\server\\foo"), u"\\\\?\\UNC\\server\\foo")
        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"C:\\foo"), u"\\\\?\\C:\\foo")
        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"C:\\foo/bar"), u"\\\\?\\C:\\foo\\bar")

        # adapted from <http://svn.python.org/view/python/branches/release26-maint/Lib/test/test_posixpath.py?view=markup&pathrev=78279#test_abspath>

        foo = fileutil.abspath_expanduser_unicode(u"foo")
        self.failUnless(foo.endswith(u"%sfoo" % (os.path.sep,)), foo)

        foobar = fileutil.abspath_expanduser_unicode(u"bar", base=foo)
        self.failUnless(foobar.endswith(u"%sfoo%sbar" % (os.path.sep, os.path.sep)), foobar)

        if sys.platform == "win32":
            # This is checking that a drive letter is added for a path without one.
            baz = fileutil.abspath_expanduser_unicode(u"\\baz")
            self.failUnless(baz.startswith(u"\\\\?\\"), baz)
            self.failUnlessReallyEqual(baz[5 :], u":\\baz")

            bar = fileutil.abspath_expanduser_unicode(u"\\bar", base=baz)
            self.failUnless(bar.startswith(u"\\\\?\\"), bar)
            self.failUnlessReallyEqual(bar[5 :], u":\\bar")
            # not u":\\baz\\bar", because \bar is absolute on the current drive.

            self.failUnlessReallyEqual(baz[4], bar[4])  # same drive

            baz_notlong = fileutil.abspath_expanduser_unicode(u"\\baz", long_path=False)
            self.failIf(baz_notlong.startswith(u"\\\\?\\"), baz_notlong)
            self.failUnlessReallyEqual(baz_notlong[1 :], u":\\baz")

            bar_notlong = fileutil.abspath_expanduser_unicode(u"\\bar", base=baz_notlong, long_path=False)
            self.failIf(bar_notlong.startswith(u"\\\\?\\"), bar_notlong)
            self.failUnlessReallyEqual(bar_notlong[1 :], u":\\bar")
            # not u":\\baz\\bar", because \bar is absolute on the current drive.

            self.failUnlessReallyEqual(baz_notlong[0], bar_notlong[0])  # same drive

        self.failIfIn(u"~", fileutil.abspath_expanduser_unicode(u"~"))
        self.failIfIn(u"~", fileutil.abspath_expanduser_unicode(u"~", long_path=False))

        cwds = ['cwd']
        try:
            cwds.append(u'\xe7w\xf0'.encode(sys.getfilesystemencoding()
                                            or 'ascii'))
        except UnicodeEncodeError:
            pass # the cwd can't be encoded -- test with ascii cwd only

        for cwd in cwds:
            try:
                os.mkdir(cwd)
                os.chdir(cwd)
                for upath in (u'', u'fuu', u'f\xf9\xf9', u'/fuu', u'U:\\', u'~'):
                    uabspath = fileutil.abspath_expanduser_unicode(upath)
                    self.failUnless(isinstance(uabspath, str), uabspath)

                    uabspath_notlong = fileutil.abspath_expanduser_unicode(upath, long_path=False)
                    self.failUnless(isinstance(uabspath_notlong, str), uabspath_notlong)
            finally:
                os.chdir(saved_cwd)

    def test_make_dirs_with_absolute_mode(self):
        if sys.platform == 'win32':
            raise unittest.SkipTest("Permissions don't work the same on windows.")

        workdir = fileutil.abspath_expanduser_unicode(u"test_make_dirs_with_absolute_mode")
        fileutil.make_dirs(workdir)
        abspath = fileutil.abspath_expanduser_unicode(u"a/b/c/d", base=workdir)
        fileutil.make_dirs_with_absolute_mode(workdir, abspath, 0o766)
        new_mode = os.stat(os.path.join(workdir, "a", "b", "c", "d")).st_mode & 0o777
        self.failUnlessEqual(new_mode, 0o766)
        new_mode = os.stat(os.path.join(workdir, "a", "b", "c")).st_mode & 0o777
        self.failUnlessEqual(new_mode, 0o766)
        new_mode = os.stat(os.path.join(workdir, "a", "b")).st_mode & 0o777
        self.failUnlessEqual(new_mode, 0o766)
        new_mode = os.stat(os.path.join(workdir, "a")).st_mode & 0o777
        self.failUnlessEqual(new_mode, 0o766)
        new_mode = os.stat(workdir).st_mode & 0o777
        self.failIfEqual(new_mode, 0o766)

    def test_create_long_path(self):
        """
        Even for paths with total length greater than 260 bytes,
        ``fileutil.abspath_expanduser_unicode`` produces a path on which other
        path-related APIs can operate.

        https://msdn.microsoft.com/en-us/library/windows/desktop/aa365247(v=vs.85).aspx
        documents certain Windows-specific path length limitations this test
        is specifically intended to demonstrate can be overcome.
        """
        workdir = u"test_create_long_path"
        fileutil.make_dirs(workdir)
        base_path = fileutil.abspath_expanduser_unicode(workdir)
        base_length = len(base_path)

        # Construct a path /just/ long enough to exercise the important case.
        # It would be nice if we could just use a seemingly globally valid
        # long file name (the `x...` portion) here - for example, a name 255
        # bytes long- and a previous version of this test did just that.
        # However, aufs imposes a 242 byte length limit on file names.  Most
        # other POSIX filesystems do allow names up to 255 bytes.  It's not
        # clear there's anything we can *do* about lower limits, though, and
        # POSIX.1-2017 (and earlier) only requires that the maximum be at
        # least 14 (!!!)  bytes.
        long_path = os.path.join(base_path, u'x' * (261 - base_length))

        def _cleanup():
            fileutil.remove(long_path)
        self.addCleanup(_cleanup)

        fileutil.write(long_path, b"test")
        self.failUnless(os.path.exists(long_path))
        self.failUnlessEqual(fileutil.read(long_path), b"test")
        _cleanup()
        self.failIf(os.path.exists(long_path))

    def _test_windows_expanduser(self, userprofile=None, homedrive=None, homepath=None):
        def call_windows_getenv(name):
            if name == u"USERPROFILE": return userprofile
            if name == u"HOMEDRIVE":   return homedrive
            if name == u"HOMEPATH":    return homepath
            self.fail("unexpected argument to call_windows_getenv")
        self.patch(fileutil, 'windows_getenv', call_windows_getenv)

        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"~"), os.path.join(u"C:", u"\\Documents and Settings\\\u0100"))
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"~\\foo"), os.path.join(u"C:", u"\\Documents and Settings\\\u0100", u"foo"))
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"~/foo"), os.path.join(u"C:", u"\\Documents and Settings\\\u0100", u"foo"))
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"a"), u"a")
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"a~"), u"a~")
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"a\\~\\foo"), u"a\\~\\foo")

    def test_windows_expanduser_xp(self):
        return self._test_windows_expanduser(homedrive=u"C:", homepath=u"\\Documents and Settings\\\u0100")

    def test_windows_expanduser_win7(self):
        return self._test_windows_expanduser(userprofile=os.path.join(u"C:", u"\\Documents and Settings\\\u0100"))

    def test_disk_stats(self):
        avail = fileutil.get_available_space('.', 2**14)
        if avail == 0:
            raise unittest.SkipTest("This test will spuriously fail there is no disk space left.")

        disk = fileutil.get_disk_stats('.', 2**13)
        self.failUnless(disk['total'] > 0, disk['total'])
        # we tolerate used==0 for a Travis-CI bug, see #2290
        self.failUnless(disk['used'] >= 0, disk['used'])
        self.failUnless(disk['free_for_root'] > 0, disk['free_for_root'])
        self.failUnless(disk['free_for_nonroot'] > 0, disk['free_for_nonroot'])
        self.failUnless(disk['avail'] > 0, disk['avail'])

    def test_disk_stats_avail_nonnegative(self):
        # This test will spuriously fail if you have more than 2^128
        # bytes of available space on your filesystem.
        disk = fileutil.get_disk_stats('.', 2**128)
        self.failUnlessEqual(disk['avail'], 0)

    def test_get_pathinfo(self):
        basedir = "util/FileUtil/test_get_pathinfo"
        fileutil.make_dirs(basedir)

        # create a directory
        self.mkdir(basedir, "a")
        dirinfo = fileutil.get_pathinfo(basedir)
        self.failUnlessTrue(dirinfo.isdir)
        self.failUnlessTrue(dirinfo.exists)
        self.failUnlessFalse(dirinfo.isfile)
        self.failUnlessFalse(dirinfo.islink)

        # create a file
        f = os.path.join(basedir, "1.txt")
        fileutil.write(f, b"a"*10)
        fileinfo = fileutil.get_pathinfo(f)
        self.failUnlessTrue(fileinfo.isfile)
        self.failUnlessTrue(fileinfo.exists)
        self.failUnlessFalse(fileinfo.isdir)
        self.failUnlessFalse(fileinfo.islink)
        self.failUnlessEqual(fileinfo.size, 10)

        # path at which nothing exists
        dnename = os.path.join(basedir, "doesnotexist")
        now_ns = fileutil.seconds_to_ns(time.time())
        dneinfo = fileutil.get_pathinfo(dnename, now_ns=now_ns)
        self.failUnlessFalse(dneinfo.exists)
        self.failUnlessFalse(dneinfo.isfile)
        self.failUnlessFalse(dneinfo.isdir)
        self.failUnlessFalse(dneinfo.islink)
        self.failUnlessEqual(dneinfo.size, None)
        self.failUnlessEqual(dneinfo.mtime_ns, now_ns)
        self.failUnlessEqual(dneinfo.ctime_ns, now_ns)

    def test_get_pathinfo_symlink(self):
        if not hasattr(os, 'symlink'):
            raise unittest.SkipTest("can't create symlinks on this platform")

        basedir = "util/FileUtil/test_get_pathinfo"
        fileutil.make_dirs(basedir)

        f = os.path.join(basedir, "1.txt")
        fileutil.write(f, b"a"*10)

        # create a symlink pointing to 1.txt
        slname = os.path.join(basedir, "linkto1.txt")
        os.symlink(f, slname)
        symlinkinfo = fileutil.get_pathinfo(slname)
        self.failUnlessTrue(symlinkinfo.islink)
        self.failUnlessTrue(symlinkinfo.exists)
        self.failUnlessFalse(symlinkinfo.isfile)
        self.failUnlessFalse(symlinkinfo.isdir)

    def test_encrypted_tempfile(self):
        f = EncryptedTemporaryFile()
        f.write(b"foobar")
        f.close()

    def test_write(self):
        """fileutil.write() can write both unicode and bytes."""
        path = self.mktemp()
        fileutil.write(path, b"abc")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"abc")
        fileutil.write(path, u"def \u1234")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), u"def \u1234".encode("utf-8"))


class PollMixinTests(unittest.TestCase):
    def setUp(self):
        self.pm = pollmixin.PollMixin()

    def test_PollMixin_True(self):
        d = self.pm.poll(check_f=lambda : True,
                         pollinterval=0.1)
        return d

    def test_PollMixin_False_then_True(self):
        i = iter([False, True])
        d = self.pm.poll(check_f=lambda: next(i),
                         pollinterval=0.1)
        return d

    def test_timeout(self):
        d = self.pm.poll(check_f=lambda: False,
                         pollinterval=0.01,
                         timeout=1)
        def _suc(res):
            self.fail("poll should have failed, not returned %s" % (res,))
        def _err(f):
            f.trap(pollmixin.TimeoutError)
            return None # success
        d.addCallbacks(_suc, _err)
        return d


ctr = [0]
class EqButNotIs(object):
    def __init__(self, x):
        self.x = x
        self.hash = ctr[0]
        ctr[0] += 1
    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.x,)
    def __hash__(self):
        return self.hash
    def __le__(self, other):
        return self.x <= other
    def __lt__(self, other):
        return self.x < other
    def __ge__(self, other):
        return self.x >= other
    def __gt__(self, other):
        return self.x > other
    def __ne__(self, other):
        return self.x != other
    def __eq__(self, other):
        return self.x == other


class YAML(unittest.TestCase):
    def test_convert(self):
        """
        Unicode and (ASCII) native strings get roundtripped to Unicode strings.
        """
        data = yaml.safe_dump(
            [six.ensure_str("str"), u"unicode", u"\u1234nicode"]
        )
        back = yamlutil.safe_load(data)
        self.assertIsInstance(back[0], str)
        self.assertIsInstance(back[1], str)
        self.assertIsInstance(back[2], str)


class JSONBytes(unittest.TestCase):
    """Tests for jsonbytes module."""

    def test_encode_bytes(self):
        """jsonbytes.dumps() encodes bytes.

        Bytes are presumed to be UTF-8 encoded.
        """
        snowman = u"def\N{SNOWMAN}\uFF00"
        data = {
            b"hello": [1, b"cd", {b"abc": [123, snowman.encode("utf-8")]}],
        }
        expected = {
            u"hello": [1, u"cd", {u"abc": [123, snowman]}],
        }
        # Bytes get passed through as if they were UTF-8 Unicode:
        encoded = jsonbytes.dumps(data)
        self.assertEqual(json.loads(encoded), expected)
        self.assertEqual(jsonbytes.loads(encoded), expected)

    def test_encode_unicode(self):
        """jsonbytes.dumps() encodes Unicode string as usual."""
        expected = {
            u"hello": [1, u"cd"],
        }
        encoded = jsonbytes.dumps(expected)
        self.assertEqual(json.loads(encoded), expected)

    def test_dumps_bytes(self):
        """jsonbytes.dumps_bytes always returns bytes."""
        x = {u"def\N{SNOWMAN}\uFF00": 123}
        encoded = jsonbytes.dumps_bytes(x)
        self.assertIsInstance(encoded, bytes)
        self.assertEqual(json.loads(encoded), x)

    def test_any_bytes_unsupported_by_default(self):
        """By default non-UTF-8 bytes raise error."""
        bytestring = b"abc\xff\x00"
        with self.assertRaises(UnicodeDecodeError):
            jsonbytes.dumps(bytestring)
        with self.assertRaises(UnicodeDecodeError):
            jsonbytes.dumps_bytes(bytestring)
        with self.assertRaises(UnicodeDecodeError):
            json.dumps(bytestring, cls=jsonbytes.UTF8BytesJSONEncoder)

    def test_any_bytes(self):
        """If any_bytes is True, non-UTF-8 bytes don't break encoding."""
        bytestring = b"abc\xff\xff123"
        o = {bytestring: bytestring}
        expected = {"abc\\xff\\xff123": "abc\\xff\\xff123"}
        self.assertEqual(
            json.loads(jsonbytes.dumps(o, any_bytes=True)),
            expected,
        )
        self.assertEqual(
            json.loads(json.dumps(
                o, cls=jsonbytes.AnyBytesJSONEncoder)),
            expected,
        )
        self.assertEqual(
            json.loads(jsonbytes.dumps(o, any_bytes=True)),
            expected
        )

    def test_dumps_bytes_unicode_separators(self):
        """Unicode separators don't prevent the result from being bytes."""
        result = jsonbytes.dumps_bytes([1, 2], separators=(u',', u':'))
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, b"[1,2]")



class FakeGetVersion(object):
    """Emulate an object with a get_version."""

    def __init__(self, result):
        self.result = result

    def remote_get_version(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RrefUtilTests(unittest.TestCase):
    """Tests for rrefutil."""

    def test_version_returned(self):
        """If get_version() succeeded, it is set on the rref."""
        rref = LocalWrapper(FakeGetVersion(12345), fireNow)
        result = self.successResultOf(
            rrefutil.add_version_to_remote_reference(rref, "default")
        )
        self.assertEqual(result.version, 12345)
        self.assertIdentical(result, rref)

    def test_exceptions(self):
        """If get_version() failed, default version is set on the rref."""
        for exception in (Violation(), RemoteException(ValueError())):
            rref = LocalWrapper(FakeGetVersion(exception), fireNow)
            result = self.successResultOf(
                rrefutil.add_version_to_remote_reference(rref, "Default")
            )
            self.assertEqual(result.version, "Default")
            self.assertIdentical(result, rref)


class CPUThreadPool(unittest.TestCase):
    """Tests for cputhreadpool."""

    async def test_runs_in_thread(self):
        """The given function runs in a thread."""
        def f(*args, **kwargs):
            return current_thread(), args, kwargs

        this_thread = current_thread().ident
        thread, args, kwargs = await defer_to_thread(f, 1, 3, key=4, value=5)

        # The task ran in a different thread:
        self.assertNotEqual(thread.ident, this_thread)
        self.assertEqual(args, (1, 3))
        self.assertEqual(kwargs, {"key": 4, "value": 5})

    async def test_when_disabled_runs_in_same_thread(self):
        """
        If the CPU thread pool is disabled, the given function runs in the
        current thread.
        """
        disable_thread_pool_for_test(self)
        def f(*args, **kwargs):
            return current_thread().ident, args, kwargs

        this_thread = current_thread().ident
        thread, args, kwargs = await defer_to_thread(f, 1, 3, key=4, value=5)

        self.assertEqual(thread, this_thread)
        self.assertEqual(args, (1, 3))
        self.assertEqual(kwargs, {"key": 4, "value": 5})
