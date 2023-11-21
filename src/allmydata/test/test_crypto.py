from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from future.utils import native_bytes

import unittest

from base64 import b64decode
from binascii import a2b_hex, b2a_hex

from twisted.python.filepath import FilePath

from allmydata.crypto import (
    aes,
    ed25519,
    rsa,
)
from allmydata.crypto.util import remove_prefix
from allmydata.crypto.error import BadPrefixError



RESOURCE_DIR = FilePath(__file__).parent().child('data')


class TestRegression(unittest.TestCase):
    '''
    These tests are regression tests to ensure that the upgrade from `pycryptopp` to `cryptography`
    doesn't break anything. They check that data encrypted with old keys can be decrypted with new
    keys.
    '''

    AES_KEY = b'My\x9c\xc0f\xd3\x03\x9a1\x8f\xbd\x17W_\x1f2'
    IV = b'\x96\x1c\xa0\xbcUj\x89\xc1\x85J\x1f\xeb=\x17\x04\xca'

    with RESOURCE_DIR.child('pycryptopp-rsa-2048-priv.txt').open('r') as f:
        # Created using `pycryptopp`:
        #
        #     from base64 import b64encode
        #     from pycryptopp.publickey import rsa
        #     priv = rsa.generate(2048)
        #     priv_str = b64encode(priv.serialize())
        #     pub_str = b64encode(priv.get_verifying_key().serialize())
        RSA_2048_PRIV_KEY = b64decode(f.read().strip())
        assert isinstance(RSA_2048_PRIV_KEY, native_bytes)

    with RESOURCE_DIR.child('pycryptopp-rsa-2048-sig.txt').open('r') as f:
        # Signature created using `RSA_2048_PRIV_KEY` via:
        #
        #     sig = priv.sign(b'test')
        RSA_2048_SIG = b64decode(f.read().strip())

    with RESOURCE_DIR.child('pycryptopp-rsa-2048-pub.txt').open('r') as f:
        # The public key corresponding to `RSA_2048_PRIV_KEY`.
        RSA_2048_PUB_KEY = b64decode(f.read().strip())

    with RESOURCE_DIR.child('pycryptopp-rsa-1024-priv.txt').open('r') as f:
        # Created using `pycryptopp`:
        #
        #     from base64 import b64encode
        #     from pycryptopp.publickey import rsa
        #     priv = rsa.generate(1024)
        #     priv_str = b64encode(priv.serialize())
        #     pub_str = b64encode(priv.get_verifying_key().serialize())
        RSA_TINY_PRIV_KEY = b64decode(f.read().strip())
        assert isinstance(RSA_TINY_PRIV_KEY, native_bytes)

    with RESOURCE_DIR.child('pycryptopp-rsa-32768-priv.txt').open('r') as f:
        # Created using `pycryptopp`:
        #
        #     from base64 import b64encode
        #     from pycryptopp.publickey import rsa
        #     priv = rsa.generate(32768)
        #     priv_str = b64encode(priv.serialize())
        #     pub_str = b64encode(priv.get_verifying_key().serialize())
        RSA_HUGE_PRIV_KEY = b64decode(f.read().strip())
        assert isinstance(RSA_HUGE_PRIV_KEY, native_bytes)

    def test_old_start_up_test(self):
        """
        This was the old startup test run at import time in `pycryptopp.cipher.aes`.
        """
        enc0 = b"dc95c078a2408989ad48a21492842087530f8afbc74536b9a963b4f1c4cb738b"
        cryptor = aes.create_decryptor(key=b"\x00" * 32)
        ct = aes.decrypt_data(cryptor, b"\x00" * 32)
        self.assertEqual(enc0, b2a_hex(ct))

        cryptor = aes.create_decryptor(key=b"\x00" * 32)
        ct1 = aes.decrypt_data(cryptor, b"\x00" * 15)
        ct2 = aes.decrypt_data(cryptor, b"\x00" * 17)
        self.assertEqual(enc0, b2a_hex(ct1+ct2))

        enc0 = b"66e94bd4ef8a2c3b884cfa59ca342b2e"
        cryptor = aes.create_decryptor(key=b"\x00" * 16)
        ct = aes.decrypt_data(cryptor, b"\x00" * 16)
        self.assertEqual(enc0, b2a_hex(ct))

        cryptor = aes.create_decryptor(key=b"\x00" * 16)
        ct1 = aes.decrypt_data(cryptor, b"\x00" * 8)
        ct2 = aes.decrypt_data(cryptor, b"\x00" * 8)
        self.assertEqual(enc0, b2a_hex(ct1+ct2))

        def _test_from_Niels_AES(keysize, result):
            def fake_ecb_using_ctr(k, p):
                encryptor = aes.create_encryptor(key=k, iv=p)
                return aes.encrypt_data(encryptor, b'\x00' * 16)

            E = fake_ecb_using_ctr
            b = 16
            k = keysize
            S = b'\x00' * (k + b)

            for i in range(1000):
                K = S[-k:]
                P = S[-k-b:-k]
                S += E(K, E(K, P))

            self.assertEqual(S[-b:], a2b_hex(result))

        _test_from_Niels_AES(16, b'bd883f01035e58f42f9d812f2dacbcd8')
        _test_from_Niels_AES(32, b'c84b0f3a2c76dd9871900b07f09bdd3e')

    def test_aes_no_iv_process_short_input(self):
        '''
        The old code used the following patterns with AES ciphers.

            import os
            from pycryptopp.cipher.aes import AES
            key = = os.urandom(16)
            ciphertext = AES(key).process(plaintext)

        This test verifies that using the new AES wrapper generates the same output.
        '''
        plaintext = b'test'
        expected_ciphertext = b'\x7fEK\\'

        k = aes.create_decryptor(self.AES_KEY)
        ciphertext = aes.decrypt_data(k, plaintext)

        self.assertEqual(ciphertext, expected_ciphertext)

    def test_aes_no_iv_process_long_input(self):
        '''
        The old code used the following patterns with AES ciphers.

            import os
            from pycryptopp.cipher.aes import AES
            key = = os.urandom(16)
            ciphertext = AES(key).process(plaintext)

        This test verifies that using the new AES wrapper generates the same output.
        '''
        plaintext = b'hi' * 32
        expected_ciphertext = (
            b'cIPAY%o:\xce\xfex\x8e@^.\x90\xb1\x80a\xff\xd8^\xac\x8d\xa7/\x1d\xe6\x92\xa1\x04\x92'
            b'\x1f\xa1|\xd2$E\xb5\xe7\x9d\xae\xd1\x1f)\xe4\xc7\x83\xb8\xd5|dHhU\xc8\x9a\xb1\x10\xed'
            b'\xd1\xe7|\xd1')

        k = aes.create_decryptor(self.AES_KEY)
        ciphertext = aes.decrypt_data(k, plaintext)

        self.assertEqual(ciphertext, expected_ciphertext)

    def test_aes_with_iv_process_short_input(self):
        '''
        The old code used the following patterns with AES ciphers.

            import os
            from pycryptopp.cipher.aes import AES
            key = = os.urandom(16)
            ciphertext = AES(key).process(plaintext)

        This test verifies that using the new AES wrapper generates the same output.
        '''
        plaintext = b'test'
        expected_ciphertext = b'\x82\x0e\rt'

        k = aes.create_decryptor(self.AES_KEY, iv=self.IV)
        ciphertext = aes.decrypt_data(k, plaintext)

        self.assertEqual(ciphertext, expected_ciphertext)

    def test_aes_with_iv_process_long_input(self):
        '''
        The old code used the following patterns with AES ciphers.

            import os
            from pycryptopp.cipher.aes import AES
            key = = os.urandom(16)
            ciphertext = AES(key).process(plaintext)

        This test verifies that using the new AES wrapper generates the same output.
        '''
        plaintext = b'hi' * 32
        expected_ciphertext = (
            b'\x9e\x02\x16i}WL\xbf\x83\xac\xb4K\xf7\xa0\xdf\xa3\xba!3\x15\xd3(L\xb7\xb3\x91\xbcb'
            b'\x97a\xdc\x100?\xf5L\x9f\xd9\xeeO\x98\xda\xf5g\x93\xa7q\xe1\xb1~\xf8\x1b\xe8[\\s'
            b'\x144$\x86\xeaC^f')

        k = aes.create_decryptor(self.AES_KEY, iv=self.IV)
        ciphertext = aes.decrypt_data(k, plaintext)

        self.assertEqual(ciphertext, expected_ciphertext)

    def test_decode_ed15519_keypair(self):
        '''
        Created using the old code:

            from allmydata.util.keyutil import make_keypair, parse_privkey, parse_pubkey
            test_data = b'test'
            priv_str, pub_str = make_keypair()
            priv, _ = parse_privkey(priv_str)
            pub = parse_pubkey(pub_str)
            sig = priv.sign(test_data)
            pub.verify(sig, test_data)

        This simply checks that keys and signatures generated using the old code are still valid
        using the new code.
        '''
        priv_str = b'priv-v0-lqcj746bqa4npkb6zpyc6esd74x3bl6mbcjgqend7cvtgmcpawhq'
        pub_str = b'pub-v0-yzpqin3of3ep363lwzxwpvgai3ps43dao46k2jds5kw5ohhpcwhq'
        test_data = b'test'
        sig = (b'\xde\x0e\xd6\xe2\xf5\x03]8\xfe\xa71\xad\xb4g\x03\x11\x81\x8b\x08\xffz\xf4K\xa0'
               b'\x86 ier!\xe8\xe5#*\x9d\x8c\x0bI\x02\xd90\x0e7\xbeW\xbf\xa3\xfe\xc1\x1c\xf5+\xe9)'
               b'\xa3\xde\xc9\xc6s\xc9\x90\xf7x\x08')

        private_key, derived_public_key = ed25519.signing_keypair_from_string(priv_str)
        public_key = ed25519.verifying_key_from_string(pub_str)

        self.assertEqual(
            ed25519.string_from_verifying_key(public_key),
            ed25519.string_from_verifying_key(derived_public_key),
        )

        new_sig = ed25519.sign_data(private_key, test_data)
        self.assertEqual(new_sig, sig)

        ed25519.verify_signature(public_key, new_sig, test_data)
        ed25519.verify_signature(derived_public_key, new_sig, test_data)
        ed25519.verify_signature(public_key, sig, test_data)
        ed25519.verify_signature(derived_public_key, sig, test_data)

    def test_decode_rsa_keypair(self):
        '''
        This simply checks that keys and signatures generated using the old code are still valid
        using the new code.
        '''
        priv_key, pub_key = rsa.create_signing_keypair_from_string(self.RSA_2048_PRIV_KEY)
        rsa.verify_signature(pub_key, self.RSA_2048_SIG, b'test')

    def test_decode_tiny_rsa_keypair(self):
        '''
        An unreasonably small RSA key is rejected ("unreasonably small"
        means less that 2048 bits)
        '''
        with self.assertRaises(ValueError):
            rsa.create_signing_keypair_from_string(self.RSA_TINY_PRIV_KEY)

    def test_decode_huge_rsa_keypair(self):
        '''
        An unreasonably _large_ RSA key is rejected ("unreasonably large"
        means 32768 or more bits)
        '''
        with self.assertRaises(ValueError):
            rsa.create_signing_keypair_from_string(self.RSA_HUGE_PRIV_KEY)

    def test_encrypt_data_not_bytes(self):
        '''
        only bytes can be encrypted
        '''
        key = b'\x00' * 16
        encryptor = aes.create_encryptor(key)
        with self.assertRaises(ValueError) as ctx:
            aes.encrypt_data(encryptor, u"not bytes")
        self.assertIn(
            "must be bytes",
            str(ctx.exception)
        )

    def test_key_incorrect_size(self):
        '''
        keys that aren't 16 or 32 bytes are rejected
        '''
        key = b'\x00' * 12
        with self.assertRaises(ValueError) as ctx:
            aes.create_encryptor(key)
        self.assertIn(
            "16 or 32 bytes long",
            str(ctx.exception)
        )

    def test_iv_not_bytes(self):
        '''
        iv must be bytes
        '''
        key = b'\x00' * 16
        with self.assertRaises(TypeError) as ctx:
            aes.create_encryptor(key, iv=u"1234567890abcdef")
        self.assertIn(
            "must be bytes",
            str(ctx.exception)
        )

    def test_incorrect_iv_size(self):
        '''
        iv must be 16 bytes
        '''
        key = b'\x00' * 16
        with self.assertRaises(ValueError) as ctx:
            aes.create_encryptor(key, iv=b'\x00' * 3)
        self.assertIn(
            "16 bytes long",
            str(ctx.exception)
        )


