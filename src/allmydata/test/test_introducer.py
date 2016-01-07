
import os, re, itertools
from base64 import b32decode
import simplejson

from twisted.trial import unittest
from twisted.internet import defer, address
from twisted.python import log

from foolscap.api import Tub, Referenceable, fireEventually, flushEventualQueue
from twisted.application import service
from allmydata.interfaces import InsufficientVersionError
from allmydata.introducer.client import IntroducerClient, \
     WrapV2ClientInV1Interface
from allmydata.introducer.server import IntroducerService, FurlFileConflictError
from allmydata.introducer.common import get_tubid_string_from_ann, \
     get_tubid_string, sign_to_foolscap, unsign_from_foolscap, \
     UnknownKeyError
from allmydata.introducer import old
# test compatibility with old introducer .tac files
from allmydata.introducer import IntroducerNode
from allmydata.web import introweb
from allmydata.client import Client as TahoeClient
from allmydata.util import pollmixin, keyutil, idlib, fileutil
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
        d = fireEventually(None)
        d.addCallback(lambda res: q1.startService())
        d.addCallback(lambda res: q1.when_tub_ready())
        d.addCallback(lambda res: q1.stopService())
        d.addCallback(flushEventualQueue)
        def _check_furl(res):
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
            d2 = fireEventually(None)
            d2.addCallback(lambda res: q2.startService())
            d2.addCallback(lambda res: q2.when_tub_ready())
            d2.addCallback(lambda res: q2.stopService())
            d2.addCallback(flushEventualQueue)
            def _check_furl2(res):
                self.failIf(os.path.exists(public_fn))
                ifurl2 = fileutil.read(private_fn)
                self.failUnless(ifurl2)
                self.failUnlessEqual(ifurl2.strip(), guessable)
            d2.addCallback(_check_furl2)
            return d2
        d.addCallback(_check_furl)
        return d

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
                              "my_version", "oldest_version", {})
        self.failUnless(isinstance(ic, IntroducerClient))

    def test_listen(self):
        i = IntroducerService()
        i.setServiceParent(self.parent)

    def test_duplicate_publish(self):
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

    def test_id_collision(self):
        # test replacement case where tubid equals a keyid (one should
        # not replace the other)
        i = IntroducerService()
        ic = IntroducerClient(None,
                              "introducer.furl", u"my_nickname",
                              "my_version", "oldest_version", {})
        sk_s, vk_s = keyutil.make_keypair()
        sk, _ignored = keyutil.parse_privkey(sk_s)
        keyid = keyutil.remove_prefix(vk_s, "pub-v0-")
        furl1 = "pb://onug64tu@127.0.0.1:123/short" # base32("short")
        ann_t = make_ann_t(ic, furl1, sk, 1)
        i.remote_publish_v2(ann_t, Referenceable())
        announcements = i.get_announcements()
        self.failUnlessEqual(len(announcements), 1)
        key1 = ("storage", "v0-"+keyid, None)
        self.failUnlessEqual(announcements[0].index, key1)
        ann1_out = announcements[0].announcement
        self.failUnlessEqual(ann1_out["anonymous-storage-FURL"], furl1)

        furl2 = "pb://%s@127.0.0.1:36106/swissnum" % keyid
        ann2 = (furl2, "storage", "RIStorage", "nick1", "ver23", "ver0")
        i.remote_publish(ann2)
        announcements = i.get_announcements()
        self.failUnlessEqual(len(announcements), 2)
        key2 = ("storage", None, keyid)
        wanted = [ad for ad in announcements if ad.index == key2]
        self.failUnlessEqual(len(wanted), 1)
        ann2_out = wanted[0].announcement
        self.failUnlessEqual(ann2_out["anonymous-storage-FURL"], furl2)


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
    ann_d = ic.create_announcement_dict("storage", make_ann(furl))
    ann_d["seqnum"] = seqnum
    ann_d["nonce"] = "nonce"
    ann_t = sign_to_foolscap(ann_d, privkey)
    return ann_t

