# -*- coding: utf-8 -*-
from allmydata.test import common
from allmydata.monitor import Monitor
from allmydata import check_results
from allmydata.interfaces import NotEnoughSharesError
from allmydata.immutable import upload
from allmydata.util.consumer import download_to_data
from twisted.internet import defer
from twisted.trial import unittest
import random
from allmydata.test.no_network import GridTestMixin

# We'll allow you to pass this test even if you trigger eighteen times as
# many disk reads and block fetches as would be optimal.
READ_LEEWAY = 18
MAX_DELTA_READS = 10 * READ_LEEWAY # N = 10

timeout=240 # François's ARM box timed out after 120 seconds of Verifier.test_corrupt_crypttext_hashtree

class RepairTestMixin:
    def failUnlessIsInstance(self, x, xtype):
        self.failUnless(isinstance(x, xtype), x)

    def _count_reads(self):
        sum_of_read_counts = 0
        for (i, ss, storedir) in self.iterate_servers():
            counters = ss.stats_provider.get_stats()['counters']
            sum_of_read_counts += counters.get('storage_server.read', 0)
        return sum_of_read_counts

    def _count_allocates(self):
        sum_of_allocate_counts = 0
        for (i, ss, storedir) in self.iterate_servers():
            counters = ss.stats_provider.get_stats()['counters']
            sum_of_allocate_counts += counters.get('storage_server.allocate', 0)
        return sum_of_allocate_counts

    def _count_writes(self):
        sum_of_write_counts = 0
        for (i, ss, storedir) in self.iterate_servers():
            counters = ss.stats_provider.get_stats()['counters']
            sum_of_write_counts += counters.get('storage_server.write', 0)
        return sum_of_write_counts

    def _stash_counts(self):
        self.before_repair_reads = self._count_reads()
        self.before_repair_allocates = self._count_allocates()
        self.before_repair_writes = self._count_writes()

    def _get_delta_counts(self):
        delta_reads = self._count_reads() - self.before_repair_reads
        delta_allocates = self._count_allocates() - self.before_repair_allocates
        delta_writes = self._count_writes() - self.before_repair_writes
        return (delta_reads, delta_allocates, delta_writes)

    def failIfBigger(self, x, y):
        self.failIf(x > y, "%s > %s" % (x, y))

    def upload_and_stash(self):
        c0 = self.g.clients[0]
        c1 = self.g.clients[1]
        c0.DEFAULT_ENCODING_PARAMETERS['max_segment_size'] = 12
        d = c0.upload(upload.Data(common.TEST_DATA, convergence=""))
        def _stash_uri(ur):
            self.uri = ur.uri
            self.c0_filenode = c0.create_node_from_uri(ur.uri)
            self.c1_filenode = c1.create_node_from_uri(ur.uri)
        d.addCallback(_stash_uri)
        return d

