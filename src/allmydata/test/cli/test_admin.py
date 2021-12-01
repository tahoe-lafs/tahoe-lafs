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

from six.moves import StringIO

from testtools.matchers import (
    Contains,
)

from twisted.python.filepath import (
    FilePath,
)

from allmydata.scripts.admin import (
    migrate_crawler,
)
from allmydata.scripts.runner import (
    Options,
)
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
