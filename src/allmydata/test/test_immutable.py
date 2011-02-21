from allmydata.test import common
from allmydata.interfaces import NotEnoughSharesError
from allmydata.util.consumer import download_to_data
from allmydata import uri
from twisted.internet import defer
from twisted.trial import unittest
import random

from foolscap.api import eventually
from allmydata.util import log

from allmydata.immutable.downloader import finder

import mock

class MockNode(object):
    def __init__(self, check_reneging, check_fetch_failed):
        self.got = 0
        self.finished_d = defer.Deferred()
        self.segment_size = 78
        self.guessed_segment_size = 78
        self._no_more_shares = False
        self.check_reneging = check_reneging
        self.check_fetch_failed = check_fetch_failed
        self._si_prefix='aa'
        self.have_UEB = True
        self.share_hash_tree = mock.Mock()
        self.share_hash_tree.needed_hashes.return_value = False
        self.on_want_more_shares = None

    def when_finished(self):
        return self.finished_d
    def get_num_segments(self):
        return (5, True)
    def _calculate_sizes(self, guessed_segment_size):
        return {'block_size': 4, 'num_segments': 5}
    def no_more_shares(self):
        self._no_more_shares = True
    def got_shares(self, shares):
        if self.check_reneging:
            if self._no_more_shares:
                self.finished_d.errback(unittest.FailTest("The node was told by the share finder that it is destined to remain hungry, then was given another share."))
                return
        self.got += len(shares)
        log.msg("yyy 3 %s.got_shares(%s) got: %s" % (self, shares, self.got))
        if self.got == 3:
            self.finished_d.callback(True)
    def get_desired_ciphertext_hashes(self, *args, **kwargs):
        return iter([])
    def fetch_failed(self, *args, **kwargs):
        if self.check_fetch_failed:
            if self.finished_d:
                self.finished_d.errback(unittest.FailTest("The node was told by the segment fetcher that the download failed."))
                self.finished_d = None
    def want_more_shares(self):
        if self.on_want_more_shares:
            self.on_want_more_shares()
    def process_blocks(self, *args, **kwargs):
        if self.finished_d:
            self.finished_d.callback(None)

class TestShareFinder(unittest.TestCase):
    def test_no_reneging_on_no_more_shares_ever(self):
        # ticket #1191

        # Suppose that K=3 and you send two DYHB requests, the first
        # response offers two shares, and then the last offers one
        # share. If you tell your share consumer "no more shares,
        # ever", and then immediately tell them "oh, and here's
        # another share", then you lose.

        rcap = uri.CHKFileURI('a'*32, 'a'*32, 3, 99, 100)
        vcap = rcap.get_verify_cap()

        class MockServer(object):
            def __init__(self, buckets):
                self.version = {
                    'http://allmydata.org/tahoe/protocols/storage/v1': {
                        "tolerates-immutable-read-overrun": True
                        }
                    }
                self.buckets = buckets
                self.d = defer.Deferred()
                self.s = None
            def callRemote(self, methname, *args, **kwargs):
                d = defer.Deferred()

                # Even after the 3rd answer we're still hungry because
                # we're interested in finding a share on a 3rd server
                # so we don't have to download more than one share
                # from the first server. This is actually necessary to
                # trigger the bug.
                def _give_buckets_and_hunger_again():
                    d.callback(self.buckets)
                    self.s.hungry()
                eventually(_give_buckets_and_hunger_again)
                return d
        class MockIServer(object):
            def __init__(self, serverid, rref):
                self.serverid = serverid
                self.rref = rref
            def get_serverid(self):
                return self.serverid
            def get_rref(self):
                return self.rref

        mockserver1 = MockServer({1: mock.Mock(), 2: mock.Mock()})
        mockserver2 = MockServer({})
        mockserver3 = MockServer({3: mock.Mock()})
        mockstoragebroker = mock.Mock()
        servers = [ MockIServer("ms1", mockserver1),
                    MockIServer("ms2", mockserver2),
                    MockIServer("ms3", mockserver3), ]
        mockstoragebroker.get_servers_for_psi.return_value = servers
        mockdownloadstatus = mock.Mock()
        mocknode = MockNode(check_reneging=True, check_fetch_failed=True)

        s = finder.ShareFinder(mockstoragebroker, vcap, mocknode, mockdownloadstatus)

        mockserver1.s = s
        mockserver2.s = s
        mockserver3.s = s

        s.hungry()

        return mocknode.when_finished()

