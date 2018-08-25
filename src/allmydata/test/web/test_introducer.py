from twisted.trial import unittest
from foolscap.api import fireEventually, flushEventualQueue
from twisted.internet import defer
from allmydata.introducer import IntroducerNode
from allmydata import node
from .common import FAVICON_MARKUP
from ..common_web import do_http

class IntroducerWeb(unittest.TestCase):
    def setUp(self):
        self.node = None

    def tearDown(self):
        d = defer.succeed(None)
        if self.node:
            d.addCallback(lambda ign: self.node.stopService())
        d.addCallback(flushEventualQueue)
        return d

    @defer.inlineCallbacks
    def test_welcome(self):
        config = (
            "[node]\n"
            "tub.location = 127.0.0.1:1\n"
            "web.port = tcp:0\n"
        )
        basedir = self.mktemp()
        node.create_node_dir(basedir, "testing")

        from allmydata.node import config_from_string
        self.node = IntroducerNode(
            config_from_string(config, "introducer.port", basedir),
        )
        self.ws = self.node.getServiceNamed("webish")

        yield fireEventually(None)
        self.node.startService()

        url = "http://localhost:%d/" % self.ws.getPortnum()
        res = yield do_http("get", url)
        self.failUnlessIn('Welcome to the Tahoe-LAFS Introducer', res)
        self.failUnlessIn(FAVICON_MARKUP, res)
        self.failUnlessIn('Page rendered at', res)
        self.failUnlessIn('Tahoe-LAFS code imported from:', res)
