from allmydata.immutable import encode, upload
from allmydata.test.common import SystemTestMixin, ShareManglingMixin
from allmydata.util import testutil
from allmydata.monitor import Monitor
from allmydata.interfaces import IURI
from twisted.internet import defer
from twisted.trial import unittest
import random, struct

TEST_DATA="\x02"*(upload.Uploader.URI_LIT_SIZE_THRESHOLD+1)

def corrupt_field(data, offset, size):
    if random.random() < 0.5:
        return testutil.flip_one_bit(data, offset, size)
    else:
        return data[:offset]+testutil.insecurerandstr(size)+data[offset+size:]

def _corrupt_file_version_number(data):
    """ Scramble the file data -- the share file version number have one bit flipped or else
    will be changed to a random value."""
    return corrupt_field(data, 0x00, 4)

def _corrupt_size_of_file_data(data):
    """ Scramble the file data -- the field showing the size of the share data within the
    file will have one bit flipped or else will be changed to a random value. """
    return corrupt_field(data, 0x04, 4)

def _corrupt_sharedata_version_number(data):
    """ Scramble the file data -- the share data version number will have one bit flipped or
    else will be changed to a random value."""
    return corrupt_field(data, 0x0c, 4)

def _corrupt_segment_size(data):
    """ Scramble the file data -- the field showing the size of the segment will have one
    bit flipped or else be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x04, 4)
    else:
        return corrupt_field(data, 0x0c+0x04, 8)

def _corrupt_size_of_sharedata(data):
    """ Scramble the file data -- the field showing the size of the data within the share
    data will have one bit flipped or else will be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x08, 4)
    else:
        return corrupt_field(data, 0x0c+0x0c, 8)

def _corrupt_offset_of_sharedata(data):
    """ Scramble the file data -- the field showing the offset of the data within the share
    data will have one bit flipped or else be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x0c, 4)
    else:
        return corrupt_field(data, 0x0c+0x14, 8)

def _corrupt_offset_of_ciphertext_hash_tree(data):
    """ Scramble the file data -- the field showing the offset of the ciphertext hash tree
    within the share data will have one bit flipped or else be changed to a random value.
    """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x14, 4)
    else:
        return corrupt_field(data, 0x0c+0x24, 8)

def _corrupt_offset_of_block_hashes(data):
    """ Scramble the file data -- the field showing the offset of the block hash tree within
    the share data will have one bit flipped or else will be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x18, 4)
    else:
        return corrupt_field(data, 0x0c+0x2c, 8)

def _corrupt_offset_of_share_hashes(data):
    """ Scramble the file data -- the field showing the offset of the share hash tree within
    the share data will have one bit flipped or else will be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x1c, 4)
    else:
        return corrupt_field(data, 0x0c+0x34, 8)

def _corrupt_offset_of_uri_extension(data):
    """ Scramble the file data -- the field showing the offset of the uri extension will
    have one bit flipped or else will be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x20, 4)
    else:
        return corrupt_field(data, 0x0c+0x3c, 8)

def _corrupt_share_data(data):
    """ Scramble the file data -- the field containing the share data itself will have one
    bit flipped or else will be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        sharedatasize = struct.unpack(">L", data[0x0c+0x08:0x0c+0x08+4])[0]

        return corrupt_field(data, 0x0c+0x24, sharedatasize)
    else:
        sharedatasize = struct.unpack(">Q", data[0x0c+0x08:0x0c+0x0c+8])[0]

        return corrupt_field(data, 0x0c+0x44, sharedatasize)

def _corrupt_crypttext_hash_tree(data):
    """ Scramble the file data -- the field containing the crypttext hash tree will have one
    bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        crypttexthashtreeoffset = struct.unpack(">L", data[0x0c+0x14:0x0c+0x14+4])[0]
        blockhashesoffset = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
    else:
        crypttexthashtreeoffset = struct.unpack(">Q", data[0x0c+0x24:0x0c+0x24+8])[0]
        blockhashesoffset = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]

    return corrupt_field(data, crypttexthashtreeoffset, blockhashesoffset-crypttexthashtreeoffset)

def _corrupt_block_hashes(data):
    """ Scramble the file data -- the field containing the block hash tree will have one bit
    flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        blockhashesoffset = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
        sharehashesoffset = struct.unpack(">L", data[0x0c+0x1c:0x0c+0x1c+4])[0]
    else:
        blockhashesoffset = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]
        sharehashesoffset = struct.unpack(">Q", data[0x0c+0x34:0x0c+0x34+8])[0]

    return corrupt_field(data, blockhashesoffset, sharehashesoffset-blockhashesoffset)

def _corrupt_share_hashes(data):
    """ Scramble the file data -- the field containing the share hash chain will have one
    bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        sharehashesoffset = struct.unpack(">L", data[0x0c+0x1c:0x0c+0x1c+4])[0]
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
    else:
        sharehashesoffset = struct.unpack(">Q", data[0x0c+0x34:0x0c+0x34+8])[0]
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]

    return corrupt_field(data, sharehashesoffset, uriextoffset-sharehashesoffset)

