
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import log

from foolscap import Tub, Referenceable
from foolscap.eventual import flushEventualQueue
from twisted.application import service
from allmydata.node import Node
from allmydata.util import idlib, testutil

class LoggingMultiService(service.MultiService):
    def log(self, msg):
        pass

class TestNode(Node):
    CERTFILE='DEFAULT_CERTFILE_BLANK'
    PORTNUMFILE='DEFAULT_PORTNUMFILE_BLANK'

class TestCase(unittest.TestCase, testutil.SignalMixin):
    def setUp(self):
        self.parent = LoggingMultiService()
        self.parent.startService()
    def tearDown(self):
        log.msg("%s.tearDown" % self.__class__.__name__)
        d = defer.succeed(None)
        d.addCallback(lambda res: self.parent.stopService())
        d.addCallback(flushEventualQueue)
        return d

    def test_advertised_ip_addresses(self):
        open('advertised_ip_addresses','w').write('1.2.3.4:5')

        n = TestNode()
        n.setServiceParent(self.parent)
        d = n.when_tub_ready()

        def _check_addresses(ignored_result):
            self.failUnless("1.2.3.4:5" in n.tub.registerReference(n), n.tub.registerReference(n))

        d.addCallback(_check_addresses)
        return d

