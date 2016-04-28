import os.path
from twisted.trial import unittest
from allmydata.util import fileutil
from allmydata.test.no_network import GridTestMixin
from allmydata.scripts import tahoe_mv
from .test_cli import CLITestMixin

timeout = 480 # deep_check takes 360s on Zandr's linksys box, others take > 240s

class Mv(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_mv_behavior(self):
        self.basedir = "cli/Mv/mv_behavior"
        self.set_up_grid()
        fn1 = os.path.join(self.basedir, "file1")
        DATA1 = "Nuclear launch codes"
        fileutil.write(fn1, DATA1)
        fn2 = os.path.join(self.basedir, "file2")
        DATA2 = "UML diagrams"
        fileutil.write(fn2, DATA2)
        # copy both files to the grid
        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res:
            self.do_cli("cp", fn1, "tahoe:"))
        d.addCallback(lambda res:
            self.do_cli("cp", fn2, "tahoe:"))

        # do mv file1 file3
        # (we should be able to rename files)
        d.addCallback(lambda res:
            self.do_cli("mv", "tahoe:file1", "tahoe:file3"))
        d.addCallback(lambda (rc, out, err):
            self.failUnlessIn("OK", out, "mv didn't rename a file"))

        # do mv file3 file2
        # (This should succeed without issue)
        d.addCallback(lambda res:
            self.do_cli("mv", "tahoe:file3", "tahoe:file2"))
        # Out should contain "OK" to show that the transfer worked.
        d.addCallback(lambda (rc,out,err):
            self.failUnlessIn("OK", out, "mv didn't output OK after mving"))

        # Next, make a remote directory.
        d.addCallback(lambda res:
            self.do_cli("mkdir", "tahoe:directory"))

        # mv file2 directory
        # (should fail with a descriptive error message; the CLI mv
        #  client should support this)
        d.addCallback(lambda res:
            self.do_cli("mv", "tahoe:file2", "tahoe:directory"))
        d.addCallback(lambda (rc, out, err):
            self.failUnlessIn(
                "Error: You can't overwrite a directory with a file", err,
                "mv shouldn't overwrite directories" ))

        # mv file2 directory/
        # (should succeed by making file2 a child node of directory)
        d.addCallback(lambda res:
            self.do_cli("mv", "tahoe:file2", "tahoe:directory/"))
        # We should see an "OK"...
        d.addCallback(lambda (rc, out, err):
            self.failUnlessIn("OK", out,
                            "mv didn't mv a file into a directory"))
        # ... and be able to GET the file
        d.addCallback(lambda res:
            self.do_cli("get", "tahoe:directory/file2", self.basedir + "new"))
        d.addCallback(lambda (rc, out, err):
            self.failUnless(os.path.exists(self.basedir + "new"),
                            "mv didn't write the destination file"))
        # ... and not find the file where it was before.
        d.addCallback(lambda res:
            self.do_cli("get", "tahoe:file2", "file2"))
        d.addCallback(lambda (rc, out, err):
            self.failUnlessIn("404", err,
                            "mv left the source file intact"))

        # Let's build:
        # directory/directory2/some_file
        # directory3
        d.addCallback(lambda res:
            self.do_cli("mkdir", "tahoe:directory/directory2"))
        d.addCallback(lambda res:
            self.do_cli("cp", fn2, "tahoe:directory/directory2/some_file"))
        d.addCallback(lambda res:
            self.do_cli("mkdir", "tahoe:directory3"))

        # Let's now try to mv directory/directory2/some_file to
        # directory3/some_file
        d.addCallback(lambda res:
            self.do_cli("mv", "tahoe:directory/directory2/some_file",
                        "tahoe:directory3/"))
        # We should have just some_file in tahoe:directory3
        d.addCallback(lambda res:
            self.do_cli("get", "tahoe:directory3/some_file", "some_file"))
        d.addCallback(lambda (rc, out, err):
            self.failUnless("404" not in err,
                              "mv didn't handle nested directories correctly"))
        d.addCallback(lambda res:
            self.do_cli("get", "tahoe:directory3/directory", "directory"))
        d.addCallback(lambda (rc, out, err):
            self.failUnlessIn("404", err,
                              "mv moved the wrong thing"))
        return d

    def test_mv_error_if_DELETE_fails(self):
        self.basedir = "cli/Mv/mv_error_if_DELETE_fails"
        self.set_up_grid()
        fn1 = os.path.join(self.basedir, "file1")
        DATA1 = "Nuclear launch codes"
        fileutil.write(fn1, DATA1)

        original_do_http = tahoe_mv.do_http
        def mock_do_http(method, url, body=""):
            if method == "DELETE":
                class FakeResponse:
                    def read(self):
                        return "response"
                resp = FakeResponse()
                resp.status = '500 Something Went Wrong'
                resp.reason = '*shrug*'
                return resp
            else:
                return original_do_http(method, url, body=body)
        tahoe_mv.do_http = mock_do_http

        # copy file to the grid
        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res:
            self.do_cli("cp", fn1, "tahoe:"))

        # do mv file1 file2
        d.addCallback(lambda res:
            self.do_cli("mv", "tahoe:file1", "tahoe:file2"))
        def _check( (rc, out, err) ):
            self.failIfIn("OK", out, "mv printed 'OK' even though the DELETE failed")
            self.failUnlessEqual(rc, 2)
        d.addCallback(_check)

        def _restore_do_http(res):
            tahoe_mv.do_http = original_do_http
            return res
        d.addBoth(_restore_do_http)
        return d

    def test_mv_without_alias(self):
        # doing 'tahoe mv' without explicitly specifying an alias or
        # creating the default 'tahoe' alias should fail with a useful
        # error message.
        self.basedir = "cli/Mv/mv_without_alias"
        self.set_up_grid()
        d = self.do_cli("mv", "afile", "anotherfile")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        # check to see that the validation extends to the
        # target argument by making an alias that will work with the first
        # one.
        d.addCallback(lambda ign: self.do_cli("create-alias", "havasu"))
        def _create_a_test_file(ign):
            self.test_file_path = os.path.join(self.basedir, "afile")
            fileutil.write(self.test_file_path, "puppies" * 100)
        d.addCallback(_create_a_test_file)
        d.addCallback(lambda ign: self.do_cli("put", self.test_file_path,
                                              "havasu:afile"))
        d.addCallback(lambda ign: self.do_cli("mv", "havasu:afile",
                                              "anotherfile"))
        d.addCallback(_check)
        return d

    def test_mv_with_nonexistent_alias(self):
        # doing 'tahoe mv' with an alias that doesn't exist should fail
        # with an informative error message.
        self.basedir = "cli/Mv/mv_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("mv", "fake:afile", "fake:anotherfile")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("fake", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        # check to see that the validation extends to the
        # target argument by making an alias that will work with the first
        # one.
        d.addCallback(lambda ign: self.do_cli("create-alias", "havasu"))
        def _create_a_test_file(ign):
            self.test_file_path = os.path.join(self.basedir, "afile")
            fileutil.write(self.test_file_path, "puppies" * 100)
        d.addCallback(_create_a_test_file)
        d.addCallback(lambda ign: self.do_cli("put", self.test_file_path,
                                              "havasu:afile"))
        d.addCallback(lambda ign: self.do_cli("mv", "havasu:afile",
                                              "fake:anotherfile"))
        d.addCallback(_check)
        return d
