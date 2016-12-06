
import os, re, itertools
from base64 import b32decode
import simplejson

from twisted.trial import unittest
from twisted.internet import defer, address
from twisted.python import log
from twisted.python.filepath import FilePath

from foolscap.api import Tub, Referenceable, fireEventually, flushEventualQueue
from twisted.application import service
from allmydata.interfaces import InsufficientVersionError
from allmydata.introducer.client import IntroducerClient
from allmydata.introducer.server import IntroducerService, FurlFileConflictError
from allmydata.introducer.common import get_tubid_string_from_ann, \
     get_tubid_string, sign_to_foolscap, unsign_from_foolscap, \
     UnknownKeyError
# test compatibility with old introducer .tac files
from allmydata.introducer import IntroducerNode
from allmydata.web import introweb
from allmydata.client import Client as TahoeClient
from allmydata.util import pollmixin, keyutil, idlib, fileutil, iputil, yamlutil
import allmydata.test.common_util as testutil

class LoggingMultiService(service.MultiService):
    def log(self, msg, **kw):
        log.msg(msg, **kw)

class Node(testutil.SignalMixin, testutil.ReallyEqualMixin, unittest.TestCase):
    def test_furl(self):
        basedir = "introducer.IntroducerNode.test_furl"
        os.mkdir(basedir)
        public_fn = os.path.join(basedir, "introducer.furl")
        private_fn = os.path.join(basedir, "private", "introducer.furl")

        q1 = IntroducerNode(basedir)
        del q1
        # new nodes create unguessable furls in private/introducer.furl
        ifurl = fileutil.read(private_fn)
        self.failUnless(ifurl)
        ifurl = ifurl.strip()
        self.failIf(ifurl.endswith("/introducer"), ifurl)

        # old nodes created guessable furls in BASEDIR/introducer.furl
        guessable = ifurl[:ifurl.rfind("/")] + "/introducer"
        fileutil.write(public_fn, guessable+"\n", mode="w") # text

        # if we see both files, throw an error
        self.failUnlessRaises(FurlFileConflictError,
                              IntroducerNode, basedir)

        # when we see only the public one, move it to private/ and use
        # the existing furl instead of creating a new one
        os.unlink(private_fn)

        q2 = IntroducerNode(basedir)
        del q2
        self.failIf(os.path.exists(public_fn))
        ifurl2 = fileutil.read(private_fn)
        self.failUnless(ifurl2)
        self.failUnlessEqual(ifurl2.strip(), guessable)

    def test_web_static(self):
        basedir = u"introducer.Node.test_web_static"
        os.mkdir(basedir)
        fileutil.write(os.path.join(basedir, "tahoe.cfg"),
                       "[node]\n" +
                       "web.port = tcp:0:interface=127.0.0.1\n" +
                       "web.static = relative\n")
        c = IntroducerNode(basedir)
        w = c.getServiceNamed("webish")
        abs_basedir = fileutil.abspath_expanduser_unicode(basedir)
        expected = fileutil.abspath_expanduser_unicode(u"relative", abs_basedir)
        self.failUnlessReallyEqual(w.staticdir, expected)


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
                              "my_version", "oldest_version", {}, fakeseq,
                              FilePath(self.mktemp()))
        self.failUnless(isinstance(ic, IntroducerClient))

    def test_listen(self):
        i = IntroducerService()
        i.setServiceParent(self.parent)


def fakeseq():
    return 1, "nonce"

seqnum_counter = itertools.count(1)
def realseq():
    return seqnum_counter.next(), str(os.randint(1,100000))

def make_ann(furl):
    ann = { "anonymous-storage-FURL": furl,
            "permutation-seed-base32": get_tubid_string(furl) }
    return ann

def make_ann_t(ic, furl, privkey, seqnum):
    assert privkey
    ann_d = ic.create_announcement_dict("storage", make_ann(furl))
    ann_d["seqnum"] = seqnum
    ann_d["nonce"] = "nonce"
    ann_t = sign_to_foolscap(ann_d, privkey)
    return ann_t

