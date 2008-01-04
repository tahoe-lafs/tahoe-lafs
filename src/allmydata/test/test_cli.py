
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
        fileutil.make_dirs("cli/test_options/private")
        open("cli/test_options/node.url","w").write("http://localhost:8080/\n")
        filenode_uri = uri.WriteableSSKFileURI(writekey="\x00"*16,
                                               fingerprint="\x00"*32)
        private_uri = uri.NewDirectoryURI(filenode_uri).to_string()
        open("cli/test_options/private/root_dir.cap", "w").write(private_uri + "\n")
        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['dir-uri'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--node-url", "http://example.org:8111/"])
        self.failUnlessEqual(o['node-url'], "http://example.org:8111/")
        self.failUnlessEqual(o['dir-uri'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-uri", "root"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['dir-uri'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        other_filenode_uri = uri.WriteableSSKFileURI(writekey="\x11"*16,
                                                     fingerprint="\x11"*32)
        other_uri = uri.NewDirectoryURI(other_filenode_uri).to_string()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-uri", other_uri])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['dir-uri'], other_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-uri", other_uri, "subdir"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['dir-uri'], other_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "subdir")
