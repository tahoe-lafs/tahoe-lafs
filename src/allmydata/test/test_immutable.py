
from allmydata.test.common import SystemTestMixin, ShareManglingMixin
from allmydata.monitor import Monitor
from allmydata.interfaces import IURI, NotEnoughSharesError
from allmydata.immutable import upload
from allmydata.util import log
from twisted.internet import defer
from twisted.trial import unittest
import random, struct
import common_util as testutil

TEST_DATA="\x02"*(upload.Uploader.URI_LIT_SIZE_THRESHOLD+1)

def corrupt_field(data, offset, size, debug=False):
    if random.random() < 0.5:
        newdata = testutil.flip_one_bit(data, offset, size)
        if debug:
            log.msg("testing: corrupting offset %d, size %d flipping one bit orig: %r, newdata: %r" % (offset, size, data[offset:offset+size], newdata[offset:offset+size]))
        return newdata
    else:
        newval = testutil.insecurerandstr(size)
        if debug:
            log.msg("testing: corrupting offset %d, size %d randomizing field, orig: %r, newval: %r" % (offset, size, data[offset:offset+size], newval))
        return data[:offset]+newval+data[offset+size:]

def _corrupt_file_version_number(data):
    """ Scramble the file data -- the share file version number have one bit flipped or else
    will be changed to a random value."""
    return corrupt_field(data, 0x00, 4)

def _corrupt_size_of_file_data(data):
    """ Scramble the file data -- the field showing the size of the share data within the file
    will be set to one smaller. """
    return corrupt_field(data, 0x04, 4)

def _corrupt_sharedata_version_number(data):
    """ Scramble the file data -- the share data version number will have one bit flipped or
    else will be changed to a random value, but not 1 or 2."""
    return corrupt_field(data, 0x0c, 4)
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    newsharevernum = sharevernum
    while newsharevernum in (1, 2):
        newsharevernum = random.randrange(0, 2**32)
    newsharevernumbytes = struct.pack(">l", newsharevernum)
    return data[:0x0c] + newsharevernumbytes + data[0x0c+4:]

def _corrupt_sharedata_version_number_to_known_version(data):
    """ Scramble the file data -- the share data version number will
    be changed to 2 if it is 1 or else to 1 if it is 2."""
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        newsharevernum = 2
    else:
        newsharevernum = 1
    newsharevernumbytes = struct.pack(">l", newsharevernum)
    return data[:0x0c] + newsharevernumbytes + data[0x0c+4:]

def _corrupt_segment_size(data):
    """ Scramble the file data -- the field showing the size of the segment will have one
    bit flipped or else be changed to a random value. """
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x04, 4, debug=False)
    else:
        return corrupt_field(data, 0x0c+0x04, 8, debug=False)

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
        return corrupt_field(data, 0x0c+0x14, 4, debug=False)
    else:
        return corrupt_field(data, 0x0c+0x24, 8, debug=False)

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

