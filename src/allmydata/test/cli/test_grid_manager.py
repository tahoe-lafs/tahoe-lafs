
import os
import json

from ..common import SyncTestCase
from allmydata.cli.grid_manager import (
    grid_manager,
)

import click.testing


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
