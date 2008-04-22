from base64 import b32decode

import os

from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import log

from foolscap import Tub, Referenceable
from foolscap.eventual import fireEventually, flushEventualQueue
from twisted.application import service
from allmydata.introducer import IntroducerClient, IntroducerService, IntroducerNode
from allmydata.util import testutil, idlib

class FakeNode(Referenceable):
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
        ic = IntroducerClient(None, "introducer.furl", "my_nickname",
                              "my_version", "oldest_version")

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
        introducer_furl = tub.registerReference(i)
        NUMCLIENTS = 5
        # we have 5 clients who publish themselves, and an extra one which
        # does not. When the connections are fully established, all six nodes
        # should have 5 connections each.

        clients = []
        tubs = {}
        for i in range(NUMCLIENTS+1):
            tub = Tub()
            #tub.setOption("logLocalFailures", True)
            #tub.setOption("logRemoteFailures", True)
            tub.setServiceParent(self.parent)
            l = tub.listenOn("tcp:0")
            portnum = l.getPortnum()
            tub.setLocation("localhost:%d" % portnum)

            n = FakeNode()
            log.msg("creating client %d: %s" % (i, tub.getShortTubID()))
            c = IntroducerClient(tub, introducer_furl,
                                 "nickname-%d" % i, "version", "oldest")
            if i < NUMCLIENTS:
                node_furl = tub.registerReference(n)
                c.publish(node_furl, "storage", "ri_name")
            # the last one does not publish anything

            c.subscribe_to("storage")

            c.setServiceParent(self.parent)
            clients.append(c)
            tubs[c] = tub

        def _wait_for_all_connections():
            for c in clients:
                if len(c.get_all_connections()) < NUMCLIENTS:
                    return False
            return True
        d = self.poll(_wait_for_all_connections)

        def _check1(res):
            log.msg("doing _check1")
            for c in clients:
                self.failUnless(c.connected_to_introducer())
                self.failUnlessEqual(len(c.get_all_connections()), NUMCLIENTS)
                self.failUnlessEqual(len(c.get_all_peerids()), NUMCLIENTS)
                self.failUnlessEqual(len(c.get_all_connections_for("storage")),
                                     NUMCLIENTS)
        d.addCallback(_check1)

        origin_c = clients[0]
        def _disconnect_somebody_else(res):
            # now disconnect somebody's connection to someone else
            current_counter = origin_c.counter
            victim_nodeid = b32decode(tubs[clients[1]].tubID.upper())
            log.msg(" disconnecting %s->%s" %
                    (tubs[origin_c].tubID,
                     idlib.shortnodeid_b2a(victim_nodeid)))
            origin_c.debug_disconnect_from_peerid(victim_nodeid)
            log.msg(" did disconnect")

            # then wait until something changes, which ought to be them
            # noticing the loss
            def _compare():
                return current_counter != origin_c.counter
            return self.poll(_compare)

        d.addCallback(_disconnect_somebody_else)

        # and wait for them to reconnect
        d.addCallback(lambda res: self.poll(_wait_for_all_connections))
        def _check2(res):
            log.msg("doing _check2")
            for c in clients:
                self.failUnlessEqual(len(c.get_all_connections()), NUMCLIENTS)
        d.addCallback(_check2)

        def _disconnect_yourself(res):
            # now disconnect somebody's connection to themselves.
            current_counter = origin_c.counter
            victim_nodeid = b32decode(tubs[clients[0]].tubID.upper())
            log.msg(" disconnecting %s->%s" %
                    (tubs[origin_c].tubID,
                     idlib.shortnodeid_b2a(victim_nodeid)))
            origin_c.debug_disconnect_from_peerid(victim_nodeid)
            log.msg(" did disconnect from self")

            def _compare():
                return current_counter != origin_c.counter
            return self.poll(_compare)
        d.addCallback(_disconnect_yourself)

        d.addCallback(lambda res: self.poll(_wait_for_all_connections))
        def _check3(res):
            log.msg("doing _check3")
            for c in clients:
                self.failUnlessEqual(len(c.get_all_connections_for("storage")),
                                     NUMCLIENTS)
        d.addCallback(_check3)
        def _shutdown_introducer(res):
            # now shut down the introducer. We do this by shutting down the
            # tub it's using. Nobody's connections (to each other) should go
            # down. All clients should notice the loss, and no other errors
            # should occur.
            log.msg("shutting down the introducer")
            return self.central_tub.disownServiceParent()
        d.addCallback(_shutdown_introducer)
        def _wait_for_introducer_loss():
            for c in clients:
                if c.connected_to_introducer():
                    return False
            return True
        d.addCallback(lambda res: self.poll(_wait_for_introducer_loss))

        def _check4(res):
            log.msg("doing _check4")
            for c in clients:
                self.failUnlessEqual(len(c.get_all_connections_for("storage")),
                                     NUMCLIENTS)
                self.failIf(c.connected_to_introducer())
        d.addCallback(_check4)
        return d

