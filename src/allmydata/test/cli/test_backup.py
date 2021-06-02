"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os.path
from six.moves import cStringIO as StringIO
from datetime import timedelta
import re

from twisted.trial import unittest
from twisted.python.monkey import MonkeyPatcher

from allmydata.util import fileutil
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import get_io_encoding, unicode_to_argv
from allmydata.util.namespace import Namespace
from allmydata.scripts import cli, backupdb
from ..common_util import StallMixin
from ..no_network import GridTestMixin
from .common import (
    CLITestMixin,
    parse_options,
)


def _unsupported(what):
    return "{} are not supported by Python on this platform.".format(what)


class Backup(GridTestMixin, CLITestMixin, StallMixin, unittest.TestCase):

    def writeto(self, path, data):
        full_path = os.path.join(self.basedir, "home", path)
        fileutil.make_dirs(os.path.dirname(full_path))
        fileutil.write(full_path, data)

    def count_output(self, out):
        mo = re.search(r"(\d)+ files uploaded \((\d+) reused\), "
                        "(\d)+ files skipped, "
                        "(\d+) directories created \((\d+) reused\), "
                        "(\d+) directories skipped", out)
        return [int(s) for s in mo.groups()]

    def count_output2(self, out):
        mo = re.search(r"(\d)+ files checked, (\d+) directories checked", out)
        return [int(s) for s in mo.groups()]

    def progress_output(self, out):
        def parse_timedelta(h, m, s):
            return timedelta(int(h), int(m), int(s))
        mos = re.findall(
            r"Backing up (\d)+/(\d)+\.\.\. (\d+)h (\d+)m (\d+)s elapsed\.\.\.",
            out,
        )
        return list(
            (int(progress), int(total), parse_timedelta(h, m, s))
            for (progress, total, h, m, s)
            in mos
        )

    def test_backup(self):
        self.basedir = "cli/Backup/backup"
        self.set_up_grid(oneshare=True)

        # is the backupdb available? If so, we test that a second backup does
        # not create new directories.
        hush = StringIO()
        bdb = backupdb.get_backupdb(os.path.join(self.basedir, "dbtest"),
                                    hush)
        self.failUnless(bdb)

        # create a small local directory with a couple of files
        source = os.path.join(self.basedir, "home")
        fileutil.make_dirs(os.path.join(source, "empty"))
        self.writeto("parent/subdir/foo.txt", "foo")
        self.writeto("parent/subdir/bar.txt", "bar\n" * 1000)
        self.writeto("parent/blah.txt", "blah")

        def do_backup(verbose=False):
            cmd = ["backup"]
            if verbose:
                cmd.append("--verbose")
            cmd.append(source)
            cmd.append("tahoe:backups")
            return self.do_cli(*cmd)

        d = self.do_cli("create-alias", "tahoe")

        d.addCallback(lambda res: do_backup(True))
        def _check0(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0,  err)
            self.failUnlessReallyEqual(rc, 0)
            (
                files_uploaded,
                files_reused,
                files_skipped,
                directories_created,
                directories_reused,
                directories_skipped,
            ) = self.count_output(out)
            # foo.txt, bar.txt, blah.txt
            self.failUnlessReallyEqual(files_uploaded, 3)
            self.failUnlessReallyEqual(files_reused, 0)
            self.failUnlessReallyEqual(files_skipped, 0)
            # empty, home, home/parent, home/parent/subdir
            self.failUnlessReallyEqual(directories_created, 4)
            self.failUnlessReallyEqual(directories_reused, 0)
            self.failUnlessReallyEqual(directories_skipped, 0)

            # This is the first-upload scenario so there should have been
            # nothing to check.
            (files_checked, directories_checked) = self.count_output2(out)
            self.failUnlessReallyEqual(files_checked, 0)
            self.failUnlessReallyEqual(directories_checked, 0)

            progress = self.progress_output(out)
            for left, right in zip(progress[:-1], progress[1:]):
                # Progress as measured by file count should progress
                # monotonically.
                self.assertTrue(
                    left[0] < right[0],
                    "Failed: {} < {}".format(left[0], right[0]),
                )

                # Total work to do should remain the same.
                self.assertEqual(left[1], right[1])

                # Amount of elapsed time should only go up.  Allow it to
                # remain the same to account for resolution of the report.
                self.assertTrue(
                    left[2] <= right[2],
                    "Failed: {} <= {}".format(left[2], right[2]),
                )

            for element in progress:
                # Can't have more progress than the total.
                self.assertTrue(
                    element[0] <= element[1],
                    "Failed: {} <= {}".format(element[0], element[1]),
                )


        d.addCallback(_check0)

        d.addCallback(lambda res: self.do_cli("ls", "--uri", "tahoe:backups"))
        def _check1(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0,  err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.split("\n")
            children = dict([line.split() for line in lines if line])
            latest_uri = children["Latest"]
            self.failUnless(latest_uri.startswith("URI:DIR2-CHK:"), latest_uri)
            childnames = list(children.keys())
            self.failUnlessReallyEqual(sorted(childnames), ["Archives", "Latest"])
        d.addCallback(_check1)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Latest"))
        def _check2(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0,  err)
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(sorted(out.split()), ["empty", "parent"])
        d.addCallback(_check2)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Latest/empty"))
        def _check2a(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0,  err)
            self.failUnlessReallyEqual(rc, 0)
            self.assertFalse(out.strip())
        d.addCallback(_check2a)
        d.addCallback(lambda res: self.do_cli("get", "tahoe:backups/Latest/parent/subdir/foo.txt"))
        def _check3(args):
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            self.assertEqual(out, "foo")
        d.addCallback(_check3)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Archives"))
        def _check4(args):
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            self.old_archives = out.split()
            self.failUnlessReallyEqual(len(self.old_archives), 1)
        d.addCallback(_check4)


        d.addCallback(self.stall, 1.1)
        d.addCallback(lambda res: do_backup())
        def _check4a(args):
            # second backup should reuse everything, if the backupdb is
            # available
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            fu, fr, fs, dc, dr, ds = self.count_output(out)
            # foo.txt, bar.txt, blah.txt
            self.failUnlessReallyEqual(fu, 0)
            self.failUnlessReallyEqual(fr, 3)
            self.failUnlessReallyEqual(fs, 0)
            # empty, home, home/parent, home/parent/subdir
            self.failUnlessReallyEqual(dc, 0)
            self.failUnlessReallyEqual(dr, 4)
            self.failUnlessReallyEqual(ds, 0)
        d.addCallback(_check4a)

        # sneak into the backupdb, crank back the "last checked"
        # timestamp to force a check on all files
        def _reset_last_checked(res):
            dbfile = self.get_client_config().get_private_path("backupdb.sqlite")
            self.failUnless(os.path.exists(dbfile), dbfile)
            bdb = backupdb.get_backupdb(dbfile)
            bdb.cursor.execute("UPDATE last_upload SET last_checked=0")
            bdb.cursor.execute("UPDATE directories SET last_checked=0")
            bdb.connection.commit()

        d.addCallback(_reset_last_checked)

        d.addCallback(self.stall, 1.1)
        d.addCallback(lambda res: do_backup(verbose=True))
        def _check4b(args):
            # we should check all files, and re-use all of them. None of
            # the directories should have been changed, so we should
            # re-use all of them too.
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            fu, fr, fs, dc, dr, ds = self.count_output(out)
            fchecked, dchecked = self.count_output2(out)
            self.failUnlessReallyEqual(fchecked, 3)
            self.failUnlessReallyEqual(fu, 0)
            self.failUnlessReallyEqual(fr, 3)
            self.failUnlessReallyEqual(fs, 0)
            self.failUnlessReallyEqual(dchecked, 4)
            self.failUnlessReallyEqual(dc, 0)
            self.failUnlessReallyEqual(dr, 4)
            self.failUnlessReallyEqual(ds, 0)
        d.addCallback(_check4b)

        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Archives"))
        def _check5(args):
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            self.new_archives = out.split()
            self.failUnlessReallyEqual(len(self.new_archives), 3, out)
            # the original backup should still be the oldest (i.e. sorts
            # alphabetically towards the beginning)
            self.failUnlessReallyEqual(sorted(self.new_archives)[0],
                                 self.old_archives[0])
        d.addCallback(_check5)

        d.addCallback(self.stall, 1.1)
        def _modify(res):
            self.writeto("parent/subdir/foo.txt", "FOOF!")
            # and turn a file into a directory
            os.unlink(os.path.join(source, "parent/blah.txt"))
            os.mkdir(os.path.join(source, "parent/blah.txt"))
            self.writeto("parent/blah.txt/surprise file", "surprise")
            self.writeto("parent/blah.txt/surprisedir/subfile", "surprise")
            # turn a directory into a file
            os.rmdir(os.path.join(source, "empty"))
            self.writeto("empty", "imagine nothing being here")
            return do_backup()
        d.addCallback(_modify)
        def _check5a(args):
            # second backup should reuse bar.txt (if backupdb is available),
            # and upload the rest. None of the directories can be reused.
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            fu, fr, fs, dc, dr, ds = self.count_output(out)
            # new foo.txt, surprise file, subfile, empty
            self.failUnlessReallyEqual(fu, 4)
            # old bar.txt
            self.failUnlessReallyEqual(fr, 1)
            self.failUnlessReallyEqual(fs, 0)
            # home, parent, subdir, blah.txt, surprisedir
            self.failUnlessReallyEqual(dc, 5)
            self.failUnlessReallyEqual(dr, 0)
            self.failUnlessReallyEqual(ds, 0)
        d.addCallback(_check5a)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Archives"))
        def _check6(args):
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            self.new_archives = out.split()
            self.failUnlessReallyEqual(len(self.new_archives), 4)
            self.failUnlessReallyEqual(sorted(self.new_archives)[0],
                                 self.old_archives[0])
        d.addCallback(_check6)
        d.addCallback(lambda res: self.do_cli("get", "tahoe:backups/Latest/parent/subdir/foo.txt"))
        def _check7(args):
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            self.assertEqual(out, "FOOF!")
            # the old snapshot should not be modified
            return self.do_cli("get", "tahoe:backups/Archives/%s/parent/subdir/foo.txt" % self.old_archives[0])
        d.addCallback(_check7)
        def _check8(args):
            (rc, out, err) = args
            self.assertFalse(err)
            self.failUnlessReallyEqual(rc, 0)
            self.assertEqual(out, "foo")
        d.addCallback(_check8)

        return d

    def _check_filtering(self, filtered, all, included, excluded):
        filtered = set(filtered)
        all = set(all)
        included = set(included)
        excluded = set(excluded)
        self.failUnlessReallyEqual(filtered, included)
        self.failUnlessReallyEqual(all.difference(filtered), excluded)

    def test_exclude_options(self):
        root_listdir = (u'lib.a', u'_darcs', u'subdir', u'nice_doc.lyx')
        subdir_listdir = (u'another_doc.lyx', u'run_snake_run.py', u'CVS', u'.svn', u'_darcs')
        basedir = "cli/Backup/exclude_options"
        fileutil.make_dirs(basedir)
        nodeurl_path = os.path.join(basedir, 'node.url')
        fileutil.write(nodeurl_path, 'http://example.net:2357/')
        def parse(args): return parse_options(basedir, "backup", args)

        # test simple exclude
        backup_options = parse(['--exclude', '*lyx', 'from', 'to'])
        filtered = list(backup_options.filter_listdir(root_listdir))
        self._check_filtering(filtered, root_listdir, (u'lib.a', u'_darcs', u'subdir'),
                              (u'nice_doc.lyx',))
        # multiple exclude
        backup_options = parse(['--exclude', '*lyx', '--exclude', 'lib.?', 'from', 'to'])
        filtered = list(backup_options.filter_listdir(root_listdir))
        self._check_filtering(filtered, root_listdir, (u'_darcs', u'subdir'),
                              (u'nice_doc.lyx', u'lib.a'))
        # vcs metadata exclusion
        backup_options = parse(['--exclude-vcs', 'from', 'to'])
        filtered = list(backup_options.filter_listdir(subdir_listdir))
        self._check_filtering(filtered, subdir_listdir, (u'another_doc.lyx', u'run_snake_run.py',),
                              (u'CVS', u'.svn', u'_darcs'))
        # read exclude patterns from file
        exclusion_string = "_darcs\n*py\n.svn"
        excl_filepath = os.path.join(basedir, 'exclusion')
        fileutil.write(excl_filepath, exclusion_string)
        backup_options = parse(['--exclude-from-utf-8', excl_filepath, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(subdir_listdir))
        self._check_filtering(filtered, subdir_listdir, (u'another_doc.lyx', u'CVS'),
                              (u'.svn', u'_darcs', u'run_snake_run.py'))
        # test BackupConfigurationError
        self.failUnlessRaises(cli.BackupConfigurationError,
                              parse,
                              ['--exclude-from-utf-8', excl_filepath + '.no', 'from', 'to'])

        # test that an iterator works too
        backup_options = parse(['--exclude', '*lyx', 'from', 'to'])
        filtered = list(backup_options.filter_listdir(iter(root_listdir)))
        self._check_filtering(filtered, root_listdir, (u'lib.a', u'_darcs', u'subdir'),
                              (u'nice_doc.lyx',))

    def test_exclude_options_unicode(self):
        nice_doc = u"nice_d\u00F8c.lyx"
        try:
            doc_pattern_arg_unicode = doc_pattern_arg = u"*d\u00F8c*"
            if PY2:
                doc_pattern_arg = doc_pattern_arg.encode(get_io_encoding())
        except UnicodeEncodeError:
            raise unittest.SkipTest("A non-ASCII command argument could not be encoded on this platform.")

        root_listdir = (u'lib.a', u'_darcs', u'subdir', nice_doc)
        basedir = "cli/Backup/exclude_options_unicode"
        fileutil.make_dirs(basedir)
        nodeurl_path = os.path.join(basedir, 'node.url')
        fileutil.write(nodeurl_path, 'http://example.net:2357/')
        def parse(args): return parse_options(basedir, "backup", args)

        # test simple exclude
        backup_options = parse(['--exclude', doc_pattern_arg, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(root_listdir))
        self._check_filtering(filtered, root_listdir, (u'lib.a', u'_darcs', u'subdir'),
                              (nice_doc,))
        # multiple exclude
        backup_options = parse(['--exclude', doc_pattern_arg, '--exclude', 'lib.?', 'from', 'to'])
        filtered = list(backup_options.filter_listdir(root_listdir))
        self._check_filtering(filtered, root_listdir, (u'_darcs', u'subdir'),
                             (nice_doc, u'lib.a'))
        # read exclude patterns from file
        exclusion_string = (doc_pattern_arg_unicode + "\nlib.?").encode("utf-8")
        excl_filepath = os.path.join(basedir, 'exclusion')
        fileutil.write(excl_filepath, exclusion_string)
        backup_options = parse(['--exclude-from-utf-8', excl_filepath, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(root_listdir))
        self._check_filtering(filtered, root_listdir, (u'_darcs', u'subdir'),
                             (nice_doc, u'lib.a'))

        # test that an iterator works too
        backup_options = parse(['--exclude', doc_pattern_arg, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(iter(root_listdir)))
        self._check_filtering(filtered, root_listdir, (u'lib.a', u'_darcs', u'subdir'),
                              (nice_doc,))

    def test_exclude_from_tilde_expansion(self):
        basedir = "cli/Backup/exclude_from_tilde_expansion"
        fileutil.make_dirs(basedir)
        nodeurl_path = os.path.join(basedir, 'node.url')
        fileutil.write(nodeurl_path, 'http://example.net:2357/')

        # ensure that tilde expansion is performed on exclude-from argument
        exclude_file = u'~/.tahoe/excludes.dummy'

        ns = Namespace()
        ns.called = False
        original_open = open
        def call_file(name, *args, **kwargs):
            if name.endswith("excludes.dummy"):
                ns.called = True
                self.failUnlessEqual(name, abspath_expanduser_unicode(exclude_file))
                return StringIO()
            else:
                return original_open(name, *args, **kwargs)

        if PY2:
            from allmydata.scripts import cli as module_to_patch
        else:
            import builtins as module_to_patch
        patcher = MonkeyPatcher((module_to_patch, 'open', call_file))
        patcher.runWithPatches(parse_options, basedir, "backup", ['--exclude-from-utf-8', unicode_to_argv(exclude_file), 'from', 'to'])
        self.failUnless(ns.called)

    def test_ignore_symlinks(self):
        """
        A symlink encountered in the backed-up directory is skipped with a
        warning.
        """
        if not hasattr(os, 'symlink'):
            raise unittest.SkipTest(_unsupported("Symlinks"))

        def make_symlink(path):
            self.writeto("foo.txt", "foo")
            os.symlink(
                os.path.join(
                    os.path.dirname(path),
                    "foo.txt",
                ),
                path,
            )

        return self._ignore_something_test(u"Symlink", make_symlink)

    def test_ignore_fifo(self):
        """
        A FIFO encountered in the backed-up directory is skipped with a warning.
        """
        if getattr(os, "mkfifo", None) is None:
            raise unittest.SkipTest(_unsupported("FIFOs"))

        def make_fifo(path):
            # Create the thing to ignore
            os.makedirs(os.path.dirname(path))
            os.mkfifo(path)
            # Also create anothing thing so the counts end up the same as
            # those in the symlink test and it's easier to re-use the testing
            # helper.
            self.writeto("count-dummy.txt", "foo")

        return self._ignore_something_test(u"special", make_fifo)

    def _ignore_something_test(self, kind_of_thing, make_something_to_ignore):
        """
        Assert that when a a certain kind of file is encountered in the backed-up
        directory a warning that it is not supported is emitted and the backup
        proceeds to other files with no other error.

        :param unicode kind_of_thing: The name of the kind of file that will
            be ignored.  This is expected to appear in the warning.

        :param make_something_to_ignore: A one-argument callable which creates
            the file that is expected to be ignored.  It is called with the
            path at which the file must be created.

        :return Deferred: A ``Deferred`` that fires when the assertion has
            been made.
        """
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid(oneshare=True)

        source = os.path.join(self.basedir, "home")
        ignored_path = os.path.join(source, "foo2.txt")
        make_something_to_ignore(ignored_path)

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("backup", "--verbose", source, "tahoe:test"))

        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 2)
            self.assertIn(
                "WARNING: cannot backup {} ".format(kind_of_thing.lower()),
                err,
            )
            self.assertIn(ignored_path, err)

            fu, fr, fs, dc, dr, ds = self.count_output(out)
            # foo.txt
            self.failUnlessReallyEqual(fu, 1)
            self.failUnlessReallyEqual(fr, 0)
            # foo2.txt
            self.failUnlessReallyEqual(fs, 1)
            # home
            self.failUnlessReallyEqual(dc, 1)
            self.failUnlessReallyEqual(dr, 0)
            self.failUnlessReallyEqual(ds, 0)

        d.addCallback(_check)
        return d

    def test_ignore_unreadable_file(self):
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid(oneshare=True)

        source = os.path.join(self.basedir, "home")
        self.writeto("foo.txt", "foo")
        os.chmod(os.path.join(source, "foo.txt"), 0000)

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("backup", source, "tahoe:test"))

        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 2)
            self.failUnlessReallyEqual(err, "WARNING: permission denied on file %s\n" % os.path.join(source, "foo.txt"))

            fu, fr, fs, dc, dr, ds = self.count_output(out)
            self.failUnlessReallyEqual(fu, 0)
            self.failUnlessReallyEqual(fr, 0)
            # foo.txt
            self.failUnlessReallyEqual(fs, 1)
            # home
            self.failUnlessReallyEqual(dc, 1)
            self.failUnlessReallyEqual(dr, 0)
            self.failUnlessReallyEqual(ds, 0)
        d.addCallback(_check)

        # This is necessary for the temp files to be correctly removed
        def _cleanup(self):
            os.chmod(os.path.join(source, "foo.txt"), 0o644)
        d.addCallback(_cleanup)
        d.addErrback(_cleanup)

        return d

    def test_ignore_unreadable_directory(self):
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid(oneshare=True)

        source = os.path.join(self.basedir, "home")
        os.mkdir(source)
        os.mkdir(os.path.join(source, "test"))
        os.chmod(os.path.join(source, "test"), 0000)

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("backup", source, "tahoe:test"))

        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 2)
            self.failUnlessReallyEqual(err, "WARNING: permission denied on directory %s\n" % os.path.join(source, "test"))

            fu, fr, fs, dc, dr, ds = self.count_output(out)
            self.failUnlessReallyEqual(fu, 0)
            self.failUnlessReallyEqual(fr, 0)
            self.failUnlessReallyEqual(fs, 0)
            # home, test
            self.failUnlessReallyEqual(dc, 2)
            self.failUnlessReallyEqual(dr, 0)
            # test
            self.failUnlessReallyEqual(ds, 1)
        d.addCallback(_check)

        # This is necessary for the temp files to be correctly removed
        def _cleanup(self):
            os.chmod(os.path.join(source, "test"), 0o655)
        d.addCallback(_cleanup)
        d.addErrback(_cleanup)
        return d

    def test_backup_without_alias(self):
        # 'tahoe backup' should output a sensible error message when invoked
        # without an alias instead of a stack trace.
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid(oneshare=True)
        source = os.path.join(self.basedir, "file1")
        d = self.do_cli('backup', source, source)
        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.assertEqual(len(out), 0)
        d.addCallback(_check)
        return d

    def test_backup_with_nonexistent_alias(self):
        # 'tahoe backup' should output a sensible error message when invoked
        # with a nonexistent alias.
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid(oneshare=True)
        source = os.path.join(self.basedir, "file1")
        d = self.do_cli("backup", source, "nonexistent:" + source)
        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
            self.assertEqual(len(out), 0)
        d.addCallback(_check)
        return d