class Client(unittest.TestCase):
    def test_duplicate_receive_v1(self):
        ic = IntroducerClient(None,
                              "introducer.furl", u"my_nickname",
                              "my_version", "oldest_version", {})
        announcements = []
        ic.subscribe_to("storage",
                        lambda key_s,ann: announcements.append(ann))
        furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:36106/gydnpigj2ja2qr2srq4ikjwnl7xfgbra"
        ann1 = (furl1, "storage", "RIStorage", "nick1", "ver23", "ver0")
        ann1b = (furl1, "storage", "RIStorage", "nick1", "ver24", "ver0")
        ca = WrapV2ClientInV1Interface(ic)

        ca.remote_announce([ann1])
        d = fireEventually()
        def _then(ign):
            self.failUnlessEqual(len(announcements), 1)
            self.failUnlessEqual(announcements[0]["nickname"], u"nick1")
            self.failUnlessEqual(announcements[0]["my-version"], "ver23")
            self.failUnlessEqual(ic._debug_counts["inbound_announcement"], 1)
            self.failUnlessEqual(ic._debug_counts["new_announcement"], 1)
            self.failUnlessEqual(ic._debug_counts["update"], 0)
            self.failUnlessEqual(ic._debug_counts["duplicate_announcement"], 0)
            # now send a duplicate announcement: this should not notify clients
            ca.remote_announce([ann1])
            return fireEventually()
        d.addCallback(_then)
        def _then2(ign):
            self.failUnlessEqual(len(announcements), 1)
            self.failUnlessEqual(ic._debug_counts["inbound_announcement"], 2)
            self.failUnlessEqual(ic._debug_counts["new_announcement"], 1)
            self.failUnlessEqual(ic._debug_counts["update"], 0)
            self.failUnlessEqual(ic._debug_counts["duplicate_announcement"], 1)
            # and a replacement announcement: same FURL, new other stuff.
            # Clients should be notified.
            ca.remote_announce([ann1b])
            return fireEventually()
        d.addCallback(_then2)
        def _then3(ign):
            self.failUnlessEqual(len(announcements), 2)
            self.failUnlessEqual(ic._debug_counts["inbound_announcement"], 3)
            self.failUnlessEqual(ic._debug_counts["new_announcement"], 1)
            self.failUnlessEqual(ic._debug_counts["update"], 1)
            self.failUnlessEqual(ic._debug_counts["duplicate_announcement"], 1)
            # test that the other stuff changed
            self.failUnlessEqual(announcements[-1]["nickname"], u"nick1")
            self.failUnlessEqual(announcements[-1]["my-version"], "ver24")
        d.addCallback(_then3)
        return d

    def test_duplicate_receive_v2(self):
        ic1 = IntroducerClient(None,
                               "introducer.furl", u"my_nickname",
                               "ver23", "oldest_version", {})
        # we use a second client just to create a different-looking
        # announcement
        ic2 = IntroducerClient(None,
                               "introducer.furl", u"my_nickname",
                               "ver24","oldest_version",{})
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

    def test_id_collision(self):
        # test replacement case where tubid equals a keyid (one should
        # not replace the other)
        ic = IntroducerClient(None,
                              "introducer.furl", u"my_nickname",
                              "my_version", "oldest_version", {})
        announcements = []
        ic.subscribe_to("storage",
                        lambda key_s,ann: announcements.append(ann))
        sk_s, vk_s = keyutil.make_keypair()
        sk, _ignored = keyutil.parse_privkey(sk_s)
        keyid = keyutil.remove_prefix(vk_s, "pub-v0-")
        furl1 = "pb://onug64tu@127.0.0.1:123/short" # base32("short")
        furl2 = "pb://%s@127.0.0.1:36106/swissnum" % keyid
        ann_t = make_ann_t(ic, furl1, sk, 1)
        ic.remote_announce_v2([ann_t])
        d = fireEventually()
        def _then(ign):
            # first announcement has been processed
            self.failUnlessEqual(len(announcements), 1)
            self.failUnlessEqual(announcements[0]["anonymous-storage-FURL"],
                                 furl1)
            # now submit a second one, with a tubid that happens to look just
            # like the pubkey-based serverid we just processed. They should
            # not overlap.
            ann2 = (furl2, "storage", "RIStorage", "nick1", "ver23", "ver0")
            ca = WrapV2ClientInV1Interface(ic)
            ca.remote_announce([ann2])
            return fireEventually()
        d.addCallback(_then)
        def _then2(ign):
            # if they overlapped, the second announcement would be ignored
            self.failUnlessEqual(len(announcements), 2)
            self.failUnlessEqual(announcements[1]["anonymous-storage-FURL"],
                                 furl2)
        d.addCallback(_then2)
        return d

