import os.path
from twisted.trial import unittest
from twisted.python import usage

from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from allmydata.scripts import cli
from allmydata.test.no_network import GridTestMixin
from allmydata.util.encodingutil import get_io_encoding, unicode_to_argv
from allmydata.util.fileutil import abspath_expanduser_unicode
from .test_cli import CLITestMixin

timeout = 480 # deep_check takes 360s on Zandr's linksys box, others take > 240s

class Put(GridTestMixin, CLITestMixin, unittest.TestCase):

    def test_unlinked_immutable_stdin(self):
        # tahoe get `echo DATA | tahoe put`
        # tahoe get `echo DATA | tahoe put -`
        self.basedir = "cli/Put/unlinked_immutable_stdin"
        DATA = "data" * 100
        self.set_up_grid()
        d = self.do_cli("put", stdin=DATA)
        def _uploaded(res):
            (rc, out, err) = res
            self.failUnlessIn("waiting for file data on stdin..", err)
            self.failUnlessIn("200 OK", err)
            self.readcap = out
            self.failUnless(self.readcap.startswith("URI:CHK:"))
        d.addCallback(_uploaded)
        d.addCallback(lambda res: self.do_cli("get", self.readcap))
        def _downloaded(res):
            (rc, out, err) = res
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(out, DATA)
        d.addCallback(_downloaded)
        d.addCallback(lambda res: self.do_cli("put", "-", stdin=DATA))
        d.addCallback(lambda (rc, out, err):
                      self.failUnlessReallyEqual(out, self.readcap))
        return d

    def test_unlinked_immutable_from_file(self):
        # tahoe put file.txt
        # tahoe put ./file.txt
        # tahoe put /tmp/file.txt
        # tahoe put ~/file.txt
        self.basedir = "cli/Put/unlinked_immutable_from_file"
        self.set_up_grid()

        rel_fn = os.path.join(self.basedir, "DATAFILE")
        abs_fn = unicode_to_argv(abspath_expanduser_unicode(unicode(rel_fn)))
        # we make the file small enough to fit in a LIT file, for speed
        fileutil.write(rel_fn, "short file")
        d = self.do_cli("put", rel_fn)
        def _uploaded((rc, out, err)):
            readcap = out
            self.failUnless(readcap.startswith("URI:LIT:"), readcap)
            self.readcap = readcap
        d.addCallback(_uploaded)
        d.addCallback(lambda res: self.do_cli("put", "./" + rel_fn))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessReallyEqual(stdout, self.readcap))
        d.addCallback(lambda res: self.do_cli("put", abs_fn))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessReallyEqual(stdout, self.readcap))
        # we just have to assume that ~ is handled properly
        return d

    def test_immutable_from_file(self):
        # tahoe put file.txt uploaded.txt
        # tahoe - uploaded.txt
        # tahoe put file.txt subdir/uploaded.txt
        # tahoe put file.txt tahoe:uploaded.txt
        # tahoe put file.txt tahoe:subdir/uploaded.txt
        # tahoe put file.txt DIRCAP:./uploaded.txt
        # tahoe put file.txt DIRCAP:./subdir/uploaded.txt
        self.basedir = "cli/Put/immutable_from_file"
        self.set_up_grid()

        rel_fn = os.path.join(self.basedir, "DATAFILE")
        # we make the file small enough to fit in a LIT file, for speed
        DATA = "short file"
        DATA2 = "short file two"
        fileutil.write(rel_fn, DATA)

        d = self.do_cli("create-alias", "tahoe")

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "uploaded.txt"))
        def _uploaded((rc, out, err)):
            readcap = out.strip()
            self.failUnless(readcap.startswith("URI:LIT:"), readcap)
            self.failUnlessIn("201 Created", err)
            self.readcap = readcap
        d.addCallback(_uploaded)
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:uploaded.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessReallyEqual(stdout, DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", "-", "uploaded.txt", stdin=DATA2))
        def _replaced((rc, out, err)):
            readcap = out.strip()
            self.failUnless(readcap.startswith("URI:LIT:"), readcap)
            self.failUnlessIn("200 OK", err)
        d.addCallback(_replaced)

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "subdir/uploaded2.txt"))
        d.addCallback(lambda res: self.do_cli("get", "subdir/uploaded2.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessReallyEqual(stdout, DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "tahoe:uploaded3.txt"))
        d.addCallback(lambda res: self.do_cli("get", "tahoe:uploaded3.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessReallyEqual(stdout, DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "tahoe:subdir/uploaded4.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:subdir/uploaded4.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessReallyEqual(stdout, DATA))

        def _get_dircap(res):
            self.dircap = get_aliases(self.get_clientdir())["tahoe"]
        d.addCallback(_get_dircap)

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn,
                                  self.dircap+":./uploaded5.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:uploaded5.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessReallyEqual(stdout, DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn,
                                  self.dircap+":./subdir/uploaded6.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:subdir/uploaded6.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessReallyEqual(stdout, DATA))

        return d

    def test_mutable_unlinked(self):
        # FILECAP = `echo DATA | tahoe put --mutable`
        # tahoe get FILECAP, compare against DATA
        # echo DATA2 | tahoe put - FILECAP
        # tahoe get FILECAP, compare against DATA2
        # tahoe put file.txt FILECAP
        self.basedir = "cli/Put/mutable_unlinked"
        self.set_up_grid()

        DATA = "data" * 100
        DATA2 = "two" * 100
        rel_fn = os.path.join(self.basedir, "DATAFILE")
        DATA3 = "three" * 100
        fileutil.write(rel_fn, DATA3)

        d = self.do_cli("put", "--mutable", stdin=DATA)
        def _created(res):
            (rc, out, err) = res
            self.failUnlessIn("waiting for file data on stdin..", err)
            self.failUnlessIn("200 OK", err)
            self.filecap = out
            self.failUnless(self.filecap.startswith("URI:SSK:"), self.filecap)
        d.addCallback(_created)
        d.addCallback(lambda res: self.do_cli("get", self.filecap))
        d.addCallback(lambda (rc,out,err): self.failUnlessReallyEqual(out, DATA))

        d.addCallback(lambda res: self.do_cli("put", "-", self.filecap, stdin=DATA2))
        def _replaced(res):
            (rc, out, err) = res
            self.failUnlessIn("waiting for file data on stdin..", err)
            self.failUnlessIn("200 OK", err)
            self.failUnlessReallyEqual(self.filecap, out)
        d.addCallback(_replaced)
        d.addCallback(lambda res: self.do_cli("get", self.filecap))
        d.addCallback(lambda (rc,out,err): self.failUnlessReallyEqual(out, DATA2))

        d.addCallback(lambda res: self.do_cli("put", rel_fn, self.filecap))
        def _replaced2(res):
            (rc, out, err) = res
            self.failUnlessIn("200 OK", err)
            self.failUnlessReallyEqual(self.filecap, out)
        d.addCallback(_replaced2)
        d.addCallback(lambda res: self.do_cli("get", self.filecap))
        d.addCallback(lambda (rc,out,err): self.failUnlessReallyEqual(out, DATA3))

        return d

    def test_mutable(self):
        # echo DATA1 | tahoe put --mutable - uploaded.txt
        # echo DATA2 | tahoe put - uploaded.txt # should modify-in-place
        # tahoe get uploaded.txt, compare against DATA2

        self.basedir = "cli/Put/mutable"
        self.set_up_grid()

        DATA1 = "data" * 100
        fn1 = os.path.join(self.basedir, "DATA1")
        fileutil.write(fn1, DATA1)
        DATA2 = "two" * 100
        fn2 = os.path.join(self.basedir, "DATA2")
        fileutil.write(fn2, DATA2)

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res:
                      self.do_cli("put", "--mutable", fn1, "tahoe:uploaded.txt"))
        def _check(res):
            (rc, out, err) = res
            self.failUnlessEqual(rc, 0, str(res))
            self.failUnlessEqual(err.strip(), "201 Created", str(res))
            self.uri = out
        d.addCallback(_check)
        d.addCallback(lambda res:
                      self.do_cli("put", fn2, "tahoe:uploaded.txt"))
        def _check2(res):
            (rc, out, err) = res
            self.failUnlessEqual(rc, 0, str(res))
            self.failUnlessEqual(err.strip(), "200 OK", str(res))
            self.failUnlessEqual(out, self.uri, str(res))
        d.addCallback(_check2)
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:uploaded.txt"))
        d.addCallback(lambda (rc,out,err): self.failUnlessReallyEqual(out, DATA2))
        return d

    def _check_mdmf_json(self, (rc, json, err)):
         self.failUnlessEqual(rc, 0)
         self.failUnlessEqual(err, "")
         self.failUnlessIn('"format": "MDMF"', json)
         # We also want a valid MDMF cap to be in the json.
         self.failUnlessIn("URI:MDMF", json)
         self.failUnlessIn("URI:MDMF-RO", json)
         self.failUnlessIn("URI:MDMF-Verifier", json)

    def _check_sdmf_json(self, (rc, json, err)):
        self.failUnlessEqual(rc, 0)
        self.failUnlessEqual(err, "")
        self.failUnlessIn('"format": "SDMF"', json)
        # We also want to see the appropriate SDMF caps.
        self.failUnlessIn("URI:SSK", json)
        self.failUnlessIn("URI:SSK-RO", json)
        self.failUnlessIn("URI:SSK-Verifier", json)

    def _check_chk_json(self, (rc, json, err)):
        self.failUnlessEqual(rc, 0)
        self.failUnlessEqual(err, "")
        self.failUnlessIn('"format": "CHK"', json)
        # We also want to see the appropriate CHK caps.
        self.failUnlessIn("URI:CHK", json)
        self.failUnlessIn("URI:CHK-Verifier", json)

    def test_format(self):
        self.basedir = "cli/Put/format"
        self.set_up_grid()
        data = "data" * 40000 # 160kB total, two segments
        fn1 = os.path.join(self.basedir, "data")
        fileutil.write(fn1, data)
        d = self.do_cli("create-alias", "tahoe")

        def _put_and_ls(ign, cmdargs, expected, filename=None):
            if filename:
                args = ["put"] + cmdargs + [fn1, filename]
            else:
                # unlinked
                args = ["put"] + cmdargs + [fn1]
            d2 = self.do_cli(*args)
            def _list((rc, out, err)):
                self.failUnlessEqual(rc, 0) # don't allow failure
                if filename:
                    return self.do_cli("ls", "--json", filename)
                else:
                    cap = out.strip()
                    return self.do_cli("ls", "--json", cap)
            d2.addCallback(_list)
            return d2

        # 'tahoe put' to a directory
        d.addCallback(_put_and_ls, ["--mutable"], "SDMF", "tahoe:s1.txt")
        d.addCallback(self._check_sdmf_json) # backwards-compatibility
        d.addCallback(_put_and_ls, ["--format=SDMF"], "SDMF", "tahoe:s2.txt")
        d.addCallback(self._check_sdmf_json)
        d.addCallback(_put_and_ls, ["--format=sdmf"], "SDMF", "tahoe:s3.txt")
        d.addCallback(self._check_sdmf_json)
        d.addCallback(_put_and_ls, ["--mutable", "--format=SDMF"], "SDMF", "tahoe:s4.txt")
        d.addCallback(self._check_sdmf_json)

        d.addCallback(_put_and_ls, ["--format=MDMF"], "MDMF", "tahoe:m1.txt")
        d.addCallback(self._check_mdmf_json)
        d.addCallback(_put_and_ls, ["--mutable", "--format=MDMF"], "MDMF", "tahoe:m2.txt")
        d.addCallback(self._check_mdmf_json)

        d.addCallback(_put_and_ls, ["--format=CHK"], "CHK", "tahoe:c1.txt")
        d.addCallback(self._check_chk_json)
        d.addCallback(_put_and_ls, [], "CHK", "tahoe:c1.txt")
        d.addCallback(self._check_chk_json)

        # 'tahoe put' unlinked
        d.addCallback(_put_and_ls, ["--mutable"], "SDMF")
        d.addCallback(self._check_sdmf_json) # backwards-compatibility
        d.addCallback(_put_and_ls, ["--format=SDMF"], "SDMF")
        d.addCallback(self._check_sdmf_json)
        d.addCallback(_put_and_ls, ["--format=sdmf"], "SDMF")
        d.addCallback(self._check_sdmf_json)
        d.addCallback(_put_and_ls, ["--mutable", "--format=SDMF"], "SDMF")
        d.addCallback(self._check_sdmf_json)

        d.addCallback(_put_and_ls, ["--format=MDMF"], "MDMF")
        d.addCallback(self._check_mdmf_json)
        d.addCallback(_put_and_ls, ["--mutable", "--format=MDMF"], "MDMF")
        d.addCallback(self._check_mdmf_json)

        d.addCallback(_put_and_ls, ["--format=CHK"], "CHK")
        d.addCallback(self._check_chk_json)
        d.addCallback(_put_and_ls, [], "CHK")
        d.addCallback(self._check_chk_json)

        return d

    def test_put_to_mdmf_cap(self):
        self.basedir = "cli/Put/put_to_mdmf_cap"
        self.set_up_grid()
        data = "data" * 100000
        fn1 = os.path.join(self.basedir, "data")
        fileutil.write(fn1, data)
        d = self.do_cli("put", "--format=MDMF", fn1)
        def _got_cap((rc, out, err)):
            self.failUnlessEqual(rc, 0)
            self.cap = out.strip()
        d.addCallback(_got_cap)
        # Now try to write something to the cap using put.
        data2 = "data2" * 100000
        fn2 = os.path.join(self.basedir, "data2")
        fileutil.write(fn2, data2)
        d.addCallback(lambda ignored:
            self.do_cli("put", fn2, self.cap))
        def _got_put((rc, out, err)):
            self.failUnlessEqual(rc, 0)
            self.failUnlessIn(self.cap, out)
        d.addCallback(_got_put)
        # Now get the cap. We should see the data we just put there.
        d.addCallback(lambda ignored:
            self.do_cli("get", self.cap))
        def _got_data((rc, out, err)):
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out, data2)
        d.addCallback(_got_data)
        # add some extension information to the cap and try to put something
        # to it.
        def _make_extended_cap(ignored):
            self.cap = self.cap + ":Extension-Stuff"
        d.addCallback(_make_extended_cap)
        data3 = "data3" * 100000
        fn3 = os.path.join(self.basedir, "data3")
        fileutil.write(fn3, data3)
        d.addCallback(lambda ignored:
            self.do_cli("put", fn3, self.cap))
        d.addCallback(lambda ignored:
            self.do_cli("get", self.cap))
        def _got_data3((rc, out, err)):
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out, data3)
        d.addCallback(_got_data3)
        return d

    def test_put_to_sdmf_cap(self):
        self.basedir = "cli/Put/put_to_sdmf_cap"
        self.set_up_grid()
        data = "data" * 100000
        fn1 = os.path.join(self.basedir, "data")
        fileutil.write(fn1, data)
        d = self.do_cli("put", "--format=SDMF", fn1)
        def _got_cap((rc, out, err)):
            self.failUnlessEqual(rc, 0)
            self.cap = out.strip()
        d.addCallback(_got_cap)
        # Now try to write something to the cap using put.
        data2 = "data2" * 100000
        fn2 = os.path.join(self.basedir, "data2")
        fileutil.write(fn2, data2)
        d.addCallback(lambda ignored:
            self.do_cli("put", fn2, self.cap))
        def _got_put((rc, out, err)):
            self.failUnlessEqual(rc, 0)
            self.failUnlessIn(self.cap, out)
        d.addCallback(_got_put)
        # Now get the cap. We should see the data we just put there.
        d.addCallback(lambda ignored:
            self.do_cli("get", self.cap))
        def _got_data((rc, out, err)):
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out, data2)
        d.addCallback(_got_data)
        return d

    def test_mutable_type_invalid_format(self):
        o = cli.PutOptions()
        self.failUnlessRaises(usage.UsageError,
                              o.parseOptions,
                              ["--format=LDMF"])

    def test_put_with_nonexistent_alias(self):
        # when invoked with an alias that doesn't exist, 'tahoe put'
        # should output a useful error message, not a stack trace
        self.basedir = "cli/Put/put_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("put", "somefile", "fake:afile")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d

    def test_immutable_from_file_unicode(self):
        # tahoe put "\u00E0 trier.txt" "\u00E0 trier.txt"

        try:
            a_trier_arg = u"\u00E0 trier.txt".encode(get_io_encoding())
        except UnicodeEncodeError:
            raise unittest.SkipTest("A non-ASCII command argument could not be encoded on this platform.")

        self.skip_if_cannot_represent_filename(u"\u00E0 trier.txt")

        self.basedir = "cli/Put/immutable_from_file_unicode"
        self.set_up_grid()

        rel_fn = os.path.join(unicode(self.basedir), u"\u00E0 trier.txt")
        # we make the file small enough to fit in a LIT file, for speed
        DATA = "short file"
        fileutil.write(rel_fn, DATA)

        d = self.do_cli("create-alias", "tahoe")

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn.encode(get_io_encoding()), a_trier_arg))
        def _uploaded((rc, out, err)):
            readcap = out.strip()
            self.failUnless(readcap.startswith("URI:LIT:"), readcap)
            self.failUnlessIn("201 Created", err)
            self.readcap = readcap
        d.addCallback(_uploaded)

        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:" + a_trier_arg))
        d.addCallback(lambda (rc, out, err):
                      self.failUnlessReallyEqual(out, DATA))

        return d

