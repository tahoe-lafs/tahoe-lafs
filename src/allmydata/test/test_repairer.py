from allmydata.test import common
from allmydata.monitor import Monitor
from allmydata import check_results
from allmydata.interfaces import NotEnoughSharesError
from allmydata.immutable import repairer
from twisted.internet import defer
from twisted.trial import unittest
import random

# We'll allow you to pass this test even if you trigger eighteen times as
# many disk reads and block fetches as would be optimal.
READ_LEEWAY = 18
DELTA_READS = 10 * READ_LEEWAY # N = 10

class Verifier(common.ShareManglingMixin, unittest.TestCase):
    def test_check_without_verify(self):
        """Check says the file is healthy when none of the shares have been
        touched. It says that the file is unhealthy when all of them have
        been removed. It doesn't use any reads.
        """
        d = defer.succeed(self.filenode)
        def _check1(filenode):
            before_check_reads = self._count_reads()

            d2 = filenode.check(Monitor(), verify=False)
            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > 0, after_check_reads - before_check_reads)
                self.failUnless(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check1)

        d.addCallback(lambda ignore: self.replace_shares({}, storage_index=self.uri.storage_index))
        def _check2(ignored):
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(Monitor(), verify=False)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > 0, after_check_reads - before_check_reads)
                self.failIf(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check2)

        return d

    def _help_test_verify(self, corruptor_funcs, judgement_func):
        d = defer.succeed(None)

        d.addCallback(self.find_shares)
        stash = [None]
        def _stash_it(res):
            stash[0] = res
            return res
        d.addCallback(_stash_it)
        def _put_it_all_back(ignored):
            self.replace_shares(stash[0], storage_index=self.uri.storage_index)
            return ignored

        def _verify_after_corruption(shnum, corruptor_func):
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(Monitor(), verify=True)
            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > DELTA_READS, (after_check_reads, before_check_reads))
                try:
                    return judgement_func(checkresults)
                except Exception, le:
                    le.args = tuple(le.args + ("corruptor_func: " + corruptor_func.__name__,))
                    raise

            d2.addCallback(_after_check)
            return d2

        for corruptor_func in corruptor_funcs:
            d.addCallback(self._corrupt_a_random_share, corruptor_func)
            d.addCallback(_verify_after_corruption, corruptor_func)
            d.addCallback(_put_it_all_back)

        return d

    def test_verify_no_problem(self):
        """ Verify says the file is healthy when none of the shares have been
        touched in a way that matters. It doesn't use more than seven times
        as many reads as it needs."""
        def judge(checkresults):
            self.failUnless(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 10, data)
            self.failUnless(len(data['sharemap']) == 10, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['list-corrupt-shares']) == 0, data)
        return self._help_test_verify([
            common._corrupt_nothing,
            common._corrupt_size_of_file_data,
            common._corrupt_size_of_sharedata,
            common._corrupt_segment_size, ], judge)

    def test_verify_server_visible_corruption(self):
        """Corruption which is detected by the server means that the server
        will send you back a Failure in response to get_bucket instead of
        giving you the share data. Test that verifier handles these answers
        correctly. It doesn't use more than seven times as many reads as it
        needs."""
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            # The server might fail to serve up its other share as well as
            # the corrupted one, so count-shares-good could be 8 or 9.
            self.failUnless(data['count-shares-good'] in (8, 9), data)
            self.failUnless(len(data['sharemap']) in (8, 9,), data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            # The server may have served up the non-corrupted share, or it
            # may not have, so the checker could have detected either 4 or 5
            # good servers.
            self.failUnless(data['count-good-share-hosts'] in (4, 5), data)
            self.failUnless(len(data['servers-responding']) in (4, 5), data)
            # If the server served up the other share, then the checker
            # should consider it good, else it should not.
            self.failUnless((data['count-shares-good'] == 9) == (data['count-good-share-hosts'] == 5), data)
            self.failUnless(len(data['list-corrupt-shares']) == 0, data)
        return self._help_test_verify([
            common._corrupt_file_version_number,
            ], judge)

    def test_verify_share_incompatibility(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(len(data['sharemap']) == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
        return self._help_test_verify([
            common._corrupt_sharedata_version_number,
            ], judge)

    def test_verify_server_invisible_corruption(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_offset_of_sharedata,
            common._corrupt_offset_of_uri_extension,
            common._corrupt_offset_of_uri_extension_to_force_short_read,
            common._corrupt_share_data,
            common._corrupt_length_of_uri_extension,
            common._corrupt_uri_extension,
            ], judge)

    def test_verify_server_invisible_corruption_offset_of_block_hashtree_to_truncate_crypttext_hashtree_TODO(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_offset_of_block_hashes_to_truncate_crypttext_hashes,
            ], judge)
    test_verify_server_invisible_corruption_offset_of_block_hashtree_to_truncate_crypttext_hashtree_TODO.todo = "Verifier doesn't yet properly detect this kind of corruption."

    def test_verify_server_invisible_corruption_offset_of_block_hashtree_TODO(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_offset_of_block_hashes,
            ], judge)
    test_verify_server_invisible_corruption_offset_of_block_hashtree_TODO.todo = "Verifier doesn't yet properly detect this kind of corruption."

    def test_verify_server_invisible_corruption_sharedata_plausible_version(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_sharedata_version_number_to_plausible_version,
            ], judge)

    def test_verify_server_invisible_corruption_offset_of_share_hashtree_TODO(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_offset_of_share_hashes,
            ], judge)
    test_verify_server_invisible_corruption_offset_of_share_hashtree_TODO.todo = "Verifier doesn't yet properly detect this kind of corruption."

    def test_verify_server_invisible_corruption_offset_of_ciphertext_hashtree_TODO(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_offset_of_ciphertext_hash_tree,
            ], judge)
    test_verify_server_invisible_corruption_offset_of_ciphertext_hashtree_TODO.todo = "Verifier doesn't yet properly detect this kind of corruption."

    def test_verify_server_invisible_corruption_cryptext_hash_tree_TODO(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_crypttext_hash_tree,
            ], judge)
    test_verify_server_invisible_corruption_cryptext_hash_tree_TODO.todo = "Verifier doesn't yet properly detect this kind of corruption."

    def test_verify_server_invisible_corruption_block_hash_tree_TODO(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_block_hashes,
            ], judge)
    test_verify_server_invisible_corruption_block_hash_tree_TODO.todo = "Verifier doesn't yet properly detect this kind of corruption."

    def test_verify_server_invisible_corruption_share_hash_tree_TODO(self):
        def judge(checkresults):
            self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data()))
            data = checkresults.get_data()
            self.failUnless(data['count-shares-good'] == 9, data)
            self.failUnless(data['count-shares-needed'] == 3, data)
            self.failUnless(data['count-shares-expected'] == 10, data)
            self.failUnless(data['count-good-share-hosts'] == 5, data)
            self.failUnless(data['count-corrupt-shares'] == 1, (data,))
            self.failUnless(len(data['list-corrupt-shares']) == 1, data)
            self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
            self.failUnless(len(data['list-incompatible-shares']) == 0, data)
            self.failUnless(len(data['servers-responding']) == 5, data)
            self.failUnless(len(data['sharemap']) == 9, data)
        return self._help_test_verify([
            common._corrupt_share_hashes,
            ], judge)
    test_verify_server_invisible_corruption_share_hash_tree_TODO.todo = "Verifier doesn't yet properly detect this kind of corruption."

