
import os, re
from base64 import b32decode

from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import log

from foolscap.api import Tub, Referenceable, fireEventually, flushEventualQueue
from twisted.application import service
from allmydata.interfaces import InsufficientVersionError
from allmydata.introducer.client import IntroducerClient
from allmydata.introducer.server import IntroducerService
# test compatibility with old introducer .tac files
from allmydata.introducer import IntroducerNode
from allmydata.util import pollmixin
import common_util as testutil

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
        ic = IntroducerClient(None, "introducer.furl", u"my_nickname",
                              "my_version", "oldest_version")
        self.failUnless(isinstance(ic, IntroducerClient))

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

    def create_tub(self, portnum=0):
        tubfile = os.path.join(self.basedir, "tub.pem")
        self.central_tub = tub = Tub(certFile=tubfile)
        #tub.setOption("logLocalFailures", True)
        #tub.setOption("logRemoteFailures", True)
        tub.setOption("expose-remote-exception-types", False)
        tub.setServiceParent(self.parent)
        l = tub.listenOn("tcp:%d" % portnum)
        self.central_portnum = l.getPortnum()
        if portnum != 0:
            assert self.central_portnum == portnum
        tub.setLocation("localhost:%d" % self.central_portnum)

class SystemTest(SystemTestMixin, unittest.TestCase):

    def test_system(self):
        self.basedir = "introducer/SystemTest/system"
        os.makedirs(self.basedir)
        return self.do_system_test(IntroducerService)
    test_system.timeout = 480 # occasionally takes longer than 350s on "draco"

    def do_system_test(self, create_introducer):
        self.create_tub()
        introducer = create_introducer()
        introducer.setServiceParent(self.parent)
        iff = os.path.join(self.basedir, "introducer.furl")
        tub = self.central_tub
        ifurl = self.central_tub.registerReference(introducer, furlFile=iff)
        self.introducer_furl = ifurl

        NUMCLIENTS = 5
        # we have 5 clients who publish themselves, and an extra one does
        # which not. When the connections are fully established, all six nodes
        # should have 5 connections each.

        clients = []
        tubs = {}
        received_announcements = {}
        NUM_SERVERS = NUMCLIENTS
        subscribing_clients = []
        publishing_clients = []

        for i in range(NUMCLIENTS+1):
            tub = Tub()
            #tub.setOption("logLocalFailures", True)
            #tub.setOption("logRemoteFailures", True)
            tub.setOption("expose-remote-exception-types", False)
            tub.setServiceParent(self.parent)
            l = tub.listenOn("tcp:0")
            portnum = l.getPortnum()
            tub.setLocation("localhost:%d" % portnum)

            log.msg("creating client %d: %s" % (i, tub.getShortTubID()))
            c = IntroducerClient(tub, self.introducer_furl, u"nickname-%d" % i,
                                 "version", "oldest")
            received_announcements[c] = {}
            def got(serverid, ann_d, announcements):
                announcements[serverid] = ann_d
            c.subscribe_to("storage", got, received_announcements[c])
            subscribing_clients.append(c)

            if i < NUMCLIENTS:
                node_furl = tub.registerReference(Referenceable())
                c.publish(node_furl, "storage", "ri_name")
                publishing_clients.append(c)
            # the last one does not publish anything

            c.setServiceParent(self.parent)
            clients.append(c)
            tubs[c] = tub

        def _wait_for_all_connections():
            for c in subscribing_clients:
                if len(received_announcements[c]) < NUM_SERVERS:
                    return False
            return True
        d = self.poll(_wait_for_all_connections)

        def _check1(res):
            log.msg("doing _check1")
            dc = introducer._debug_counts
            self.failUnlessEqual(dc["inbound_message"], NUM_SERVERS)
            self.failUnlessEqual(dc["inbound_duplicate"], 0)
            self.failUnlessEqual(dc["inbound_update"], 0)
            self.failUnless(dc["outbound_message"])

            for c in clients:
                self.failUnless(c.connected_to_introducer())
            for c in subscribing_clients:
                cdc = c._debug_counts
                self.failUnless(cdc["inbound_message"])
                self.failUnlessEqual(cdc["inbound_announcement"],
                                     NUM_SERVERS)
                self.failUnlessEqual(cdc["wrong_service"], 0)
                self.failUnlessEqual(cdc["duplicate_announcement"], 0)
                self.failUnlessEqual(cdc["update"], 0)
                self.failUnlessEqual(cdc["new_announcement"],
                                     NUM_SERVERS)
                anns = received_announcements[c]
                self.failUnlessEqual(len(anns), NUM_SERVERS)

                nodeid0 = b32decode(tubs[clients[0]].tubID.upper())
                ann_d = anns[nodeid0]
                nick = ann_d["nickname"]
                self.failUnlessEqual(type(nick), unicode)
                self.failUnlessEqual(nick, u"nickname-0")
            for c in publishing_clients:
                cdc = c._debug_counts
                self.failUnlessEqual(cdc["outbound_message"], 1)
        d.addCallback(_check1)

        # force an introducer reconnect, by shutting down the Tub it's using
        # and starting a new Tub (with the old introducer). Everybody should
        # reconnect and republish, but the introducer should ignore the
        # republishes as duplicates. However, because the server doesn't know
        # what each client does and does not know, it will send them a copy
        # of the current announcement table anyway.

        d.addCallback(lambda _ign: log.msg("shutting down introducer's Tub"))
        d.addCallback(lambda _ign: self.central_tub.disownServiceParent())

        def _wait_for_introducer_loss():
            for c in clients:
                if c.connected_to_introducer():
                    return False
            return True
        d.addCallback(lambda res: self.poll(_wait_for_introducer_loss))

        def _restart_introducer_tub(_ign):
            log.msg("restarting introducer's Tub")

            dc = introducer._debug_counts
            self.expected_count = dc["inbound_message"] + NUM_SERVERS
            self.expected_subscribe_count = dc["inbound_subscribe"] + NUMCLIENTS+1
            introducer._debug0 = dc["outbound_message"]
            for c in subscribing_clients:
                cdc = c._debug_counts
                c._debug0 = cdc["inbound_message"]

            self.create_tub(self.central_portnum)
            newfurl = self.central_tub.registerReference(introducer,
                                                         furlFile=iff)
            assert newfurl == self.introducer_furl
        d.addCallback(_restart_introducer_tub)

        def _wait_for_introducer_reconnect():
            # wait until:
            #  all clients are connected
            #  the introducer has received publish messages from all of them
            #  the introducer has received subscribe messages from all of them
            #  the introducer has sent (duplicate) announcements to all of them
            #  all clients have received (duplicate) announcements
            dc = introducer._debug_counts
            for c in clients:
                if not c.connected_to_introducer():
                    return False
            if dc["inbound_message"] < self.expected_count:
                return False
            if dc["inbound_subscribe"] < self.expected_subscribe_count:
                return False
            for c in subscribing_clients:
                cdc = c._debug_counts
                if cdc["inbound_message"] < c._debug0+1:
                    return False
            return True
        d.addCallback(lambda res: self.poll(_wait_for_introducer_reconnect))

        def _check2(res):
            log.msg("doing _check2")
            # assert that the introducer sent out new messages, one per
            # subscriber
            dc = introducer._debug_counts
            self.failUnlessEqual(dc["inbound_message"], 2*NUM_SERVERS)
            self.failUnlessEqual(dc["inbound_duplicate"], NUM_SERVERS)
            self.failUnlessEqual(dc["inbound_update"], 0)
            self.failUnlessEqual(dc["outbound_message"],
                                 introducer._debug0 + len(subscribing_clients))
            for c in clients:
                self.failUnless(c.connected_to_introducer())
            for c in subscribing_clients:
                cdc = c._debug_counts
                self.failUnlessEqual(cdc["duplicate_announcement"], NUM_SERVERS)
        d.addCallback(_check2)

        # Then force an introducer restart, by shutting down the Tub,
        # destroying the old introducer, and starting a new Tub+Introducer.
        # Everybody should reconnect and republish, and the (new) introducer
        # will distribute the new announcements, but the clients should
        # ignore the republishes as duplicates.

        d.addCallback(lambda _ign: log.msg("shutting down introducer"))
        d.addCallback(lambda _ign: self.central_tub.disownServiceParent())
        d.addCallback(lambda res: self.poll(_wait_for_introducer_loss))

        def _restart_introducer(_ign):
            log.msg("restarting introducer")
            self.create_tub(self.central_portnum)

            for c in subscribing_clients:
                # record some counters for later comparison. Stash the values
                # on the client itself, because I'm lazy.
                cdc = c._debug_counts
                c._debug1 = cdc["inbound_announcement"]
                c._debug2 = cdc["inbound_message"]
                c._debug3 = cdc["new_announcement"]
            newintroducer = create_introducer()
            self.expected_message_count = NUM_SERVERS
            self.expected_announcement_count = NUM_SERVERS*len(subscribing_clients)
            self.expected_subscribe_count = len(subscribing_clients)
            newfurl = self.central_tub.registerReference(newintroducer,
                                                         furlFile=iff)
            assert newfurl == self.introducer_furl
        d.addCallback(_restart_introducer)
        def _wait_for_introducer_reconnect2():
            # wait until:
            #  all clients are connected
            #  the introducer has received publish messages from all of them
            #  the introducer has received subscribe messages from all of them
            #  the introducer has sent announcements for everybody to everybody
            #  all clients have received all the (duplicate) announcements
            # at that point, the system should be quiescent
            dc = introducer._debug_counts
            for c in clients:
                if not c.connected_to_introducer():
                    return False
            if dc["inbound_message"] < self.expected_message_count:
                return False
            if dc["outbound_announcements"] < self.expected_announcement_count:
                return False
            if dc["inbound_subscribe"] < self.expected_subscribe_count:
                return False
            for c in subscribing_clients:
                cdc = c._debug_counts
                if cdc["inbound_announcement"] < c._debug1+NUM_SERVERS:
                    return False
            return True
        d.addCallback(lambda res: self.poll(_wait_for_introducer_reconnect2))

        def _check3(res):
            log.msg("doing _check3")
            for c in clients:
                self.failUnless(c.connected_to_introducer())
            for c in subscribing_clients:
                cdc = c._debug_counts
                self.failUnless(cdc["inbound_announcement"] > c._debug1)
                self.failUnless(cdc["inbound_message"] > c._debug2)
                # there should have been no new announcements
                self.failUnlessEqual(cdc["new_announcement"], c._debug3)
                # and the right number of duplicate ones. There were
                # NUM_SERVERS from the servertub restart, and there should be
                # another NUM_SERVERS now
                self.failUnlessEqual(cdc["duplicate_announcement"],
                                     2*NUM_SERVERS)

        d.addCallback(_check3)
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
        self.basedir = "introducer/NonV1Server/failure"
        os.makedirs(self.basedir)
        self.create_tub()
        i = TooNewServer()
        i.setServiceParent(self.parent)
        self.introducer_furl = self.central_tub.registerReference(i)

        tub = Tub()
        tub.setOption("expose-remote-exception-types", False)
        tub.setServiceParent(self.parent)
        l = tub.listenOn("tcp:0")
        portnum = l.getPortnum()
        tub.setLocation("localhost:%d" % portnum)

        c = IntroducerClient(tub, self.introducer_furl,
                             u"nickname-client", "version", "oldest")
        announcements = {}
        def got(serverid, ann_d):
            announcements[serverid] = ann_d
        c.subscribe_to("storage", got)

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

class DecodeFurl(unittest.TestCase):
    def test_decode(self):
        # make sure we have a working base64.b32decode. The one in
        # python2.4.[01] was broken.
        furl = 'pb://t5g7egomnnktbpydbuijt6zgtmw4oqi5@127.0.0.1:51857/hfzv36i'
        m = re.match(r'pb://(\w+)@', furl)
        assert m
        nodeid = b32decode(m.group(1).upper())
        self.failUnlessEqual(nodeid, "\x9fM\xf2\x19\xcckU0\xbf\x03\r\x10\x99\xfb&\x9b-\xc7A\x1d")

