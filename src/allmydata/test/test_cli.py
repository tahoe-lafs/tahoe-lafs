
from twisted.trial import unittest

from allmydata.util import fileutil
from allmydata import uri

# at least import the CLI scripts, even if we don't have any real tests for
# them yet.

from allmydata.scripts import cli, tahoe_ls, tahoe_get, tahoe_put, tahoe_rm
_hush_pyflakes = [tahoe_ls, tahoe_get, tahoe_put, tahoe_rm]


class CLI(unittest.TestCase):
    def test_options(self):
        fileutil.rm_dir("cli/test_options")
        fileutil.make_dirs("cli/test_options")
        open("cli/test_options/node.url","w").write("http://localhost:8080/\n")
        private_uri = uri.DirnodeURI("furl", "key").to_string()
        open("cli/test_options/my_private_dir.uri", "w").write(private_uri + "\n")
        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['root-uri'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--node-url", "http://example.org:8111/"])
        self.failUnlessEqual(o['node-url'], "http://example.org:8111/")
        self.failUnlessEqual(o['root-uri'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--root-uri", "private"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['root-uri'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        other_uri = uri.DirnodeURI("furl", "otherkey").to_string()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--root-uri", other_uri])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['root-uri'], other_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--root-uri", other_uri, "subdir"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['root-uri'], other_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "subdir")
