
import os.path
from cStringIO import StringIO
import re

from twisted.trial import unittest
from twisted.python.monkey import MonkeyPatcher

import __builtin__
from allmydata.util import fileutil
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import get_io_encoding, unicode_to_argv
from allmydata.util.namespace import Namespace
from allmydata.scripts import cli, backupdb
from .common_util import StallMixin
from .no_network import GridTestMixin
from .test_cli import CLITestMixin, parse_options

timeout = 480 # deep_check takes 360s on Zandr's linksys box, others take > 240s

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

    def test_backup(self):
        self.basedir = "cli/Backup/backup"
        self.set_up_grid()

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

        d.addCallback(lambda res: do_backup())
        def _check0((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            fu, fr, fs, dc, dr, ds = self.count_output(out)
            # foo.txt, bar.txt, blah.txt
            self.failUnlessReallyEqual(fu, 3)
            self.failUnlessReallyEqual(fr, 0)
            self.failUnlessReallyEqual(fs, 0)
            # empty, home, home/parent, home/parent/subdir
            self.failUnlessReallyEqual(dc, 4)
            self.failUnlessReallyEqual(dr, 0)
            self.failUnlessReallyEqual(ds, 0)
        d.addCallback(_check0)

        d.addCallback(lambda res: self.do_cli("ls", "--uri", "tahoe:backups"))
        def _check1((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            lines = out.split("\n")
            children = dict([line.split() for line in lines if line])
            latest_uri = children["Latest"]
            self.failUnless(latest_uri.startswith("URI:DIR2-CHK:"), latest_uri)
            childnames = children.keys()
            self.failUnlessReallyEqual(sorted(childnames), ["Archives", "Latest"])
        d.addCallback(_check1)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Latest"))
        def _check2((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(sorted(out.split()), ["empty", "parent"])
        d.addCallback(_check2)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Latest/empty"))
        def _check2a((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(out.strip(), "")
        d.addCallback(_check2a)
        d.addCallback(lambda res: self.do_cli("get", "tahoe:backups/Latest/parent/subdir/foo.txt"))
        def _check3((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(out, "foo")
        d.addCallback(_check3)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Archives"))
        def _check4((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            self.old_archives = out.split()
            self.failUnlessReallyEqual(len(self.old_archives), 1)
        d.addCallback(_check4)


        d.addCallback(self.stall, 1.1)
        d.addCallback(lambda res: do_backup())
        def _check4a((rc, out, err)):
            # second backup should reuse everything, if the backupdb is
            # available
            self.failUnlessReallyEqual(err, "")
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
            dbfile = os.path.join(self.get_clientdir(),
                                  "private", "backupdb.sqlite")
            self.failUnless(os.path.exists(dbfile), dbfile)
            bdb = backupdb.get_backupdb(dbfile)
            bdb.cursor.execute("UPDATE last_upload SET last_checked=0")
            bdb.cursor.execute("UPDATE directories SET last_checked=0")
            bdb.connection.commit()

        d.addCallback(_reset_last_checked)

        d.addCallback(self.stall, 1.1)
        d.addCallback(lambda res: do_backup(verbose=True))
        def _check4b((rc, out, err)):
            # we should check all files, and re-use all of them. None of
            # the directories should have been changed, so we should
            # re-use all of them too.
            self.failUnlessReallyEqual(err, "")
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
        def _check5((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
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
        def _check5a((rc, out, err)):
            # second backup should reuse bar.txt (if backupdb is available),
            # and upload the rest. None of the directories can be reused.
            self.failUnlessReallyEqual(err, "")
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
        def _check6((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            self.new_archives = out.split()
            self.failUnlessReallyEqual(len(self.new_archives), 4)
            self.failUnlessReallyEqual(sorted(self.new_archives)[0],
                                 self.old_archives[0])
        d.addCallback(_check6)
        d.addCallback(lambda res: self.do_cli("get", "tahoe:backups/Latest/parent/subdir/foo.txt"))
        def _check7((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(out, "FOOF!")
            # the old snapshot should not be modified
            return self.do_cli("get", "tahoe:backups/Archives/%s/parent/subdir/foo.txt" % self.old_archives[0])
        d.addCallback(_check7)
        def _check8((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(out, "foo")
        d.addCallback(_check8)

        return d

    # on our old dapper buildslave, this test takes a long time (usually
    # 130s), so we have to bump up the default 120s timeout. The create-alias
    # and initial backup alone take 60s, probably because of the handful of
    # dirnodes being created (RSA key generation). The backup between check4
    # and check4a takes 6s, as does the backup before check4b.
    test_backup.timeout = 3000

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
        backup_options = parse(['--exclude-from', excl_filepath, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(subdir_listdir))
        self._check_filtering(filtered, subdir_listdir, (u'another_doc.lyx', u'CVS'),
                              (u'.svn', u'_darcs', u'run_snake_run.py'))
        # test BackupConfigurationError
        self.failUnlessRaises(cli.BackupConfigurationError,
                              parse,
                              ['--exclude-from', excl_filepath + '.no', 'from', 'to'])

        # test that an iterator works too
        backup_options = parse(['--exclude', '*lyx', 'from', 'to'])
        filtered = list(backup_options.filter_listdir(iter(root_listdir)))
        self._check_filtering(filtered, root_listdir, (u'lib.a', u'_darcs', u'subdir'),
                              (u'nice_doc.lyx',))

    def test_exclude_options_unicode(self):
        nice_doc = u"nice_d\u00F8c.lyx"
        try:
            doc_pattern_arg = u"*d\u00F8c*".encode(get_io_encoding())
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
        exclusion_string = doc_pattern_arg + "\nlib.?"
        excl_filepath = os.path.join(basedir, 'exclusion')
        fileutil.write(excl_filepath, exclusion_string)
        backup_options = parse(['--exclude-from', excl_filepath, 'from', 'to'])
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
        def call_file(name, *args):
            ns.called = True
            self.failUnlessEqual(name, abspath_expanduser_unicode(exclude_file))
            return StringIO()

        patcher = MonkeyPatcher((__builtin__, 'file', call_file))
        patcher.runWithPatches(parse_options, basedir, "backup", ['--exclude-from', unicode_to_argv(exclude_file), 'from', 'to'])
        self.failUnless(ns.called)

    def test_ignore_symlinks(self):
        if not hasattr(os, 'symlink'):
            raise unittest.SkipTest("Symlinks are not supported by Python on this platform.")

        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid()

        source = os.path.join(self.basedir, "home")
        self.writeto("foo.txt", "foo")
        os.symlink(os.path.join(source, "foo.txt"), os.path.join(source, "foo2.txt"))

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("backup", "--verbose", source, "tahoe:test"))

        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 2)
            foo2 = os.path.join(source, "foo2.txt")
            self.failUnlessIn("WARNING: cannot backup symlink ", err)
            self.failUnlessIn(foo2, err)

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
        self.set_up_grid()

        source = os.path.join(self.basedir, "home")
        self.writeto("foo.txt", "foo")
        os.chmod(os.path.join(source, "foo.txt"), 0000)

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("backup", source, "tahoe:test"))

        def _check((rc, out, err)):
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
            os.chmod(os.path.join(source, "foo.txt"), 0644)
        d.addCallback(_cleanup)
        d.addErrback(_cleanup)

        return d

    def test_ignore_unreadable_directory(self):
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid()

        source = os.path.join(self.basedir, "home")
        os.mkdir(source)
        os.mkdir(os.path.join(source, "test"))
        os.chmod(os.path.join(source, "test"), 0000)

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("backup", source, "tahoe:test"))

        def _check((rc, out, err)):
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
            os.chmod(os.path.join(source, "test"), 0655)
        d.addCallback(_cleanup)
        d.addErrback(_cleanup)
        return d

    def test_backup_without_alias(self):
        # 'tahoe backup' should output a sensible error message when invoked
        # without an alias instead of a stack trace.
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid()
        source = os.path.join(self.basedir, "file1")
        d = self.do_cli('backup', source, source)
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d

    def test_backup_with_nonexistent_alias(self):
        # 'tahoe backup' should output a sensible error message when invoked
        # with a nonexistent alias.
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid()
        source = os.path.join(self.basedir, "file1")
        d = self.do_cli("backup", source, "nonexistent:" + source)
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d