class TestEd25519(unittest.TestCase):
    """
    Test allmydata.crypto.ed25519
    """

    def test_key_serialization(self):
        """
        a serialized+deserialized keypair is the same as the original
        """
        private_key, public_key = ed25519.create_signing_keypair()
        private_key_str = ed25519.string_from_signing_key(private_key)

        self.assertIsInstance(private_key_str, native_bytes)

        private_key2, public_key2 = ed25519.signing_keypair_from_string(private_key_str)

        # the deserialized signing keys are the same as the original
        self.assertEqual(
            ed25519.string_from_signing_key(private_key),
            ed25519.string_from_signing_key(private_key2),
        )
        self.assertEqual(
            ed25519.string_from_verifying_key(public_key),
            ed25519.string_from_verifying_key(public_key2),
        )

        # ditto, but for the verifying keys
        public_key_str = ed25519.string_from_verifying_key(public_key)
        self.assertIsInstance(public_key_str, native_bytes)

        public_key2 = ed25519.verifying_key_from_string(public_key_str)
        self.assertEqual(
            ed25519.string_from_verifying_key(public_key),
            ed25519.string_from_verifying_key(public_key2),
        )

    def test_deserialize_private_not_bytes(self):
        '''
        serialized key must be bytes
        '''
        with self.assertRaises(ValueError) as ctx:
            ed25519.signing_keypair_from_string(u"not bytes")
        self.assertIn(
            "must be bytes",
            str(ctx.exception)
        )

    def test_deserialize_public_not_bytes(self):
        '''
        serialized key must be bytes
        '''
        with self.assertRaises(ValueError) as ctx:
            ed25519.verifying_key_from_string(u"not bytes")
        self.assertIn(
            "must be bytes",
            str(ctx.exception)
        )

    def test_signed_data_not_bytes(self):
        '''
        data to sign must be bytes
        '''
        priv, pub = ed25519.create_signing_keypair()
        with self.assertRaises(ValueError) as ctx:
            ed25519.sign_data(priv, u"not bytes")
        self.assertIn(
            "must be bytes",
            str(ctx.exception)
        )

    def test_signature_not_bytes(self):
        '''
        signature must be bytes
        '''
        priv, pub = ed25519.create_signing_keypair()
        with self.assertRaises(ValueError) as ctx:
            ed25519.verify_signature(pub, u"not bytes", b"data")
        self.assertIn(
            "must be bytes",
            str(ctx.exception)
        )

    def test_signature_data_not_bytes(self):
        '''
        signed data must be bytes
        '''
        priv, pub = ed25519.create_signing_keypair()
        with self.assertRaises(ValueError) as ctx:
            ed25519.verify_signature(pub, b"signature", u"not bytes")
        self.assertIn(
            "must be bytes",
            str(ctx.exception)
        )

    def test_sign_invalid_pubkey(self):
        '''
        pubkey must be correct kind of object
        '''
        priv, pub = ed25519.create_signing_keypair()
        with self.assertRaises(ValueError) as ctx:
            ed25519.sign_data(object(), b"data")
        self.assertIn(
            "must be an Ed25519PrivateKey",
            str(ctx.exception)
        )

    def test_verify_invalid_pubkey(self):
        '''
        pubkey must be correct kind of object
        '''
        priv, pub = ed25519.create_signing_keypair()
        with self.assertRaises(ValueError) as ctx:
            ed25519.verify_signature(object(), b"signature", b"data")
        self.assertIn(
            "must be an Ed25519PublicKey",
            str(ctx.exception)
        )