class Server(unittest.TestCase):
    def test_duplicate(self):
        i = IntroducerService()
        ic1 = IntroducerClient(None,
                               "introducer.furl", u"my_nickname",
                               "ver23", "oldest_version", {})
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
                             u"nickname", "version", "oldest", {})
        furl1 = "pb://onug64tu@127.0.0.1:123/short" # base32("short")
        sk_s, vk_s = keyutil.make_keypair()
        sk, _ignored = keyutil.parse_privkey(sk_s)

        d = introducer.disownServiceParent()
        def _offline(ign):
            # now that the introducer server is offline, create a client and
            # publish some messages
            c.setServiceParent(self.parent) # this starts the reconnector
            c.publish("storage", make_ann(furl1), 0, "", sk)

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


V1 = "v1"; V2 = "v2"
class SystemTest(SystemTestMixin, unittest.TestCase):

    def do_system_test(self, server_version):
        self.create_tub()
        if server_version == V1:
            introducer = old.IntroducerService_v1()
        else:
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
        expected_announcements = [0 for c in range(NUM_CLIENTS)]

        for i in range(NUM_CLIENTS):
            tub = Tub()
            #tub.setOption("logLocalFailures", True)
            #tub.setOption("logRemoteFailures", True)
            tub.setOption("expose-remote-exception-types", False)
            tub.setServiceParent(self.parent)
            l = tub.listenOn("tcp:0")
            portnum = l.getPortnum()
            tub.setLocation("localhost:%d" % portnum)

            log.msg("creating client %d: %s" % (i, tub.getShortTubID()))
            if i == 0:
                c = old.IntroducerClient_v1(tub, self.introducer_furl,
                                            NICKNAME % str(i),
                                            "version", "oldest")
            else:
                c = IntroducerClient(tub, self.introducer_furl,
                                     NICKNAME % str(i),
                                     "version", "oldest",
                                     {"component": "component-v1"})
            received_announcements[c] = {}
            def got(key_s_or_tubid, ann, announcements, i):
                if i == 0:
                    index = get_tubid_string_from_ann(ann)
                else:
                    index = key_s_or_tubid or get_tubid_string_from_ann(ann)
                announcements[index] = ann
            c.subscribe_to("storage", got, received_announcements[c], i)
            subscribing_clients.append(c)
            expected_announcements[i] += 1 # all expect a 'storage' announcement

            node_furl = tub.registerReference(Referenceable())
            if i < NUM_STORAGE:
                if i == 0:
                    c.publish(node_furl, "storage", "ri_name")
                    printable_serverids[i] = get_tubid_string(node_furl)
                elif i == 1:
                    # sign the announcement
                    privkey_s, pubkey_s = keyutil.make_keypair()
                    privkey, _ignored = keyutil.parse_privkey(privkey_s)
                    privkeys[c] = privkey
                    c.publish("storage", make_ann(node_furl), 0, "", privkey)
                    if server_version == V1:
                        printable_serverids[i] = get_tubid_string(node_furl)
                    else:
                        assert pubkey_s.startswith("pub-")
                        printable_serverids[i] = pubkey_s[len("pub-"):]
                else:
                    c.publish("storage", make_ann(node_furl), 0, "")
                    printable_serverids[i] = get_tubid_string(node_furl)
                publishing_clients.append(c)
            else:
                # the last one does not publish anything
                pass

            if i == 0:
                # users of the V1 client were required to publish a
                # 'stub_client' record (somewhat after they published the
                # 'storage' record), so the introducer could see their
                # version. Match that behavior.
                c.publish(node_furl, "stub_client", "stub_ri_name")

            if i == 2:
                # also publish something that nobody cares about
                boring_furl = tub.registerReference(Referenceable())
                c.publish("boring", make_ann(boring_furl), 0, "")

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
            if server_version == V1:
                # each storage server publishes a record, and (after its
                # 'subscribe' has been ACKed) also publishes a "stub_client".
                # The non-storage client (which subscribes) also publishes a
                # stub_client. There is also one "boring" service. The number
                # of messages is higher, because the stub_clients aren't
                # published until after we get the 'subscribe' ack (since we
                # don't realize that we're dealing with a v1 server [which
                # needs stub_clients] until then), and the act of publishing
                # the stub_client causes us to re-send all previous
                # announcements.
                self.failUnlessEqual(dc["inbound_message"] - dc["inbound_duplicate"],
                                     NUM_STORAGE + NUM_CLIENTS + 1)
            else:
                # each storage server publishes a record. There is also one
                # "stub_client" and one "boring"
                self.failUnlessEqual(dc["inbound_message"], NUM_STORAGE+2)
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

                nodeid0 = tubs[clients[0]].tubID
                ann = anns[nodeid0]
                nick = ann["nickname"]
                self.failUnlessEqual(type(nick), unicode)
                self.failUnlessEqual(nick, NICKNAME % "0")
            if server_version == V1:
                for c in publishing_clients:
                    cdc = c._debug_counts
                    expected = 1 # storage
                    if c is clients[2]:
                        expected += 1 # boring
                    if c is not clients[0]:
                        # the v2 client tries to call publish_v2, which fails
                        # because the server is v1. It then re-sends
                        # everything it has so far, plus a stub_client record
                        expected = 2*expected + 1
                    if c is clients[0]:
                        # we always tell v1 client to send stub_client
                        expected += 1
                    self.failUnlessEqual(cdc["outbound_message"], expected)
            else:
                for c in publishing_clients:
                    cdc = c._debug_counts
                    expected = 1
                    if c in [clients[0], # stub_client
                             clients[2], # boring
                             ]:
                        expected = 2
                    self.failUnlessEqual(cdc["outbound_message"], expected)
            # now check the web status, make sure it renders without error
            ir = introweb.IntroducerRoot(self.parent)
            self.parent.nodeid = "NODEID"
            text = ir.renderSynchronously().decode("utf-8")
            self.failUnlessIn(NICKNAME % "0", text) # the v1 client
            self.failUnlessIn(NICKNAME % "1", text) # a v2 client
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
            if server_version == V1:
                introducer = old.IntroducerService_v1()
            else:
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
        return self.do_system_test(V2)
    test_system_v2_server.timeout = 480
    # occasionally takes longer than 350s on "draco"

    def test_system_v1_server(self):
        self.basedir = "introducer/SystemTest/system_v1_server"
        os.makedirs(self.basedir)
        return self.do_system_test(V1)
    test_system_v1_server.timeout = 480
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
                                     "my_version", "oldest", app_versions)
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

    def test_client_v1(self):
        introducer = IntroducerService()
        subscriber = FakeRemoteReference()
        introducer.remote_subscribe(subscriber, "storage")
        # the v1 subscribe interface had no subscriber_info: that was usually
        # sent in a separate stub_client pseudo-announcement
        subs = introducer.get_subscribers()
        self.failUnlessEqual(len(subs), 1)
        s0 = subs[0]
        self.failUnlessEqual(s0.nickname, u"?") # not known yet
        self.failUnlessEqual(s0.service_name, "storage")

        # now submit the stub_client announcement
        furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:0/swissnum"
        ann = (furl1, "stub_client", "RIStubClient",
               (NICKNAME % u"v1").encode("utf-8"), "my_version", "oldest")
        introducer.remote_publish(ann)
        # the server should correlate the two
        subs = introducer.get_subscribers()
        self.failUnlessEqual(len(subs), 1)
        s0 = subs[0]
        self.failUnlessEqual(s0.service_name, "storage")
        # v1 announcements do not contain app-versions
        self.failUnlessEqual(s0.app_versions, {})
        self.failUnlessEqual(s0.nickname, NICKNAME % u"v1")
        self.failUnlessEqual(s0.version, "my_version")

        # a subscription that arrives after the stub_client announcement
        # should be correlated too
        subscriber2 = FakeRemoteReference()
        introducer.remote_subscribe(subscriber2, "thing2")

        subs = introducer.get_subscribers()
        self.failUnlessEqual(len(subs), 2)
        s0 = [s for s in subs if s.service_name == "thing2"][0]
        # v1 announcements do not contain app-versions
        self.failUnlessEqual(s0.app_versions, {})
        self.failUnlessEqual(s0.nickname, NICKNAME % u"v1")
        self.failUnlessEqual(s0.version, "my_version")