class Verifier(GridTestMixin, unittest.TestCase, RepairTestMixin):
    def test_check_without_verify(self):
        """Check says the file is healthy when none of the shares have been
        touched. It says that the file is unhealthy when all of them have
        been removed. It doesn't use any reads.
        """
        self.basedir = "repairer/Verifier/check_without_verify"
        self.set_up_grid(num_clients=2)
        d = self.upload_and_stash()
        d.addCallback(lambda ignored: self._stash_counts())
        d.addCallback(lambda ignored:
                      self.c0_filenode.check(Monitor(), verify=False))
        def _check(cr):
            self.failUnless(cr.is_healthy())
            delta_reads, delta_allocates, delta_writes = self._get_delta_counts()
            self.failIfBigger(delta_reads, 0)
        d.addCallback(_check)

        def _remove_all(ignored):
            for sh in self.find_uri_shares(self.uri):
                self.delete_share(sh)
        d.addCallback(_remove_all)

        d.addCallback(lambda ignored: self._stash_counts())
        d.addCallback(lambda ignored:
                      self.c0_filenode.check(Monitor(), verify=False))
        def _check2(cr):
            self.failIf(cr.is_healthy())
            delta_reads, delta_allocates, delta_writes = self._get_delta_counts()
            self.failIfBigger(delta_reads, 0)
        d.addCallback(_check2)
        return d

    def _help_test_verify(self, corruptor, judgement, shnum=0, debug=False):
        self.set_up_grid(num_clients=2)
        d = self.upload_and_stash()
        d.addCallback(lambda ignored: self._stash_counts())

        d.addCallback(lambda ignored:
                      self.corrupt_shares_numbered(self.uri, [shnum],corruptor,debug=debug))
        d.addCallback(lambda ignored:
                      self.c1_filenode.check(Monitor(), verify=True))
        def _check(vr):
            delta_reads, delta_allocates, delta_writes = self._get_delta_counts()
            self.failIfBigger(delta_reads, MAX_DELTA_READS)
            try:
                judgement(vr)
            except unittest.FailTest, e:
                # FailTest just uses e.args[0] == str
                new_arg = str(e.args[0]) + "\nvr.data is: " + str(vr.get_data())
                e.args = (new_arg,)
                raise
        d.addCallback(_check)
        return d

    def judge_no_problem(self, vr):
        """ Verify says the file is healthy when none of the shares have been
        touched in a way that matters. It doesn't use more than seven times
        as many reads as it needs."""
        self.failUnless(vr.is_healthy(), (vr, vr.is_healthy(), vr.get_data()))
        data = vr.get_data()
        self.failUnless(data['count-shares-good'] == 10, data)
        self.failUnless(len(data['sharemap']) == 10, data)
        self.failUnless(data['count-shares-needed'] == 3, data)
        self.failUnless(data['count-shares-expected'] == 10, data)
        self.failUnless(data['count-good-share-hosts'] == 10, data)
        self.failUnless(len(data['servers-responding']) == 10, data)
        self.failUnless(len(data['list-corrupt-shares']) == 0, data)

    def test_ok_no_corruption(self):
        self.basedir = "repairer/Verifier/ok_no_corruption"
        return self._help_test_verify(common._corrupt_nothing,
                                      self.judge_no_problem)

    def test_ok_filedata_size(self):
        self.basedir = "repairer/Verifier/ok_filedatasize"
        return self._help_test_verify(common._corrupt_size_of_file_data,
                                      self.judge_no_problem)

    def test_ok_sharedata_size(self):
        self.basedir = "repairer/Verifier/ok_sharedata_size"
        return self._help_test_verify(common._corrupt_size_of_sharedata,
                                      self.judge_no_problem)

    def test_ok_segment_size(self):
        self.basedir = "repairer/Verifier/test_ok_segment_size"
        return self._help_test_verify(common._corrupt_segment_size,
                                      self.judge_no_problem)

    def judge_visible_corruption(self, vr):
        """Corruption which is detected by the server means that the server
        will send you back a Failure in response to get_bucket instead of
        giving you the share data. Test that verifier handles these answers
        correctly. It doesn't use more than seven times as many reads as it
        needs."""
        self.failIf(vr.is_healthy(), (vr, vr.is_healthy(), vr.get_data()))
        data = vr.get_data()
        self.failUnless(data['count-shares-good'] == 9, data)
        self.failUnless(len(data['sharemap']) == 9, data)
        self.failUnless(data['count-shares-needed'] == 3, data)
        self.failUnless(data['count-shares-expected'] == 10, data)
        self.failUnless(data['count-good-share-hosts'] == 9, data)
        self.failUnless(len(data['servers-responding']) == 10, data)
        self.failUnless(len(data['list-corrupt-shares']) == 0, data)

    def test_corrupt_file_verno(self):
        self.basedir = "repairer/Verifier/corrupt_file_verno"
        return self._help_test_verify(common._corrupt_file_version_number,
                                      self.judge_visible_corruption)

    def judge_share_version_incompatibility(self, vr):
        # corruption of the share version (inside the container, the 1/2
        # value that determines whether we've got 4-byte offsets or 8-byte
        # offsets) to something larger than 2 will trigger a
        # ShareVersionIncompatible exception, which should be counted in
        # list-incompatible-shares, rather than list-corrupt-shares.
        self.failIf(vr.is_healthy(), (vr, vr.is_healthy(), vr.get_data()))
        data = vr.get_data()
        self.failUnlessEqual(data['count-shares-good'], 9)
        self.failUnlessEqual(len(data['sharemap']), 9)
        self.failUnlessEqual(data['count-shares-needed'], 3)
        self.failUnlessEqual(data['count-shares-expected'], 10)
        self.failUnlessEqual(data['count-good-share-hosts'], 9)
        self.failUnlessEqual(len(data['servers-responding']), 10)
        self.failUnlessEqual(len(data['list-corrupt-shares']), 0)
        self.failUnlessEqual(data['count-corrupt-shares'], 0)
        self.failUnlessEqual(len(data['list-incompatible-shares']), 1)
        self.failUnlessEqual(data['count-incompatible-shares'], 1)

    def test_corrupt_share_verno(self):
        self.basedir = "repairer/Verifier/corrupt_share_verno"
        return self._help_test_verify(common._corrupt_sharedata_version_number,
                                      self.judge_share_version_incompatibility)

    def judge_invisible_corruption(self, vr):
        # corruption of fields that the server does not check (which is most
        # of them), which will be detected by the client as it downloads
        # those shares.
        self.failIf(vr.is_healthy(), (vr, vr.is_healthy(), vr.get_data()))
        data = vr.get_data()
        self.failUnlessEqual(data['count-shares-good'], 9)
        self.failUnlessEqual(data['count-shares-needed'], 3)
        self.failUnlessEqual(data['count-shares-expected'], 10)
        self.failUnlessEqual(data['count-good-share-hosts'], 9)
        self.failUnlessEqual(data['count-corrupt-shares'], 1)
        self.failUnlessEqual(len(data['list-corrupt-shares']), 1)
        self.failUnlessEqual(data['count-incompatible-shares'], 0)
        self.failUnlessEqual(len(data['list-incompatible-shares']), 0)
        self.failUnlessEqual(len(data['servers-responding']), 10)
        self.failUnlessEqual(len(data['sharemap']), 9)

    def test_corrupt_sharedata_offset(self):
        self.basedir = "repairer/Verifier/corrupt_sharedata_offset"
        return self._help_test_verify(common._corrupt_offset_of_sharedata,
                                      self.judge_invisible_corruption)

    def test_corrupt_ueb_offset(self):
        self.basedir = "repairer/Verifier/corrupt_ueb_offset"
        return self._help_test_verify(common._corrupt_offset_of_uri_extension,
                                      self.judge_invisible_corruption)

    def test_corrupt_ueb_offset_shortread(self):
        self.basedir = "repairer/Verifier/corrupt_ueb_offset_shortread"
        return self._help_test_verify(common._corrupt_offset_of_uri_extension_to_force_short_read,
                                      self.judge_invisible_corruption)

    def test_corrupt_sharedata(self):
        self.basedir = "repairer/Verifier/corrupt_sharedata"
        return self._help_test_verify(common._corrupt_share_data,
                                      self.judge_invisible_corruption)

    def test_corrupt_ueb_length(self):
        self.basedir = "repairer/Verifier/corrupt_ueb_length"
        return self._help_test_verify(common._corrupt_length_of_uri_extension,
                                      self.judge_invisible_corruption)

    def test_corrupt_ueb(self):
        self.basedir = "repairer/Verifier/corrupt_ueb"
        return self._help_test_verify(common._corrupt_uri_extension,
                                      self.judge_invisible_corruption)

    def test_truncate_crypttext_hashtree(self):
        # change the start of the block hashtree, to truncate the preceding
        # crypttext hashtree
        self.basedir = "repairer/Verifier/truncate_crypttext_hashtree"
        return self._help_test_verify(common._corrupt_offset_of_block_hashes_to_truncate_crypttext_hashes,
                                      self.judge_invisible_corruption)

    def test_corrupt_block_hashtree_offset(self):
        self.basedir = "repairer/Verifier/corrupt_block_hashtree_offset"
        return self._help_test_verify(common._corrupt_offset_of_block_hashes,
                                      self.judge_invisible_corruption)

    def test_wrong_share_verno(self):
        self.basedir = "repairer/Verifier/wrong_share_verno"
        return self._help_test_verify(common._corrupt_sharedata_version_number_to_plausible_version,
                                      self.judge_invisible_corruption)

    def test_corrupt_share_hashtree_offset(self):
        self.basedir = "repairer/Verifier/corrupt_share_hashtree_offset"
        return self._help_test_verify(common._corrupt_offset_of_share_hashes,
                                      self.judge_invisible_corruption)

    def test_corrupt_crypttext_hashtree_offset(self):
        self.basedir = "repairer/Verifier/corrupt_crypttext_hashtree_offset"
        return self._help_test_verify(common._corrupt_offset_of_ciphertext_hash_tree,
                                      self.judge_invisible_corruption)

    def test_corrupt_crypttext_hashtree(self):
        self.basedir = "repairer/Verifier/corrupt_crypttext_hashtree"
        return self._help_test_verify(common._corrupt_crypttext_hash_tree,
                                      self.judge_invisible_corruption)

    def test_corrupt_crypttext_hashtree_byte_x221(self):
        self.basedir = "repairer/Verifier/corrupt_crypttext_hashtree_byte_9_bit_7"
        return self._help_test_verify(common._corrupt_crypttext_hash_tree_byte_x221,
                                      self.judge_invisible_corruption, debug=True)

    def test_corrupt_block_hashtree(self):
        self.basedir = "repairer/Verifier/corrupt_block_hashtree"
        return self._help_test_verify(common._corrupt_block_hashes,
                                      self.judge_invisible_corruption)

    def test_corrupt_share_hashtree(self):
        self.basedir = "repairer/Verifier/corrupt_share_hashtree"
        return self._help_test_verify(common._corrupt_share_hashes,
                                      self.judge_invisible_corruption)

    # TODO: the Verifier should decode to ciphertext and check it against the
    # crypttext-hash-tree. Check this by constructing a bogus file, in which
    # the crypttext-hash-tree is modified after encoding is done, but before
    # the UEB is finalized. The Verifier should see a valid
    # crypttext-hash-tree but then the ciphertext should show up as invalid.
    # Normally this could only be triggered by a bug in FEC decode.

    def OFF_test_each_byte(self):
        # this test takes 140s to run on my laptop, and doesn't have any
        # actual asserts, so it's commented out. It corrupts each byte of the
        # share in sequence, and checks to see which ones the Verifier
        # catches and which it misses. Ticket #819 contains details: there
        # are several portions of the share that are unused, for which
        # corruption is not supposed to be caught.
        #
        # If the test ran quickly, we could use the share size to compute the
        # offsets of these unused portions and assert that everything outside
        # of them was detected. We could then replace the rest of
        # Verifier.test_* (which takes 16s to run on my laptop) with this
        # one.
        self.basedir = "repairer/Verifier/each_byte"
        self.set_up_grid(num_clients=2)
        d = self.upload_and_stash()
        def _grab_sh0(res):
            self.sh0_file = [sharefile
                             for (shnum, serverid, sharefile)
                             in self.find_uri_shares(self.uri)
                             if shnum == 0][0]
            self.sh0_orig = open(self.sh0_file, "rb").read()
        d.addCallback(_grab_sh0)
        def _fix_sh0(res):
            f = open(self.sh0_file, "wb")
            f.write(self.sh0_orig)
            f.close()
        def _corrupt(ign, which):
            def _corruptor(s, debug=False):
                return s[:which] + chr(ord(s[which])^0x01) + s[which+1:]
            self.corrupt_shares_numbered(self.uri, [0], _corruptor)
        results = {}
        def _did_check(vr, i):
            #print "corrupt %d: healthy=%s" % (i, vr.is_healthy())
            results[i] = vr.is_healthy()
        def _start(ign):
            d = defer.succeed(None)
            for i in range(len(self.sh0_orig)):
                d.addCallback(_corrupt, i)
                d.addCallback(lambda ign:
                              self.c1_filenode.check(Monitor(), verify=True))
                d.addCallback(_did_check, i)
                d.addCallback(_fix_sh0)
            return d
        d.addCallback(_start)
        def _show_results(ign):
            f = open("test_each_byte_output", "w")
            for i in sorted(results.keys()):
                print >>f, "%d: %s" % (i, results[i])
            f.close()
            print "Please look in _trial_temp/test_each_byte_output for results"
        d.addCallback(_show_results)
        return d