def _corrupt_length_of_uri_extension(data):
    """ Scramble the file data -- the field showing the length of the uri extension will
    have one bit flipped or else will be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
        return corrupt_field(data, uriextoffset, 4)
    else:
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]
        return corrupt_field(data, uriextoffset, 8)

def _corrupt_uri_extension(data):
    """ Scramble the file data -- the field containing the uri extension will have one bit
    flipped or else will be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
        uriextlen = struct.unpack(">L", data[0x0c+uriextoffset:0x0c+uriextoffset+4])[0]
    else:
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]
        uriextlen = struct.unpack(">Q", data[0x0c+uriextoffset:0x0c+uriextoffset+8])[0]

    return corrupt_field(data, uriextoffset, uriextlen)

class Test(ShareManglingMixin, unittest.TestCase):
    def setUp(self):
        # Set self.basedir to a temp dir which has the name of the current test method in its
        # name.
        self.basedir = self.mktemp()

        d = defer.maybeDeferred(SystemTestMixin.setUp, self)
        d.addCallback(lambda x: self.set_up_nodes())

        def _upload_a_file(ignored):
            d2 = self.clients[0].upload(upload.Data(TEST_DATA, convergence=""))
            def _after_upload(u):
                self.uri = IURI(u.uri)
                return self.clients[0].create_node_from_uri(self.uri)
            d2.addCallback(_after_upload)
            return d2
        d.addCallback(_upload_a_file)

        def _stash_it(filenode):
            self.filenode = filenode
        d.addCallback(_stash_it)
        return d

    def _download_and_check_plaintext(self, unused=None):
        self.downloader = self.clients[1].getServiceNamed("downloader")
        d = self.downloader.download_to_data(self.uri)

        def _after_download(result):
            self.failUnlessEqual(result, TEST_DATA)
        d.addCallback(_after_download)
        return d

    def _delete_a_share(self, unused=None, sharenum=None):
        """ Delete one share. """

        shares = self.find_shares()
        ks = shares.keys()
        if sharenum is not None:
            k = [ key for key in shares.keys() if key[1] == sharenum ][0]
        else:
            k = random.choice(ks)
        del shares[k]
        self.replace_shares(shares, storage_index=self.uri.storage_index)

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

        # The following process of deleting 8 of the shares and asserting that you can't
        # download it is more to test this test code than to test the Tahoe code...
        def _then_delete_8(unused=None):
            self.replace_shares(stash[0], storage_index=self.uri.storage_index)
            for sharenum in range(2, 10):
                self._delete_a_share()
        d.addCallback(_then_delete_8)

        def _then_download(unused=None):
            self.downloader = self.clients[1].getServiceNamed("downloader")
            d = self.downloader.download_to_data(self.uri)

            def _after_download_callb(result):
                self.fail() # should have gotten an errback instead
                return result
            def _after_download_errb(failure):
                failure.trap(encode.NotEnoughSharesError)
                return None # success!
            d.addCallbacks(_after_download_callb, _after_download_errb)
        d.addCallback(_then_download)

        # The following process of leaving 8 of the shares deleted and asserting that you can't
        # repair it is more to test this test code than to test the Tahoe code...
        def _then_repair(unused=None):
            d2 = self.filenode.check_and_repair(Monitor(), verify=False)
            def _after_repair(checkandrepairresults):
                prerepairres = checkandrepairresults.get_pre_repair_results()
                postrepairres = checkandrepairresults.get_post_repair_results()
                self.failIf(prerepairres.is_healthy())
                self.failIf(postrepairres.is_healthy())
            d2.addCallback(_after_repair)
            return d2
        d.addCallback(_then_repair)
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

    def _corrupt_a_random_share(self, unused, corruptor_func):
        """ Exactly one share on disk will be corrupted by corruptor_func. """
        shares = self.find_shares()
        ks = shares.keys()
        k = random.choice(ks)

        shares[k] = corruptor_func(shares[k])

        self.replace_shares(shares, storage_index=self.uri.storage_index)

    def test_check_without_verify(self):
        """ Check says the file is healthy when none of the shares have been touched.  It says
        that the file is unhealthy when all of them have been removed. It doesn't use any reads.
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

    def test_check_with_verify(self):
        """ Check says the file is healthy when none of the shares have been touched.  It says
        that the file is unhealthy if any field of any share has been corrupted.  It doesn't use
        more than twice as many reads as it needs. """
        # N == 10.  2 is the "efficiency leeway" -- we'll allow you to pass this test even if
        # you trigger twice as many disk reads and blocks sends as would be optimal.
        DELTA_READS = 10 * 2
        d = defer.succeed(self.filenode)
        def _check1(filenode):
            before_check_reads = self._count_reads()

            d2 = filenode.check(Monitor(), verify=True)
            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                # print "delta was ", after_check_reads - before_check_reads
                self.failIf(after_check_reads - before_check_reads > DELTA_READS)
                self.failUnless(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check1)

        d.addCallback(self.find_shares)
        stash = [None]
        def _stash_it(res):
            stash[0] = res
            return res
        d.addCallback(_stash_it)

        def _check2(ignored):
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(Monitor(), verify=True)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                # print "delta was ", after_check_reads - before_check_reads
                self.failIf(after_check_reads - before_check_reads > DELTA_READS)
                self.failIf(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2

        def _put_it_all_back(ignored):
            self.replace_shares(stash[0], storage_index=self.uri.storage_index)
            return ignored

        for corruptor_func in (
            _corrupt_file_version_number,
            _corrupt_size_of_file_data,
            _corrupt_sharedata_version_number,
            _corrupt_segment_size,
            _corrupt_size_of_sharedata,
            _corrupt_offset_of_sharedata,
            _corrupt_offset_of_ciphertext_hash_tree,
            _corrupt_offset_of_block_hashes,
            _corrupt_offset_of_share_hashes,
            _corrupt_offset_of_uri_extension,
            _corrupt_share_data,
            _corrupt_crypttext_hash_tree,
            _corrupt_block_hashes,
            _corrupt_share_hashes,
            _corrupt_length_of_uri_extension,
            _corrupt_uri_extension,
            ):
            d.addCallback(self._corrupt_a_random_share, corruptor_func)
            d.addCallback(_check2)
            d.addCallback(_put_it_all_back)
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
        d.addCallback(self._delete_a_share, sharenum=2)

        def _repair_from_deletion_of_1(filenode):
            before_repair_reads = self._count_reads()
            before_repair_allocates = self._count_allocates()

            d2 = filenode.check_and_repair(Monitor(), verify=False)
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

                # Now we inspect the filesystem to make sure that it has 10 shares.
                shares = self.find_shares()
                self.failIf(len(shares) < 10)

                # Now we delete seven of the other shares, then try to download the file and
                # assert that it succeeds at downloading and has the right contents.  This can't
                # work unless it has already repaired the previously-deleted share #2.
                for sharenum in range(3, 10):
                    self._delete_a_share(sharenum=sharenum)

                return self._download_and_check_plaintext()

            d2.addCallback(_after_repair)
            return d2
        d.addCallback(_repair_from_deletion_of_1)

        # Now we repair again to get all of those 7 back...
        def _repair_from_deletion_of_7(filenode):
            before_repair_reads = self._count_reads()
            before_repair_allocates = self._count_allocates()

            d2 = filenode.check_and_repair(Monitor(), verify=False)
            def _after_repair(checkandrepairresults):
                prerepairres = checkandrepairresults.get_pre_repair_results()
                postrepairres = checkandrepairresults.get_post_repair_results()
                after_repair_reads = self._count_reads()
                after_repair_allocates = self._count_allocates()

                # print "delta was ", after_repair_reads - before_repair_reads, after_repair_allocates - before_repair_allocates
                self.failIf(after_repair_reads - before_repair_reads > DELTA_READS)
                self.failIf(after_repair_allocates - before_repair_allocates > (DELTA_ALLOCATES*7))
                self.failIf(prerepairres.is_healthy())
                self.failUnless(postrepairres.is_healthy())

                # Now we inspect the filesystem to make sure that it has 10 shares.
                shares = self.find_shares()
                self.failIf(len(shares) < 10)

                return self._download_and_check_plaintext()

            d2.addCallback(_after_repair)
            return d2
        d.addCallback(_repair_from_deletion_of_7)

        def _repair_from_corruption(filenode):
            before_repair_reads = self._count_reads()
            before_repair_allocates = self._count_allocates()

            d2 = filenode.check_and_repair(Monitor(), verify=False)
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

                return self._download_and_check_plaintext()

            d2.addCallback(_after_repair)
            return d2

        for corruptor_func in (
            _corrupt_file_version_number,
            _corrupt_size_of_file_data,
            _corrupt_sharedata_version_number,
            _corrupt_segment_size,
            _corrupt_size_of_sharedata,
            _corrupt_offset_of_sharedata,
            _corrupt_offset_of_ciphertext_hash_tree,
            _corrupt_offset_of_block_hashes,
            _corrupt_offset_of_share_hashes,
            _corrupt_offset_of_uri_extension,
            _corrupt_share_data,
            _corrupt_crypttext_hash_tree,
            _corrupt_block_hashes,
            _corrupt_share_hashes,
            _corrupt_length_of_uri_extension,
            _corrupt_uri_extension,
            ):
            # Now we corrupt a share...
            d.addCallback(self._corrupt_a_random_share, corruptor_func)
            # And repair...
            d.addCallback(_repair_from_corruption)

        return d
    test_repair.todo = "We haven't implemented a repairer yet."
