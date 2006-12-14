#!/usr/bin/env python

from pyutil.assertutil import _assert, precondition, postcondition
from pyutil.randutil import insecurerandstr
from pyutil import benchutil

from Crypto.Cipher import AES

MODE_CTR = AES.MODE_CTR # MODE_CTR is same value for all ciphers in pycrypto 2.0.1

class CipherRunner:
    def __init__(self, ciph, mode):
        if not ciph.key_size: ciph.key_size = 16
        self.ciph = ciph
        self.mode = mode
        self.counterstart = None
        self.key = None
        self.text = None
        self.obj = None
        self.obj2 = None

    def init(self, n):
        precondition(self.ciph.key_size, self.ciph.key_size)
        self.key = insecurerandstr(self.ciph.key_size)
        if self.mode == MODE_CTR:
            self.counterstart = insecurerandstr(self.ciph.block_size)
        _assert(self.key, self.key)
        self.text = insecurerandstr(n)
        if self.mode == MODE_CTR:
            self.obj = self.ciph.new(self.key, self.mode, counterstart=self.counterstart)
        else:
            self.obj = self.ciph.new(self.key, self.mode)
        if self.mode == MODE_CTR:
            self.obj2 = self.ciph.new(self.key, self.mode, counterstart=self.counterstart)
        else:
            self.obj2 = self.ciph.new(self.key, self.mode)

    def construct_then_encrypt(self, n):
        assert len(self.text) == n
        if self.mode == MODE_CTR:
            self.obj = self.ciph.new(self.key, self.mode, counterstart=self.counterstart)
        else:
            self.obj = self.ciph.new(self.key, self.mode)
        return self.obj.encrypt(self.text)

    def encrypt(self, n):
        assert len(self.text) == n
        return self.obj.encrypt(self.text)
        
    def decrypt(self, n):
        assert len(self.text) == n
        return self.obj.decrypt(self.text)

    def encrypt_then_decrypt_then_compare(self, n):
        assert len(self.text) == n
        ciphertext = self.obj.encrypt(self.text)
        decrypted = self.obj2.decrypt(ciphertext)
        if decrypted != self.text:
            raise "FAILURE!  decrypted does match original plaintext, self.text[:64]: %r, decrypted[:64]: %r" % (self.text[:64], decrypted[:64],)

    def construct_then_encrypt_then_decrypt_then_compare(self, n):
        assert len(self.text) == n
        if self.mode == MODE_CTR:
            self.obj = self.ciph.new(self.key, self.mode, counterstart=self.counterstart)
        else:
            self.obj = self.ciph.new(self.key, self.mode)
        if self.mode == MODE_CTR:
            self.obj2 = self.ciph.new(self.key, self.mode, counterstart=self.counterstart)
        else:
            self.obj2 = self.ciph.new(self.key, self.mode)
        ciphertext = self.obj.encrypt(self.text)
        decrypted = self.obj2.decrypt(ciphertext)
        if decrypted != self.text:
            raise "FAILURE!  decrypted does match original plaintext, self.text[:64]: %r, decrypted[:64]: %r" % (self.text[:64], decrypted[:64],)

from Crypto.Cipher import *
def bench_aes_ctr():
    c = CipherRunner(AES, AES.MODE_CTR)

    for m in (c.construct_then_encrypt, c.encrypt, c.decrypt, c.encrypt_then_decrypt_then_compare, c.construct_then_encrypt_then_decrypt_then_compare,):
        print m.__name__
        for BSIZE in (2**4, 2**8, 2**10, 2**14, 2**16,):
            benchutil.rep_bench(m, BSIZE, initfunc=c.init, MAXREPS=2**14, MAXTIME=10.0)

