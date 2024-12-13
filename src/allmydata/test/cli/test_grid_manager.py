"""
Tests for the grid manager CLI.
"""

import os
from io import (
    BytesIO,
)
from unittest import (
    skipIf,
)

from twisted.trial.unittest import (
    TestCase,
)
from allmydata.cli.grid_manager import (
    grid_manager,
)

import click.testing

# these imports support the tests for `tahoe *` subcommands
from ..common_util import (
    run_cli,
)
from ..common import (
    superuser,
)
from twisted.internet.defer import (
    inlineCallbacks,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python.runtime import (
    platform,
)
from allmydata.util import jsonbytes as json

class GridManagerCommandLine(TestCase):
    """
    Test the mechanics of the `grid-manager` command
    """

    def setUp(self):
        self.runner = click.testing.CliRunner()
        super(GridManagerCommandLine, self).setUp()

    def invoke_and_check(self, *args, **kwargs):
        """Invoke a command with the runner and ensure it succeeded."""
        result = self.runner.invoke(*args, **kwargs)
        if result.exception is not None:
            raise result.exc_info[1].with_traceback(result.exc_info[2])
        self.assertEqual(result.exit_code, 0, result)
        return result

    def test_create(self):
        """
        Create a new grid-manager
        """
        with self.runner.isolated_filesystem():
            result = self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            self.assertEqual(["foo"], os.listdir("."))
            self.assertEqual(["config.json"], os.listdir("./foo"))
            result = self.invoke_and_check(grid_manager, ["--config", "foo", "public-identity"])
            self.assertTrue(result.output.startswith("pub-v0-"))

    def test_load_invalid(self):
        """
        An invalid config is reported to the user
        """
        with self.runner.isolated_filesystem():
            with open("config.json", "wb") as f:
                f.write(json.dumps_bytes({"not": "valid"}))
            result = self.runner.invoke(grid_manager, ["--config", ".", "public-identity"])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn(
                "Error loading Grid Manager",
                result.output,
            )

    def test_create_already(self):
        """
        It's an error to create a new grid-manager in an existing
        directory.
        """
        with self.runner.isolated_filesystem():
            result = self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            result = self.runner.invoke(grid_manager, ["--config", "foo", "create"])
            self.assertEqual(1, result.exit_code)
            self.assertIn(
                "Can't create",
                result.stdout,
            )

    def test_create_stdout(self):
        """
        Create a new grid-manager with no files
        """
        with self.runner.isolated_filesystem():
            result = self.invoke_and_check(grid_manager, ["--config", "-", "create"])
            self.assertEqual([], os.listdir("."))
            config = json.loads(result.output)
            self.assertEqual(
                {"private_key", "grid_manager_config_version"},
                set(config.keys()),
            )

    def test_list_stdout(self):
        """
        Load Grid Manager without files (using 'list' subcommand, but any will do)
        """
        config = {
            "storage_servers": {
                "storage0": {
                    "public_key": "pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga"
                }
            },
            "private_key": "priv-v0-6uinzyaxy3zvscwgsps5pxcfezhrkfb43kvnrbrhhfzyduyqnniq",
            "grid_manager_config_version": 0
        }
        result = self.invoke_and_check(
            grid_manager, ["--config", "-", "list"],
            input=BytesIO(json.dumps_bytes(config)),
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            "storage0: pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga\n",
            result.output,
        )

    def test_add_and_sign(self):
        """
        Add a new storage-server and sign a certificate for it
        """
        pubkey = "pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga"
        with self.runner.isolated_filesystem():
            self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            self.invoke_and_check(grid_manager, ["--config", "foo", "add", "storage0", pubkey])
            result = self.invoke_and_check(grid_manager, ["--config", "foo", "sign", "storage0", "10"])
            sigcert = json.loads(result.output)
            self.assertEqual({"certificate", "signature"}, set(sigcert.keys()))
            cert = json.loads(sigcert['certificate'])
            self.assertEqual(cert["public_key"], pubkey)

    def test_add_and_sign_second_cert(self):
        """
        Add a new storage-server and sign two certificates.
        """
        pubkey = "pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga"
        with self.runner.isolated_filesystem():
            self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            self.invoke_and_check(grid_manager, ["--config", "foo", "add", "storage0", pubkey])
            self.invoke_and_check(grid_manager, ["--config", "foo", "sign", "storage0", "10"])
            self.invoke_and_check(grid_manager, ["--config", "foo", "sign", "storage0", "10"])
            # we should now have two certificates stored
            self.assertEqual(
                set(FilePath("foo").listdir()),
                {'storage0.cert.1', 'storage0.cert.0', 'config.json'},
            )

    def test_add_twice(self):
        """
        An error is reported trying to add an existing server
        """
        pubkey0 = "pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga"
        pubkey1 = "pub-v0-5ysc55trfvfvg466v46j4zmfyltgus3y2gdejifctv7h4zkuyveq"
        with self.runner.isolated_filesystem():
            self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            self.invoke_and_check(grid_manager, ["--config", "foo", "add", "storage0", pubkey0])
            result = self.runner.invoke(grid_manager, ["--config", "foo", "add", "storage0", pubkey1])
            self.assertNotEquals(result.exit_code, 0)
            self.assertIn(
                "A storage-server called 'storage0' already exists",
                result.output,
            )

    def test_add_list_remove(self):
        """
        Add a storage server, list it, remove it.
        """
        pubkey = "pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga"
        with self.runner.isolated_filesystem():
            self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            self.invoke_and_check(grid_manager, ["--config", "foo", "add", "storage0", pubkey])
            self.invoke_and_check(grid_manager, ["--config", "foo", "sign", "storage0", "1"])

            result = self.invoke_and_check(grid_manager, ["--config", "foo", "list"])
            names = [
                line.split(':')[0]
                for line in result.output.strip().split('\n')
                if not line.startswith("  ")  # "cert" lines start with whitespace
            ]
            self.assertEqual(names, ["storage0"])

            self.invoke_and_check(grid_manager, ["--config", "foo", "remove", "storage0"])

            result = self.invoke_and_check(grid_manager, ["--config", "foo", "list"])
            self.assertEqual(result.output.strip(), "")

    def test_remove_missing(self):
        """
        Error reported when removing non-existant server
        """
        with self.runner.isolated_filesystem():
            self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            result = self.runner.invoke(grid_manager, ["--config", "foo", "remove", "storage0"])
            self.assertNotEquals(result.exit_code, 0)
            self.assertIn(
                "No storage-server called 'storage0' exists",
                result.output,
            )

    def test_sign_missing(self):
        """
        Error reported when signing non-existant server
        """
        with self.runner.isolated_filesystem():
            self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            result = self.runner.invoke(grid_manager, ["--config", "foo", "sign", "storage0", "42"])
            self.assertNotEquals(result.exit_code, 0)
            self.assertIn(
                "No storage-server called 'storage0' exists",
                result.output,
            )

    @skipIf(platform.isWindows(), "We don't know how to set permissions on Windows.")
    @skipIf(superuser, "cannot test as superuser with all permissions")
    def test_sign_bad_perms(self):
        """
        Error reported if we can't create certificate file
        """
        pubkey = "pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga"
        with self.runner.isolated_filesystem():
            self.invoke_and_check(grid_manager, ["--config", "foo", "create"])
            self.invoke_and_check(grid_manager, ["--config", "foo", "add", "storage0", pubkey])
            # make the directory un-writable (so we can't create a new cert)
            os.chmod("foo", 0o550)
            result = self.runner.invoke(grid_manager, ["--config", "foo", "sign", "storage0", "42"])
            self.assertEquals(result.exit_code, 1)
            self.assertIn(
                "Permission denied",
                result.output,
            )


class TahoeAddGridManagerCert(TestCase):
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
            stdin=b"the cert",
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
            stdin=b"the cert",
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
        fake_cert = b"""{"certificate": "", "signature": ""}"""

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
            b"foo = foo.cert",
            config_data,
        )
        self.assertIn("foo.cert", nodepath.listdir())
        with nodepath.child("foo.cert").open("r") as f:
            self.assertEqual(
                json.load(f),
                json.loads(fake_cert)
            )
