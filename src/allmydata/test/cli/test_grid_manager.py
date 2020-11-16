
import os
import json

from ..common import (
    SyncTestCase,
    AsyncTestCase,
)
from allmydata.cli.grid_manager import (
    grid_manager,
)

import click.testing

# these imports support the tests for `tahoe *` subcommands
from ..common_util import (
    run_cli,
)
from twisted.internet.defer import (
    inlineCallbacks,
)
from twisted.python.filepath import (
    FilePath,
)


class GridManagerCommandLine(SyncTestCase):
    """
    Test the mechanics of the `grid-manager` command
    """

    def setUp(self):
        self.runner = click.testing.CliRunner()
        super(GridManagerCommandLine, self).setUp()

    def test_create(self):
        """
        Create a new grid-manager
        """
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(grid_manager, ["--config", "foo", "create"])
            self.assertEqual(["foo"], os.listdir("."))
            self.assertEqual(["config.json"], os.listdir("./foo"))
            result = self.runner.invoke(grid_manager, ["--config", "foo", "public-identity"])
            self.assertTrue(result.output.startswith("pub-v0-"))

    def test_create_stdout(self):
        """
        Create a new grid-manager with no files
        """
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(grid_manager, ["--config", "-", "create"])
            self.assertEqual([], os.listdir("."))
            config = json.loads(result.output)
            self.assertEqual(
                {"private_key", "grid_manager_config_version"},
                set(config.keys()),
            )

    def test_add_and_sign(self):
        """
        Add a new storage-server and sign a certificate for it
        """
        pubkey = "pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga"
        with self.runner.isolated_filesystem():
            self.runner.invoke(grid_manager, ["--config", "foo", "create"])
            self.runner.invoke(grid_manager, ["--config", "foo", "add", "storage0", pubkey])
            result = self.runner.invoke(grid_manager, ["--config", "foo", "sign", "storage0", "10"])
            sigcert = json.loads(result.output)
            self.assertEqual({"certificate", "signature"}, set(sigcert.keys()))
            cert = json.loads(sigcert['certificate'])
            self.assertEqual(cert["public_key"], pubkey)

    def test_add_list_remove(self):
        """
        Add a storage server, list it, remove it.
        """
        pubkey = "pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga"
        with self.runner.isolated_filesystem():
            self.runner.invoke(grid_manager, ["--config", "foo", "create"])
            self.runner.invoke(grid_manager, ["--config", "foo", "add", "storage0", pubkey])
            self.runner.invoke(grid_manager, ["--config", "foo", "sign", "storage0", "1"])

            result = self.runner.invoke(grid_manager, ["--config", "foo", "list"])
            names = [
                line.split(':')[0]
                for line in result.output.strip().split('\n')
                if not line.startswith("  ")  # "cert" lines start with whitespace
            ]
            self.assertEqual(names, ["storage0"])

            self.runner.invoke(grid_manager, ["--config", "foo", "remove", "storage0"])

            result = self.runner.invoke(grid_manager, ["--config", "foo", "list"])
            self.assertEqual(result.output.strip(), "")


class TahoeAddGridManagerCert(AsyncTestCase):
    """
    Test `tahoe admin add-grid-manager-cert` subcommand
    """

    @inlineCallbacks
    def test_help(self):
        """
        some kind of help is printed
        """
        code, out, err = yield run_cli("admin", "add-grid-manager-cert")
        self.assertEqual(err, "")
        self.assertNotEqual(0, code)

    @inlineCallbacks
    def test_no_name(self):
        """
        error to miss --name option
        """
        code, out, err = yield run_cli(
            "admin", "add-grid-manager-cert", "--filename", "-",
            stdin="the cert",
        )
        self.assertIn(
            "Must provide --name",
            out
        )

    @inlineCallbacks
    def test_no_filename(self):
        """
        error to miss --name option
        """
        code, out, err = yield run_cli(
            "admin", "add-grid-manager-cert", "--name", "foo",
            stdin="the cert",
        )
        self.assertIn(
            "Must provide --filename",
            out
        )

    @inlineCallbacks
    def test_add_one(self):
        """
        we can add a certificate
        """
        nodedir = self.mktemp()
        fake_cert = """{"certificate": "", "signature": ""}"""

        code, out, err = yield run_cli(
            "--node-directory", nodedir,
            "admin", "add-grid-manager-cert", "-f", "-", "--name", "foo",
            stdin=fake_cert,
            ignore_stderr=True,
        )
        nodepath = FilePath(nodedir)
        with nodepath.child("tahoe.cfg").open("r") as f:
            config_data = f.read()

        self.assertIn("tahoe.cfg", nodepath.listdir())
        self.assertIn(
            "foo = foo.cert",
            config_data,
        )
        self.assertIn("foo.cert", nodepath.listdir())
        with nodepath.child("foo.cert").open("r") as f:
            self.assertEqual(
                json.load(f),
                json.loads(fake_cert)
            )
