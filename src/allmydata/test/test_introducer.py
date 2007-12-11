from base64 import b32encode

import os

from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import log

from foolscap import Tub, Referenceable
from foolscap.eventual import fireEventually, flushEventualQueue
from twisted.application import service
from allmydata.introducer import IntroducerClient, IntroducerService, IntroducerNode
from allmydata.util import testutil

class MyNode(Referenceable):
    pass

class LoggingMultiService(service.MultiService):
    def log(self, msg, **kw):
        log.msg(msg, **kw)

class TestIntroducerNode(testutil.SignalMixin, unittest.TestCase):
    def test_loadable(self):
        basedir = "introducer.IntroducerNode.test_loadable"
        os.mkdir(basedir)
        q = IntroducerNode(basedir)
        d = fireEventually(None)
        d.addCallback(lambda res: q.startService())
        d.addCallback(lambda res: q.when_tub_ready())
        def _check_parameters(res):
            i = q.getServiceNamed("introducer")
            self.failUnlessEqual(i._encoding_parameters, (3, 7, 10))
        d.addCallback(_check_parameters)
        d.addCallback(lambda res: q.stopService())
        d.addCallback(flushEventualQueue)
        return d

    def test_set_parameters(self):
        basedir = "introducer.IntroducerNode.test_set_parameters"
        os.mkdir(basedir)
        f = open(os.path.join(basedir, "encoding_parameters"), "w")
        f.write("25 75 100")
        f.close()
        q = IntroducerNode(basedir)
        d = fireEventually(None)
        d.addCallback(lambda res: q.startService())
        d.addCallback(lambda res: q.when_tub_ready())
        def _check_parameters(res):
            i = q.getServiceNamed("introducer")
            self.failUnlessEqual(i._encoding_parameters, (25, 75, 100))
        d.addCallback(_check_parameters)
        d.addCallback(lambda res: q.stopService())
        d.addCallback(flushEventualQueue)
        return d

