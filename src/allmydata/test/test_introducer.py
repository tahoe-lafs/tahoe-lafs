
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import log

from foolscap import Tub, Referenceable
from foolscap.eventual import flushEventualQueue
from twisted.application import service
from allmydata.introducer import IntroducerClient, Introducer
from allmydata.util import idlib

class MyNode(Referenceable):
    pass

class LoggingMultiService(service.MultiService):
    def log(self, msg):
        pass

class TestIntroducer(unittest.TestCase):
    def setUp(self):
        self.parent = LoggingMultiService()
        self.parent.startService()
    def tearDown(self):
        log.msg("TestIntroducer.tearDown")
        d = defer.succeed(None)
        d.addCallback(lambda res: self.parent.stopService())
        d.addCallback(flushEventualQueue)
        return d


    def poll(self, check_f, pollinterval=0.01):
        # Return a Deferred, then call check_f periodically until it returns
        # True, at which point the Deferred will fire.. If check_f raises an
        # exception, the Deferred will errback.
        d = defer.maybeDeferred(self._poll, None, check_f, pollinterval)
        return d

    def _poll(self, res, check_f, pollinterval):
        if check_f():
            return True
        d = defer.Deferred()
        d.addCallback(self._poll, check_f, pollinterval)
        reactor.callLater(pollinterval, d.callback, None)
        return d


    def test_create(self):
        ic = IntroducerClient(None, "introducer", "myfurl")
        def _ignore(nodeid, rref):
            pass
        ic.notify_on_new_connection(_ignore)

    def test_listen(self):
        i = Introducer()
        i.setServiceParent(self.parent)

    def test_system(self):

        self.central_tub = tub = Tub()
        #tub.setOption("logLocalFailures", True)
        #tub.setOption("logRemoteFailures", True)
        tub.setServiceParent(self.parent)
        l = tub.listenOn("tcp:0")
        portnum = l.getPortnum()
        tub.setLocation("localhost:%d" % portnum)

        i = Introducer()
        i.setServiceParent(self.parent)
        iurl = tub.registerReference(i)
        NUMCLIENTS = 5

        self.waiting_for_connections = NUMCLIENTS*NUMCLIENTS
        d = self._done_counting = defer.Deferred()
        def _count(nodeid, rref):
            log.msg("NEW CONNECTION! %s %s" % (idlib.b2a(nodeid), rref))
            self.waiting_for_connections -= 1
            if self.waiting_for_connections == 0:
                self._done_counting.callback("done!")

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
            c.notify_on_new_connection(_count)
            c.setServiceParent(self.parent)
            clients.append(c)
            tubs[c] = tub

        # d will fire once everybody is connected

        def _check(res):
            log.msg("doing _check")
            for c in clients:
                self.failUnlessEqual(len(c.connections), NUMCLIENTS)
            # now disconnect somebody's connection to someone else
            self.waiting_for_connections = 2
            d2 = self._done_counting = defer.Deferred()
            origin_c = clients[0]
            # find a target that is not themselves
            for nodeid,rref in origin_c.connections.items():
                if idlib.b2a(nodeid) != tubs[origin_c].tubID:
                    victim = rref
                    break
            log.msg(" disconnecting %s->%s" % (tubs[origin_c].tubID, victim))
            victim.tracker.broker.transport.loseConnection()
            log.msg(" did disconnect")
            return d2
        d.addCallback(_check)
        def _check_again(res):
            log.msg("doing _check_again")
            for c in clients:
                self.failUnlessEqual(len(c.connections), NUMCLIENTS)
            # now disconnect somebody's connection to themselves. This will
            # only result in one new connection, since it is a loopback.
            self.waiting_for_connections = 1
            d2 = self._done_counting = defer.Deferred()
            origin_c = clients[0]
            # find a target that *is* themselves
            for nodeid,rref in origin_c.connections.items():
                if idlib.b2a(nodeid) == tubs[origin_c].tubID:
                    victim = rref
                    break
            log.msg(" disconnecting %s->%s" % (tubs[origin_c].tubID, victim))
            victim.tracker.broker.transport.loseConnection()
            log.msg(" did disconnect")
            return d2
        d.addCallback(_check_again)
        def _check_again2(res):
            log.msg("doing _check_again2")
            for c in clients:
                self.failUnlessEqual(len(c.connections), NUMCLIENTS)
            # now disconnect somebody's connection to themselves
        d.addCallback(_check_again2)
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

        i = Introducer()
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

        i = Introducer()
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

