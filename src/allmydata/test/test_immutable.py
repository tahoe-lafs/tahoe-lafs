from allmydata.test import common
from allmydata.interfaces import NotEnoughSharesError
from allmydata.util.consumer import download_to_data
from twisted.internet import defer
from twisted.trial import unittest
import random

class Test(common.ShareManglingMixin, unittest.TestCase):
    def test_test_code(self):
        # The following process of stashing the shares, running
        # replace_shares, and asserting that the new set of shares equals the
        # old is more to test this test code than to test the Tahoe code...
        d = defer.succeed(None)
        d.addCallback(self.find_shares)
        stash = [None]
        def _stash_it(res):
            stash[0] = res
            return res
        d.addCallback(_stash_it)

        # The following process of deleting 8 of the shares and asserting that you can't
        # download it is more to test this test code than to test the Tahoe code...
        def _then_delete_8(unused=None):
            self.replace_shares(stash[0], storage_index=self.uri.get_storage_index())
            for i in range(8):
                self._delete_a_share()
        d.addCallback(_then_delete_8)

        def _then_download(unused=None):
            d2 = download_to_data(self.n)

            def _after_download_callb(result):
                self.fail() # should have gotten an errback instead
                return result
            def _after_download_errb(failure):
                failure.trap(NotEnoughSharesError)
                return None # success!
            d2.addCallbacks(_after_download_callb, _after_download_errb)
            return d2
        d.addCallback(_then_download)

        return d

    def test_download(self):
        """ Basic download.  (This functionality is more or less already tested by test code in
        other modules, but this module is also going to test some more specific things about
        immutable download.)
        """
        d = defer.succeed(None)
        before_download_reads = self._count_reads()
        def _after_download(unused=None):
            after_download_reads = self._count_reads()
            self.failIf(after_download_reads-before_download_reads > 27, (after_download_reads, before_download_reads))
        d.addCallback(self._download_and_check_plaintext)
        d.addCallback(_after_download)
        return d

    def test_download_from_only_3_remaining_shares(self):
        """ Test download after 7 random shares (of the 10) have been removed. """
        d = defer.succeed(None)
        def _then_delete_7(unused=None):
            for i in range(7):
                self._delete_a_share()
        before_download_reads = self._count_reads()
        d.addCallback(_then_delete_7)
        def _after_download(unused=None):
            after_download_reads = self._count_reads()
            self.failIf(after_download_reads-before_download_reads > 27, (after_download_reads, before_download_reads))
        d.addCallback(self._download_and_check_plaintext)
        d.addCallback(_after_download)
        return d

    def test_download_from_only_3_shares_with_good_crypttext_hash(self):
        """ Test download after 7 random shares (of the 10) have had their crypttext hash tree corrupted. """
        d = defer.succeed(None)
        def _then_corrupt_7(unused=None):
            shnums = range(10)
            random.shuffle(shnums)
            for i in shnums[:7]:
                self._corrupt_a_share(None, common._corrupt_offset_of_block_hashes_to_truncate_crypttext_hashes, i)
        #before_download_reads = self._count_reads()
        d.addCallback(_then_corrupt_7)
        d.addCallback(self._download_and_check_plaintext)
        return d

    def test_download_abort_if_too_many_missing_shares(self):
        """ Test that download gives up quickly when it realizes there aren't enough shares out
        there."""
        d = defer.succeed(None)
        def _then_delete_8(unused=None):
            for i in range(8):
                self._delete_a_share()
        d.addCallback(_then_delete_8)

        before_download_reads = self._count_reads()
        def _attempt_to_download(unused=None):
            d2 = download_to_data(self.n)

            def _callb(res):
                self.fail("Should have gotten an error from attempt to download, not %r" % (res,))
            def _errb(f):
                self.failUnless(f.check(NotEnoughSharesError))
            d2.addCallbacks(_callb, _errb)
            return d2

        d.addCallback(_attempt_to_download)

        def _after_attempt(unused=None):
            after_download_reads = self._count_reads()
            # To pass this test, you are required to give up before actually trying to read any
            # share data.
            self.failIf(after_download_reads-before_download_reads > 0, (after_download_reads, before_download_reads))
        d.addCallback(_after_attempt)
        return d

    def test_download_abort_if_too_many_corrupted_shares(self):
        """ Test that download gives up quickly when it realizes there aren't enough uncorrupted
        shares out there. It should be able to tell because the corruption occurs in the
        sharedata version number, which it checks first."""
        d = defer.succeed(None)
        def _then_corrupt_8(unused=None):
            shnums = range(10)
            random.shuffle(shnums)
            for shnum in shnums[:8]:
                self._corrupt_a_share(None, common._corrupt_sharedata_version_number, shnum)
        d.addCallback(_then_corrupt_8)

        before_download_reads = self._count_reads()
        def _attempt_to_download(unused=None):
            d2 = download_to_data(self.n)

            def _callb(res):
                self.fail("Should have gotten an error from attempt to download, not %r" % (res,))
            def _errb(f):
                self.failUnless(f.check(NotEnoughSharesError))
            d2.addCallbacks(_callb, _errb)
            return d2

        d.addCallback(_attempt_to_download)

        def _after_attempt(unused=None):
            after_download_reads = self._count_reads()
            # To pass this test, you are required to give up before reading all of the share
            # data.  Actually, we could give up sooner than 45 reads, but currently our download
            # code does 45 reads.  This test then serves as a "performance regression detector"
            # -- if you change download code so that it takes *more* reads, then this test will
            # fail.
            self.failIf(after_download_reads-before_download_reads > 45, (after_download_reads, before_download_reads))
        d.addCallback(_after_attempt)
        return d


# XXX extend these tests to show bad behavior of various kinds from servers: raising exception from each remove_foo() method, for example

# XXX test disconnect DeadReferenceError from get_buckets and get_block_whatsit

