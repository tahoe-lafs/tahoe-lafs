
from twisted.trial import unittest

import time
from cStringIO import StringIO
from twisted.python import usage
import sys, os.path
from allmydata.scripts import runner, debug
from allmydata.util import fileutil

class CreateNode(unittest.TestCase):
    def workdir(self, name):
        basedir = os.path.join("test_runner", "CreateNode", name)
        fileutil.make_dirs(basedir)
        return basedir

    def test_client(self):
        basedir = self.workdir("test_client")
        c1 = os.path.join(basedir, "c1")
        argv = ["--quiet", "create-client", "--basedir", c1]
        out,err = StringIO(), StringIO()
        rc = runner.runner(argv, stdout=out, stderr=err)
        self.failUnlessEqual(err.getvalue(), "")
        self.failUnlessEqual(out.getvalue(), "")
        self.failUnlessEqual(rc, 0)
        self.failUnless(os.path.exists(c1))
        self.failUnless(os.path.exists(os.path.join(c1, "client.tac")))

        # creating the client a second time should throw an exception
        out,err = StringIO(), StringIO()
        rc = runner.runner(argv, stdout=out, stderr=err)
        self.failIfEqual(rc, 0)
        self.failUnlessEqual(out.getvalue(), "")
        self.failUnless("The base directory already exists" in err.getvalue())

        c2 = os.path.join(basedir, "c2")
        argv = ["--quiet", "create-client", c2]
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
        argv = ["--quiet", "create-introducer", "--basedir", c1]
        out,err = StringIO(), StringIO()
        rc = runner.runner(argv, stdout=out, stderr=err)
        self.failUnlessEqual(err.getvalue(), "")
        self.failUnlessEqual(out.getvalue(), "")
        self.failUnlessEqual(rc, 0)
        self.failUnless(os.path.exists(c1))
        self.failUnless(os.path.exists(os.path.join(c1, "introducer.tac")))

        # creating the introducer a second time should throw an exception
        out,err = StringIO(), StringIO()
        rc = runner.runner(argv, stdout=out, stderr=err)
        self.failIfEqual(rc, 0)
        self.failUnlessEqual(out.getvalue(), "")
        self.failUnless("The base directory already exists" in err.getvalue())

        c2 = os.path.join(basedir, "c2")
        argv = ["--quiet", "create-introducer", c2]
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
        config = {'basedirs': ["missing_basedir"]}
        rc = debug.dump_root_dirnode(config, s)
        output = s.getvalue()
        self.failUnless("unable to read root dirnode file from" in output)
        self.failIfEqual(rc, 0)

class RunNode(unittest.TestCase):
    def workdir(self, name):
        basedir = os.path.join("test_runner", "RunNode", name)
        fileutil.make_dirs(basedir)
        return basedir

    def test_client(self):
        if sys.platform in ("win32", "cygwin"):
            # thus might not be entirely true, but I've yet to see proper
            # daemonization on a windows box. -warner
            raise unittest.SkipTest("twistd does not fork under windows")
        basedir = self.workdir("test_client")
        c1 = os.path.join(basedir, "c1")
        argv = ["--quiet", "create-client", "--basedir", c1]
        out,err = StringIO(), StringIO()
        rc = runner.runner(argv, stdout=out, stderr=err)
        self.failUnlessEqual(rc, 0)
        open(os.path.join(c1, "suicide_prevention_hotline_file"), "w").write("")
        open(os.path.join(c1, "introducer.furl"), "w").write("pb://xrndsskn2zuuian5ltnxrte7lnuqdrkz@127.0.0.1:55617/introducer\n")
        # now it's safe to start the node

        argv = ["--quiet", "start", c1]
        out,err = StringIO(), StringIO()
        rc = runner.runner(argv, stdout=out, stderr=err)
        self.failUnlessEqual(rc, 0)
        time.sleep(0.1) # the child process needs a moment to write the pidfile
        self.failUnless(os.path.exists(os.path.join(c1, "twistd.pid")))

        argv = ["--quiet", "restart", c1]
        out,err = StringIO(), StringIO()
        rc = runner.runner(argv, stdout=out, stderr=err)
        self.failUnlessEqual(rc, 0)
        time.sleep(0.1)
        self.failUnless(os.path.exists(os.path.join(c1, "twistd.pid")))

        argv = ["--quiet", "stop", c1]
        out,err = StringIO(), StringIO()
        rc = runner.runner(argv, stdout=out, stderr=err)
        self.failUnlessEqual(rc, 0)
        self.failIf(os.path.exists(os.path.join(c1, "twistd.pid")))