class Announcements(unittest.TestCase):
    def test_client_v2_unsigned(self):
        introducer = IntroducerService()
        tub = introducer_furl = None
        app_versions = {"whizzy": "fizzy"}
        client_v2 = IntroducerClient(tub, introducer_furl, u"nick-v2",
                                     "my_version", "oldest", app_versions)
        furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:0/swissnum"
        tubid = "62ubehyunnyhzs7r6vdonnm2hpi52w6y"
        ann_s0 = make_ann_t(client_v2, furl1, None, 10)
        canary0 = Referenceable()
        introducer.remote_publish_v2(ann_s0, canary0)
        a = introducer.get_announcements()
        self.failUnlessEqual(len(a), 1)
        self.failUnlessIdentical(a[0].canary, canary0)
        self.failUnlessEqual(a[0].index, ("storage", None, tubid))
        self.failUnlessEqual(a[0].announcement["app-versions"], app_versions)
        self.failUnlessEqual(a[0].nickname, u"nick-v2")
        self.failUnlessEqual(a[0].service_name, "storage")
        self.failUnlessEqual(a[0].version, "my_version")
        self.failUnlessEqual(a[0].announcement["anonymous-storage-FURL"], furl1)

    def test_client_v2_signed(self):
        introducer = IntroducerService()
        tub = introducer_furl = None
        app_versions = {"whizzy": "fizzy"}
        client_v2 = IntroducerClient(tub, introducer_furl, u"nick-v2",
                                     "my_version", "oldest", app_versions)
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
        self.failUnlessEqual(a[0].index, ("storage", pks, None))
        self.failUnlessEqual(a[0].announcement["app-versions"], app_versions)
        self.failUnlessEqual(a[0].nickname, u"nick-v2")
        self.failUnlessEqual(a[0].service_name, "storage")
        self.failUnlessEqual(a[0].version, "my_version")
        self.failUnlessEqual(a[0].announcement["anonymous-storage-FURL"], furl1)

    def test_client_v1(self):
        introducer = IntroducerService()

        furl1 = "pb://62ubehyunnyhzs7r6vdonnm2hpi52w6y@127.0.0.1:0/swissnum"
        tubid = "62ubehyunnyhzs7r6vdonnm2hpi52w6y"
        ann = (furl1, "storage", "RIStorage",
               u"nick-v1".encode("utf-8"), "my_version", "oldest")
        introducer.remote_publish(ann)

        a = introducer.get_announcements()
        self.failUnlessEqual(len(a), 1)
        self.failUnlessEqual(a[0].index, ("storage", None, tubid))
        self.failUnlessEqual(a[0].canary, None)
        self.failUnlessEqual(a[0].announcement["app-versions"], {})
        self.failUnlessEqual(a[0].nickname, u"nick-v1".encode("utf-8"))
        self.failUnlessEqual(a[0].service_name, "storage")
        self.failUnlessEqual(a[0].version, "my_version")
        self.failUnlessEqual(a[0].announcement["anonymous-storage-FURL"], furl1)

class ClientSeqnums(unittest.TestCase):
    def test_client(self):
        basedir = "introducer/ClientSeqnums/test_client"
        fileutil.make_dirs(basedir)
        f = open(os.path.join(basedir, "tahoe.cfg"), "w")
        f.write("[client]\n")
        f.write("introducer.furl = nope\n")
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
        current_seqnum, current_nonce = c._sequencer()
        ic.publish("sA", {"key": "value1"}, current_seqnum, current_nonce, c._node_key)
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
        current_seqnum, current_nonce = c._sequencer()
        ic.publish("sB", {"key": "value2"}, current_seqnum, current_nonce, c._node_key)
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
                             u"nickname-client", "version", "oldest", {})
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

        # bad signature
        bad_ann = {"key1": "value2"}
        bad_msg = simplejson.dumps(bad_ann).encode("utf-8")
        self.failUnlessRaises(keyutil.BadSignatureError,
                              unsign_from_foolscap, (bad_msg,sig,key))
        # sneaky bad signature should be ignored
        (ann2,key2) = unsign_from_foolscap( (bad_msg,None,key) )
        self.failUnlessEqual(key2, None)
        self.failUnlessEqual(ann2, bad_ann)

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