# We'll allow you to pass this test even if you trigger thirty-five times as many block sends
# and disk writes as would be optimal.
WRITE_LEEWAY = 35
# Optimally, you could repair one of these (small) files in a single write.
DELTA_WRITES_PER_SHARE = 1 * WRITE_LEEWAY

class DownUpConnector(unittest.TestCase):
    def test_deferred_satisfaction(self):
        duc = repairer.DownUpConnector()
        duc.registerProducer(None, True) # just because you have to call registerProducer first
        # case 1: total data in buf is < requested data at time of request
        duc.write('\x01')
        d = duc.read_encrypted(2, False)
        def _then(data):
            self.failUnlessEqual(len(data), 2)
            self.failUnlessEqual(data[0], '\x01')
            self.failUnlessEqual(data[1], '\x02')
        d.addCallback(_then)
        duc.write('\x02')
        return d

    def test_extra(self):
        duc = repairer.DownUpConnector()
        duc.registerProducer(None, True) # just because you have to call registerProducer first
        # case 1: total data in buf is < requested data at time of request
        duc.write('\x01')
        d = duc.read_encrypted(2, False)
        def _then(data):
            self.failUnlessEqual(len(data), 2)
            self.failUnlessEqual(data[0], '\x01')
            self.failUnlessEqual(data[1], '\x02')
        d.addCallback(_then)
        duc.write('\x02\0x03')
        return d

    def test_premature_close(self):
        duc = repairer.DownUpConnector()
        duc.registerProducer(None, True) # just because you have to call registerProducer first
        d = duc.read_encrypted(2, False)
        duc.close()
        def _callb(f):
            self.fail("Should have errbacked.")
        def _errb(f):
            f.trap(repairer.PrematureClose)
            self.failUnlessEqual(f.value.requested, 2)
            # Okay, you pass the test.
        d.addCallbacks(_callb, _errb)
        return d

