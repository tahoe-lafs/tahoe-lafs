
from twisted.trial import unittest

from cStringIO import StringIO
from twisted.python import usage
import os.path
from allmydata.scripts import runner
from allmydata.util import fileutil

class CreateNode(unittest.TestCase):
    def workdir(self, name):
        basedir = os.path.join("test_runner", name)
        fileutil.make_dirs(basedir)
        return basedir

    def test_client(self):
        basedir = self.workdir("test_client")
        c1 = os.path.join(basedir, "c1")
        argv = ["create-client", "--basedir", c1, "--quiet"]
        runner.runner(argv)
        self.failUnless(os.path.exists(c1))
        self.failUnless(os.path.exists(os.path.join(c1, "client.tac")))

        c2 = os.path.join(basedir, "c2")
        argv = ["create-client", "--quiet", c2]
        runner.runner(argv)
        self.failUnless(os.path.exists(c2))
        self.failUnless(os.path.exists(os.path.join(c2, "client.tac")))

        self.failUnlessRaises(usage.UsageError,
                              runner.runner,
                              ["create-client", "basedir", "extraarg"],
                              run_by_human=False)

        self.failUnlessRaises(usage.UsageError,
                              runner.runner,
                              ["create-client"],
                              run_by_human=False)

    def test_introducer(self):
        basedir = self.workdir("test_introducer")
        c1 = os.path.join(basedir, "c1")
        argv = ["create-introducer", "--basedir", c1, "--quiet"]
        runner.runner(argv)
        self.failUnless(os.path.exists(c1))
        self.failUnless(os.path.exists(os.path.join(c1, "introducer.tac")))

        c2 = os.path.join(basedir, "c2")
        argv = ["create-introducer", "--quiet", c2]
        runner.runner(argv)
        self.failUnless(os.path.exists(c2))
        self.failUnless(os.path.exists(os.path.join(c2, "introducer.tac")))

        self.failUnlessRaises(usage.UsageError,
                              runner.runner,
                              ["create-introducer", "basedir", "extraarg"],
                              run_by_human=False)

        self.failUnlessRaises(usage.UsageError,
                              runner.runner,
                              ["create-introducer"],
                              run_by_human=False)

    def test_subcommands(self):
        self.failUnlessRaises(usage.UsageError,
                              runner.runner,
                              [],
                              run_by_human=False)

class Diagnostics(unittest.TestCase):
    def test_dump_root_dirnode_failure(self):
        s = StringIO()
        rc = runner.dump_root_dirnode("missing_basedir", {}, s)
        output = s.getvalue()
        self.failUnless("unable to read root dirnode file from" in output)
        self.failIfEqual(rc, 0)
