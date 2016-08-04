import os.path
from twisted.trial import unittest
from foolscap.api import fireEventually, flushEventualQueue
from allmydata.util import fileutil
from twisted.internet import defer, reactor
from allmydata.introducer import IntroducerNode
from .common import FAVICON_MARKUP
from ..common_web import HTTPClientGETFactory

class IntroducerWeb(unittest.TestCase):
    def setUp(self):
        self.node = None

    def tearDown(self):
        d = defer.succeed(None)
        if self.node:
            d.addCallback(lambda ign: self.node.stopService())
        d.addCallback(flushEventualQueue)
        return d

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

        d = fireEventually(None)
        d.addCallback(lambda ign: self.node.startService())

        d.addCallback(lambda ign: self.GET("/"))
        def _check(res):
            self.failUnlessIn('Welcome to the Tahoe-LAFS Introducer', res)
            self.failUnlessIn(FAVICON_MARKUP, res)
            self.failUnlessIn('Page rendered at', res)
            self.failUnlessIn('Tahoe-LAFS code imported from:', res)
        d.addCallback(_check)
        return d

    def GET(self, urlpath, followRedirect=False, return_response=False,
            **kwargs):
        # if return_response=True, this fires with (data, statuscode,
        # respheaders) instead of just data.
        assert not isinstance(urlpath, unicode)
        url = self.ws.getURL().rstrip('/') + urlpath
        factory = HTTPClientGETFactory(url, method="GET",
                                       followRedirect=followRedirect, **kwargs)
        reactor.connectTCP("localhost", self.ws.getPortnum(), factory)
        d = factory.deferred
        def _got_data(data):
            return (data, factory.status, factory.response_headers)
        if return_response:
            d.addCallback(_got_data)
        return factory.deferred

