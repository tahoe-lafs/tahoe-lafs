"""
Tests for the implementation of ``tahoe debug``.
"""

from .common import (
    SyncTestCase,
)

class DumpShareTests(SyncTestCase):
    """
    Tests for the implementation of ``tahoe debug dump-share``.
    """
    @given(
    def test_dump_share_scrape_info(self):
        """
        ``dump_share_scrape_info`` returns a ``dict`` containing all of the
        information to be dumped about the share.
        """
        sharefilepath = FilePath(self.mktemp())
        sharefilepath.setContent(immutable_share)
        with sharefilepath.open("rb") as sharefile:
            dumped = dump_share_impl(
                sharefile,
                sharefilepath.path,
                show_offsets=True,
            )

        self.assertThat(
