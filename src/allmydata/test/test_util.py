from __future__ import print_function

import six
import os, time, sys
import yaml

from six.moves import StringIO
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python.failure import Failure

from allmydata.util import idlib, mathutil
from allmydata.util import fileutil
from allmydata.util import limiter, pollmixin
from allmydata.util import statistics, dictutil, yamlutil
from allmydata.util import log as tahoe_log
from allmydata.util.fileutil import EncryptedTemporaryFile
from allmydata.test.common_util import ReallyEqualMixin

if six.PY3:
    long = int


class IDLib(unittest.TestCase):
    def test_nodeid_b2a(self):
        self.failUnlessEqual(idlib.nodeid_b2a("\x00"*20), "a"*32)


class MyList(list):
    pass

class Math(unittest.TestCase):
    def test_round_sigfigs(self):
        f = mathutil.round_sigfigs
        self.failUnlessEqual(f(22.0/3, 4), 7.3330000000000002)

class Statistics(unittest.TestCase):
    def should_assert(self, msg, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
            self.fail(msg)
        except AssertionError:
            pass

    def failUnlessListEqual(self, a, b, msg = None):
        self.failUnlessEqual(len(a), len(b))
        for i in range(len(a)):
            self.failUnlessEqual(a[i], b[i], msg)

    def failUnlessListAlmostEqual(self, a, b, places = 7, msg = None):
        self.failUnlessEqual(len(a), len(b))
        for i in range(len(a)):
            self.failUnlessAlmostEqual(a[i], b[i], places, msg)

    def test_binomial_coeff(self):
        f = statistics.binomial_coeff
        self.failUnlessEqual(f(20, 0), 1)
        self.failUnlessEqual(f(20, 1), 20)
        self.failUnlessEqual(f(20, 2), 190)
        self.failUnlessEqual(f(20, 8), f(20, 12))
        self.should_assert("Should assert if n < k", f, 2, 3)

    def test_binomial_distribution_pmf(self):
        f = statistics.binomial_distribution_pmf

        pmf_comp = f(2, .1)
        pmf_stat = [0.81, 0.18, 0.01]
        self.failUnlessListAlmostEqual(pmf_comp, pmf_stat)

        # Summing across a PMF should give the total probability 1
        self.failUnlessAlmostEqual(sum(pmf_comp), 1)
        self.should_assert("Should assert if not 0<=p<=1", f, 1, -1)
        self.should_assert("Should assert if n < 1", f, 0, .1)

        out = StringIO()
        statistics.print_pmf(pmf_comp, out=out)
        lines = out.getvalue().splitlines()
        self.failUnlessEqual(lines[0], "i=0: 0.81")
        self.failUnlessEqual(lines[1], "i=1: 0.18")
        self.failUnlessEqual(lines[2], "i=2: 0.01")

    def test_survival_pmf(self):
        f = statistics.survival_pmf
        # Cross-check binomial-distribution method against convolution
        # method.
        p_list = [.9999] * 100 + [.99] * 50 + [.8] * 20
        pmf1 = statistics.survival_pmf_via_conv(p_list)
        pmf2 = statistics.survival_pmf_via_bd(p_list)
        self.failUnlessListAlmostEqual(pmf1, pmf2)
        self.failUnlessTrue(statistics.valid_pmf(pmf1))
        self.should_assert("Should assert if p_i > 1", f, [1.1]);
        self.should_assert("Should assert if p_i < 0", f, [-.1]);

    def test_repair_count_pmf(self):
        survival_pmf = statistics.binomial_distribution_pmf(5, .9)
        repair_pmf = statistics.repair_count_pmf(survival_pmf, 3)
        # repair_pmf[0] == sum(survival_pmf[0,1,2,5])
        # repair_pmf[1] == survival_pmf[4]
        # repair_pmf[2] = survival_pmf[3]
        self.failUnlessListAlmostEqual(repair_pmf,
                                       [0.00001 + 0.00045 + 0.0081 + 0.59049,
                                        .32805,
                                        .0729,
                                        0, 0, 0])

    def test_repair_cost(self):
        survival_pmf = statistics.binomial_distribution_pmf(5, .9)
        bwcost = statistics.bandwidth_cost_function
        cost = statistics.mean_repair_cost(bwcost, 1000,
                                           survival_pmf, 3, ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 558.90)
        cost = statistics.mean_repair_cost(bwcost, 1000,
                                           survival_pmf, 3, ul_dl_ratio=8.0)
        self.failUnlessAlmostEqual(cost, 1664.55)

        # I haven't manually checked the math beyond here -warner
        cost = statistics.eternal_repair_cost(bwcost, 1000,
                                              survival_pmf, 3,
                                              discount_rate=0, ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 65292.056074766246)
        cost = statistics.eternal_repair_cost(bwcost, 1000,
                                              survival_pmf, 3,
                                              discount_rate=0.05,
                                              ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 9133.6097158191551)

    def test_convolve(self):
        f = statistics.convolve
        v1 = [ 1, 2, 3 ]
        v2 = [ 4, 5, 6 ]
        v3 = [ 7, 8 ]
        v1v2result = [ 4, 13, 28, 27, 18 ]
        # Convolution is commutative
        r1 = f(v1, v2)
        r2 = f(v2, v1)
        self.failUnlessListEqual(r1, r2, "Convolution should be commutative")
        self.failUnlessListEqual(r1, v1v2result, "Didn't match known result")
        # Convolution is associative
        r1 = f(f(v1, v2), v3)
        r2 = f(v1, f(v2, v3))
        self.failUnlessListEqual(r1, r2, "Convolution should be associative")
        # Convolution is distributive
        r1 = f(v3, [ a + b for a, b in zip(v1, v2) ])
        tmp1 = f(v3, v1)
        tmp2 = f(v3, v2)
        r2 = [ a + b for a, b in zip(tmp1, tmp2) ]
        self.failUnlessListEqual(r1, r2, "Convolution should be distributive")
        # Convolution is scalar multiplication associative
        tmp1 = f(v1, v2)
        r1 = [ a * 4 for a in tmp1 ]
        tmp2 = [ a * 4 for a in v1 ]
        r2 = f(tmp2, v2)
        self.failUnlessListEqual(r1, r2, "Convolution should be scalar multiplication associative")

    def test_find_k(self):
        f = statistics.find_k
        g = statistics.pr_file_loss
        plist = [.9] * 10 + [.8] * 10 # N=20
        t = .0001
        k = f(plist, t)
        self.failUnlessEqual(k, 10)
        self.failUnless(g(plist, k) < t)

    def test_pr_file_loss(self):
        f = statistics.pr_file_loss
        plist = [.5] * 10
        self.failUnlessEqual(f(plist, 3), .0546875)

    def test_pr_backup_file_loss(self):
        f = statistics.pr_backup_file_loss
        plist = [.5] * 10
        self.failUnlessEqual(f(plist, .5, 3), .02734375)


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
        fileutil.write_atomically(fn, "one")
        self.failUnlessEqual(fileutil.read(fn), "one")
        fileutil.write_atomically(fn, "two", mode="") # non-binary
        self.failUnlessEqual(fileutil.read(fn), "two")

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
        fileutil.write(dest_path,   "dest")
        self.failUnlessRaises(OSError, fileutil.rename_no_overwrite, source_path, dest_path)
        self.failUnlessEqual(fileutil.read(dest_path),   "dest")

        # when both exist
        fileutil.write(source_path, "source")
        self.failUnlessRaises(OSError, fileutil.rename_no_overwrite, source_path, dest_path)
        self.failUnlessEqual(fileutil.read(source_path), "source")
        self.failUnlessEqual(fileutil.read(dest_path),   "dest")

        # when only source exists
        os.remove(dest_path)
        fileutil.rename_no_overwrite(source_path, dest_path)
        self.failUnlessEqual(fileutil.read(dest_path), "source")
        self.failIf(os.path.exists(source_path))

    def test_replace_file(self):
        workdir = fileutil.abspath_expanduser_unicode(u"test_replace_file")
        fileutil.make_dirs(workdir)

        replaced_path    = os.path.join(workdir, "replaced")
        replacement_path = os.path.join(workdir, "replacement")

        # when none of the files exist
        self.failUnlessRaises(fileutil.ConflictError, fileutil.replace_file, replaced_path, replacement_path)

        # when only replaced exists
        fileutil.write(replaced_path,    "foo")
        self.failUnlessRaises(fileutil.ConflictError, fileutil.replace_file, replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), "foo")

        # when both replaced and replacement exist
        fileutil.write(replacement_path, "bar")
        fileutil.replace_file(replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), "bar")
        self.failIf(os.path.exists(replacement_path))

        # when only replacement exists
        os.remove(replaced_path)
        fileutil.write(replacement_path, "bar")
        fileutil.replace_file(replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), "bar")
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
        self.failUnlessRaises(AssertionError, fileutil.abspath_expanduser_unicode, "bytestring")

        saved_cwd = os.path.normpath(os.getcwdu())
        abspath_cwd = fileutil.abspath_expanduser_unicode(u".")
        abspath_cwd_notlong = fileutil.abspath_expanduser_unicode(u".", long_path=False)
        self.failUnless(isinstance(saved_cwd, unicode), saved_cwd)
        self.failUnless(isinstance(abspath_cwd, unicode), abspath_cwd)
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
                    self.failUnless(isinstance(uabspath, unicode), uabspath)

                    uabspath_notlong = fileutil.abspath_expanduser_unicode(upath, long_path=False)
                    self.failUnless(isinstance(uabspath_notlong, unicode), uabspath_notlong)
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

        fileutil.write(long_path, "test")
        self.failUnless(os.path.exists(long_path))
        self.failUnlessEqual(fileutil.read(long_path), "test")
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
        fileutil.write(f, "a"*10)
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
        fileutil.write(f, "a"*10)

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
        f.write("foobar")
        f.close()