# We'll allow you to pass this test even if you trigger thirty-five times as
# many block sends and disk writes as would be optimal.
WRITE_LEEWAY = 35
# Optimally, you could repair one of these (small) files in a single write.
DELTA_WRITES_PER_SHARE = 1 * WRITE_LEEWAY

class Repairer(GridTestMixin, unittest.TestCase, RepairTestMixin,
               common.ShouldFailMixin):

    def test_harness(self):
        # This test is actually to make sure our test harness works, rather
        # than testing anything about Tahoe code itself.

        self.basedir = "repairer/Repairer/test_code"
        self.set_up_grid(num_clients=2)
        d = self.upload_and_stash()

        d.addCallback(lambda ignored: self.find_uri_shares(self.uri))
        def _stash_shares(oldshares):
            self.oldshares = oldshares
        d.addCallback(_stash_shares)
        d.addCallback(lambda ignored: self.find_uri_shares(self.uri))
        def _compare(newshares):
            self.failUnlessEqual(newshares, self.oldshares)
        d.addCallback(_compare)

        def _delete_8(ignored):
            shnum = self.oldshares[0][0]
            self.delete_shares_numbered(self.uri, [shnum])
            for sh in self.oldshares[1:8]:
                self.delete_share(sh)
        d.addCallback(_delete_8)
        d.addCallback(lambda ignored: self.find_uri_shares(self.uri))
        d.addCallback(lambda shares: self.failUnlessEqual(len(shares), 2))

        d.addCallback(lambda ignored:
                      self.shouldFail(NotEnoughSharesError, "then_download",
                                      None,
                                      download_to_data, self.c1_filenode))

        d.addCallback(lambda ignored:
                      self.shouldFail(NotEnoughSharesError, "then_repair",
                                      None,
                                      self.c1_filenode.check_and_repair,
                                      Monitor(), verify=False))

        # test share corruption
        def _test_corrupt(ignored):
            olddata = {}
            shares = self.find_uri_shares(self.uri)
            for (shnum, serverid, sharefile) in shares:
                olddata[ (shnum, serverid) ] = open(sharefile, "rb").read()
            for sh in shares:
                self.corrupt_share(sh, common._corrupt_uri_extension)
            for (shnum, serverid, sharefile) in shares:
                newdata = open(sharefile, "rb").read()
                self.failIfEqual(olddata[ (shnum, serverid) ], newdata)
        d.addCallback(_test_corrupt)

        def _remove_all(ignored):
            for sh in self.find_uri_shares(self.uri):
                self.delete_share(sh)
        d.addCallback(_remove_all)
        d.addCallback(lambda ignored: self.find_uri_shares(self.uri))
        d.addCallback(lambda shares: self.failUnlessEqual(shares, []))

        return d

    def test_repair_from_deletion_of_1(self):
        """ Repair replaces a share that got deleted. """
        self.basedir = "repairer/Repairer/repair_from_deletion_of_1"
        self.set_up_grid(num_clients=2)
        d = self.upload_and_stash()

        d.addCallback(lambda ignored:
                      self.delete_shares_numbered(self.uri, [2]))
        d.addCallback(lambda ignored: self._stash_counts())
        d.addCallback(lambda ignored:
                      self.c0_filenode.check_and_repair(Monitor(),
                                                        verify=False))
        def _check_results(crr):
            self.failUnlessIsInstance(crr, check_results.CheckAndRepairResults)
            pre = crr.get_pre_repair_results()
            self.failUnlessIsInstance(pre, check_results.CheckResults)
            post = crr.get_post_repair_results()
            self.failUnlessIsInstance(post, check_results.CheckResults)
            delta_reads, delta_allocates, delta_writes = self._get_delta_counts()
            self.failIfBigger(delta_reads, MAX_DELTA_READS)
            self.failIfBigger(delta_allocates, DELTA_WRITES_PER_SHARE)
            self.failIf(pre.is_healthy())
            self.failUnless(post.is_healthy())

            # Now we inspect the filesystem to make sure that it has 10
            # shares.
            shares = self.find_uri_shares(self.uri)
            self.failIf(len(shares) < 10)
        d.addCallback(_check_results)

        d.addCallback(lambda ignored:
                      self.c0_filenode.check(Monitor(), verify=True))
        d.addCallback(lambda vr: self.failUnless(vr.is_healthy()))

        # Now we delete seven of the other shares, then try to download the
        # file and assert that it succeeds at downloading and has the right
        # contents. This can't work unless it has already repaired the
        # previously-deleted share #2.

        d.addCallback(lambda ignored:
                      self.delete_shares_numbered(self.uri, range(3, 10+1)))
        d.addCallback(lambda ignored: download_to_data(self.c1_filenode))
        d.addCallback(lambda newdata:
                      self.failUnlessEqual(newdata, common.TEST_DATA))
        return d

    def test_repair_from_deletion_of_7(self):
        """ Repair replaces seven shares that got deleted. """
        self.basedir = "repairer/Repairer/repair_from_deletion_of_7"
        self.set_up_grid(num_clients=2)
        d = self.upload_and_stash()
        d.addCallback(lambda ignored:
                      self.delete_shares_numbered(self.uri, range(7)))
        d.addCallback(lambda ignored: self._stash_counts())
        d.addCallback(lambda ignored:
                      self.c0_filenode.check_and_repair(Monitor(),
                                                        verify=False))
        def _check_results(crr):
            self.failUnlessIsInstance(crr, check_results.CheckAndRepairResults)
            pre = crr.get_pre_repair_results()
            self.failUnlessIsInstance(pre, check_results.CheckResults)
            post = crr.get_post_repair_results()
            self.failUnlessIsInstance(post, check_results.CheckResults)
            delta_reads, delta_allocates, delta_writes = self._get_delta_counts()

            self.failIfBigger(delta_reads, MAX_DELTA_READS)
            self.failIfBigger(delta_allocates, (DELTA_WRITES_PER_SHARE * 7))
            self.failIf(pre.is_healthy())
            self.failUnless(post.is_healthy(), post.data)

            # Make sure we really have 10 shares.
            shares = self.find_uri_shares(self.uri)
            self.failIf(len(shares) < 10)
        d.addCallback(_check_results)

        d.addCallback(lambda ignored:
                      self.c0_filenode.check(Monitor(), verify=True))
        d.addCallback(lambda vr: self.failUnless(vr.is_healthy()))

        # Now we delete seven of the other shares, then try to download the
        # file and assert that it succeeds at downloading and has the right
        # contents. This can't work unless it has already repaired the
        # previously-deleted share #2.

        d.addCallback(lambda ignored:
                      self.delete_shares_numbered(self.uri, range(3, 10+1)))
        d.addCallback(lambda ignored: download_to_data(self.c1_filenode))
        d.addCallback(lambda newdata:
                      self.failUnlessEqual(newdata, common.TEST_DATA))
        return d

    def test_repairer_servers_of_happiness(self):
        # The repairer is supposed to generate and place as many of the
        # missing shares as possible without caring about how they are
        # distributed.
        self.basedir = "repairer/Repairer/repairer_servers_of_happiness"
        self.set_up_grid(num_clients=2, num_servers=10)
        d = self.upload_and_stash()
        # Now delete some servers. We want to leave 3 servers, which
        # will allow us to restore the file to a healthy state without
        # distributing the shares widely enough to satisfy the default
        # happiness setting.
        def _delete_some_servers(ignored):
            for i in xrange(7):
                self.g.remove_server(self.g.servers_by_number[i].my_nodeid)

            assert len(self.g.servers_by_number) == 3

        d.addCallback(_delete_some_servers)
        # Now try to repair the file.
        d.addCallback(lambda ignored:
            self.c0_filenode.check_and_repair(Monitor(), verify=False))
        def _check_results(crr):
            self.failUnlessIsInstance(crr,
                                      check_results.CheckAndRepairResults)
            pre = crr.get_pre_repair_results()
            post = crr.get_post_repair_results()
            for p in (pre, post):
                self.failUnlessIsInstance(p, check_results.CheckResults)

            self.failIf(pre.is_healthy())
            self.failUnless(post.is_healthy())

        d.addCallback(_check_results)
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
    # In addition, I (warner) have recently refactored the rest of this class
    # to use the much-faster no_network.GridTestMixin, so this tests needs to
    # be updated before it will be able to run again.
    #
    # So we're turning this test off until we've done one or more of the
    # following:
    #  * remove some of these limitations
    #  * break the test up into smaller, more functionally-oriented pieces
    #  * simplify the repairer enough to let us be confident that it is free
    #    of lost-progress bugs

    def OFF_test_repair_from_corruption_of_1(self):
        d = defer.succeed(None)

        d.addCallback(self.find_all_shares)
        stash = [None]
        def _stash_it(res):
            stash[0] = res
            return res
        d.addCallback(_stash_it)
        def _put_it_all_back(ignored):
            self.replace_shares(stash[0], storage_index=self.uri.get_storage_index())
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
                self.failIf(after_repair_reads - before_repair_reads > (MAX_DELTA_READS * 2), (after_repair_reads, before_repair_reads))
                # The "* 2" in writes is because each server has two shares,
                # and it is reasonable for repairer to conclude that there
                # are two shares that it should upload, if the server fails
                # to serve the first share.
                self.failIf(after_repair_allocates - before_repair_allocates > (DELTA_WRITES_PER_SHARE * 2), (after_repair_allocates, before_repair_allocates))
                self.failIf(prerepairres.is_healthy(), (prerepairres.data, corruptor_func))
                self.failUnless(postrepairres.is_healthy(), (postrepairres.data, corruptor_func))

                # Now we inspect the filesystem to make sure that it has 10
                # shares.
                shares = self.find_all_shares()
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
