"""
Tests for allmydata.util.hashutil.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.trial import unittest

from allmydata.util import hashutil, base32


class HashUtilTests(unittest.TestCase):

    def test_random_key(self):
        k = hashutil.random_key()
        self.failUnlessEqual(len(k), hashutil.KEYLEN)
        self.assertIsInstance(k, bytes)

    def test_sha256d(self):
        h1 = hashutil.tagged_hash(b"tag1", b"value")
        self.assertIsInstance(h1, bytes)
        h2 = hashutil.tagged_hasher(b"tag1")
        h2.update(b"value")
        h2a = h2.digest()
        h2b = h2.digest()
        self.assertIsInstance(h2a, bytes)
        self.failUnlessEqual(h1, h2a)
        self.failUnlessEqual(h2a, h2b)

    def test_sha256d_truncated(self):
        h1 = hashutil.tagged_hash(b"tag1", b"value", 16)
        h2 = hashutil.tagged_hasher(b"tag1", 16)
        h2.update(b"value")
        h2 = h2.digest()
        self.failUnlessEqual(len(h1), 16)
        self.failUnlessEqual(len(h2), 16)
        self.failUnlessEqual(h1, h2)

    def test_well_known_tagged_hash(self):
        self.assertEqual(
            b"yra322btzoqjp4ts2jon5dztgnilcdg6jgztgk7joi6qpjkitg2q",
            base32.b2a(hashutil.tagged_hash(b"tag", b"hello world")),
        )
        self.assertEqual(
            b"kfbsfssrv2bvtp3regne6j7gpdjcdjwncewriyfdtt764o5oa7ta",
            base32.b2a(hashutil.tagged_hash(b"different", b"hello world")),
        )
        self.assertEqual(
            b"z34pzkgo36chbjz2qykonlxthc4zdqqquapw4bcaoogzvmmcr3zq",
            base32.b2a(hashutil.tagged_hash(b"different", b"goodbye world")),
        )

    def test_well_known_tagged_pair_hash(self):
        self.assertEqual(
            b"wmto44q3shtezwggku2fxztfkwibvznkfu6clatnvfog527sb6dq",
            base32.b2a(hashutil.tagged_pair_hash(b"tag", b"hello", b"world")),
        )
        self.assertEqual(
            b"lzn27njx246jhijpendqrxlk4yb23nznbcrihommbymg5e7quh4a",
            base32.b2a(hashutil.tagged_pair_hash(b"different", b"hello", b"world")),
        )
        self.assertEqual(
            b"qnehpoypxxdhjheqq7dayloghtu42yr55uylc776zt23ii73o3oq",
            base32.b2a(hashutil.tagged_pair_hash(b"different", b"goodbye", b"world")),
        )

    def test_chk(self):
        h1 = hashutil.convergence_hash(3, 10, 1000, b"data", b"secret")
        h2 = hashutil.convergence_hasher(3, 10, 1000, b"secret")
        h2.update(b"data")
        h2 = h2.digest()
        self.failUnlessEqual(h1, h2)
        self.assertIsInstance(h1, bytes)
        self.assertIsInstance(h2, bytes)

    def test_hashers(self):
        h1 = hashutil.block_hash(b"foo")
        h2 = hashutil.block_hasher()
        h2.update(b"foo")
        self.failUnlessEqual(h1, h2.digest())
        self.assertIsInstance(h1, bytes)

        h1 = hashutil.uri_extension_hash(b"foo")
        h2 = hashutil.uri_extension_hasher()
        h2.update(b"foo")
        self.failUnlessEqual(h1, h2.digest())
        self.assertIsInstance(h1, bytes)

        h1 = hashutil.plaintext_hash(b"foo")
        h2 = hashutil.plaintext_hasher()
        h2.update(b"foo")
        self.failUnlessEqual(h1, h2.digest())
        self.assertIsInstance(h1, bytes)

        h1 = hashutil.crypttext_hash(b"foo")
        h2 = hashutil.crypttext_hasher()
        h2.update(b"foo")
        self.failUnlessEqual(h1, h2.digest())
        self.assertIsInstance(h1, bytes)

        h1 = hashutil.crypttext_segment_hash(b"foo")
        h2 = hashutil.crypttext_segment_hasher()
        h2.update(b"foo")
        self.failUnlessEqual(h1, h2.digest())
        self.assertIsInstance(h1, bytes)

        h1 = hashutil.plaintext_segment_hash(b"foo")
        h2 = hashutil.plaintext_segment_hasher()
        h2.update(b"foo")
        self.failUnlessEqual(h1, h2.digest())
        self.assertIsInstance(h1, bytes)

    def test_timing_safe_compare(self):
        self.failUnless(hashutil.timing_safe_compare(b"a", b"a"))
        self.failUnless(hashutil.timing_safe_compare(b"ab", b"ab"))
        self.failIf(hashutil.timing_safe_compare(b"a", b"b"))
        self.failIf(hashutil.timing_safe_compare(b"a", b"aa"))

    def _testknown(self, hashf, expected_a, *args):
        got = hashf(*args)
        self.assertIsInstance(got, bytes)
        got_a = base32.b2a(got)
        self.failUnlessEqual(got_a, expected_a)

    def test_storage_index_hash_known_answers(self):
        """
        Verify backwards compatibility by comparing ``storage_index_hash`` outputs
        for some well-known (to us) inputs.
        """
        # This is a marginal case.  b"" is not a valid aes 128 key.  The
        # implementation does nothing to avoid producing a result for it,
        # though.
        self._testknown(hashutil.storage_index_hash, b"qb5igbhcc5esa6lwqorsy7e6am", b"")

        # This is a little bit more realistic though clearly this is a poor key choice.
        self._testknown(hashutil.storage_index_hash, b"wvggbrnrezdpa5yayrgiw5nzja", b"x" * 16)

        # Here's a much more realistic key that I generated by reading some
        # bytes from /dev/urandom.  I computed the expected hash value twice.
        # First using hashlib.sha256 and then with sha256sum(1).  The input
        # string given to the hash function was "43:<storage index tag>,<key>"
        # in each case.
        self._testknown(
            hashutil.storage_index_hash,
            b"aarbseqqrpsfowduchcjbonscq",
            base32.a2b(b"2ckv3dfzh6rgjis6ogfqhyxnzy"),
        )

    def test_convergence_hasher_tag(self):
        """
        ``_convergence_hasher_tag`` constructs the convergence hasher tag from a
        unique prefix, the required, total, and segment size parameters, and a
        convergence secret.
        """
        self.assertEqual(
            b"allmydata_immutable_content_to_key_with_added_secret_v1+"
            b"16:\x42\x42\x42\x42\x42\x42\x42\x42\x42\x42\x42\x42\x42\x42\x42\x42,"
            b"9:3,10,1024,",
            hashutil._convergence_hasher_tag(
                k=3,
                n=10,
                segsize=1024,
                convergence=b"\x42" * 16,
            ),
        )

    def test_convergence_hasher_out_of_bounds(self):
        """
        ``_convergence_hasher_tag`` raises ``ValueError`` if k or n is not between
        1 and 256 inclusive or if k is greater than n.
        """
        segsize = 1024
        secret = b"\x42" * 16
        for bad_k in (0, 2, 257):
            with self.assertRaises(ValueError):
                hashutil._convergence_hasher_tag(
                    k=bad_k, n=1, segsize=segsize, convergence=secret,
                )
        for bad_n in (0, 1, 257):
            with self.assertRaises(ValueError):
                hashutil._convergence_hasher_tag(
                    k=2, n=bad_n, segsize=segsize, convergence=secret,
                )

    def test_known_answers(self):
        """
        Verify backwards compatibility by comparing hash outputs for some
        well-known (to us) inputs.
        """
        self._testknown(hashutil.block_hash, b"msjr5bh4evuh7fa3zw7uovixfbvlnstr5b65mrerwfnvjxig2jvq", b"")
        self._testknown(hashutil.uri_extension_hash, b"wthsu45q7zewac2mnivoaa4ulh5xvbzdmsbuyztq2a5fzxdrnkka", b"")
        self._testknown(hashutil.plaintext_hash, b"5lz5hwz3qj3af7n6e3arblw7xzutvnd3p3fjsngqjcb7utf3x3da", b"")
        self._testknown(hashutil.crypttext_hash, b"itdj6e4njtkoiavlrmxkvpreosscssklunhwtvxn6ggho4rkqwga", b"")
        self._testknown(hashutil.crypttext_segment_hash, b"aovy5aa7jej6ym5ikgwyoi4pxawnoj3wtaludjz7e2nb5xijb7aa", b"")
        self._testknown(hashutil.plaintext_segment_hash, b"4fdgf6qruaisyukhqcmoth4t3li6bkolbxvjy4awwcpprdtva7za", b"")
        self._testknown(hashutil.convergence_hash, b"3mo6ni7xweplycin6nowynw2we", 3, 10, 100, b"", b"converge")
        self._testknown(hashutil.my_renewal_secret_hash, b"ujhr5k5f7ypkp67jkpx6jl4p47pyta7hu5m527cpcgvkafsefm6q", b"")
        self._testknown(hashutil.my_cancel_secret_hash, b"rjwzmafe2duixvqy6h47f5wfrokdziry6zhx4smew4cj6iocsfaa", b"")
        self._testknown(hashutil.file_renewal_secret_hash, b"hzshk2kf33gzbd5n3a6eszkf6q6o6kixmnag25pniusyaulqjnia", b"", b"si")
        self._testknown(hashutil.file_cancel_secret_hash, b"bfciwvr6w7wcavsngxzxsxxaszj72dej54n4tu2idzp6b74g255q", b"", b"si")
        self._testknown(hashutil.bucket_renewal_secret_hash, b"e7imrzgzaoashsncacvy3oysdd2m5yvtooo4gmj4mjlopsazmvuq", b"", b"\x00"*20)
        self._testknown(hashutil.bucket_cancel_secret_hash, b"dvdujeyxeirj6uux6g7xcf4lvesk632aulwkzjar7srildvtqwma", b"", b"\x00"*20)
        self._testknown(hashutil.hmac, b"c54ypfi6pevb3nvo6ba42jtglpkry2kbdopqsi7dgrm4r7tw5sra", b"tag", b"")
        self._testknown(hashutil.mutable_rwcap_key_hash, b"6rvn2iqrghii5n4jbbwwqqsnqu", b"iv", b"wk")
        self._testknown(hashutil.ssk_writekey_hash, b"ykpgmdbpgbb6yqz5oluw2q26ye", b"")
        self._testknown(hashutil.ssk_write_enabler_master_hash, b"izbfbfkoait4dummruol3gy2bnixrrrslgye6ycmkuyujnenzpia", b"")
        self._testknown(hashutil.ssk_write_enabler_hash, b"fuu2dvx7g6gqu5x22vfhtyed7p4pd47y5hgxbqzgrlyvxoev62tq", b"wk", b"\x00"*20)
        self._testknown(hashutil.ssk_pubkey_fingerprint_hash, b"3opzw4hhm2sgncjx224qmt5ipqgagn7h5zivnfzqycvgqgmgz35q", b"")
        self._testknown(hashutil.ssk_readkey_hash, b"vugid4as6qbqgeq2xczvvcedai", b"")
        self._testknown(hashutil.ssk_readkey_data_hash, b"73wsaldnvdzqaf7v4pzbr2ae5a", b"iv", b"rk")
        self._testknown(hashutil.ssk_storage_index_hash, b"j7icz6kigb6hxrej3tv4z7ayym", b"")

        self._testknown(hashutil.permute_server_hash,
                        b"kb4354zeeurpo3ze5e275wzbynm6hlap", # b32(expected)
                        b"SI", # peer selection index == storage_index
                        base32.a2b(b"u33m4y7klhz3bypswqkozwetvabelhxt"), # seed
                        )