class TestIntroducer(unittest.TestCase, testutil.PollMixin):
    def setUp(self):
        self.parent = LoggingMultiService()
        self.parent.startService()
    def tearDown(self):
        log.msg("TestIntroducer.tearDown")
        d = defer.succeed(None)
        d.addCallback(lambda res: self.parent.stopService())
        d.addCallback(flushEventualQueue)
        return d


    def test_create(self):
        ic = IntroducerClient(None, "introducer", "myfurl")
        def _ignore(nodeid, rref):
            pass
        ic.notify_on_new_connection(_ignore)

    def test_listen(self):
        i = IntroducerService()
        i.setServiceParent(self.parent)

    def test_system(self):

        self.central_tub = tub = Tub()
        #tub.setOption("logLocalFailures", True)
        #tub.setOption("logRemoteFailures", True)
        tub.setServiceParent(self.parent)
        l = tub.listenOn("tcp:0")
        portnum = l.getPortnum()
        tub.setLocation("localhost:%d" % portnum)

        i = IntroducerService()
        i.setServiceParent(self.parent)
        iurl = tub.registerReference(i)
        NUMCLIENTS = 5

        clients = []
        tubs = {}
        for i in range(NUMCLIENTS):
            tub = Tub()
            #tub.setOption("logLocalFailures", True)
            #tub.setOption("logRemoteFailures", True)
            tub.setServiceParent(self.parent)
            l = tub.listenOn("tcp:0")
            portnum = l.getPortnum()
            tub.setLocation("localhost:%d" % portnum)

            n = MyNode()
            node_furl = tub.registerReference(n)
            c = IntroducerClient(tub, iurl, node_furl)

            c.setServiceParent(self.parent)
            clients.append(c)
            tubs[c] = tub

        def _wait_for_all_connections(res):
            dl = [] # list of when_enough_peers() for each peer
            # will fire once everybody is connected
            for c in clients:
                dl.append(c.when_enough_peers(NUMCLIENTS))
            return defer.DeferredList(dl, fireOnOneErrback=True)

        d = _wait_for_all_connections(None)

        def _check1(res):
            log.msg("doing _check1")
            for c in clients:
                self.failUnlessEqual(len(c.connections), NUMCLIENTS)
                self.failUnless(c._connected) # to the introducer
        d.addCallback(_check1)
        origin_c = clients[0]
        def _disconnect_somebody_else(res):
            # now disconnect somebody's connection to someone else
            # find a target that is not themselves
            for nodeid,rref in origin_c.connections.items():
                if b32encode(nodeid).lower() != tubs[origin_c].tubID:
                    victim = rref
                    break
            log.msg(" disconnecting %s->%s" % (tubs[origin_c].tubID, victim))
            victim.tracker.broker.transport.loseConnection()
            log.msg(" did disconnect")
        d.addCallback(_disconnect_somebody_else)
        def _wait_til_he_notices(res):
            # wait til the origin_c notices the loss
            log.msg(" waiting until peer notices the disconnection")
            return origin_c.when_fewer_than_peers(NUMCLIENTS)
        d.addCallback(_wait_til_he_notices)
        d.addCallback(_wait_for_all_connections)
        def _check2(res):
            log.msg("doing _check2")
            for c in clients:
                self.failUnlessEqual(len(c.connections), NUMCLIENTS)
        d.addCallback(_check2)
        def _disconnect_yourself(res):
            # now disconnect somebody's connection to themselves.
            # find a target that *is* themselves
            for nodeid,rref in origin_c.connections.items():
                if b32encode(nodeid).lower() == tubs[origin_c].tubID:
                    victim = rref
                    break
            log.msg(" disconnecting %s->%s" % (tubs[origin_c].tubID, victim))
            victim.tracker.broker.transport.loseConnection()
            log.msg(" did disconnect from self")
        d.addCallback(_disconnect_yourself)
        d.addCallback(_wait_til_he_notices)
        d.addCallback(_wait_for_all_connections)
        def _check3(res):
            log.msg("doing _check3")
            for c in clients:
                self.failUnlessEqual(len(c.connections), NUMCLIENTS)
        d.addCallback(_check3)
        def _shutdown_introducer(res):
            # now shut down the introducer. We do this by shutting down the
            # tub it's using. Nobody's connections (to each other) should go
            # down. All clients should notice the loss, and no other errors
            # should occur.
            log.msg("shutting down the introducer")
            return self.central_tub.disownServiceParent()
        d.addCallback(_shutdown_introducer)
        d.addCallback(self.stall, 2)
        def _check4(res):
            log.msg("doing _check4")
            for c in clients:
                self.failUnlessEqual(len(c.connections), NUMCLIENTS)
                self.failIf(c._connected)
        d.addCallback(_check4)
        return d
    test_system.timeout = 2400

    def stall(self, res, timeout):
        d = defer.Deferred()
        reactor.callLater(timeout, d.callback, res)
        return d

    def test_system_this_one_breaks(self):
        # this uses a single Tub, which has a strong effect on the
        # failingness
        tub = Tub()
        tub.setOption("logLocalFailures", True)
        tub.setOption("logRemoteFailures", True)
        tub.setServiceParent(self.parent)
        l = tub.listenOn("tcp:0")
        portnum = l.getPortnum()
        tub.setLocation("localhost:%d" % portnum)

        i = IntroducerService()
        i.setServiceParent(self.parent)
        iurl = tub.registerReference(i)

        clients = []
        for i in range(5):
            n = MyNode()
            node_furl = tub.registerReference(n)
            c = IntroducerClient(tub, iurl, node_furl)
            c.setServiceParent(self.parent)
            clients.append(c)

        # time passes..
        d = defer.Deferred()
        def _check(res):
            log.msg("doing _check")
            self.failUnlessEqual(len(clients[0].connections), 5)
        d.addCallback(_check)
        reactor.callLater(2, d.callback, None)
        return d
    del test_system_this_one_breaks


    def test_system_this_one_breaks_too(self):
        # this one shuts down so quickly that it fails in a different way
        self.central_tub = tub = Tub()
        tub.setOption("logLocalFailures", True)
        tub.setOption("logRemoteFailures", True)
        tub.setServiceParent(self.parent)
        l = tub.listenOn("tcp:0")
        portnum = l.getPortnum()
        tub.setLocation("localhost:%d" % portnum)

        i = IntroducerService()
        i.setServiceParent(self.parent)
        iurl = tub.registerReference(i)

        clients = []
        for i in range(5):
            tub = Tub()
            tub.setOption("logLocalFailures", True)
            tub.setOption("logRemoteFailures", True)
            tub.setServiceParent(self.parent)
            l = tub.listenOn("tcp:0")
            portnum = l.getPortnum()
            tub.setLocation("localhost:%d" % portnum)

            n = MyNode()
            node_furl = tub.registerReference(n)
            c = IntroducerClient(tub, iurl, node_furl)
            c.setServiceParent(self.parent)
            clients.append(c)

        # time passes..
        d = defer.Deferred()
        reactor.callLater(0.01, d.callback, None)
        def _check(res):
            log.msg("doing _check")
            self.fail("BOOM")
            for c in clients:
                self.failUnlessEqual(len(c.connections), 5)
            c.connections.values()[0].tracker.broker.transport.loseConnection()
            return self.stall(None, 2)
        d.addCallback(_check)
        def _check_again(res):
            log.msg("doing _check_again")
            for c in clients:
                self.failUnlessEqual(len(c.connections), 5)
        d.addCallback(_check_again)
        return d
    del test_system_this_one_breaks_too