def _corrupt_offset_of_uri_extension_to_force_short_read(data, debug=False):
    """ Scramble the file data -- the field showing the offset of the uri extension will be set
    to the size of the file minus 3.  This means when the client tries to read the length field
    from that location it will get a short read -- the result string will be only 3 bytes long,
    not the 4 or 8 bytes necessary to do a successful struct.unpack."""
    sharevernum = struct.unpack(">l", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    # The "-0x0c" in here is to skip the server-side header in the share file, which the client doesn't see when seeking and reading.
    if sharevernum == 1:
        if debug:
            log.msg("testing: corrupting offset %d, size %d, changing %d to %d (len(data) == %d)" % (0x2c, 4, struct.unpack(">L", data[0x2c:0x2c+4])[0], len(data)-0x0c-3, len(data)))
        return data[:0x2c] + struct.pack(">L", len(data)-0x0c-3) + data[0x2c+4:]
    else:
        if debug:
            log.msg("testing: corrupting offset %d, size %d, changing %d to %d (len(data) == %d)" % (0x48, 8, struct.unpack(">Q", data[0x48:0x48+8])[0], len(data)-0x0c-3, len(data)))
        return data[:0x48] + struct.pack(">Q", len(data)-0x0c-3) + data[0x48+8:]

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

    def _corrupt_a_share(self, unused, corruptor_func, sharenum):
        shares = self.find_shares()
        ks = [ key for key in shares.keys() if key[1] == sharenum ]
        assert ks, (shares.keys(), sharenum)
        k = ks[0]
        shares[k] = corruptor_func(shares[k])
        self.replace_shares(shares, storage_index=self.uri.storage_index)

    def _corrupt_all_shares(self, unused, corruptor_func):
        """ All shares on disk will be corrupted by corruptor_func. """
        shares = self.find_shares()
        for k in shares.keys():
            self._corrupt_a_share(unused, corruptor_func, k[1])

    def _corrupt_a_random_share(self, unused, corruptor_func):
        """ Exactly one share on disk will be corrupted by corruptor_func. """
        shares = self.find_shares()
        ks = shares.keys()
        k = random.choice(ks)
        return self._corrupt_a_share(unused, corruptor_func, k[1])

    def test_download(self):
        """ Basic download.  (This functionality is more or less already tested by test code in
        other modules, but this module is also going to test some more specific things about
        immutable download.)
        """
        d = defer.succeed(None)
        before_download_reads = self._count_reads()
        def _after_download(unused=None):
            after_download_reads = self._count_reads()
            # To pass this test, you have to download the file using only 10 reads to get the
            # UEB (in parallel from all shares), plus one read for each of the 3 shares.
            self.failIf(after_download_reads-before_download_reads > 13, (after_download_reads, before_download_reads))
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
            # To pass this test, you have to download the file using only 10 reads to get the
            # UEB (in parallel from all shares), plus one read for each of the 3 shares.
            self.failIf(after_download_reads-before_download_reads > 13, (after_download_reads, before_download_reads))
        d.addCallback(self._download_and_check_plaintext)
        d.addCallback(_after_download)
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
            downloader = self.clients[1].getServiceNamed("downloader")
            d = downloader.download_to_data(self.uri)

            def _callb(res):
                self.fail("Should have gotten an error from attempt to download, not %r" % (res,))
            def _errb(f):
                self.failUnless(f.check(NotEnoughSharesError))
            d.addCallbacks(_callb, _errb)
            return d

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
                self._corrupt_a_share(None, _corrupt_sharedata_version_number, shnum)
        d.addCallback(_then_corrupt_8)

        before_download_reads = self._count_reads()
        def _attempt_to_download(unused=None):
            downloader = self.clients[1].getServiceNamed("downloader")
            d = downloader.download_to_data(self.uri)

            def _callb(res):
                self.fail("Should have gotten an error from attempt to download, not %r" % (res,))
            def _errb(f):
                self.failUnless(f.check(NotEnoughSharesError))
            d.addCallbacks(_callb, _errb)
            return d

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
        LEEWAY = 7 # We'll allow you to pass this test even if you trigger seven times as many disk reads and blocks sends as would be optimal.
        DELTA_READS = 10 * LEEWAY # N = 10
        d = defer.succeed(self.filenode)
        def _check_pristine(filenode):
            before_check_reads = self._count_reads()

            d2 = filenode.check(Monitor(), verify=True)
            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > DELTA_READS, (after_check_reads, before_check_reads, DELTA_READS))
                self.failUnless(checkresults.is_healthy())

            d2.addCallback(_after_check)
            return d2
        d.addCallback(_check_pristine)

        d.addCallback(self.find_shares)
        stash = [None]
        def _stash_it(res):
            stash[0] = res
            return res
        d.addCallback(_stash_it)

        def _check_after_feckless_corruption(ignored, corruptor_func):
            # Corruption which has no effect -- bits of the share file that are unused.
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(Monitor(), verify=True)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > DELTA_READS)
                self.failUnless(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data(), corruptor_func))
                data = checkresults.get_data()
                self.failUnless(data['count-shares-good'] == 10, data)
                self.failUnless(len(data['sharemap']) == 10, data)
                self.failUnless(data['count-shares-needed'] == 3, data)
                self.failUnless(data['count-shares-expected'] == 10, data)
                self.failUnless(data['count-good-share-hosts'] == 5, data)
                self.failUnless(len(data['servers-responding']) == 5, data)
                self.failUnless(len(data['list-corrupt-shares']) == 0, data)

            d2.addCallback(_after_check)
            return d2

        def _put_it_all_back(ignored):
            self.replace_shares(stash[0], storage_index=self.uri.storage_index)
            return ignored

        for corruptor_func in (
            _corrupt_size_of_file_data,
            _corrupt_size_of_sharedata,
            _corrupt_segment_size,
            ):
            d.addCallback(self._corrupt_a_random_share, corruptor_func)
            d.addCallback(_check_after_feckless_corruption, corruptor_func=corruptor_func)
            d.addCallback(_put_it_all_back)

        def _check_after_server_visible_corruption(ignored, corruptor_func):
            # Corruption which is detected by the server means that the server will send you
            # back a Failure in response to get_bucket instead of giving you the share data.
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(Monitor(), verify=True)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > DELTA_READS)
                self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data(), corruptor_func))
                data = checkresults.get_data()
                # The server might fail to serve up its other share as well as the corrupted
                # one, so count-shares-good could be 8 or 9.
                self.failUnless(data['count-shares-good'] in (8, 9), data)
                self.failUnless(len(data['sharemap']) in (8, 9,), data)
                self.failUnless(data['count-shares-needed'] == 3, data)
                self.failUnless(data['count-shares-expected'] == 10, data)
                # The server may have served up the non-corrupted share, or it may not have, so
                # the checker could have detected either 4 or 5 good servers.
                self.failUnless(data['count-good-share-hosts'] in (4, 5), data)
                self.failUnless(len(data['servers-responding']) in (4, 5), data)
                # If the server served up the other share, then the checker should consider it good, else it should 
                # not.
                self.failUnless((data['count-shares-good'] == 9) == (data['count-good-share-hosts'] == 5), data)
                self.failUnless(len(data['list-corrupt-shares']) == 0, data)

            d2.addCallback(_after_check)
            return d2

        for corruptor_func in (
            _corrupt_file_version_number,
            ):
            d.addCallback(self._corrupt_a_random_share, corruptor_func)
            d.addCallback(_check_after_server_visible_corruption, corruptor_func=corruptor_func)
            d.addCallback(_put_it_all_back)

        def _check_after_share_incompatibility(ignored, corruptor_func):
            # Corruption which means the share is indistinguishable from a share of an
            # incompatible version.
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(Monitor(), verify=True)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                self.failIf(after_check_reads - before_check_reads > DELTA_READS)
                self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data(), corruptor_func))
                data = checkresults.get_data()
                self.failUnless(data['count-shares-good'] == 9, data)
                self.failUnless(len(data['sharemap']) == 9, data)
                self.failUnless(data['count-shares-needed'] == 3, data)
                self.failUnless(data['count-shares-expected'] == 10, data)
                self.failUnless(data['count-good-share-hosts'] == 5, data)
                self.failUnless(len(data['servers-responding']) == 5, data)
                self.failUnless(len(data['list-corrupt-shares']) == 0, data)
                self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
                self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
                self.failUnless(len(data['list-incompatible-shares']) == 1, data)

            d2.addCallback(_after_check)
            return d2

        for corruptor_func in (
            _corrupt_sharedata_version_number,
            ):
            d.addCallback(self._corrupt_a_random_share, corruptor_func)
            d.addCallback(_check_after_share_incompatibility, corruptor_func=corruptor_func)
            d.addCallback(_put_it_all_back)

        def _check_after_server_invisible_corruption(ignored, corruptor_func):
            # Corruption which is not detected by the server means that the server will send you
            # back the share data, but you will detect that it is wrong.
            before_check_reads = self._count_reads()
            d2 = self.filenode.check(Monitor(), verify=True)

            def _after_check(checkresults):
                after_check_reads = self._count_reads()
                # print "delta was ", after_check_reads - before_check_reads
                self.failIf(after_check_reads - before_check_reads > DELTA_READS)
                self.failIf(checkresults.is_healthy(), (checkresults, checkresults.is_healthy(), checkresults.get_data(), corruptor_func))
                data = checkresults.get_data()
                self.failUnless(data['count-shares-good'] == 9, data)
                self.failUnless(data['count-shares-needed'] == 3, data)
                self.failUnless(data['count-shares-expected'] == 10, data)
                self.failUnless(data['count-good-share-hosts'] == 5, data)
                self.failUnless(data['count-corrupt-shares'] == 1, (data, corruptor_func))
                self.failUnless(len(data['list-corrupt-shares']) == 1, data)
                self.failUnless(len(data['list-corrupt-shares']) == data['count-corrupt-shares'], data)
                self.failUnless(len(data['list-incompatible-shares']) == data['count-incompatible-shares'], data)
                self.failUnless(len(data['list-incompatible-shares']) == 0, data)
                self.failUnless(len(data['servers-responding']) == 5, data)
                self.failUnless(len(data['sharemap']) == 9, data)

            d2.addCallback(_after_check)
            return d2

        for corruptor_func in (
            _corrupt_sharedata_version_number_to_known_version,
            _corrupt_offset_of_sharedata,
            _corrupt_offset_of_ciphertext_hash_tree,
            _corrupt_offset_of_block_hashes,
            _corrupt_offset_of_share_hashes,
            _corrupt_offset_of_uri_extension,
            _corrupt_offset_of_uri_extension_to_force_short_read,
            _corrupt_share_data,
            _corrupt_crypttext_hash_tree,
            _corrupt_block_hashes,
            _corrupt_share_hashes,
            _corrupt_length_of_uri_extension,
            _corrupt_uri_extension,
            ):
            d.addCallback(self._corrupt_a_random_share, corruptor_func)
            d.addCallback(_check_after_server_invisible_corruption, corruptor_func=corruptor_func)
            d.addCallback(_put_it_all_back)
        return d
    test_check_with_verify.todo = "We haven't implemented a verifier this thorough yet."

    def test_repair(self):
        """ Repair replaces a share that got deleted. """
        # N == 10.  7 is the "efficiency leeway" -- we'll allow you to pass this test even if
        # you trigger seven times as many disk reads and blocks sends as would be optimal.
        DELTA_READS = 10 * 7
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
            _corrupt_sharedata_version_number,
            _corrupt_sharedata_version_number_to_known_version,
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


# XXX extend these tests to show that the checker detects which specific share on which specific server is broken -- this is necessary so that the checker results can be passed to the repairer and the repairer can go ahead and upload fixes without first doing what is effectively a check (/verify) run

# XXX extend these tests to show bad behavior of various kinds from servers: raising exception from each remove_foo() method, for example

# XXX test disconnect DeadReferenceError from get_buckets and get_block_whatsit