class Client(unittest.TestCase):
    def test_duplicate_receive_v2(self):
        ic1 = IntroducerClient(None,
                               "introducer.furl", u"my_nickname",
                               "ver23", "oldest_version", {}, fakeseq,
                               FilePath(self.mktemp()))
        # we use a second client just to create a different-looking
        # announcement
        ic2 = IntroducerClient(None,
                               "introducer.furl", u"my_nickname",
                               "ver24","oldest_version",{}, fakeseq,
                               FilePath(self.mktemp()))
        announcements = []
        def _received(key_s, ann):
            announcements.append( (key_s, ann) )
        ic1.subscribe_to("storage", _received)
        furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:36106/gydnp"
        furl1a = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:7777/gydnp"
        furl2 = "pb://ttwwooyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:36106/ttwwoo"

        privkey_s, pubkey_vs = keyutil.make_keypair()
        privkey, _ignored = keyutil.parse_privkey(privkey_s)
        pubkey_s = keyutil.remove_prefix(pubkey_vs, "pub-")

        # ann1: ic1, furl1
        # ann1a: ic1, furl1a (same SturdyRef, different connection hints)
        # ann1b: ic2, furl1
        # ann2: ic2, furl2

        self.ann1 = make_ann_t(ic1, furl1, privkey, seqnum=10)
        self.ann1old = make_ann_t(ic1, furl1, privkey, seqnum=9)
        self.ann1noseqnum = make_ann_t(ic1, furl1, privkey, seqnum=None)
        self.ann1b = make_ann_t(ic2, furl1, privkey, seqnum=11)
        self.ann1a = make_ann_t(ic1, furl1a, privkey, seqnum=12)
        self.ann2 = make_ann_t(ic2, furl2, privkey, seqnum=13)

        ic1.remote_announce_v2([self.ann1]) # queues eventual-send
        d = fireEventually()
        def _then1(ign):
            self.failUnlessEqual(len(announcements), 1)
            key_s,ann = announcements[0]
            self.failUnlessEqual(key_s, pubkey_s)
            self.failUnlessEqual(ann["anonymous-storage-FURL"], furl1)
            self.failUnlessEqual(ann["my-version"], "ver23")
        d.addCallback(_then1)

        # now send a duplicate announcement. This should not fire the
        # subscriber
        d.addCallback(lambda ign: ic1.remote_announce_v2([self.ann1]))
        d.addCallback(fireEventually)
        def _then2(ign):
            self.failUnlessEqual(len(announcements), 1)
        d.addCallback(_then2)

        # an older announcement shouldn't fire the subscriber either
        d.addCallback(lambda ign: ic1.remote_announce_v2([self.ann1old]))
        d.addCallback(fireEventually)
        def _then2a(ign):
            self.failUnlessEqual(len(announcements), 1)
        d.addCallback(_then2a)

        # announcement with no seqnum cannot replace one with-seqnum
        d.addCallback(lambda ign: ic1.remote_announce_v2([self.ann1noseqnum]))
        d.addCallback(fireEventually)
        def _then2b(ign):
            self.failUnlessEqual(len(announcements), 1)
        d.addCallback(_then2b)

        # and a replacement announcement: same FURL, new other stuff. The
        # subscriber *should* be fired.
        d.addCallback(lambda ign: ic1.remote_announce_v2([self.ann1b]))
        d.addCallback(fireEventually)
        def _then3(ign):
            self.failUnlessEqual(len(announcements), 2)
            key_s,ann = announcements[-1]
            self.failUnlessEqual(key_s, pubkey_s)
            self.failUnlessEqual(ann["anonymous-storage-FURL"], furl1)
            self.failUnlessEqual(ann["my-version"], "ver24")
        d.addCallback(_then3)

        # and a replacement announcement with a different FURL (it uses
        # different connection hints)
        d.addCallback(lambda ign: ic1.remote_announce_v2([self.ann1a]))
        d.addCallback(fireEventually)
        def _then4(ign):
            self.failUnlessEqual(len(announcements), 3)
            key_s,ann = announcements[-1]
            self.failUnlessEqual(key_s, pubkey_s)
            self.failUnlessEqual(ann["anonymous-storage-FURL"], furl1a)
            self.failUnlessEqual(ann["my-version"], "ver23")
        d.addCallback(_then4)

        # now add a new subscription, which should be called with the
        # backlog. The introducer only records one announcement per index, so
        # the backlog will only have the latest message.
        announcements2 = []
        def _received2(key_s, ann):
            announcements2.append( (key_s, ann) )
        d.addCallback(lambda ign: ic1.subscribe_to("storage", _received2))
        d.addCallback(fireEventually)
        def _then5(ign):
            self.failUnlessEqual(len(announcements2), 1)
            key_s,ann = announcements2[-1]
            self.failUnlessEqual(key_s, pubkey_s)
            self.failUnlessEqual(ann["anonymous-storage-FURL"], furl1a)
            self.failUnlessEqual(ann["my-version"], "ver23")
        d.addCallback(_then5)
        return d