class Repairer(common.ShareManglingMixin, unittest.TestCase):
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
        d.addCallback(self.replace_shares, storage_index=self.uri.storage_index)

        def _compare(res):
            oldshares = stash[0]
            self.failUnless(isinstance(oldshares, dict), oldshares)
            self.failUnlessEqual(oldshares, res)

        d.addCallback(self.find_shares)
        d.addCallback(_compare)

        d.addCallback(lambda ignore: self.replace_shares({}, storage_index=self.uri.storage_index))
        d.addCallback(self.find_shares)
        d.addCallback(lambda x: self.failUnlessEqual(x, {}))

        # The following process of deleting 8 of the shares and asserting
        # that you can't download it is more to test this test code than to
        # test the Tahoe code...
        def _then_delete_8(unused=None):
            self.replace_shares(stash[0], storage_index=self.uri.storage_index)
            for i in range(8):
                self._delete_a_share()
        d.addCallback(_then_delete_8)

        def _then_download(unused=None):
            self.downloader = self.clients[1].getServiceNamed("downloader")
            d = self.downloader.download_to_data(self.uri)

            def _after_download_callb(result):
                self.fail() # should have gotten an errback instead
                return result
            def _after_download_errb(failure):
                failure.trap(NotEnoughSharesError)
                return None # success!
            d.addCallbacks(_after_download_callb, _after_download_errb)
        d.addCallback(_then_download)

        # The following process of deleting 8 of the shares and asserting
        # that you can't repair it is more to test this test code than to
        # test the Tahoe code...
        d.addCallback(_then_delete_8)

        def _then_repair(unused=None):
            d2 = self.filenode.check_and_repair(Monitor(), verify=False)
            def _after_repair_callb(result):
                self.fail() # should have gotten an errback instead
                return result
            def _after_repair_errb(f):
                f.trap(NotEnoughSharesError)
                return None # success!
            d2.addCallbacks(_after_repair_callb, _after_repair_errb)
            return d2
        d.addCallback(_then_repair)

        return d

    def test_repair_from_deletion_of_1(self):
        """ Repair replaces a share that got deleted. """
        d = defer.succeed(None)
        d.addCallback(self._delete_a_share, sharenum=2)

        def _repair_from_deletion_of_1(unused):
            before_repair_reads = self._count_reads()
            before_repair_allocates = self._count_writes()

            d2 = self.filenode.check_and_repair(Monitor(), verify=False)
            def _after_repair(checkandrepairresults):
                assert isinstance(checkandrepairresults, check_results.CheckAndRepairResults), checkandrepairresults
                prerepairres = checkandrepairresults.get_pre_repair_results()
                assert isinstance(prerepairres, check_results.CheckResults), prerepairres
                postrepairres = checkandrepairresults.get_post_repair_results()
                assert isinstance(postrepairres, check_results.CheckResults), postrepairres
                after_repair_reads = self._count_reads()
                after_repair_allocates = self._count_writes()

                # print "delta was ", after_repair_reads - before_repair_reads, after_repair_allocates - before_repair_allocates
                self.failIf(after_repair_reads - before_repair_reads > DELTA_READS)
                self.failIf(after_repair_allocates - before_repair_allocates > DELTA_WRITES_PER_SHARE, (after_repair_allocates, before_repair_allocates))
                self.failIf(prerepairres.is_healthy())
                self.failUnless(postrepairres.is_healthy())

                # Now we inspect the filesystem to make sure that it has 10
                # shares.
                shares = self.find_shares()
                self.failIf(len(shares) < 10)

                # Now we assert that the verifier reports the file as healthy.
                d3 = self.filenode.check(Monitor(), verify=True)
                def _after_verify(verifyresults):
                    self.failUnless(verifyresults.is_healthy())
                d3.addCallback(_after_verify)

                # Now we delete seven of the other shares, then try to
                # download the file and assert that it succeeds at
                # downloading and has the right contents. This can't work
                # unless it has already repaired the previously-deleted share
                # #2.
                def _then_delete_7_and_try_a_download(unused=None):
                    for sharenum in range(3, 10):
                        self._delete_a_share(sharenum=sharenum)

                    return self._download_and_check_plaintext()
                d3.addCallback(_then_delete_7_and_try_a_download)
                return d3

            d2.addCallback(_after_repair)
            return d2
        d.addCallback(_repair_from_deletion_of_1)
        return d

    def test_repair_from_deletion_of_7(self):
        """ Repair replaces seven shares that got deleted. """
        shares = self.find_shares()
        self.failIf(len(shares) != 10)
        d = defer.succeed(None)

        def _delete_7(unused=None):
            shnums = range(10)
            random.shuffle(shnums)
            for sharenum in shnums[:7]:
                self._delete_a_share(sharenum=sharenum)
        d.addCallback(_delete_7)

        def _repair_from_deletion_of_7(unused):
            before_repair_reads = self._count_reads()
            before_repair_allocates = self._count_writes()

            d2 = self.filenode.check_and_repair(Monitor(), verify=False)
            def _after_repair(checkandrepairresults):
                assert isinstance(checkandrepairresults, check_results.CheckAndRepairResults), checkandrepairresults
                prerepairres = checkandrepairresults.get_pre_repair_results()
                assert isinstance(prerepairres, check_results.CheckResults), prerepairres
                postrepairres = checkandrepairresults.get_post_repair_results()
                assert isinstance(postrepairres, check_results.CheckResults), postrepairres
                after_repair_reads = self._count_reads()
                after_repair_allocates = self._count_writes()

                # print "delta was ", after_repair_reads - before_repair_reads, after_repair_allocates - before_repair_allocates
                self.failIf(after_repair_reads - before_repair_reads > DELTA_READS)
                self.failIf(after_repair_allocates - before_repair_allocates > (DELTA_WRITES_PER_SHARE * 7), (after_repair_allocates, before_repair_allocates))
                self.failIf(prerepairres.is_healthy())
                self.failUnless(postrepairres.is_healthy(), postrepairres.data)

                # Now we inspect the filesystem to make sure that it has 10
                # shares.
                shares = self.find_shares()
                self.failIf(len(shares) < 10)

                # Now we assert that the verifier reports the file as healthy.
                d3 = self.filenode.check(Monitor(), verify=True)
                def _after_verify(verifyresults):
                    self.failUnless(verifyresults.is_healthy())
                d3.addCallback(_after_verify)

                # Now we delete seven random shares, then try to download the
                # file and assert that it succeeds at downloading and has the
                # right contents.
                def _then_delete_7_and_try_a_download(unused=None):
                    for i in range(7):
                        self._delete_a_share()
                    return self._download_and_check_plaintext()
                d3.addCallback(_then_delete_7_and_try_a_download)
                return d3

            d2.addCallback(_after_repair)
            return d2
        d.addCallback(_repair_from_deletion_of_7)
        return d

    # why is test_repair_from_corruption_of_1 disabled? Read on:
    #
    # As recently documented in NEWS for the 1.3.0 release, the current
    # immutable repairer suffers from several limitations:
    #
    #  * minimalistic verifier: it's just download without decryption, so we
    #    don't look for corruption in N-k shares, and for many fields (those
    #    which are the same in all shares) we only look for corruption in a
    #    single share
    #
    #  * some kinds of corruption cause download to fail (when it ought to
    #    just switch to a different share), so repair will fail on these too
    #
    #  * RIStorageServer doesn't offer a way to delete old corrupt immutable
    #    shares (the authority model is not at all clear), so the best the
    #    repairer can do is to put replacement shares on new servers,
    #    unfortunately leaving the corrupt shares in place
    #
    # This test is pretty strenuous: it asserts that the repairer does the
    # ideal thing in 8 distinct situations, with randomized corruption in
    # each. Because of the aforementioned limitations, it is highly unlikely
    # to pass any of these. We're also concerned that the download-fails case
    # can provoke a lost-progress bug (one was fixed, but there might be more
    # lurking), which will cause the test to fail despite a ".todo" marker,
    # and will probably cause subsequent unrelated tests to fail too (due to
    # "unclean reactor" problems).
    #
    # So we're turning this test off until we've done one or more of the
    # following:
    #  * remove some of these limitations
    #  * break the test up into smaller, more functionally-oriented pieces
    #  * simplify the repairer enough to let us be confident that it is free
    #    of lost-progress bugs

    def OFF_test_repair_from_corruption_of_1(self):
        d = defer.succeed(None)

        d.addCallback(self.find_shares)
        stash = [None]
        def _stash_it(res):
            stash[0] = res
            return res
        d.addCallback(_stash_it)
        def _put_it_all_back(ignored):
            self.replace_shares(stash[0], storage_index=self.uri.storage_index)
            return ignored

        def _repair_from_corruption(shnum, corruptor_func):
            before_repair_reads = self._count_reads()
            before_repair_allocates = self._count_writes()

            d2 = self.filenode.check_and_repair(Monitor(), verify=True)
            def _after_repair(checkandrepairresults):
                prerepairres = checkandrepairresults.get_pre_repair_results()
                postrepairres = checkandrepairresults.get_post_repair_results()
                after_repair_reads = self._count_reads()
                after_repair_allocates = self._count_writes()

                # The "* 2" in reads is because you might read a whole share
                # before figuring out that it is corrupted. It might be
                # possible to make this delta reads number a little tighter.
                self.failIf(after_repair_reads - before_repair_reads > (DELTA_READS * 2), (after_repair_reads, before_repair_reads))
                # The "* 2" in writes is because each server has two shares,
                # and it is reasonable for repairer to conclude that there
                # are two shares that it should upload, if the server fails
                # to serve the first share.
                self.failIf(after_repair_allocates - before_repair_allocates > (DELTA_WRITES_PER_SHARE * 2), (after_repair_allocates, before_repair_allocates))
                self.failIf(prerepairres.is_healthy(), (prerepairres.data, corruptor_func))
                self.failUnless(postrepairres.is_healthy(), (postrepairres.data, corruptor_func))

                # Now we inspect the filesystem to make sure that it has 10
                # shares.
                shares = self.find_shares()
                self.failIf(len(shares) < 10)

                # Now we assert that the verifier reports the file as healthy.
                d3 = self.filenode.check(Monitor(), verify=True)
                def _after_verify(verifyresults):
                    self.failUnless(verifyresults.is_healthy())
                d3.addCallback(_after_verify)

                # Now we delete seven of the other shares, then try to
                # download the file and assert that it succeeds at
                # downloading and has the right contents. This can't work
                # unless it has already repaired the previously-corrupted share.
                def _then_delete_7_and_try_a_download(unused=None):
                    shnums = range(10)
                    shnums.remove(shnum)
                    random.shuffle(shnums)
                    for sharenum in shnums[:7]:
                        self._delete_a_share(sharenum=sharenum)

                    return self._download_and_check_plaintext()
                d3.addCallback(_then_delete_7_and_try_a_download)
                return d3

            d2.addCallback(_after_repair)
            return d2

        for corruptor_func in (
            common._corrupt_file_version_number,
            common._corrupt_sharedata_version_number,
            common._corrupt_offset_of_sharedata,
            common._corrupt_offset_of_uri_extension,
            common._corrupt_offset_of_uri_extension_to_force_short_read,
            common._corrupt_share_data,
            common._corrupt_length_of_uri_extension,
            common._corrupt_uri_extension,
            ):
            # Now we corrupt a share...
            d.addCallback(self._corrupt_a_random_share, corruptor_func)
            # And repair...
            d.addCallback(_repair_from_corruption, corruptor_func)

        return d
    #test_repair_from_corruption_of_1.todo = "Repairer doesn't properly replace corrupted shares yet."


# XXX extend these tests to show that the checker detects which specific
# share on which specific server is broken -- this is necessary so that the
# checker results can be passed to the repairer and the repairer can go ahead
# and upload fixes without first doing what is effectively a check (/verify)
# run

# XXX extend these tests to show bad behavior of various kinds from servers:
# raising exception from each remove_foo() method, for example

# XXX test disconnect DeadReferenceError from get_buckets and get_block_whatsit

# XXX test corruption that truncates other hash trees than just the crypttext
# hash tree

# XXX test the notify-someone-about-corruption feature (also implement that
# feature)

# XXX test whether repairer (downloader) correctly downloads a file even if
# to do so it has to acquire shares from a server that has already tried to
# serve it a corrupted share. (I don't think the current downloader would
# pass this test, depending on the kind of corruption.)