class Test(common.ShareManglingMixin, common.ShouldFailMixin, unittest.TestCase):
    def test_test_code(self):
        # The following process of stashing the shares, running
        # replace_shares, and asserting that the new set of shares equals the
        # old is more to test this test code than to test the Tahoe code...
        d = defer.succeed(None)
        d.addCallback(self.find_all_shares)
        stash = [None]
        def _stash_it(res):
            stash[0] = res
            return res
        d.addCallback(_stash_it)

        # The following process of deleting 8 of the shares and asserting
        # that you can't download it is more to test this test code than to
        # test the Tahoe code...
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
        """ Basic download. (This functionality is more or less already
        tested by test code in other modules, but this module is also going
        to test some more specific things about immutable download.)
        """
        d = defer.succeed(None)
        before_download_reads = self._count_reads()
        def _after_download(unused=None):
            after_download_reads = self._count_reads()
            #print before_download_reads, after_download_reads
            self.failIf(after_download_reads-before_download_reads > 41,
                        (after_download_reads, before_download_reads))
        d.addCallback(self._download_and_check_plaintext)
        d.addCallback(_after_download)
        return d

    def test_download_from_only_3_remaining_shares(self):
        """ Test download after 7 random shares (of the 10) have been
        removed."""
        d = defer.succeed(None)
        def _then_delete_7(unused=None):
            for i in range(7):
                self._delete_a_share()
        before_download_reads = self._count_reads()
        d.addCallback(_then_delete_7)
        def _after_download(unused=None):
            after_download_reads = self._count_reads()
            #print before_download_reads, after_download_reads
            self.failIf(after_download_reads-before_download_reads > 41, (after_download_reads, before_download_reads))
        d.addCallback(self._download_and_check_plaintext)
        d.addCallback(_after_download)
        return d

    def test_download_from_only_3_shares_with_good_crypttext_hash(self):
        """ Test download after 7 random shares (of the 10) have had their
        crypttext hash tree corrupted."""
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
        """ Test that download gives up quickly when it realizes there aren't
        enough shares out there."""
        for i in range(8):
            self._delete_a_share()
        d = self.shouldFail(NotEnoughSharesError, "delete 8", None,
                            download_to_data, self.n)
        # the new downloader pipelines a bunch of read requests in parallel,
        # so don't bother asserting anything about the number of reads
        return d

    def test_download_abort_if_too_many_corrupted_shares(self):
        """Test that download gives up quickly when it realizes there aren't
        enough uncorrupted shares out there. It should be able to tell
        because the corruption occurs in the sharedata version number, which
        it checks first."""
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
            #print before_download_reads, after_download_reads
            # To pass this test, you are required to give up before reading
            # all of the share data. Actually, we could give up sooner than
            # 45 reads, but currently our download code does 45 reads. This
            # test then serves as a "performance regression detector" -- if
            # you change download code so that it takes *more* reads, then
            # this test will fail.
            self.failIf(after_download_reads-before_download_reads > 45,
                        (after_download_reads, before_download_reads))
        d.addCallback(_after_attempt)
        return d


# XXX extend these tests to show bad behavior of various kinds from servers:
# raising exception from each remove_foo() method, for example

# XXX test disconnect DeadReferenceError from get_buckets and get_block_whatsit

# TODO: delete this whole file
