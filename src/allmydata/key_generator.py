
import os
import time

import foolscap
from foolscap.eventual import eventually
from zope.interface import implements
from twisted.internet import reactor
from twisted.application import service
from twisted.python import log

from pycryptopp.publickey import rsa
from allmydata.interfaces import RIKeyGenerator

class KeyGenerator(foolscap.Referenceable):
    implements(RIKeyGenerator)

    DEFAULT_KEY_SIZE = 2048
    pool_size = 16 # no. keys to keep on hand in the pool
    pool_refresh_delay = 6 # no. sec to wait after a fetch before generating new keys
    verbose = False

    def __init__(self):
        self.keypool = []
        self.last_fetch = 0
        eventually(self.maybe_refill_pool)

    def __repr__(self):
        return '<KeyGenerator[%s]>' % (len(self.keypool),)

    def vlog(self, msg):
        if self.verbose:
            log.msg(msg)

    def reset_timer(self):
        self.last_fetch = time.time()
        reactor.callLater(self.pool_refresh_delay, self.maybe_refill_pool)

    def maybe_refill_pool(self):
        now = time.time()
        if self.last_fetch + self.pool_refresh_delay < now:
            self.vlog('%s refilling pool' % (self,))
            while len(self.keypool) < self.pool_size:
                self.keypool.append(self.gen_key(self.DEFAULT_KEY_SIZE))
        else:
            self.vlog('%s not refilling pool' % (self,))

    def gen_key(self, key_size):
        self.vlog('%s generating key size %s' % (self, key_size, ))
        signer = rsa.generate(key_size)
        verifier = signer.get_verifying_key()
        return verifier.serialize(), signer.serialize()

    def remote_get_rsa_key_pair(self, key_size):
        self.vlog('%s remote_get_key' % (self,))
        if key_size != self.DEFAULT_KEY_SIZE or not self.keypool:
            self.reset_timer()
            return self.gen_key(key_size)
        else:
            self.reset_timer()
            return self.keypool.pop()

class KeyGeneratorService(service.MultiService):
    furl_file = 'key_generator.furl'

    def __init__(self, display_furl=True):
        service.MultiService.__init__(self)
        self.tub = foolscap.Tub(certFile='key_generator.pem')
        self.tub.setServiceParent(self)
        self.key_generator = KeyGenerator()

        portnum = self.get_portnum()
        self.listener = self.tub.listenOn(portnum or 'tcp:0')
        d = self.tub.setLocationAutomatically()
        if portnum is None:
            d.addCallback(self.save_portnum)
        d.addCallback(self.tub_ready, display_furl)
        d.addErrback(log.err)

    def get_portnum(self):
        if os.path.exists('portnum'):
            return file('portnum', 'rb').read().strip()

    def save_portnum(self, junk):
        portnum = self.listener.getPortnum()
        file('portnum', 'wb').write('%d\n' % (portnum,))

    def tub_ready(self, junk, display_furl):
        self.keygen_furl = self.tub.registerReference(self.key_generator, furlFile=self.furl_file)
        if display_furl:
            print 'key generator at:', self.keygen_furl 
