"""
Tests for allmydata.util.hashutil.
"""

from twisted.trial import unittest

from allmydata.util import hashutil, base32


class HashUtilTests(unittest.TestCase):

    def test_random_key(self):
        k = hashutil.random_key()
        self.failUnlessEqual(len(k), hashutil.KEYLEN)

    def test_sha256d(self):
        h1 = hashutil.tagged_hash("tag1", "value")
        h2 = hashutil.tagged_hasher("tag1")
        h2.update("value")
        h2a = h2.digest()
        h2b = h2.digest()
        self.failUnlessEqual(h1, h2a)
        self.failUnlessEqual(h2a, h2b)

    def test_sha256d_truncated(self):
        h1 = hashutil.tagged_hash("tag1", "value", 16)
        h2 = hashutil.tagged_hasher("tag1", 16)
        h2.update("value")
        h2 = h2.digest()
        self.failUnlessEqual(len(h1), 16)
        self.failUnlessEqual(len(h2), 16)
        self.failUnlessEqual(h1, h2)

    def test_chk(self):
        h1 = hashutil.convergence_hash(3, 10, 1000, "data", "secret")
        h2 = hashutil.convergence_hasher(3, 10, 1000, "secret")
        h2.update("data")
        h2 = h2.digest()
        self.failUnlessEqual(h1, h2)

    def test_hashers(self):
        h1 = hashutil.block_hash("foo")
        h2 = hashutil.block_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.uri_extension_hash("foo")
        h2 = hashutil.uri_extension_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.plaintext_hash("foo")
        h2 = hashutil.plaintext_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.crypttext_hash("foo")
        h2 = hashutil.crypttext_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.crypttext_segment_hash("foo")
        h2 = hashutil.crypttext_segment_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.plaintext_segment_hash("foo")
        h2 = hashutil.plaintext_segment_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

    def test_timing_safe_compare(self):
        self.failUnless(hashutil.timing_safe_compare("a", "a"))
        self.failUnless(hashutil.timing_safe_compare("ab", "ab"))
        self.failIf(hashutil.timing_safe_compare("a", "b"))
        self.failIf(hashutil.timing_safe_compare("a", "aa"))

    def _testknown(self, hashf, expected_a, *args):
        got = hashf(*args)
        got_a = base32.b2a(got)
        self.failUnlessEqual(got_a, expected_a)

    def test_known_answers(self):
        # assert backwards compatibility
        self._testknown(hashutil.storage_index_hash, "qb5igbhcc5esa6lwqorsy7e6am", "")
        self._testknown(hashutil.block_hash, "msjr5bh4evuh7fa3zw7uovixfbvlnstr5b65mrerwfnvjxig2jvq", "")
        self._testknown(hashutil.uri_extension_hash, "wthsu45q7zewac2mnivoaa4ulh5xvbzdmsbuyztq2a5fzxdrnkka", "")
        self._testknown(hashutil.plaintext_hash, "5lz5hwz3qj3af7n6e3arblw7xzutvnd3p3fjsngqjcb7utf3x3da", "")
        self._testknown(hashutil.crypttext_hash, "itdj6e4njtkoiavlrmxkvpreosscssklunhwtvxn6ggho4rkqwga", "")
        self._testknown(hashutil.crypttext_segment_hash, "aovy5aa7jej6ym5ikgwyoi4pxawnoj3wtaludjz7e2nb5xijb7aa", "")
        self._testknown(hashutil.plaintext_segment_hash, "4fdgf6qruaisyukhqcmoth4t3li6bkolbxvjy4awwcpprdtva7za", "")
        self._testknown(hashutil.convergence_hash, "3mo6ni7xweplycin6nowynw2we", 3, 10, 100, "", "converge")
        self._testknown(hashutil.my_renewal_secret_hash, "ujhr5k5f7ypkp67jkpx6jl4p47pyta7hu5m527cpcgvkafsefm6q", "")
        self._testknown(hashutil.my_cancel_secret_hash, "rjwzmafe2duixvqy6h47f5wfrokdziry6zhx4smew4cj6iocsfaa", "")
        self._testknown(hashutil.file_renewal_secret_hash, "hzshk2kf33gzbd5n3a6eszkf6q6o6kixmnag25pniusyaulqjnia", "", "si")
        self._testknown(hashutil.file_cancel_secret_hash, "bfciwvr6w7wcavsngxzxsxxaszj72dej54n4tu2idzp6b74g255q", "", "si")
        self._testknown(hashutil.bucket_renewal_secret_hash, "e7imrzgzaoashsncacvy3oysdd2m5yvtooo4gmj4mjlopsazmvuq", "", "\x00"*20)
        self._testknown(hashutil.bucket_cancel_secret_hash, "dvdujeyxeirj6uux6g7xcf4lvesk632aulwkzjar7srildvtqwma", "", "\x00"*20)
        self._testknown(hashutil.hmac, "c54ypfi6pevb3nvo6ba42jtglpkry2kbdopqsi7dgrm4r7tw5sra", "tag", "")
        self._testknown(hashutil.mutable_rwcap_key_hash, "6rvn2iqrghii5n4jbbwwqqsnqu", "iv", "wk")
        self._testknown(hashutil.ssk_writekey_hash, "ykpgmdbpgbb6yqz5oluw2q26ye", "")
        self._testknown(hashutil.ssk_write_enabler_master_hash, "izbfbfkoait4dummruol3gy2bnixrrrslgye6ycmkuyujnenzpia", "")
        self._testknown(hashutil.ssk_write_enabler_hash, "fuu2dvx7g6gqu5x22vfhtyed7p4pd47y5hgxbqzgrlyvxoev62tq", "wk", "\x00"*20)
        self._testknown(hashutil.ssk_pubkey_fingerprint_hash, "3opzw4hhm2sgncjx224qmt5ipqgagn7h5zivnfzqycvgqgmgz35q", "")
        self._testknown(hashutil.ssk_readkey_hash, "vugid4as6qbqgeq2xczvvcedai", "")
        self._testknown(hashutil.ssk_readkey_data_hash, "73wsaldnvdzqaf7v4pzbr2ae5a", "iv", "rk")
        self._testknown(hashutil.ssk_storage_index_hash, "j7icz6kigb6hxrej3tv4z7ayym", "")

        self._testknown(hashutil.permute_server_hash,
                        "kb4354zeeurpo3ze5e275wzbynm6hlap", # b32(expected)
                        "SI", # peer selection index == storage_index
                        base32.a2b("u33m4y7klhz3bypswqkozwetvabelhxt"), # seed
                        )
