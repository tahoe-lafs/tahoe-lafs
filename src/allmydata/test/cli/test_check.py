from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from six import ensure_text

import os.path
import json
from twisted.trial import unittest
from six.moves import cStringIO as StringIO

from allmydata import uri
from allmydata.util import base32
from allmydata.util.encodingutil import to_bytes, quote_output_u
from allmydata.mutable.publish import MutableData
from allmydata.immutable import upload
from allmydata.scripts import debug
from ..no_network import GridTestMixin
from .common import CLITestMixin


class Check(GridTestMixin, CLITestMixin, unittest.TestCase):

    def test_check(self):
        self.basedir = "cli/Check/check"
        self.set_up_grid()
        c0 = self.g.clients[0]
        DATA = b"data" * 100
        DATA_uploadable = MutableData(DATA)
        d = c0.create_mutable_file(DATA_uploadable)
        def _stash_uri(n):
            self.uri = n.get_uri()
        d.addCallback(_stash_uri)

        d.addCallback(lambda ign: self.do_cli("check", self.uri))
        def _check1(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("Summary: Healthy" in lines, out)
            self.failUnless(" good-shares: 10 (encoding is 3-of-10)" in lines, out)
        d.addCallback(_check1)

        d.addCallback(lambda ign: self.do_cli("check", "--raw", self.uri))
        def _check2(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            data = json.loads(out)
            self.failUnlessReallyEqual(to_bytes(data["summary"]), b"Healthy")
            self.failUnlessReallyEqual(data["results"]["healthy"], True)
        d.addCallback(_check2)

        d.addCallback(lambda ign: c0.upload(upload.Data(b"literal", convergence=b"")))
        def _stash_lit_uri(n):
            self.lit_uri = n.get_uri()
        d.addCallback(_stash_lit_uri)

        d.addCallback(lambda ign: self.do_cli("check", self.lit_uri))
        def _check_lit(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("Summary: Healthy (LIT)" in lines, out)
        d.addCallback(_check_lit)

        d.addCallback(lambda ign: self.do_cli("check", "--raw", self.lit_uri))
        def _check_lit_raw(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            data = json.loads(out)
            self.failUnlessReallyEqual(data["results"]["healthy"], True)
        d.addCallback(_check_lit_raw)

        d.addCallback(lambda ign: c0.create_immutable_dirnode({}, convergence=b""))
        def _stash_lit_dir_uri(n):
            self.lit_dir_uri = n.get_uri()
        d.addCallback(_stash_lit_dir_uri)

        d.addCallback(lambda ign: self.do_cli("check", self.lit_dir_uri))
        d.addCallback(_check_lit)

        d.addCallback(lambda ign: self.do_cli("check", "--raw", self.lit_uri))
        d.addCallback(_check_lit_raw)

        def _clobber_shares(ignored):
            # delete one, corrupt a second
            shares = self.find_uri_shares(self.uri)
            self.failUnlessReallyEqual(len(shares), 10)
            os.unlink(shares[0][2])
            cso = debug.CorruptShareOptions()
            cso.stdout = StringIO()
            cso.parseOptions([shares[1][2]])
            storage_index = uri.from_string(self.uri).get_storage_index()
            self._corrupt_share_line = "  server %s, SI %s, shnum %d" % \
                (str(base32.b2a(shares[1][1]), "ascii"),
                 str(base32.b2a(storage_index), "ascii"),
                 shares[1][0])
            debug.corrupt_share(cso)
        d.addCallback(_clobber_shares)

        d.addCallback(lambda ign: self.do_cli("check", "--verify", self.uri))
        def _check3(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            summary = [l for l in lines if l.startswith("Summary")][0]
            self.failUnless("Summary: Unhealthy: 8 shares (enc 3-of-10)"
                            in summary, summary)
            self.failUnless(" good-shares: 8 (encoding is 3-of-10)" in lines, out)
            self.failUnless(" corrupt shares:" in lines, out)
            self.failUnless(self._corrupt_share_line in lines, out)
        d.addCallback(_check3)

        d.addCallback(lambda ign: self.do_cli("check", "--verify", "--raw", self.uri))
        def _check3_raw(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            data = json.loads(out)
            self.failUnlessReallyEqual(data["results"]["healthy"], False)
            self.failUnlessIn("Unhealthy: 8 shares (enc 3-of-10)", data["summary"])
            self.failUnlessReallyEqual(data["results"]["count-shares-good"], 8)
            self.failUnlessReallyEqual(data["results"]["count-corrupt-shares"], 1)
            self.failUnlessIn("list-corrupt-shares", data["results"])
        d.addCallback(_check3_raw)

        d.addCallback(lambda ign:
                      self.do_cli("check", "--verify", "--repair", self.uri))
        def _check4(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("Summary: not healthy" in lines, out)
            self.failUnless(" good-shares: 8 (encoding is 3-of-10)" in lines, out)
            self.failUnless(" corrupt shares:" in lines, out)
            self.failUnless(self._corrupt_share_line in lines, out)
            self.failUnless(" repair successful" in lines, out)
        d.addCallback(_check4)

        d.addCallback(lambda ign:
                      self.do_cli("check", "--verify", "--repair", self.uri))
        def _check5(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("Summary: healthy" in lines, out)
            self.failUnless(" good-shares: 10 (encoding is 3-of-10)" in lines, out)
            self.failIf(" corrupt shares:" in lines, out)
        d.addCallback(_check5)

        return d

    def test_deep_check(self):
        self.basedir = "cli/Check/deep_check"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.uris = {}
        self.fileurls = {}
        DATA = b"data" * 100
        quoted_good = quote_output_u("g\u00F6\u00F6d")

        d = c0.create_dirnode()
        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.rooturi = n.get_uri()
            return n.add_file(u"g\u00F6\u00F6d", upload.Data(DATA, convergence=b""))
        d.addCallback(_stash_root_and_create_file)
        def _stash_uri(fn, which):
            self.uris[which] = fn.get_uri()
            return fn
        d.addCallback(_stash_uri, u"g\u00F6\u00F6d")
        d.addCallback(lambda ign:
                      self.rootnode.add_file(u"small",
                                           upload.Data(b"literal",
                                                        convergence=b"")))
        d.addCallback(_stash_uri, "small")
        d.addCallback(lambda ign:
            c0.create_mutable_file(MutableData(DATA+b"1")))
        d.addCallback(lambda fn: self.rootnode.set_node(u"mutable", fn))
        d.addCallback(_stash_uri, "mutable")

        d.addCallback(lambda ign: self.do_cli("deep-check", self.rooturi))
        def _check1(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("done: 4 objects checked, 4 healthy, 0 unhealthy"
                            in lines, out)
        d.addCallback(_check1)

        # root
        # root/g\u00F6\u00F6d
        # root/small
        # root/mutable

        d.addCallback(lambda ign: self.do_cli("deep-check", "--verbose",
                                              self.rooturi))
        def _check2(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            out = ensure_text(out)
            lines = out.splitlines()
            self.failUnless("'<root>': Healthy" in lines, out)
            self.failUnless("'small': Healthy (LIT)" in lines, out)
            self.failUnless((quoted_good + ": Healthy") in lines, out)
            self.failUnless("'mutable': Healthy" in lines, out)
            self.failUnless("done: 4 objects checked, 4 healthy, 0 unhealthy"
                            in lines, out)
        d.addCallback(_check2)

        d.addCallback(lambda ign: self.do_cli("stats", self.rooturi))
        def _check_stats(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            self.failUnlessIn(" count-immutable-files: 1", lines)
            self.failUnlessIn("   count-mutable-files: 1", lines)
            self.failUnlessIn("   count-literal-files: 1", lines)
            self.failUnlessIn("     count-directories: 1", lines)
            self.failUnlessIn("  size-immutable-files: 400", lines)
            self.failUnlessIn("Size Histogram:", lines)
            self.failUnlessIn("   4-10   : 1    (10 B, 10 B)", lines)
            self.failUnlessIn(" 317-1000 : 1    (1000 B, 1000 B)", lines)
        d.addCallback(_check_stats)

        def _clobber_shares(ignored):
            shares = self.find_uri_shares(self.uris[u"g\u00F6\u00F6d"])
            self.failUnlessReallyEqual(len(shares), 10)
            os.unlink(shares[0][2])

            shares = self.find_uri_shares(self.uris["mutable"])
            cso = debug.CorruptShareOptions()
            cso.stdout = StringIO()
            cso.parseOptions([shares[1][2]])
            storage_index = uri.from_string(self.uris["mutable"]).get_storage_index()
            self._corrupt_share_line = " corrupt: server %s, SI %s, shnum %d" % \
                                       (str(base32.b2a(shares[1][1]), "ascii"),
                                        str(base32.b2a(storage_index), "ascii"),
                                        shares[1][0])
            debug.corrupt_share(cso)
        d.addCallback(_clobber_shares)

        # root
        # root/g\u00F6\u00F6d  [9 shares]
        # root/small
        # root/mutable [1 corrupt share]

        d.addCallback(lambda ign:
                      self.do_cli("deep-check", "--verbose", self.rooturi))
        def _check3(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            out = ensure_text(out)
            lines = out.splitlines()
            self.failUnless("'<root>': Healthy" in lines, out)
            self.failUnless("'small': Healthy (LIT)" in lines, out)
            self.failUnless("'mutable': Healthy" in lines, out) # needs verifier
            self.failUnless((quoted_good + ": Not Healthy: 9 shares (enc 3-of-10)") in lines, out)
            self.failIf(self._corrupt_share_line in lines, out)
            self.failUnless("done: 4 objects checked, 3 healthy, 1 unhealthy"
                            in lines, out)
        d.addCallback(_check3)

        d.addCallback(lambda ign:
                      self.do_cli("deep-check", "--verbose", "--verify",
                                  self.rooturi))
        def _check4(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            out = ensure_text(out)
            lines = out.splitlines()
            self.failUnless("'<root>': Healthy" in lines, out)
            self.failUnless("'small': Healthy (LIT)" in lines, out)
            mutable = [l for l in lines if l.startswith("'mutable'")][0]
            self.failUnless(mutable.startswith("'mutable': Unhealthy: 9 shares (enc 3-of-10)"),
                            mutable)
            self.failUnless(self._corrupt_share_line in lines, out)
            self.failUnless((quoted_good + ": Not Healthy: 9 shares (enc 3-of-10)") in lines, out)
            self.failUnless("done: 4 objects checked, 2 healthy, 2 unhealthy"
                            in lines, out)
        d.addCallback(_check4)

        d.addCallback(lambda ign:
                      self.do_cli("deep-check", "--raw",
                                  self.rooturi))
        def _check5(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            units = [json.loads(line) for line in lines]
            # root, small, g\u00F6\u00F6d, mutable,  stats
            self.failUnlessReallyEqual(len(units), 4+1)
        d.addCallback(_check5)

        d.addCallback(lambda ign:
                      self.do_cli("deep-check",
                                  "--verbose", "--verify", "--repair",
                                  self.rooturi))
        def _check6(args):
            (rc, out, err) = args
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(rc, 0)
            out = ensure_text(out)
            lines = out.splitlines()
            self.failUnless("'<root>': healthy" in lines, out)
            self.failUnless("'small': healthy" in lines, out)
            self.failUnless("'mutable': not healthy" in lines, out)
            self.failUnless(self._corrupt_share_line in lines, out)
            self.failUnless((quoted_good + ": not healthy") in lines, out)
            self.failUnless("done: 4 objects checked" in lines, out)
            self.failUnless(" pre-repair: 2 healthy, 2 unhealthy" in lines, out)
            self.failUnless(" 2 repairs attempted, 2 successful, 0 failed"
                            in lines, out)
            self.failUnless(" post-repair: 4 healthy, 0 unhealthy" in lines,out)
        d.addCallback(_check6)

        # now add a subdir, and a file below that, then make the subdir
        # unrecoverable

        d.addCallback(lambda ign: self.rootnode.create_subdirectory(u"subdir"))
        d.addCallback(_stash_uri, "subdir")
        d.addCallback(lambda fn:
                      fn.add_file(u"subfile", upload.Data(DATA+b"2", b"")))
        d.addCallback(lambda ign:
                      self.delete_shares_numbered(self.uris["subdir"],
                                                  list(range(10))))

        # root
        # rootg\u00F6\u00F6d/
        # root/small
        # root/mutable
        # root/subdir [unrecoverable: 0 shares]
        # root/subfile

        d.addCallback(lambda ign: self.do_cli("manifest", self.rooturi))
        def _manifest_failed(args):
            (rc, out, err) = args
            self.failIfEqual(rc, 0)
            self.failUnlessIn("ERROR: UnrecoverableFileError", err)
            # the fatal directory should still show up, as the last line
            self.failUnlessIn(" subdir\n", ensure_text(out))
        d.addCallback(_manifest_failed)

        d.addCallback(lambda ign: self.do_cli("deep-check", self.rooturi))
        def _deep_check_failed(args):
            (rc, out, err) = args
            self.failIfEqual(rc, 0)
            self.failUnlessIn("ERROR: UnrecoverableFileError", err)
            # we want to make sure that the error indication is the last
            # thing that gets emitted
            self.failIf("done:" in out, out)
        d.addCallback(_deep_check_failed)

        # this test is disabled until the deep-repair response to an
        # unrepairable directory is fixed. The failure-to-repair should not
        # throw an exception, but the failure-to-traverse that follows
        # should throw UnrecoverableFileError.

        #d.addCallback(lambda ign:
        #              self.do_cli("deep-check", "--repair", self.rooturi))
        #def _deep_check_repair_failed((rc, out, err)):
        #    self.failIfEqual(rc, 0)
        #    print(err)
        #    self.failUnlessIn("ERROR: UnrecoverableFileError", err)
        #    self.failIf("done:" in out, out)
        #d.addCallback(_deep_check_repair_failed)

        return d

    def test_check_without_alias(self):
        # 'tahoe check' should output a sensible error message if it needs to
        # find the default alias and can't
        self.basedir = "cli/Check/check_without_alias"
        self.set_up_grid(oneshare=True)
        d = self.do_cli("check")
        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.assertEqual(len(out), 0, out)
        d.addCallback(_check)
        d.addCallback(lambda ign: self.do_cli("deep-check"))
        d.addCallback(_check)
        return d

    def test_check_with_nonexistent_alias(self):
        # 'tahoe check' should output a sensible error message if it needs to
        # find an alias and can't.
        self.basedir = "cli/Check/check_with_nonexistent_alias"
        self.set_up_grid(oneshare=True)
        d = self.do_cli("check", "nonexistent:")
        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
            self.assertEqual(len(out), 0, out)
        d.addCallback(_check)
        return d

    def test_check_with_multiple_aliases(self):
        self.basedir = "cli/Check/check_with_multiple_aliases"
        self.set_up_grid(oneshare=True)
        self.uriList = []
        c0 = self.g.clients[0]
        d = c0.create_dirnode()
        def _stash_uri(n):
            self.uriList.append(n.get_uri())
        d.addCallback(_stash_uri)
        d.addCallback(lambda _: c0.create_dirnode())
        d.addCallback(_stash_uri)

        d.addCallback(lambda ign: self.do_cli("check", self.uriList[0], self.uriList[1]))
        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 0)
            self.assertEqual(len(err), 0, err)
            #Ensure healthy appears for each uri
            self.failUnlessIn("Healthy", out[:len(out)//2])
            self.failUnlessIn("Healthy", out[len(out)//2:])
        d.addCallback(_check)

        d.addCallback(lambda ign: self.do_cli("check", self.uriList[0], "nonexistent:"))
        def _check2(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("Healthy", out)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
        d.addCallback(_check2)

        return d
