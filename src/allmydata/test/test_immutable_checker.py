from allmydata.immutable import upload
from allmydata.test.common import SystemTestMixin, ShareManglingMixin
from allmydata.util import testutil
from twisted.internet import defer
from twisted.trial import unittest
import random, struct

class Test(ShareManglingMixin, unittest.TestCase):
    def setUp(self):
        # Set self.basedir to a temp dir which has the name of the current test method in its
        # name.
        self.basedir = self.mktemp()
        TEST_DATA="\x02"*(upload.Uploader.URI_LIT_SIZE_THRESHOLD+1)

        d = defer.maybeDeferred(SystemTestMixin.setUp, self)
        d.addCallback(lambda x: self.set_up_nodes())

        def _upload_a_file(ignored):
            d2 = self.clients[0].upload(upload.Data(TEST_DATA, convergence=""))
            d2.addCallback(lambda u: self.clients[0].create_node_from_uri(u.uri))
            return d2
        d.addCallback(_upload_a_file)

        def _stash_it(filenode):
            self.filenode = filenode
        d.addCallback(_stash_it)
        return d

    def _delete_a_share(self, unused=None):
        """ Delete one share. """

        shares = self.find_shares()
        ks = shares.keys()
        k = random.choice(ks)
        del shares[k]
        self.replace_shares(shares)

        return unused

    def _corrupt_a_share(self, unused=None):
        """ Exactly one bit of exactly one share on disk will be flipped (randomly selected from
        among the bits of the 'share data' -- the verifiable bits)."""

        shares = self.find_shares()
        ks = shares.keys()
        k = random.choice(ks)
        data = shares[k]

        (version, size, num_leases) = struct.unpack(">LLL", data[:0xc])
        sharedata = data[0xc:0xc+size]

        corruptedsharedata = testutil.flip_one_bit(sharedata)
        corrupteddata = data[:0xc]+corruptedsharedata+data[0xc+size:]
        shares[k] = corrupteddata

        self.replace_shares(shares)

        return unused

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
        d.addCallback(self.replace_shares)

        def _compare(res):
            oldshares = stash[0]
            self.failUnless(isinstance(oldshares, dict), oldshares)
            self.failUnlessEqual(oldshares, res)

        d.addCallback(self.find_shares)
        d.addCallback(_compare)

        d.addCallback(lambda ignore: self.replace_shares({}))
        d.addCallback(self.find_shares)
        d.addCallback(lambda x: self.failUnlessEqual(x, {}))

        return d

    def _count_reads(self):
        sum_of_read_counts = 0
        for client in self.clients:
            counters = client.stats_provider.get_stats()['counters']
            sum_of_read_counts += counters.get('storage_server.read', 0)
        return sum_of_read_counts

    def _count_allocates(self):
        sum_of_allocate_counts = 0
        for client in self.clients:
            counters = client.stats_provider.get_stats()['counters']
            sum_of_allocate_counts += counters.get('storage_server.allocate', 0)
        return sum_of_allocate_counts

    def test_check_without_verify(self):
        """ Check says the file is healthy when none of the shares have been
        touched.  It says that the file is unhealthy when all of them have
        been removed. It says that the file is healthy if one bit of one share
        has been flipped."""
        d = defer.succeed(self.filenode)
        def _check1(filenode):
            before_check_reads = self._count_reads()

            d2 = filenode.check(verify=False)
            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > 0, after_check_reads - before_check_reads)
                self.failUnless(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check1)

        d.addCallback(self._corrupt_a_share)
        def _check2(ignored):
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(verify=False)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > 0, after_check_reads - before_check_reads)

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check2)
        return d

        d.addCallback(lambda ignore: self.replace_shares({}))
        def _check3(ignored):
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(verify=False)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > 0, after_check_reads - before_check_reads)
                self.failIf(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check3)

        return d

    def test_check_with_verify(self):
        """ Check says the file is healthy when none of the shares have been touched.  It says
        that the file is unhealthy if one bit of one share has been flipped."""
        # N == 10.  2 is the "efficiency leeway" -- we'll allow you to pass this test even if
        # you trigger twice as many disk reads and blocks sends as would be optimal.
        DELTA_READS = 10 * 2
        d = defer.succeed(self.filenode)
        def _check1(filenode):
            before_check_reads = self._count_reads()

            d2 = filenode.check(verify=True)
            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                # print "delta was ", after_check_reads - before_check_reads
                self.failIf(after_check_reads - before_check_reads > DELTA_READS)
                self.failUnless(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check1)

        d.addCallback(self._corrupt_a_share)
        def _check2(ignored):
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(verify=True)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                # print "delta was ", after_check_reads - before_check_reads
                self.failIf(after_check_reads - before_check_reads > DELTA_READS)
                self.failIf(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check2)
        return d
    test_check_with_verify.todo = "We haven't implemented a verifier this thorough yet."

    def test_repair(self):
        """ Repair replaces a share that got deleted. """
        # N == 10.  2 is the "efficiency leeway" -- we'll allow you to pass this test even if
        # you trigger twice as many disk reads and blocks sends as would be optimal.
        DELTA_READS = 10 * 2
        # We'll allow you to pass this test only if you repair the missing share using only a
        # single allocate.
        DELTA_ALLOCATES = 1

        d = defer.succeed(self.filenode)
        d.addCallback(self._delete_a_share)

        def _repair_from_deletion(filenode):
            before_repair_reads = self._count_reads()
            before_repair_allocates = self._count_allocates()

            d2 = filenode.check_and_repair(verify=False)
            def _after_repair(checkandrepairresults):
                prerepairres = checkandrepairresults.get_pre_repair_results()
                postrepairres = checkandrepairresults.get_post_repair_results()
                after_repair_reads = self._count_reads()
                after_repair_allocates = self._count_allocates()

                # print "delta was ", after_repair_reads - before_repair_reads, after_repair_allocates - before_repair_allocates
                self.failIf(after_repair_reads - before_repair_reads > DELTA_READS)
                self.failIf(after_repair_allocates - before_repair_allocates > DELTA_ALLOCATES)
                self.failIf(prerepairres.is_healthy())
                self.failUnless(postrepairres.is_healthy())

                # Now we inspect the filesystem to make sure that it is really there.
                shares = self.find_shares()
                self.failIf(len(shares) < 10)

            d2.addCallback(_after_repair)
            return d2
        d.addCallback(_repair_from_deletion)

        d.addCallback(self._corrupt_a_share)

        def _repair_from_corruption(filenode):
            before_repair_reads = self._count_reads()
            before_repair_allocates = self._count_allocates()

            d2 = filenode.check_and_repair(verify=False)
            def _after_repair(checkandrepairresults):
                prerepairres = checkandrepairresults.get_pre_repair_results()
                postrepairres = checkandrepairresults.get_post_repair_results()
                after_repair_reads = self._count_reads()
                after_repair_allocates = self._count_allocates()

                # print "delta was ", after_repair_reads - before_repair_reads, after_repair_allocates - before_repair_allocates
                self.failIf(after_repair_reads - before_repair_reads > DELTA_READS)
                self.failIf(after_repair_allocates - before_repair_allocates > DELTA_ALLOCATES)
                self.failIf(prerepairres.is_healthy())
                self.failUnless(postrepairres.is_healthy())

            d2.addCallback(_after_repair)
            return d2
        d.addCallback(_repair_from_corruption)

        return d
    test_repair.todo = "We haven't implemented a repairer yet."
