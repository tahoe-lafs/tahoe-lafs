import os.path
from twisted.trial import unittest
from foolscap.api import fireEventually, flushEventualQueue
from allmydata.util import fileutil
from twisted.internet import defer
from allmydata.introducer import IntroducerNode
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
        basedir = "web.IntroducerWeb.test_welcome"
        os.mkdir(basedir)
        cfg = "\n".join(["[node]",
                         "tub.location = 127.0.0.1:1",
                         "web.port = tcp:0",
                         ]) + "\n"
        fileutil.write(os.path.join(basedir, "tahoe.cfg"), cfg)
        self.node = IntroducerNode(basedir)
        self.ws = self.node.getServiceNamed("webish")

        yield fireEventually(None)
        self.node.startService()

        url = "http://localhost:%d/" % self.ws.getPortnum()
        res = yield do_http("get", url)
        self.failUnlessIn('Welcome to the Tahoe-LAFS Introducer', res)
        self.failUnlessIn(FAVICON_MARKUP, res)
        self.failUnlessIn('Page rendered at', res)
        self.failUnlessIn('Tahoe-LAFS code imported from:', res)
