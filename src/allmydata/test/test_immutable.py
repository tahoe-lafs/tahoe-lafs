import random

from twisted.trial import unittest
from twisted.internet import defer
import mock
from foolscap.api import eventually

from allmydata.test import common
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common import TEST_DATA
from allmydata import uri
from allmydata.util import log
from allmydata.util.consumer import download_to_data

from allmydata.interfaces import NotEnoughSharesError
from allmydata.immutable.upload import Data
from allmydata.immutable.downloader import finder

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
            def get_name(self):
                return "name-%s" % self.serverid
            def get_version(self):
                return self.rref.version

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


class Test(GridTestMixin, unittest.TestCase, common.ShouldFailMixin):
    def startup(self, basedir):
        self.basedir = basedir
        self.set_up_grid(num_clients=2, num_servers=5)
        c1 = self.g.clients[1]
        # We need multiple segments to test crypttext hash trees that are
        # non-trivial (i.e. they have more than just one hash in them).
        c1.DEFAULT_ENCODING_PARAMETERS['max_segment_size'] = 12
        # Tests that need to test servers of happiness using this should
        # set their own value for happy -- the default (7) breaks stuff.
        c1.DEFAULT_ENCODING_PARAMETERS['happy'] = 1
        d = c1.upload(Data(TEST_DATA, convergence=""))
        def _after_upload(ur):
            self.uri = ur.uri
            self.filenode = self.g.clients[0].create_node_from_uri(ur.uri)
            return self.uri
        d.addCallback(_after_upload)
        return d

    def _stash_shares(self, shares):
        self.shares = shares

    def _download_and_check_plaintext(self, ign=None):
        num_reads = self._count_reads()
        d = download_to_data(self.filenode)
        def _after_download(result):
            self.failUnlessEqual(result, TEST_DATA)
            return self._count_reads() - num_reads
        d.addCallback(_after_download)
        return d

    def _shuffled(self, num_shnums):
        shnums = range(10)
        random.shuffle(shnums)
        return shnums[:num_shnums]

    def _count_reads(self):
        return sum([s.stats_provider.get_stats() ['counters'].get('storage_server.read', 0)
                    for s in self.g.servers_by_number.values()])


    def _count_allocates(self):
        return sum([s.stats_provider.get_stats() ['counters'].get('storage_server.allocate', 0)
                    for s in self.g.servers_by_number.values()])

    def _count_writes(self):
        return sum([s.stats_provider.get_stats() ['counters'].get('storage_server.write', 0)
                    for s in self.g.servers_by_number.values()])

    def test_test_code(self):
        # The following process of stashing the shares, running
        # replace_shares, and asserting that the new set of shares equals the
        # old is more to test this test code than to test the Tahoe code...
        d = self.startup("immutable/Test/code")
        d.addCallback(self.copy_shares)
        d.addCallback(self._stash_shares)
        d.addCallback(self._download_and_check_plaintext)

        # The following process of deleting 8 of the shares and asserting
        # that you can't download it is more to test this test code than to
        # test the Tahoe code...
        def _then_delete_8(ign):
            self.restore_all_shares(self.shares)
            self.delete_shares_numbered(self.uri, range(8))
        d.addCallback(_then_delete_8)
        d.addCallback(lambda ign:
                      self.shouldFail(NotEnoughSharesError, "download-2",
                                      "ran out of shares",
                                      download_to_data, self.filenode))
        return d

    def test_download(self):
        """ Basic download. (This functionality is more or less already
        tested by test code in other modules, but this module is also going
        to test some more specific things about immutable download.)
        """
        d = self.startup("immutable/Test/download")
        d.addCallback(self._download_and_check_plaintext)
        def _after_download(ign):
            num_reads = self._count_reads()
            #print num_reads
            self.failIf(num_reads > 41, num_reads)
        d.addCallback(_after_download)
        return d

    def test_download_from_only_3_remaining_shares(self):
        """ Test download after 7 random shares (of the 10) have been
        removed."""
        d = self.startup("immutable/Test/download_from_only_3_remaining_shares")
        d.addCallback(lambda ign:
                      self.delete_shares_numbered(self.uri, range(7)))
        d.addCallback(self._download_and_check_plaintext)
        def _after_download(num_reads):
            #print num_reads
            self.failIf(num_reads > 41, num_reads)
        d.addCallback(_after_download)
        return d

    def test_download_from_only_3_shares_with_good_crypttext_hash(self):
        """ Test download after 7 random shares (of the 10) have had their
        crypttext hash tree corrupted."""
        d = self.startup("download_from_only_3_shares_with_good_crypttext_hash")
        def _corrupt_7(ign):
            c = common._corrupt_offset_of_block_hashes_to_truncate_crypttext_hashes
            self.corrupt_shares_numbered(self.uri, self._shuffled(7), c)
        d.addCallback(_corrupt_7)
        d.addCallback(self._download_and_check_plaintext)
        return d

    def test_download_abort_if_too_many_missing_shares(self):
        """ Test that download gives up quickly when it realizes there aren't
        enough shares out there."""
        d = self.startup("download_abort_if_too_many_missing_shares")
        d.addCallback(lambda ign:
                      self.delete_shares_numbered(self.uri, range(8)))
        d.addCallback(lambda ign:
                      self.shouldFail(NotEnoughSharesError, "delete 8",
                                      "Last failure: None",
                                      download_to_data, self.filenode))
        # the new downloader pipelines a bunch of read requests in parallel,
        # so don't bother asserting anything about the number of reads
        return d

    def test_download_abort_if_too_many_corrupted_shares(self):
        """Test that download gives up quickly when it realizes there aren't
        enough uncorrupted shares out there. It should be able to tell
        because the corruption occurs in the sharedata version number, which
        it checks first."""
        d = self.startup("download_abort_if_too_many_corrupted_shares")
        def _corrupt_8(ign):
            c = common._corrupt_sharedata_version_number
            self.corrupt_shares_numbered(self.uri, self._shuffled(8), c)
        d.addCallback(_corrupt_8)
        def _try_download(ign):
            start_reads = self._count_reads()
            d2 = self.shouldFail(NotEnoughSharesError, "corrupt 8",
                                 "LayoutInvalid",
                                 download_to_data, self.filenode)
            def _check_numreads(ign):
                num_reads = self._count_reads() - start_reads
                #print num_reads

                # To pass this test, you are required to give up before
                # reading all of the share data. Actually, we could give up
                # sooner than 45 reads, but currently our download code does
                # 45 reads. This test then serves as a "performance
                # regression detector" -- if you change download code so that
                # it takes *more* reads, then this test will fail.
                self.failIf(num_reads > 45, num_reads)
            d2.addCallback(_check_numreads)
            return d2
        d.addCallback(_try_download)
        return d

    def test_download_to_data(self):
        d = self.startup("download_to_data")
        d.addCallback(lambda ign: self.filenode.download_to_data())
        d.addCallback(lambda data:
            self.failUnlessEqual(data, common.TEST_DATA))
        return d


    def test_download_best_version(self):
        d = self.startup("download_best_version")
        d.addCallback(lambda ign: self.filenode.download_best_version())
        d.addCallback(lambda data:
            self.failUnlessEqual(data, common.TEST_DATA))
        return d


    def test_get_best_readable_version(self):
        d = self.startup("get_best_readable_version")
        d.addCallback(lambda ign: self.filenode.get_best_readable_version())
        d.addCallback(lambda n2:
            self.failUnlessEqual(n2, self.filenode))
        return d

    def test_get_size_of_best_version(self):
        d = self.startup("get_size_of_best_version")
        d.addCallback(lambda ign: self.filenode.get_size_of_best_version())
        d.addCallback(lambda size:
            self.failUnlessEqual(size, len(common.TEST_DATA)))
        return d


# XXX extend these tests to show bad behavior of various kinds from servers:
# raising exception from each remove_foo() method, for example

# XXX test disconnect DeadReferenceError from get_buckets and get_block_whatsit

# TODO: delete this whole file