class Server(unittest.TestCase):
    def test_duplicate(self):
        i = IntroducerService()
        ic1 = IntroducerClient(None,
                               "introducer.furl", u"my_nickname",
                               "ver23", "oldest_version", {}, realseq,
                               FilePath(self.mktemp()))
        furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:36106/gydnp"

        privkey_s, _ = keyutil.make_keypair()
        privkey, _ = keyutil.parse_privkey(privkey_s)

        ann1 = make_ann_t(ic1, furl1, privkey, seqnum=10)
        ann1_old = make_ann_t(ic1, furl1, privkey, seqnum=9)
        ann1_new = make_ann_t(ic1, furl1, privkey, seqnum=11)
        ann1_noseqnum = make_ann_t(ic1, furl1, privkey, seqnum=None)
        ann1_badseqnum = make_ann_t(ic1, furl1, privkey, seqnum="not an int")

        i.remote_publish_v2(ann1, None)
        all = i.get_announcements()
        self.failUnlessEqual(len(all), 1)
        self.failUnlessEqual(all[0].announcement["seqnum"], 10)
        self.failUnlessEqual(i._debug_counts["inbound_message"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_duplicate"], 0)
        self.failUnlessEqual(i._debug_counts["inbound_no_seqnum"], 0)
        self.failUnlessEqual(i._debug_counts["inbound_old_replay"], 0)
        self.failUnlessEqual(i._debug_counts["inbound_update"], 0)

        i.remote_publish_v2(ann1, None)
        all = i.get_announcements()
        self.failUnlessEqual(len(all), 1)
        self.failUnlessEqual(all[0].announcement["seqnum"], 10)
        self.failUnlessEqual(i._debug_counts["inbound_message"], 2)
        self.failUnlessEqual(i._debug_counts["inbound_duplicate"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_no_seqnum"], 0)
        self.failUnlessEqual(i._debug_counts["inbound_old_replay"], 0)
        self.failUnlessEqual(i._debug_counts["inbound_update"], 0)

        i.remote_publish_v2(ann1_old, None)
        all = i.get_announcements()
        self.failUnlessEqual(len(all), 1)
        self.failUnlessEqual(all[0].announcement["seqnum"], 10)
        self.failUnlessEqual(i._debug_counts["inbound_message"], 3)
        self.failUnlessEqual(i._debug_counts["inbound_duplicate"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_no_seqnum"], 0)
        self.failUnlessEqual(i._debug_counts["inbound_old_replay"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_update"], 0)

        i.remote_publish_v2(ann1_new, None)
        all = i.get_announcements()
        self.failUnlessEqual(len(all), 1)
        self.failUnlessEqual(all[0].announcement["seqnum"], 11)
        self.failUnlessEqual(i._debug_counts["inbound_message"], 4)
        self.failUnlessEqual(i._debug_counts["inbound_duplicate"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_no_seqnum"], 0)
        self.failUnlessEqual(i._debug_counts["inbound_old_replay"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_update"], 1)

        i.remote_publish_v2(ann1_noseqnum, None)
        all = i.get_announcements()
        self.failUnlessEqual(len(all), 1)
        self.failUnlessEqual(all[0].announcement["seqnum"], 11)
        self.failUnlessEqual(i._debug_counts["inbound_message"], 5)
        self.failUnlessEqual(i._debug_counts["inbound_duplicate"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_no_seqnum"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_old_replay"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_update"], 1)

        i.remote_publish_v2(ann1_badseqnum, None)
        all = i.get_announcements()
        self.failUnlessEqual(len(all), 1)
        self.failUnlessEqual(all[0].announcement["seqnum"], 11)
        self.failUnlessEqual(i._debug_counts["inbound_message"], 6)
        self.failUnlessEqual(i._debug_counts["inbound_duplicate"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_no_seqnum"], 2)
        self.failUnlessEqual(i._debug_counts["inbound_old_replay"], 1)
        self.failUnlessEqual(i._debug_counts["inbound_update"], 1)


NICKNAME = u"n\u00EDickname-%s" # LATIN SMALL LETTER I WITH ACUTE

class SystemTestMixin(ServiceMixin, pollmixin.PollMixin):

    def create_tub(self, portnum=None):
        tubfile = os.path.join(self.basedir, "tub.pem")
        self.central_tub = tub = Tub(certFile=tubfile)
        #tub.setOption("logLocalFailures", True)
        #tub.setOption("logRemoteFailures", True)
        tub.setOption("expose-remote-exception-types", False)
        tub.setServiceParent(self.parent)
        if portnum is None:
            portnum = iputil.allocate_tcp_port()
        tub.listenOn("tcp:%d" % portnum)
        self.central_portnum = portnum
        tub.setLocation("localhost:%d" % self.central_portnum)

class Queue(SystemTestMixin, unittest.TestCase):
    def test_queue_until_connected(self):
        self.basedir = "introducer/QueueUntilConnected/queued"
        os.makedirs(self.basedir)
        self.create_tub()
        introducer = IntroducerService()
        introducer.setServiceParent(self.parent)
        iff = os.path.join(self.basedir, "introducer.furl")
        ifurl = self.central_tub.registerReference(introducer, furlFile=iff)
        tub2 = Tub()
        tub2.setServiceParent(self.parent)
        c = IntroducerClient(tub2, ifurl,
                             u"nickname", "version", "oldest", {}, fakeseq,
                             FilePath(self.mktemp()))
        furl1 = "pb://onug64tu@127.0.0.1:123/short" # base32("short")
        sk_s, vk_s = keyutil.make_keypair()
        sk, _ignored = keyutil.parse_privkey(sk_s)

        d = introducer.disownServiceParent()
        def _offline(ign):
            # now that the introducer server is offline, create a client and
            # publish some messages
            c.setServiceParent(self.parent) # this starts the reconnector
            c.publish("storage", make_ann(furl1), sk)

            introducer.setServiceParent(self.parent) # restart the server
            # now wait for the messages to be delivered
            def _got_announcement():
                return bool(introducer.get_announcements())
            return self.poll(_got_announcement)
        d.addCallback(_offline)
        def _done(ign):
            v = introducer.get_announcements()[0]
            furl = v.announcement["anonymous-storage-FURL"]
            self.failUnlessEqual(furl, furl1)
        d.addCallback(_done)

        # now let the ack get back
        def _wait_until_idle(ign):
            def _idle():
                if c._debug_outstanding:
                    return False
                if introducer._debug_outstanding:
                    return False
                return True
            return self.poll(_idle)
        d.addCallback(_wait_until_idle)
        return d


class SystemTest(SystemTestMixin, unittest.TestCase):

    def do_system_test(self):
        self.create_tub()
        introducer = IntroducerService()
        introducer.setServiceParent(self.parent)
        iff = os.path.join(self.basedir, "introducer.furl")
        tub = self.central_tub
        ifurl = self.central_tub.registerReference(introducer, furlFile=iff)
        self.introducer_furl = ifurl

        # we have 5 clients who publish themselves as storage servers, and a
        # sixth which does which not. All 6 clients subscriber to hear about
        # storage. When the connections are fully established, all six nodes
        # should have 5 connections each.
        NUM_STORAGE = 5
        NUM_CLIENTS = 6

        clients = []
        tubs = {}
        received_announcements = {}
        subscribing_clients = []
        publishing_clients = []
        printable_serverids = {}
        self.the_introducer = introducer
        privkeys = {}
        pubkeys = {}
        expected_announcements = [0 for c in range(NUM_CLIENTS)]

        for i in range(NUM_CLIENTS):
            tub = Tub()
            #tub.setOption("logLocalFailures", True)
            #tub.setOption("logRemoteFailures", True)
            tub.setOption("expose-remote-exception-types", False)
            tub.setServiceParent(self.parent)
            portnum = iputil.allocate_tcp_port()
            tub.listenOn("tcp:%d" % portnum)
            tub.setLocation("localhost:%d" % portnum)

            log.msg("creating client %d: %s" % (i, tub.getShortTubID()))
            c = IntroducerClient(tub, self.introducer_furl,
                                 NICKNAME % str(i),
                                 "version", "oldest",
                                 {"component": "component-v1"}, fakeseq,
                                 FilePath(self.mktemp()))
            received_announcements[c] = {}
            def got(key_s_or_tubid, ann, announcements):
                index = key_s_or_tubid or get_tubid_string_from_ann(ann)
                announcements[index] = ann
            c.subscribe_to("storage", got, received_announcements[c])
            subscribing_clients.append(c)
            expected_announcements[i] += 1 # all expect a 'storage' announcement

            node_furl = tub.registerReference(Referenceable())
            privkey_s, pubkey_s = keyutil.make_keypair()
            privkey, _ignored = keyutil.parse_privkey(privkey_s)
            privkeys[i] = privkey
            pubkeys[i] = pubkey_s

            if i < NUM_STORAGE:
                # sign all announcements
                c.publish("storage", make_ann(node_furl), privkey)
                assert pubkey_s.startswith("pub-")
                printable_serverids[i] = pubkey_s[len("pub-"):]
                publishing_clients.append(c)
            else:
                # the last one does not publish anything
                pass

            if i == 2:
                # also publish something that nobody cares about
                boring_furl = tub.registerReference(Referenceable())
                c.publish("boring", make_ann(boring_furl), privkey)

            c.setServiceParent(self.parent)
            clients.append(c)
            tubs[c] = tub


        def _wait_for_connected(ign):
            def _connected():
                for c in clients:
                    if not c.connected_to_introducer():
                        return False
                return True
            return self.poll(_connected)

        # we watch the clients to determine when the system has settled down.
        # Then we can look inside the server to assert things about its
        # state.

        def _wait_for_expected_announcements(ign):
            def _got_expected_announcements():
                for i,c in enumerate(subscribing_clients):
                    if len(received_announcements[c]) < expected_announcements[i]:
                        return False
                return True
            return self.poll(_got_expected_announcements)

        # before shutting down any Tub, we'd like to know that there are no
        # messages outstanding

        def _wait_until_idle(ign):
            def _idle():
                for c in subscribing_clients + publishing_clients:
                    if c._debug_outstanding:
                        return False
                if self.the_introducer._debug_outstanding:
                    return False
                return True
            return self.poll(_idle)

        d = defer.succeed(None)
        d.addCallback(_wait_for_connected)
        d.addCallback(_wait_for_expected_announcements)
        d.addCallback(_wait_until_idle)

        def _check1(res):
            log.msg("doing _check1")
            dc = self.the_introducer._debug_counts
            # each storage server publishes a record. There is also one
            # "boring"
            self.failUnlessEqual(dc["inbound_message"], NUM_STORAGE+1)
            self.failUnlessEqual(dc["inbound_duplicate"], 0)
            self.failUnlessEqual(dc["inbound_update"], 0)
            self.failUnlessEqual(dc["inbound_subscribe"], NUM_CLIENTS)
            # the number of outbound messages is tricky.. I think it depends
            # upon a race between the publish and the subscribe messages.
            self.failUnless(dc["outbound_message"] > 0)
            # each client subscribes to "storage", and each server publishes
            self.failUnlessEqual(dc["outbound_announcements"],
                                 NUM_STORAGE*NUM_CLIENTS)

            for c in subscribing_clients:
                cdc = c._debug_counts
                self.failUnless(cdc["inbound_message"])
                self.failUnlessEqual(cdc["inbound_announcement"],
                                     NUM_STORAGE)
                self.failUnlessEqual(cdc["wrong_service"], 0)
                self.failUnlessEqual(cdc["duplicate_announcement"], 0)
                self.failUnlessEqual(cdc["update"], 0)
                self.failUnlessEqual(cdc["new_announcement"],
                                     NUM_STORAGE)
                anns = received_announcements[c]
                self.failUnlessEqual(len(anns), NUM_STORAGE)

                serverid0 = printable_serverids[0]
                ann = anns[serverid0]
                nick = ann["nickname"]
                self.failUnlessEqual(type(nick), unicode)
                self.failUnlessEqual(nick, NICKNAME % "0")
            for c in publishing_clients:
                cdc = c._debug_counts
                expected = 1
                if c in [clients[2], # boring
                         ]:
                    expected = 2
                self.failUnlessEqual(cdc["outbound_message"], expected)
            # now check the web status, make sure it renders without error
            ir = introweb.IntroducerRoot(self.parent)
            self.parent.nodeid = "NODEID"
            text = ir.renderSynchronously().decode("utf-8")
            self.failUnlessIn(NICKNAME % "0", text) # a v2 client
            self.failUnlessIn(NICKNAME % "1", text) # another v2 client
            for i in range(NUM_STORAGE):
                self.failUnlessIn(printable_serverids[i], text,
                                  (i,printable_serverids[i],text))
                # make sure there isn't a double-base32ed string too
                self.failIfIn(idlib.nodeid_b2a(printable_serverids[i]), text,
                              (i,printable_serverids[i],text))
            log.msg("_check1 done")
        d.addCallback(_check1)

        # force an introducer reconnect, by shutting down the Tub it's using
        # and starting a new Tub (with the old introducer). Everybody should
        # reconnect and republish, but the introducer should ignore the
        # republishes as duplicates. However, because the server doesn't know
        # what each client does and does not know, it will send them a copy
        # of the current announcement table anyway.

        d.addCallback(lambda _ign: log.msg("shutting down introducer's Tub"))
        d.addCallback(lambda _ign: self.central_tub.disownServiceParent())

        def _wait_for_introducer_loss(ign):
            def _introducer_lost():
                for c in clients:
                    if c.connected_to_introducer():
                        return False
                return True
            return self.poll(_introducer_lost)
        d.addCallback(_wait_for_introducer_loss)

        def _restart_introducer_tub(_ign):
            log.msg("restarting introducer's Tub")
            # reset counters
            for i in range(NUM_CLIENTS):
                c = subscribing_clients[i]
                for k in c._debug_counts:
                    c._debug_counts[k] = 0
            for k in self.the_introducer._debug_counts:
                self.the_introducer._debug_counts[k] = 0
            expected_announcements[i] += 1 # new 'storage' for everyone
            self.create_tub(self.central_portnum)
            newfurl = self.central_tub.registerReference(self.the_introducer,
                                                         furlFile=iff)
            assert newfurl == self.introducer_furl
        d.addCallback(_restart_introducer_tub)

        d.addCallback(_wait_for_connected)
        d.addCallback(_wait_for_expected_announcements)
        d.addCallback(_wait_until_idle)
        d.addCallback(lambda _ign: log.msg(" reconnected"))

        # TODO: publish something while the introducer is offline, then
        # confirm it gets delivered when the connection is reestablished
        def _check2(res):
            log.msg("doing _check2")
            # assert that the introducer sent out new messages, one per
            # subscriber
            dc = self.the_introducer._debug_counts
            self.failUnlessEqual(dc["outbound_announcements"],
                                 NUM_STORAGE*NUM_CLIENTS)
            self.failUnless(dc["outbound_message"] > 0)
            self.failUnlessEqual(dc["inbound_subscribe"], NUM_CLIENTS)
            for c in subscribing_clients:
                cdc = c._debug_counts
                self.failUnlessEqual(cdc["inbound_message"], 1)
                self.failUnlessEqual(cdc["inbound_announcement"], NUM_STORAGE)
                self.failUnlessEqual(cdc["new_announcement"], 0)
                self.failUnlessEqual(cdc["wrong_service"], 0)
                self.failUnlessEqual(cdc["duplicate_announcement"], NUM_STORAGE)
        d.addCallback(_check2)

        # Then force an introducer restart, by shutting down the Tub,
        # destroying the old introducer, and starting a new Tub+Introducer.
        # Everybody should reconnect and republish, and the (new) introducer
        # will distribute the new announcements, but the clients should
        # ignore the republishes as duplicates.

        d.addCallback(lambda _ign: log.msg("shutting down introducer"))
        d.addCallback(lambda _ign: self.central_tub.disownServiceParent())
        d.addCallback(_wait_for_introducer_loss)
        d.addCallback(lambda _ign: log.msg("introducer lost"))

        def _restart_introducer(_ign):
            log.msg("restarting introducer")
            self.create_tub(self.central_portnum)
            # reset counters
            for i in range(NUM_CLIENTS):
                c = subscribing_clients[i]
                for k in c._debug_counts:
                    c._debug_counts[k] = 0
            expected_announcements[i] += 1 # new 'storage' for everyone
            introducer = IntroducerService()
            self.the_introducer = introducer
            newfurl = self.central_tub.registerReference(self.the_introducer,
                                                         furlFile=iff)
            assert newfurl == self.introducer_furl
        d.addCallback(_restart_introducer)

        d.addCallback(_wait_for_connected)
        d.addCallback(_wait_for_expected_announcements)
        d.addCallback(_wait_until_idle)

        def _check3(res):
            log.msg("doing _check3")
            dc = self.the_introducer._debug_counts
            self.failUnlessEqual(dc["outbound_announcements"],
                                 NUM_STORAGE*NUM_CLIENTS)
            self.failUnless(dc["outbound_message"] > 0)
            self.failUnlessEqual(dc["inbound_subscribe"], NUM_CLIENTS)
            for c in subscribing_clients:
                cdc = c._debug_counts
                self.failUnless(cdc["inbound_message"] > 0)
                self.failUnlessEqual(cdc["inbound_announcement"], NUM_STORAGE)
                self.failUnlessEqual(cdc["new_announcement"], 0)
                self.failUnlessEqual(cdc["wrong_service"], 0)
                self.failUnlessEqual(cdc["duplicate_announcement"], NUM_STORAGE)

        d.addCallback(_check3)
        return d


    def test_system_v2_server(self):
        self.basedir = "introducer/SystemTest/system_v2_server"
        os.makedirs(self.basedir)
        return self.do_system_test()
    test_system_v2_server.timeout = 480
    # occasionally takes longer than 350s on "draco"

class FakeRemoteReference:
    def notifyOnDisconnect(self, *args, **kwargs): pass
    def getRemoteTubID(self): return "62ubehyunnyhzs7r6vdonnm2hpi52w6y"
    def getLocationHints(self): return ["tcp:here.example.com:1234",
                                        "tcp:there.example.com2345"]
    def getPeer(self): return address.IPv4Address("TCP", "remote.example.com",
                                                  3456)

class ClientInfo(unittest.TestCase):
    def test_client_v2(self):
        introducer = IntroducerService()
        tub = introducer_furl = None
        app_versions = {"whizzy": "fizzy"}
        client_v2 = IntroducerClient(tub, introducer_furl, NICKNAME % u"v2",
                                     "my_version", "oldest", app_versions,
                                     fakeseq, FilePath(self.mktemp()))
        #furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:0/swissnum"
        #ann_s = make_ann_t(client_v2, furl1, None, 10)
        #introducer.remote_publish_v2(ann_s, Referenceable())
        subscriber = FakeRemoteReference()
        introducer.remote_subscribe_v2(subscriber, "storage",
                                       client_v2._my_subscriber_info)
        subs = introducer.get_subscribers()
        self.failUnlessEqual(len(subs), 1)
        s0 = subs[0]
        self.failUnlessEqual(s0.service_name, "storage")
        self.failUnlessEqual(s0.app_versions, app_versions)
        self.failUnlessEqual(s0.nickname, NICKNAME % u"v2")
        self.failUnlessEqual(s0.version, "my_version")

class Announcements(unittest.TestCase):
    def test_client_v2_signed(self):
        introducer = IntroducerService()
        tub = introducer_furl = None
        app_versions = {"whizzy": "fizzy"}
        client_v2 = IntroducerClient(tub, introducer_furl, u"nick-v2",
                                     "my_version", "oldest", app_versions,
                                     fakeseq, FilePath(self.mktemp()))
        furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:0/swissnum"
        sk_s, vk_s = keyutil.make_keypair()
        sk, _ignored = keyutil.parse_privkey(sk_s)
        pks = keyutil.remove_prefix(vk_s, "pub-")
        ann_t0 = make_ann_t(client_v2, furl1, sk, 10)
        canary0 = Referenceable()
        introducer.remote_publish_v2(ann_t0, canary0)
        a = introducer.get_announcements()
        self.failUnlessEqual(len(a), 1)
        self.failUnlessIdentical(a[0].canary, canary0)
        self.failUnlessEqual(a[0].index, ("storage", pks))
        self.failUnlessEqual(a[0].announcement["app-versions"], app_versions)
        self.failUnlessEqual(a[0].nickname, u"nick-v2")
        self.failUnlessEqual(a[0].service_name, "storage")
        self.failUnlessEqual(a[0].version, "my_version")
        self.failUnlessEqual(a[0].announcement["anonymous-storage-FURL"], furl1)

    def _load_cache(self, cache_filepath):
        with cache_filepath.open() as f:
            return yamlutil.safe_load(f)

    @defer.inlineCallbacks
    def test_client_cache(self):
        basedir = "introducer/ClientSeqnums/test_client_cache_1"
        fileutil.make_dirs(basedir)
        cache_filepath = FilePath(os.path.join(basedir, "private",
                                               "introducer_default_cache.yaml"))

        # if storage is enabled, the Client will publish its storage server
        # during startup (although the announcement will wait in a queue
        # until the introducer connection is established). To avoid getting
        # confused by this, disable storage.
        f = open(os.path.join(basedir, "tahoe.cfg"), "w")
        f.write("[client]\n")
        f.write("introducer.furl = nope\n")
        f.write("[storage]\n")
        f.write("enabled = false\n")
        f.close()

        c = TahoeClient(basedir)
        ic = c.introducer_clients[0]
        sk_s, vk_s = keyutil.make_keypair()
        sk, _ignored = keyutil.parse_privkey(sk_s)
        pub1 = keyutil.remove_prefix(vk_s, "pub-")
        furl1 = "pb://onug64tu@127.0.0.1:123/short" # base32("short")
        ann_t = make_ann_t(ic, furl1, sk, 1)

        ic.got_announcements([ann_t])
        yield flushEventualQueue()

        # check the cache for the announcement
        announcements = self._load_cache(cache_filepath)
        self.failUnlessEqual(len(announcements), 1)
        self.failUnlessEqual(announcements[0]['key_s'], pub1)
        ann = announcements[0]["ann"]
        self.failUnlessEqual(ann["anonymous-storage-FURL"], furl1)
        self.failUnlessEqual(ann["seqnum"], 1)

        # a new announcement that replaces the first should replace the
        # cached entry, not duplicate it
        furl2 = furl1 + "er"
        ann_t2 = make_ann_t(ic, furl2, sk, 2)
        ic.got_announcements([ann_t2])
        yield flushEventualQueue()
        announcements = self._load_cache(cache_filepath)
        self.failUnlessEqual(len(announcements), 1)
        self.failUnlessEqual(announcements[0]['key_s'], pub1)
        ann = announcements[0]["ann"]
        self.failUnlessEqual(ann["anonymous-storage-FURL"], furl2)
        self.failUnlessEqual(ann["seqnum"], 2)

        # but a third announcement with a different key should add to the
        # cache
        sk_s2, vk_s2 = keyutil.make_keypair()
        sk2, _ignored = keyutil.parse_privkey(sk_s2)
        pub2 = keyutil.remove_prefix(vk_s2, "pub-")
        furl3 = "pb://onug64tu@127.0.0.1:456/short"
        ann_t3 = make_ann_t(ic, furl3, sk2, 1)
        ic.got_announcements([ann_t3])
        yield flushEventualQueue()

        announcements = self._load_cache(cache_filepath)
        self.failUnlessEqual(len(announcements), 2)
        self.failUnlessEqual(set([pub1, pub2]),
                             set([a["key_s"] for a in announcements]))
        self.failUnlessEqual(set([furl2, furl3]),
                             set([a["ann"]["anonymous-storage-FURL"]
                                  for a in announcements]))

        # test loading
        yield flushEventualQueue()
        ic2 = IntroducerClient(None, "introducer.furl", u"my_nickname",
                               "my_version", "oldest_version", {}, fakeseq,
                               ic._cache_filepath)
        announcements = {}
        def got(key_s, ann):
            announcements[key_s] = ann
        ic2.subscribe_to("storage", got)
        ic2._load_announcements() # normally happens when connection fails
        yield flushEventualQueue()

        self.failUnless(pub1 in announcements)
        self.failUnlessEqual(announcements[pub1]["anonymous-storage-FURL"],
                             furl2)
        self.failUnlessEqual(announcements[pub2]["anonymous-storage-FURL"],
                             furl3)

        c2 = TahoeClient(basedir)
        c2.introducer_clients[0]._load_announcements()
        yield flushEventualQueue()
        self.assertEqual(c2.storage_broker.get_all_serverids(),
                         frozenset([pub1, pub2]))

class ClientSeqnums(unittest.TestCase):
    def test_client(self):
        basedir = "introducer/ClientSeqnums/test_client"
        fileutil.make_dirs(basedir)
        # if storage is enabled, the Client will publish its storage server
        # during startup (although the announcement will wait in a queue
        # until the introducer connection is established). To avoid getting
        # confused by this, disable storage.
        f = open(os.path.join(basedir, "tahoe.cfg"), "w")
        f.write("[client]\n")
        f.write("introducer.furl = nope\n")
        f.write("[storage]\n")
        f.write("enabled = false\n")
        f.close()

        c = TahoeClient(basedir)
        ic = c.introducer_clients[0]
        outbound = ic._outbound_announcements
        published = ic._published_announcements
        def read_seqnum():
            f = open(os.path.join(basedir, "announcement-seqnum"))
            seqnum = f.read().strip()
            f.close()
            return int(seqnum)

        ic.publish("sA", {"key": "value1"}, c._node_key)
        self.failUnlessEqual(read_seqnum(), 1)
        self.failUnless("sA" in outbound)
        self.failUnlessEqual(outbound["sA"]["seqnum"], 1)
        nonce1 = outbound["sA"]["nonce"]
        self.failUnless(isinstance(nonce1, str))
        self.failUnlessEqual(simplejson.loads(published["sA"][0]),
                             outbound["sA"])
        # [1] is the signature, [2] is the pubkey

        # publishing a second service causes both services to be
        # re-published, with the next higher sequence number
        ic.publish("sB", {"key": "value2"}, c._node_key)
        self.failUnlessEqual(read_seqnum(), 2)
        self.failUnless("sB" in outbound)
        self.failUnlessEqual(outbound["sB"]["seqnum"], 2)
        self.failUnless("sA" in outbound)
        self.failUnlessEqual(outbound["sA"]["seqnum"], 2)
        nonce2 = outbound["sA"]["nonce"]
        self.failUnless(isinstance(nonce2, str))
        self.failIfEqual(nonce1, nonce2)
        self.failUnlessEqual(simplejson.loads(published["sA"][0]),
                             outbound["sA"])
        self.failUnlessEqual(simplejson.loads(published["sB"][0]),
                             outbound["sB"])



class TooNewServer(IntroducerService):
    VERSION = { "http://allmydata.org/tahoe/protocols/introducer/v999":
                 { },
                "application-version": "greetings from the crazy future",
                }

class NonV1Server(SystemTestMixin, unittest.TestCase):
    # if the client connects to a server that doesn't provide the 'v2'
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
        portnum = iputil.allocate_tcp_port()
        tub.listenOn("tcp:%d" % portnum)
        tub.setLocation("localhost:%d" % portnum)

        c = IntroducerClient(tub, self.introducer_furl,
                             u"nickname-client", "version", "oldest", {},
                             fakeseq, FilePath(self.mktemp()))
        announcements = {}
        def got(key_s, ann):
            announcements[key_s] = ann
        c.subscribe_to("storage", got)

        c.setServiceParent(self.parent)

        # now we wait for it to connect and notice the bad version

        def _got_bad():
            return bool(c._introducer_error) or bool(c._publisher)
        d = self.poll(_got_bad)
        def _done(res):
            self.failUnless(c._introducer_error)
            self.failUnless(c._introducer_error.check(InsufficientVersionError),
                            c._introducer_error)
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

class Signatures(unittest.TestCase):
    def test_sign(self):
        ann = {"key1": "value1"}
        sk_s,vk_s = keyutil.make_keypair()
        sk,ignored = keyutil.parse_privkey(sk_s)
        ann_t = sign_to_foolscap(ann, sk)
        (msg, sig, key) = ann_t
        self.failUnlessEqual(type(msg), type("".encode("utf-8"))) # bytes
        self.failUnlessEqual(simplejson.loads(msg.decode("utf-8")), ann)
        self.failUnless(sig.startswith("v0-"))
        self.failUnless(key.startswith("v0-"))
        (ann2,key2) = unsign_from_foolscap(ann_t)
        self.failUnlessEqual(ann2, ann)
        self.failUnlessEqual("pub-"+key2, vk_s)

        # not signed
        self.failUnlessRaises(UnknownKeyError,
                              unsign_from_foolscap, (msg, None, key))
        self.failUnlessRaises(UnknownKeyError,
                              unsign_from_foolscap, (msg, sig, None))
        # bad signature
        bad_ann = {"key1": "value2"}
        bad_msg = simplejson.dumps(bad_ann).encode("utf-8")
        self.failUnlessRaises(keyutil.BadSignatureError,
                              unsign_from_foolscap, (bad_msg,sig,key))

        # unrecognized signatures
        self.failUnlessRaises(UnknownKeyError,
                              unsign_from_foolscap, (bad_msg,"v999-sig",key))
        self.failUnlessRaises(UnknownKeyError,
                              unsign_from_foolscap, (bad_msg,sig,"v999-key"))


# add tests of StorageFarmBroker: if it receives duplicate announcements, it
# should leave the Reconnector in place, also if it receives
# same-FURL-different-misc, but if it receives same-nodeid-different-FURL, it
# should tear down the Reconnector and make a new one. This behavior used to
# live in the IntroducerClient, and thus used to be tested by test_introducer

# copying more tests from old branch:

#  then also add Upgrade test