class TestRsa(unittest.TestCase):
    """
    Tests related to allmydata.crypto.rsa module
    """

    def test_keys(self):
        """
        test that two instances of 'the same' key sign and verify data
        in the same way
        """
        priv_key, pub_key = rsa.create_signing_keypair(2048)
        priv_key_str = rsa.der_string_from_signing_key(priv_key)

        self.assertIsInstance(priv_key_str, native_bytes)

        priv_key2, pub_key2 = rsa.create_signing_keypair_from_string(priv_key_str)

        # instead of asking "are these two keys equal", we can instead
        # test their function: can the second key verify a signature
        # produced by the first (and FAIL a signature with different
        # data)

        data_to_sign = b"test data"
        sig0 = rsa.sign_data(priv_key, data_to_sign)
        rsa.verify_signature(pub_key2, sig0, data_to_sign)

        # ..and the other way
        sig1 = rsa.sign_data(priv_key2, data_to_sign)
        rsa.verify_signature(pub_key, sig1, data_to_sign)

        # ..and a failed way
        with self.assertRaises(rsa.BadSignature):
            rsa.verify_signature(pub_key, sig1, data_to_sign + b"more")

    def test_sign_invalid_pubkey(self):
        '''
        signing data using an invalid key-object fails
        '''
        priv, pub = rsa.create_signing_keypair(1024)
        with self.assertRaises(ValueError) as ctx:
            rsa.sign_data(object(), b"data")
        self.assertIn(
            "must be an RSAPrivateKey",
            str(ctx.exception)
        )

    def test_verify_invalid_pubkey(self):
        '''
        verifying a signature using an invalid key-object fails
        '''
        priv, pub = rsa.create_signing_keypair(1024)
        with self.assertRaises(ValueError) as ctx:
            rsa.verify_signature(object(), b"signature", b"data")
        self.assertIn(
            "must be an RSAPublicKey",
            str(ctx.exception)
        )


class TestUtil(unittest.TestCase):
    """
    tests related to allmydata.crypto utils
    """

    def test_remove_prefix_good(self):
        """
        remove a simple prefix properly
        """
        self.assertEqual(
            remove_prefix(b"foobar", b"foo"),
            b"bar"
        )

    def test_remove_prefix_bad(self):
        """
        attempt to remove a prefix that doesn't exist fails with exception
        """
        with self.assertRaises(BadPrefixError):
            remove_prefix(b"foobar", b"bar")

    def test_remove_prefix_zero(self):
        """
        removing a zero-length prefix does nothing
        """
        self.assertEqual(
            remove_prefix(b"foobar", b""),
            b"foobar",
        )

    def test_remove_prefix_entire_string(self):
        """
        removing a prefix which is the whole string is empty
        """
        self.assertEqual(
            remove_prefix(b"foobar", b"foobar"),
            b"",
        )

    def test_remove_prefix_partial(self):
        """
        removing a prefix with only partial match fails with exception
        """
        with self.assertRaises(BadPrefixError):
            remove_prefix(b"foobar", b"fooz"),
