
import os
from twisted.trial import unittest
from twisted.application import service

from foolscap import Tub, eventual

from allmydata import key_generator
from allmydata.util import testutil
from pycryptopp.publickey import rsa

def flush_but_dont_ignore(res):
    d = eventual.flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

class KeyGenService(unittest.TestCase, testutil.PollMixin):
    def setUp(self):
        self.parent = service.MultiService()
        self.parent.startService()

        self.tub = t = Tub()
        t.setServiceParent(self.parent)
        t.listenOn("tcp:0")
        t.setLocationAutomatically()
        return eventual.fireEventually()

    def tearDown(self):
        d = self.parent.stopService()
        d.addCallback(eventual.fireEventually)
        d.addBoth(flush_but_dont_ignore)
        return d

    def test_key_gen_service(self):
        def p(junk, msg):
            #import time
            #print time.asctime(), msg
            return junk

        #print 'starting key generator service'
        keysize = 522
        kgs = key_generator.KeyGeneratorService(display_furl=False, default_key_size=keysize)
        kgs.key_generator.verbose = True
        kgs.setServiceParent(self.parent)
        kgs.key_generator.pool_size = 8

        def keypool_full():
            return len(kgs.key_generator.keypool) == kgs.key_generator.pool_size

        # first wait for key gen pool to fill up
        d = eventual.fireEventually()
        d.addCallback(p, 'waiting for pool to fill up')
        d.addCallback(lambda junk: self.poll(keypool_full))

        d.addCallback(p, 'grabbing a few keys')
        # grab a few keys, check that pool size shrinks
        def get_key(junk=None):
            d = self.tub.getReference(kgs.keygen_furl)
            d.addCallback(lambda kg: kg.callRemote('get_rsa_key_pair', keysize))
            return d

        def check_poolsize(junk, size):
            self.failUnlessEqual(len(kgs.key_generator.keypool), size)

        n_keys_to_waste = 4
        for i in range(n_keys_to_waste):
            d.addCallback(get_key)
        d.addCallback(check_poolsize, kgs.key_generator.pool_size - n_keys_to_waste)

        d.addCallback(p, 'checking a key works')
        # check that a retrieved key is actually useful
        d.addCallback(get_key)
        def check_key_works(keys):
            verifying_key, signing_key = keys
            v = rsa.create_verifying_key_from_string(verifying_key)
            s = rsa.create_signing_key_from_string(signing_key)
            junk = os.urandom(42)
            sig = s.sign(junk)
            self.failUnless(v.verify(junk, sig))
        d.addCallback(check_key_works)

        d.addCallback(p, 'checking pool exhaustion')
        # exhaust the pool
        for i in range(kgs.key_generator.pool_size):
            d.addCallback(get_key)
        d.addCallback(check_poolsize, 0)

        # and check it still works (will gen key synchronously on demand)
        d.addCallback(get_key)
        d.addCallback(check_key_works)

        d.addCallback(p, 'checking pool replenishment')
        # check that the pool will refill
        d.addCallback(lambda junk: self.poll(keypool_full))

        return d