class PollMixinTests(unittest.TestCase):
    def setUp(self):
        self.pm = pollmixin.PollMixin()

    def test_PollMixin_True(self):
        d = self.pm.poll(check_f=lambda : True,
                         pollinterval=0.1)
        return d

    def test_PollMixin_False_then_True(self):
        i = iter([False, True])
        d = self.pm.poll(check_f=i.next,
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


class Limiter(unittest.TestCase):

    def job(self, i, foo):
        self.calls.append( (i, foo) )
        self.simultaneous += 1
        self.peak_simultaneous = max(self.simultaneous, self.peak_simultaneous)
        d = defer.Deferred()
        def _done():
            self.simultaneous -= 1
            d.callback("done %d" % i)
        reactor.callLater(1.0, _done)
        return d

    def bad_job(self, i, foo):
        raise ValueError("bad_job %d" % i)

    def test_limiter(self):
        self.calls = []
        self.simultaneous = 0
        self.peak_simultaneous = 0
        l = limiter.ConcurrencyLimiter()
        dl = []
        for i in range(20):
            dl.append(l.add(self.job, i, foo=str(i)))
        d = defer.DeferredList(dl, fireOnOneErrback=True)
        def _done(res):
            self.failUnlessEqual(self.simultaneous, 0)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
        d.addCallback(_done)
        return d

    def test_errors(self):
        self.calls = []
        self.simultaneous = 0
        self.peak_simultaneous = 0
        l = limiter.ConcurrencyLimiter()
        dl = []
        for i in range(20):
            dl.append(l.add(self.job, i, foo=str(i)))
        d2 = l.add(self.bad_job, 21, "21")
        d = defer.DeferredList(dl, fireOnOneErrback=True)
        def _most_done(res):
            results = []
            for (success, result) in res:
                self.failUnlessEqual(success, True)
                results.append(result)
            results.sort()
            expected_results = ["done %d" % i for i in range(20)]
            expected_results.sort()
            self.failUnlessEqual(results, expected_results)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
            def _good(res):
                self.fail("should have failed, not got %s" % (res,))
            def _err(f):
                f.trap(ValueError)
                self.failUnless("bad_job 21" in str(f))
            d2.addCallbacks(_good, _err)
            return d2
        d.addCallback(_most_done)
        def _all_done(res):
            self.failUnlessEqual(self.simultaneous, 0)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
        d.addCallback(_all_done)
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

class DictUtil(unittest.TestCase):
    def test_dict_of_sets(self):
        ds = dictutil.DictOfSets()
        ds.add(1, "a")
        ds.add(2, "b")
        ds.add(2, "b")
        ds.add(2, "c")
        self.failUnlessEqual(ds[1], set(["a"]))
        self.failUnlessEqual(ds[2], set(["b", "c"]))
        ds.discard(3, "d") # should not raise an exception
        ds.discard(2, "b")
        self.failUnlessEqual(ds[2], set(["c"]))
        ds.discard(2, "c")
        self.failIf(2 in ds)

        ds.add(3, "f")
        ds2 = dictutil.DictOfSets()
        ds2.add(3, "f")
        ds2.add(3, "g")
        ds2.add(4, "h")
        ds.update(ds2)
        self.failUnlessEqual(ds[1], set(["a"]))
        self.failUnlessEqual(ds[3], set(["f", "g"]))
        self.failUnlessEqual(ds[4], set(["h"]))

    def test_auxdict(self):
        d = dictutil.AuxValueDict()
        # we put the serialized form in the auxdata
        d.set_with_aux("key", ("filecap", "metadata"), "serialized")

        self.failUnlessEqual(d.keys(), ["key"])
        self.failUnlessEqual(d["key"], ("filecap", "metadata"))
        self.failUnlessEqual(d.get_aux("key"), "serialized")
        def _get_missing(key):
            return d[key]
        self.failUnlessRaises(KeyError, _get_missing, "nonkey")
        self.failUnlessEqual(d.get("nonkey"), None)
        self.failUnlessEqual(d.get("nonkey", "nonvalue"), "nonvalue")
        self.failUnlessEqual(d.get_aux("nonkey"), None)
        self.failUnlessEqual(d.get_aux("nonkey", "nonvalue"), "nonvalue")

        d["key"] = ("filecap2", "metadata2")
        self.failUnlessEqual(d["key"], ("filecap2", "metadata2"))
        self.failUnlessEqual(d.get_aux("key"), None)

        d.set_with_aux("key2", "value2", "aux2")
        self.failUnlessEqual(sorted(d.keys()), ["key", "key2"])
        del d["key2"]
        self.failUnlessEqual(d.keys(), ["key"])
        self.failIf("key2" in d)
        self.failUnlessRaises(KeyError, _get_missing, "key2")
        self.failUnlessEqual(d.get("key2"), None)
        self.failUnlessEqual(d.get_aux("key2"), None)
        d["key2"] = "newvalue2"
        self.failUnlessEqual(d.get("key2"), "newvalue2")
        self.failUnlessEqual(d.get_aux("key2"), None)

        d = dictutil.AuxValueDict({1:2,3:4})
        self.failUnlessEqual(sorted(d.keys()), [1,3])
        self.failUnlessEqual(d[1], 2)
        self.failUnlessEqual(d.get_aux(1), None)

        d = dictutil.AuxValueDict([ (1,2), (3,4) ])
        self.failUnlessEqual(sorted(d.keys()), [1,3])
        self.failUnlessEqual(d[1], 2)
        self.failUnlessEqual(d.get_aux(1), None)

        d = dictutil.AuxValueDict(one=1, two=2)
        self.failUnlessEqual(sorted(d.keys()), ["one","two"])
        self.failUnlessEqual(d["one"], 1)
        self.failUnlessEqual(d.get_aux("one"), None)


class SampleError(Exception):
    pass

class Log(unittest.TestCase):
    def test_err(self):
        try:
            raise SampleError("simple sample")
        except:
            f = Failure()
        tahoe_log.err(format="intentional sample error",
                      failure=f, level=tahoe_log.OPERATIONAL, umid="wO9UoQ")
        self.flushLoggedErrors(SampleError)


class YAML(unittest.TestCase):
    def test_convert(self):
        data = yaml.safe_dump(["str", u"unicode", u"\u1234nicode"])
        back = yamlutil.safe_load(data)
        self.failUnlessEqual(type(back[0]), unicode)
        self.failUnlessEqual(type(back[1]), unicode)
        self.failUnlessEqual(type(back[2]), unicode)
