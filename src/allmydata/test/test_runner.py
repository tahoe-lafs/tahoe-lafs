
from twisted.trial import unittest

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
        argv = ["create-client", "--basedir", c1]
        runner.runner(argv)
        self.failUnless(os.path.exists(c1))
        self.failUnless(os.path.exists(os.path.join(c1, "client.tac")))

    def test_introducer(self):
        basedir = self.workdir("test_introducer")
        c1 = os.path.join(basedir, "c1")
        argv = ["create-introducer", "--basedir", c1]
        runner.runner(argv)
        self.failUnless(os.path.exists(c1))
        self.failUnless(os.path.exists(os.path.join(c1, "introducer.tac")))

