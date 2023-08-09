"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

# We're going to override stdin/stderr, so want to match their behavior on respective Python versions.
from io import StringIO

from twisted.python.usage import (
    UsageError,
)
from twisted.python.filepath import (
    FilePath,
)

from testtools.matchers import (
    Contains,
)

from allmydata.scripts.admin import (
    migrate_crawler,
    add_grid_manager_cert,
)
from allmydata.scripts.runner import (
    Options,
)
from allmydata.util import jsonbytes as json
from ..common import (
    SyncTestCase,
)


class AdminMigrateCrawler(SyncTestCase):
    """
    Tests related to 'tahoe admin migrate-crawler'
    """

    def test_already(self):
        """
        We've already migrated; don't do it again.
        """

        root = FilePath(self.mktemp())
        storage = root.child("storage")
        storage.makedirs()
        with storage.child("lease_checker.state.json").open("w") as f:
            f.write(b"{}\n")

        top = Options()
        top.parseOptions([
            "admin", "migrate-crawler",
            "--basedir", storage.parent().path,
        ])
        options = top.subOptions
        while hasattr(options, "subOptions"):
            options = options.subOptions
        options.stdout = StringIO()
        migrate_crawler(options)

        self.assertThat(
            options.stdout.getvalue(),
            Contains("Already converted:"),
        )

    def test_usage(self):
        """
        We've already migrated; don't do it again.
        """

        root = FilePath(self.mktemp())
        storage = root.child("storage")
        storage.makedirs()
        with storage.child("lease_checker.state.json").open("w") as f:
            f.write(b"{}\n")

        top = Options()
        top.parseOptions([
            "admin", "migrate-crawler",
            "--basedir", storage.parent().path,
        ])
        options = top.subOptions
        while hasattr(options, "subOptions"):
            options = options.subOptions
        self.assertThat(
            str(options),
            Contains("security issues with pickle")
        )


fake_cert = {
    "certificate": "{\"expires\":1601687822,\"public_key\":\"pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga\",\"version\":1}",
    "signature": "fvjd3uvvupf2v6tnvkwjd473u3m3inyqkwiclhp7balmchkmn3px5pei3qyfjnhymq4cjcwvbpqmcwwnwswdtrfkpnlaxuih2zbdmda"
}


class AddCertificateOptions(SyncTestCase):
    """
    Tests for 'tahoe admin add-grid-manager-cert' option validation
    """
    def setUp(self):
        self.tahoe = Options()
        return super(AddCertificateOptions, self).setUp()

    def test_parse_no_data(self):
        """
        When no data is passed to stdin an error is produced
        """
        self.tahoe.stdin = StringIO("")
        self.tahoe.stderr = StringIO()  # suppress message

        with self.assertRaises(UsageError) as ctx:
            self.tahoe.parseOptions(
                [
                    "admin", "add-grid-manager-cert",
                    "--name", "random-name",
                    "--filename", "-",
                ]
            )

        self.assertIn(
            "Reading certificate from stdin failed",
            str(ctx.exception)
        )

    def test_read_cert_file(self):
        """
        A certificate can be read from a file
        """
        tmp = self.mktemp()
        with open(tmp, "wb") as f:
            f.write(json.dumps_bytes(fake_cert))

        # certificate should be loaded
        self.tahoe.parseOptions(
            [
                "admin", "add-grid-manager-cert",
                "--name", "random-name",
                "--filename", tmp,
            ]
        )
        opts = self.tahoe.subOptions.subOptions
        self.assertEqual(
            fake_cert,
            opts.certificate_data
        )

    def test_bad_certificate(self):
        """
        Unparseable data produces an error
        """
        self.tahoe.stdin = StringIO("{}")
        self.tahoe.stderr = StringIO()  # suppress message

        with self.assertRaises(UsageError) as ctx:
            self.tahoe.parseOptions(
                [
                    "admin", "add-grid-manager-cert",
                    "--name", "random-name",
                    "--filename", "-",
                ]
            )

        self.assertIn(
            "Grid Manager certificate must contain",
            str(ctx.exception)
        )


class AddCertificateCommand(SyncTestCase):
    """
    Tests for 'tahoe admin add-grid-manager-cert' operation
    """

    def setUp(self):
        self.tahoe = Options()
        self.node_path = FilePath(self.mktemp())
        self.node_path.makedirs()
        with self.node_path.child("tahoe.cfg").open("w") as f:
            f.write(b"# minimal test config\n")
        return super(AddCertificateCommand, self).setUp()

    def test_add_one(self):
        """
        Adding a certificate succeeds
        """
        self.tahoe.stdin = StringIO(json.dumps(fake_cert))
        self.tahoe.stderr = StringIO()
        self.tahoe.parseOptions(
            [
                "--node-directory", self.node_path.path,
                "admin", "add-grid-manager-cert",
                "--name", "zero",
                "--filename", "-",
            ]
        )
        self.tahoe.subOptions.subOptions.stdin = self.tahoe.stdin
        self.tahoe.subOptions.subOptions.stderr = self.tahoe.stderr
        rc = add_grid_manager_cert(self.tahoe.subOptions.subOptions)

        self.assertEqual(rc, 0)
        self.assertEqual(
            {"zero.cert", "tahoe.cfg"},
            set(self.node_path.listdir())
        )
        self.assertIn(
            "There are now 1 certificates",
            self.tahoe.stderr.getvalue()
        )

    def test_add_two(self):
        """
        An error message is produced when adding a certificate with a
        duplicate name.
        """
        self.tahoe.stdin = StringIO(json.dumps(fake_cert))
        self.tahoe.stderr = StringIO()
        self.tahoe.parseOptions(
            [
                "--node-directory", self.node_path.path,
                "admin", "add-grid-manager-cert",
                "--name", "zero",
                "--filename", "-",
            ]
        )
        self.tahoe.subOptions.subOptions.stdin = self.tahoe.stdin
        self.tahoe.subOptions.subOptions.stderr = self.tahoe.stderr
        rc = add_grid_manager_cert(self.tahoe.subOptions.subOptions)
        self.assertEqual(rc, 0)

        self.tahoe.stdin = StringIO(json.dumps(fake_cert))
        self.tahoe.parseOptions(
            [
                "--node-directory", self.node_path.path,
                "admin", "add-grid-manager-cert",
                "--name", "zero",
                "--filename", "-",
            ]
        )
        self.tahoe.subOptions.subOptions.stdin = self.tahoe.stdin
        self.tahoe.subOptions.subOptions.stderr = self.tahoe.stderr
        rc = add_grid_manager_cert(self.tahoe.subOptions.subOptions)
        self.assertEqual(rc, 1)
        self.assertIn(
            "Already have certificate for 'zero'",
            self.tahoe.stderr.getvalue()
        )
