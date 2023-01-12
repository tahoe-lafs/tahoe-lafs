"""
Tests for the ``tahoe put`` CLI tool.
"""
from __future__ import annotations

from typing import Callable, Awaitable, TypeVar, Any
import os.path
from twisted.trial import unittest
from twisted.python import usage
from twisted.python.filepath import FilePath

from cryptography.hazmat.primitives.serialization import load_pem_private_key

from allmydata.crypto.rsa import PrivateKey
from allmydata.uri import from_string
from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from allmydata.scripts import cli
from ..no_network import GridTestMixin
from ..common_util import skip_if_cannot_represent_filename
from allmydata.util.encodingutil import get_io_encoding
from allmydata.util.fileutil import abspath_expanduser_unicode
from .common import CLITestMixin
from allmydata.mutable.common import derive_mutable_keys

T = TypeVar("T")

class Put(GridTestMixin, CLITestMixin, unittest.TestCase):

    def test_unlinked_immutable_stdin(self):
        # tahoe get `echo DATA | tahoe put`
        # tahoe get `echo DATA | tahoe put -`
        self.basedir = "cli/Put/unlinked_immutable_stdin"
        DATA = b"data\xff" * 100
        self.set_up_grid(oneshare=True)
        d = self.do_cli("put", stdin=DATA)
        def _uploaded(res):
            (rc, out, err) = res
            self.failUnlessIn("waiting for file data on stdin..", err)
            self.failUnlessIn("200 OK", err)
            self.readcap = out
            self.failUnless(self.readcap.startswith("URI:CHK:"))
        d.addCallback(_uploaded)
        d.addCallback(lambda res: self.do_cli("get", self.readcap,
                                              return_bytes=True))
        def _downloaded(res):
            (rc, out, err) = res
            self.failUnlessReallyEqual(err, b"")
            self.failUnlessReallyEqual(out, DATA)
        d.addCallback(_downloaded)
        d.addCallback(lambda res: self.do_cli("put", "-", stdin=DATA))
        d.addCallback(lambda rc_out_err:
                      self.failUnlessReallyEqual(rc_out_err[1], self.readcap))
        return d

    def test_unlinked_immutable_from_file(self):
        # tahoe put file.txt
        # tahoe put ./file.txt
        # tahoe put /tmp/file.txt
        # tahoe put ~/file.txt
        self.basedir = "cli/Put/unlinked_immutable_from_file"
        self.set_up_grid(oneshare=True)

        rel_fn = str(os.path.join(self.basedir, "DATAFILE"))
        abs_fn = abspath_expanduser_unicode(rel_fn)
        # we make the file small enough to fit in a LIT file, for speed
        fileutil.write(rel_fn, b"short file has some bytes \xff yes")
        d = self.do_cli_unicode(u"put", [rel_fn])
        def _uploaded(args):
            (rc, out, err) = args
            readcap = out
            self.failUnless(readcap.startswith("URI:LIT:"), readcap)
            self.readcap = readcap
        d.addCallback(_uploaded)
        d.addCallback(lambda res: self.do_cli_unicode(u"put", [u"./" + rel_fn]))
        d.addCallback(lambda rc_stdout_stderr:
                      self.failUnlessReallyEqual(rc_stdout_stderr[1], self.readcap))
        d.addCallback(lambda res: self.do_cli_unicode(u"put", [abs_fn]))
        d.addCallback(lambda rc_stdout_stderr:
                      self.failUnlessReallyEqual(rc_stdout_stderr[1], self.readcap))
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
        self.set_up_grid(oneshare=True)

        rel_fn = os.path.join(self.basedir, "DATAFILE")
        # we make the file small enough to fit in a LIT file, for speed
        DATA = b"short file"
        DATA2 = b"short file two"
        fileutil.write(rel_fn, DATA)

        d = self.do_cli("create-alias", "tahoe")

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "uploaded.txt"))
        def _uploaded(args):
            (rc, out, err) = args
            readcap = out.strip()
            self.failUnless(readcap.startswith("URI:LIT:"), readcap)
            self.failUnlessIn("201 Created", err)
            self.readcap = readcap
        d.addCallback(_uploaded)
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:uploaded.txt",
                                  return_bytes=True))
        d.addCallback(lambda rc_stdout_stderr:
                      self.failUnlessReallyEqual(rc_stdout_stderr[1], DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", "-", "uploaded.txt", stdin=DATA2))
        def _replaced(args):
            (rc, out, err) = args
            readcap = out.strip()
            self.failUnless(readcap.startswith("URI:LIT:"), readcap)
            self.failUnlessIn("200 OK", err)
        d.addCallback(_replaced)

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "subdir/uploaded2.txt"))
        d.addCallback(lambda res: self.do_cli("get", "subdir/uploaded2.txt",
                                              return_bytes=True))
        d.addCallback(lambda rc_stdout_stderr:
                      self.failUnlessReallyEqual(rc_stdout_stderr[1], DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "tahoe:uploaded3.txt"))
        d.addCallback(lambda res: self.do_cli("get", "tahoe:uploaded3.txt",
                                              return_bytes=True))
        d.addCallback(lambda rc_stdout_stderr:
                      self.failUnlessReallyEqual(rc_stdout_stderr[1], DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "tahoe:subdir/uploaded4.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:subdir/uploaded4.txt",
                                  return_bytes=True))
        d.addCallback(lambda rc_stdout_stderr:
                      self.failUnlessReallyEqual(rc_stdout_stderr[1], DATA))

        def _get_dircap(res):
            self.dircap = str(get_aliases(self.get_clientdir())["tahoe"], "ascii")
        d.addCallback(_get_dircap)

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn,
                                  self.dircap+":./uploaded5.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:uploaded5.txt",
                                  return_bytes=True))
        d.addCallback(lambda rc_stdout_stderr:
                      self.failUnlessReallyEqual(rc_stdout_stderr[1], DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn,
                                  self.dircap+":./subdir/uploaded6.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:subdir/uploaded6.txt",
                                  return_bytes=True))
        d.addCallback(lambda rc_stdout_stderr:
                      self.failUnlessReallyEqual(rc_stdout_stderr[1], DATA))

        return d

    def test_mutable_unlinked(self):
        # FILECAP = `echo DATA | tahoe put --mutable`
        # tahoe get FILECAP, compare against DATA
        # echo DATA2 | tahoe put - FILECAP
        # tahoe get FILECAP, compare against DATA2
        # tahoe put file.txt FILECAP
        self.basedir = "cli/Put/mutable_unlinked"
        self.set_up_grid(oneshare=True)

        DATA = b"data" * 100
        DATA2 = b"two" * 100
        rel_fn = os.path.join(self.basedir, "DATAFILE")
        DATA3 = b"three" * 100
        fileutil.write(rel_fn, DATA3)

        d = self.do_cli("put", "--mutable", stdin=DATA)
        def _created(res):
            (rc, out, err) = res
            self.failUnlessIn("waiting for file data on stdin..", err)
            self.failUnlessIn("200 OK", err)
            self.filecap = out
            self.failUnless(self.filecap.startswith("URI:SSK:"), self.filecap)
        d.addCallback(_created)
        d.addCallback(lambda res: self.do_cli("get", self.filecap, return_bytes=True))
        d.addCallback(lambda rc_out_err: self.failUnlessReallyEqual(rc_out_err[1], DATA))

        d.addCallback(lambda res: self.do_cli("put", "-", self.filecap, stdin=DATA2))
        def _replaced(res):
            (rc, out, err) = res
            self.failUnlessIn("waiting for file data on stdin..", err)
            self.failUnlessIn("200 OK", err)
            self.failUnlessReallyEqual(self.filecap, out)
        d.addCallback(_replaced)
        d.addCallback(lambda res: self.do_cli("get", self.filecap, return_bytes=True))
        d.addCallback(lambda rc_out_err: self.failUnlessReallyEqual(rc_out_err[1], DATA2))

        d.addCallback(lambda res: self.do_cli("put", rel_fn, self.filecap))
        def _replaced2(res):
            (rc, out, err) = res
            self.failUnlessIn("200 OK", err)
            self.failUnlessReallyEqual(self.filecap, out)
        d.addCallback(_replaced2)
        d.addCallback(lambda res: self.do_cli("get", self.filecap, return_bytes=True))
        d.addCallback(lambda rc_out_err: self.failUnlessReallyEqual(rc_out_err[1], DATA3))

        return d

    async def test_unlinked_mutable_specified_private_key(self) -> None:
        """
        A new unlinked mutable can be created using a specified private
        key.
        """
        self.basedir = "cli/Put/unlinked-mutable-with-key"
        await self._test_mutable_specified_key(
            lambda do_cli, pempath, datapath: do_cli(
                "put", "--mutable", "--private-key-path", pempath.path,
                stdin=datapath.getContent(),
            ),
        )

    async def test_linked_mutable_specified_private_key(self) -> None:
        """
        A new linked mutable can be created using a specified private key.
        """
        self.basedir = "cli/Put/linked-mutable-with-key"
        await self._test_mutable_specified_key(
            lambda do_cli, pempath, datapath: do_cli(
                "put", "--mutable", "--private-key-path", pempath.path, datapath.path,
            ),
        )

    async def _test_mutable_specified_key(
            self,
            run: Callable[[Any, FilePath, FilePath], Awaitable[tuple[int, bytes, bytes]]],
    ) -> None:
        """
        A helper for testing mutable creation.

        :param run: A function to do the creation.  It is called with
            ``self.do_cli`` and the path to a private key PEM file and a data
            file.  It returns whatever ``do_cli`` returns.
        """
        self.set_up_grid(oneshare=True)

        pempath = FilePath(__file__).parent().sibling("data").child("openssl-rsa-2048.txt")
        datapath = FilePath(self.basedir).child("data")
        datapath.setContent(b"Hello world" * 1024)

        (rc, out, err) = await run(self.do_cli, pempath, datapath)
        self.assertEqual(rc, 0, (out, err))
        cap = from_string(out.strip())
        # The capability is derived from the key we specified.
        privkey = load_pem_private_key(pempath.getContent(), password=None)
        assert isinstance(privkey, PrivateKey)
        pubkey = privkey.public_key()
        writekey, _, fingerprint = derive_mutable_keys((pubkey, privkey))
        self.assertEqual(
            (writekey, fingerprint),
            (cap.writekey, cap.fingerprint),
        )
        # Also the capability we were given actually refers to the data we
        # uploaded.
        (rc, out, err) = await self.do_cli("get", out.strip())
        self.assertEqual(rc, 0, (out, err))
        self.assertEqual(out, datapath.getContent().decode("ascii"))

    def test_mutable(self):
        # echo DATA1 | tahoe put --mutable - uploaded.txt
        # echo DATA2 | tahoe put - uploaded.txt # should modify-in-place
        # tahoe get uploaded.txt, compare against DATA2

        self.basedir = "cli/Put/mutable"
        self.set_up_grid(oneshare=True)

        DATA1 = b"data" * 100
        fn1 = os.path.join(self.basedir, "DATA1")
        fileutil.write(fn1, DATA1)
        DATA2 = b"two\xff" * 100
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
                      self.do_cli("get", "tahoe:uploaded.txt", return_bytes=True))
        d.addCallback(lambda rc_out_err: self.failUnlessReallyEqual(rc_out_err[1], DATA2))
        return d

    def _check_mdmf_json(self, args):
         (rc, json, err) = args
         self.failUnlessEqual(rc, 0)
         self.failUnlessEqual(err, "")
         self.failUnlessIn('"format": "MDMF"', json)
         # We also want a valid MDMF cap to be in the json.
         self.failUnlessIn("URI:MDMF", json)
         self.failUnlessIn("URI:MDMF-RO", json)
         self.failUnlessIn("URI:MDMF-Verifier", json)

    def _check_sdmf_json(self, args):
        (rc, json, err) = args
        self.failUnlessEqual(rc, 0)
        self.failUnlessEqual(err, "")
        self.failUnlessIn('"format": "SDMF"', json)
        # We also want to see the appropriate SDMF caps.
        self.failUnlessIn("URI:SSK", json)
        self.failUnlessIn("URI:SSK-RO", json)
        self.failUnlessIn("URI:SSK-Verifier", json)

    def _check_chk_json(self, args):
        (rc, json, err) = args
        self.failUnlessEqual(rc, 0)
        self.failUnlessEqual(err, "")
        self.failUnlessIn('"format": "CHK"', json)
        # We also want to see the appropriate CHK caps.
        self.failUnlessIn("URI:CHK", json)
        self.failUnlessIn("URI:CHK-Verifier", json)

    def test_format(self):
        self.basedir = "cli/Put/format"
        self.set_up_grid(oneshare=True)
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
            def _list(args):
                (rc, out, err) = args
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
        self.set_up_grid(oneshare=True)
        data = "data" * 100000
        fn1 = os.path.join(self.basedir, "data")
        fileutil.write(fn1, data)
        d = self.do_cli("put", "--format=MDMF", fn1)
        def _got_cap(args):
            (rc, out, err) = args
            self.failUnlessEqual(rc, 0)
            self.cap = out.strip()
        d.addCallback(_got_cap)
        # Now try to write something to the cap using put.
        data2 = "data2" * 100000
        fn2 = os.path.join(self.basedir, "data2")
        fileutil.write(fn2, data2)
        d.addCallback(lambda ignored:
            self.do_cli("put", fn2, self.cap))
        def _got_put(args):
            (rc, out, err) = args
            self.failUnlessEqual(rc, 0)
            self.failUnlessIn(self.cap, out)
        d.addCallback(_got_put)
        # Now get the cap. We should see the data we just put there.
        d.addCallback(lambda ignored:
            self.do_cli("get", self.cap))
        def _got_data(args):
            (rc, out, err) = args
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
        def _got_data3(args):
            (rc, out, err) = args
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out, data3)
        d.addCallback(_got_data3)
        return d

    def test_put_to_sdmf_cap(self):
        self.basedir = "cli/Put/put_to_sdmf_cap"
        self.set_up_grid(oneshare=True)
        data = "data" * 100000
        fn1 = os.path.join(self.basedir, "data")
        fileutil.write(fn1, data)
        d = self.do_cli("put", "--format=SDMF", fn1)
        def _got_cap(args):
            (rc, out, err) = args
            self.failUnlessEqual(rc, 0)
            self.cap = out.strip()
        d.addCallback(_got_cap)
        # Now try to write something to the cap using put.
        data2 = "data2" * 100000
        fn2 = os.path.join(self.basedir, "data2")
        fileutil.write(fn2, data2)
        d.addCallback(lambda ignored:
            self.do_cli("put", fn2, self.cap))
        def _got_put(args):
            (rc, out, err) = args
            self.failUnlessEqual(rc, 0)
            self.failUnlessIn(self.cap, out)
        d.addCallback(_got_put)
        # Now get the cap. We should see the data we just put there.
        d.addCallback(lambda ignored:
            self.do_cli("get", self.cap))
        def _got_data(args):
            (rc, out, err) = args
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
        self.set_up_grid(oneshare=True)
        d = self.do_cli("put", "somefile", "fake:afile")
        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.assertEqual(len(out), 0, out)
        d.addCallback(_check)
        return d

    def test_immutable_from_file_unicode(self):
        # tahoe put "\u00E0 trier.txt" "\u00E0 trier.txt"

        a_trier_arg = u"\u00E0 trier.txt"

        skip_if_cannot_represent_filename(u"\u00E0 trier.txt")

        self.basedir = "cli/Put/immutable_from_file_unicode"
        self.set_up_grid(oneshare=True)

        rel_fn = os.path.join(str(self.basedir), u"\u00E0 trier.txt")
        # we make the file small enough to fit in a LIT file, for speed
        DATA = b"short file \xff bytes"
        fileutil.write(rel_fn, DATA)

        d = self.do_cli("create-alias", "tahoe")

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn.encode(get_io_encoding()), a_trier_arg))
        def _uploaded(args):
            (rc, out, err) = args
            readcap = out.strip()
            self.failUnless(readcap.startswith("URI:LIT:"), readcap)
            self.failUnlessIn("201 Created", err)
            self.readcap = readcap
        d.addCallback(_uploaded)

        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:" + a_trier_arg,
                                  return_bytes=True))
        d.addCallback(lambda rc_out_err:
                      self.failUnlessReallyEqual(rc_out_err[1], DATA))

        return d

    def test_no_leading_slash(self):
        self.basedir = "cli/Put/leading_slash"
        self.set_up_grid(oneshare=True)

        fn1 = os.path.join(self.basedir, "DATA1")

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res:
                      self.do_cli("put", fn1, "tahoe:/uploaded.txt"))
        def _check(args):
            (rc, out, err) = args
            self.assertEqual(rc, 1)
            self.failUnlessIn("must not start with a slash", err)
            self.assertEqual(len(out), 0, out)
        d.addCallback(_check)
        return d
