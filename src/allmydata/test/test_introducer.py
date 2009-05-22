from base64 import b32decode

import os

from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import log

from foolscap.api import Tub, Referenceable, fireEventually, flushEventualQueue
from twisted.application import service
from allmydata.interfaces import InsufficientVersionError
from allmydata.introducer.client import IntroducerClient
from allmydata.introducer.server import IntroducerService
from allmydata.introducer.common import make_index
# test compatibility with old introducer .tac files
from allmydata.introducer import IntroducerNode
from allmydata.introducer import old
from allmydata.util import idlib, pollmixin
import common_util as testutil

class FakeNode(Referenceable):
    pass

class LoggingMultiService(service.MultiService):
    def log(self, msg, **kw):
        log.msg(msg, **kw)

class Node(testutil.SignalMixin, unittest.TestCase):
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

class ServiceMixin:
    def setUp(self):
        self.parent = LoggingMultiService()
        self.parent.startService()
    def tearDown(self):
        log.msg("TestIntroducer.tearDown")
        d = defer.succeed(None)
        d.addCallback(lambda res: self.parent.stopService())
        d.addCallback(flushEventualQueue)
        return d

class Introducer(ServiceMixin, unittest.TestCase, pollmixin.PollMixin):

    def test_create(self):
        ic = IntroducerClient(None, "introducer.furl", "my_nickname",
                              "my_version", "oldest_version")

    def test_listen(self):
        i = IntroducerService()
        i.setServiceParent(self.parent)

    def test_duplicate(self):
        i = IntroducerService()
        self.failUnlessEqual(len(i.get_announcements()), 0)
        self.failUnlessEqual(len(i.get_subscribers()), 0)
        furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@192.168.69.247:36106,127.0.0.1:36106/gydnpigj2ja2qr2srq4ikjwnl7xfgbra"
        furl2 = "pb://ttwwooyunnyhzs7r6vdonnm2hpi52w6y@192.168.69.247:36111,127.0.0.1:36106/ttwwoogj2ja2qr2srq4ikjwnl7xfgbra"
        ann1 = (furl1, "storage", "RIStorage", "nick1", "ver23", "ver0")
        ann1b = (furl1, "storage", "RIStorage", "nick1", "ver24", "ver0")
        ann2 = (furl2, "storage", "RIStorage", "nick2", "ver30", "ver0")
        i.remote_publish(ann1)
        self.failUnlessEqual(len(i.get_announcements()), 1)
        self.failUnlessEqual(len(i.get_subscribers()), 0)
        i.remote_publish(ann2)
        self.failUnlessEqual(len(i.get_announcements()), 2)
        self.failUnlessEqual(len(i.get_subscribers()), 0)
        i.remote_publish(ann1b)
        self.failUnlessEqual(len(i.get_announcements()), 2)
        self.failUnlessEqual(len(i.get_subscribers()), 0)

class SystemTestMixin(ServiceMixin, pollmixin.PollMixin):

    def setUp(self):
        ServiceMixin.setUp(self)
        self.central_tub = tub = Tub()
        #tub.setOption("logLocalFailures", True)
        #tub.setOption("logRemoteFailures", True)
        tub.setOption("expose-remote-exception-types", False)
        tub.setServiceParent(self.parent)
        l = tub.listenOn("tcp:0")
        portnum = l.getPortnum()
        tub.setLocation("localhost:%d" % portnum)

class SystemTest(SystemTestMixin, unittest.TestCase):

    def test_system(self):
        i = IntroducerService()
        i.setServiceParent(self.parent)
        self.introducer_furl = self.central_tub.registerReference(i)
        return self.do_system_test()

    def test_system_oldserver(self):
        i = old.IntroducerService_V1()
        i.setServiceParent(self.parent)
        self.introducer_furl = self.central_tub.registerReference(i)
        return self.do_system_test()

    def do_system_test(self):

        NUMCLIENTS = 5
        # we have 5 clients who publish themselves, and an extra one does
        # which not. When the connections are fully established, all six nodes
        # should have 5 connections each.

        clients = []
        tubs = {}
        for i in range(NUMCLIENTS+1):
            tub = Tub()
            #tub.setOption("logLocalFailures", True)
            #tub.setOption("logRemoteFailures", True)
            tub.setOption("expose-remote-exception-types", False)
            tub.setServiceParent(self.parent)
            l = tub.listenOn("tcp:0")
            portnum = l.getPortnum()
            tub.setLocation("localhost:%d" % portnum)

            n = FakeNode()
            log.msg("creating client %d: %s" % (i, tub.getShortTubID()))
            client_class = IntroducerClient
            if i == 0:
                client_class = old.IntroducerClient_V1
            c = client_class(tub, self.introducer_furl,
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
                nodeid0 = b32decode(tubs[clients[0]].tubID.upper())
                self.failUnlessEqual(c.get_nickname_for_peerid(nodeid0),
                                     "nickname-0")
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

class TooNewServer(IntroducerService):
    VERSION = { "http://allmydata.org/tahoe/protocols/introducer/v999":
                 { },
                "application-version": "greetings from the crazy future",
                }

class NonV1Server(SystemTestMixin, unittest.TestCase):
    # if the 1.3.0 client connects to a server that doesn't provide the 'v1'
    # protocol, it is supposed to provide a useful error instead of a weird
    # exception.

    def test_failure(self):
        i = TooNewServer()
        i.setServiceParent(self.parent)
        self.introducer_furl = self.central_tub.registerReference(i)

        tub = Tub()
        tub.setOption("expose-remote-exception-types", False)
        tub.setServiceParent(self.parent)
        l = tub.listenOn("tcp:0")
        portnum = l.getPortnum()
        tub.setLocation("localhost:%d" % portnum)

        n = FakeNode()
        c = IntroducerClient(tub, self.introducer_furl,
                             "nickname-client", "version", "oldest")
        c.subscribe_to("storage")

        c.setServiceParent(self.parent)

        # now we wait for it to connect and notice the bad version

        def _got_bad():
            return bool(c._introducer_error) or bool(c._publisher)
        d = self.poll(_got_bad)
        def _done(res):
            self.failUnless(c._introducer_error)
            self.failUnless(c._introducer_error.check(InsufficientVersionError))
        d.addCallback(_done)
        return d

class Index(unittest.TestCase):
    def test_make_index(self):
        # make sure we have a working base64.b32decode. The one in
        # python2.4.[01] was broken.
        ann = ('pb://t5g7egomnnktbpydbuijt6zgtmw4oqi5@127.0.0.1:51857/hfzv36i',
               'storage', 'RIStorageServer.tahoe.allmydata.com',
               'plancha', 'allmydata-tahoe/1.4.1', '1.0.0')
        (nodeid, service_name) = make_index(ann)
        self.failUnlessEqual(nodeid, "\x9fM\xf2\x19\xcckU0\xbf\x03\r\x10\x99\xfb&\x9b-\xc7A\x1d")
        self.failUnlessEqual(service_name, "storage")

