import six
import unittest

from binascii import a2b_hex, b2a_hex

from allmydata.crypto.aes import AES
from allmydata.crypto.ed25519 import SigningKey, VerifyingKey


class TestRegression(unittest.TestCase):
    '''
    These tests are regression tests to ensure that the upgrade from `pycryptopp` to `cryptography`
    doesn't break anything. They check that data encrypted with old keys can be decrypted with new
    keys.
    '''

    KEY = b'My\x9c\xc0f\xd3\x03\x9a1\x8f\xbd\x17W_\x1f2'
    IV = b'\x96\x1c\xa0\xbcUj\x89\xc1\x85J\x1f\xeb=\x17\x04\xca'

    def test_old_start_up_test(self):
        """
        This was the old startup test run at import time in `pycryptopp.cipher.aes`.
        """
        enc0 = b"dc95c078a2408989ad48a21492842087530f8afbc74536b9a963b4f1c4cb738b"
        cryptor = AES(key=b"\x00" * 32)
        ct = cryptor.process(b"\x00" * 32)
        self.failUnlessEqual(enc0, b2a_hex(ct))

        cryptor = AES(key=b"\x00" * 32)
        ct1 = cryptor.process(b"\x00" * 15)
        ct2 = cryptor.process(b"\x00" * 17)
        self.failUnlessEqual(enc0, b2a_hex(ct1+ct2))

        enc0 = b"66e94bd4ef8a2c3b884cfa59ca342b2e"
        cryptor = AES(key=b"\x00" * 16)
        ct = cryptor.process(b"\x00" * 16)
        self.failUnlessEqual(enc0, b2a_hex(ct))

        cryptor = AES(key=b"\x00" * 16)
        ct1 = cryptor.process(b"\x00" * 8)
        ct2 = cryptor.process(b"\x00" * 8)
        self.failUnlessEqual(enc0, b2a_hex(ct1+ct2))

        def _test_from_Niels_AES(keysize, result):
            def fake_ecb_using_ctr(k, p):
                return AES(key=k, iv=p).process(b'\x00' * 16)

            E = fake_ecb_using_ctr
            b = 16
            k = keysize
            S = '\x00' * (k+b)

            for i in range(1000):
                K = S[-k:]
                P = S[-k-b:-k]
                S += E(K, E(K, P))

            self.failUnlessEqual(S[-b:], a2b_hex(result))

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

        aes = AES(self.KEY)
        ciphertext = aes.process(plaintext)

        self.failUnlessEqual(ciphertext, expected_ciphertext)

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

        aes = AES(self.KEY)
        ciphertext = aes.process(plaintext)

        self.failUnlessEqual(ciphertext, expected_ciphertext)

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

        aes = AES(self.KEY, iv=self.IV)
        ciphertext = aes.process(plaintext)

        self.failUnlessEqual(ciphertext, expected_ciphertext)

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


        aes = AES(self.KEY, iv=self.IV)
        ciphertext = aes.process(plaintext)

        self.failUnlessEqual(ciphertext, expected_ciphertext)

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
        priv_str = 'priv-v0-lqcj746bqa4npkb6zpyc6esd74x3bl6mbcjgqend7cvtgmcpawhq'
        pub_str = 'pub-v0-yzpqin3of3ep363lwzxwpvgai3ps43dao46k2jds5kw5ohhpcwhq'
        test_data = b'test'
        sig = (b'\xde\x0e\xd6\xe2\xf5\x03]8\xfe\xa71\xad\xb4g\x03\x11\x81\x8b\x08\xffz\xf4K\xa0'
               b'\x86 ier!\xe8\xe5#*\x9d\x8c\x0bI\x02\xd90\x0e7\xbeW\xbf\xa3\xfe\xc1\x1c\xf5+\xe9)'
               b'\xa3\xde\xc9\xc6s\xc9\x90\xf7x\x08')

        priv_key = SigningKey.parse_encoded_key(priv_str)
        pub_key = VerifyingKey.parse_encoded_key(pub_str)

        self.failUnlessEqual(priv_key.public_key(), pub_key)

        new_sig = priv_key.sign(test_data)
        self.failUnlessEqual(new_sig, sig)

        pub_key.verify(new_sig, test_data)


class TestEd25519(unittest.TestCase):

    def test_keys(self):
        priv_key = SigningKey.generate()
        priv_key_str = priv_key.encoded_key()

        self.assertIsInstance(priv_key_str, six.string_types)
        self.assertIsInstance(priv_key.private_bytes(), six.binary_type)

        priv_key2 = SigningKey.parse_encoded_key(priv_key_str)

        self.failUnlessEqual(priv_key, priv_key2)

        pub_key = priv_key.public_key()
        pub_key2 = priv_key2.public_key()

        self.failUnlessEqual(pub_key, pub_key2)

        pub_key_str = pub_key.encoded_key()

        self.assertIsInstance(pub_key_str, six.string_types)
        self.assertIsInstance(pub_key.public_bytes(), six.binary_type)

        pub_key2 = VerifyingKey.parse_encoded_key(pub_key_str)

        self.failUnlessEqual(pub_key, pub_key2)
