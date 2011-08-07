
import simplejson
from twisted.trial import unittest
from twisted.internet import defer
from allmydata import check_results, uri
from allmydata.web import check_results as web_check_results
from allmydata.storage_client import StorageFarmBroker, NativeStorageServer
from allmydata.monitor import Monitor
from allmydata.test.no_network import GridTestMixin
from allmydata.immutable.upload import Data
from allmydata.test.common_web import WebRenderingMixin
from allmydata.mutable.publish import MutableData

class FakeClient:
    def get_storage_broker(self):
        return self.storage_broker

class WebResultsRendering(unittest.TestCase, WebRenderingMixin):

    def create_fake_client(self):
        sb = StorageFarmBroker(None, True)
        for (peerid, nickname) in [("\x00"*20, "peer-0"),
                                   ("\xff"*20, "peer-f"),
                                   ("\x11"*20, "peer-11")] :
            ann_d = { "version": 0,
                      "service-name": "storage",
                      "FURL": "fake furl",
                      "nickname": unicode(nickname),
                      "app-versions": {}, # need #466 and v2 introducer
                      "my-version": "ver",
                      "oldest-supported": "oldest",
                      }
            s = NativeStorageServer(peerid, ann_d)
            sb.test_add_server(peerid, s)
        c = FakeClient()
        c.storage_broker = sb
        return c

    def render_json(self, page):
        d = self.render1(page, args={"output": ["json"]})
        return d

    def test_literal(self):
        c = self.create_fake_client()
        lcr = web_check_results.LiteralCheckResults(c)

        d = self.render1(lcr)
        def _check(html):
            s = self.remove_tags(html)
            self.failUnlessIn("Literal files are always healthy", s)
        d.addCallback(_check)
        d.addCallback(lambda ignored:
                      self.render1(lcr, args={"return_to": ["FOOURL"]}))
        def _check_return_to(html):
            s = self.remove_tags(html)
            self.failUnlessIn("Literal files are always healthy", s)
            self.failUnlessIn('<a href="FOOURL">Return to file.</a>',
                              html)
        d.addCallback(_check_return_to)
        d.addCallback(lambda ignored: self.render_json(lcr))
        def _check_json(json):
            j = simplejson.loads(json)
            self.failUnlessEqual(j["storage-index"], "")
            self.failUnlessEqual(j["results"]["healthy"], True)
        d.addCallback(_check_json)
        return d

    def test_check(self):
        c = self.create_fake_client()
        serverid_1 = "\x00"*20
        serverid_f = "\xff"*20
        u = uri.CHKFileURI("\x00"*16, "\x00"*32, 3, 10, 1234)
        cr = check_results.CheckResults(u, u.get_storage_index())
        cr.set_healthy(True)
        cr.set_needs_rebalancing(False)
        cr.set_summary("groovy")
        data = { "count-shares-needed": 3,
                 "count-shares-expected": 9,
                 "count-shares-good": 10,
                 "count-good-share-hosts": 11,
                 "list-corrupt-shares": [],
                 "count-wrong-shares": 0,
                 "sharemap": {"shareid1": [serverid_1, serverid_f]},
                 "count-recoverable-versions": 1,
                 "count-unrecoverable-versions": 0,
                 "servers-responding": [],
                 }
        cr.set_data(data)

        w = web_check_results.CheckResults(c, cr)
        html = self.render2(w)
        s = self.remove_tags(html)
        self.failUnlessIn("File Check Results for SI=2k6avp", s) # abbreviated
        self.failUnlessIn("Healthy : groovy", s)
        self.failUnlessIn("Share Counts: need 3-of-9, have 10", s)
        self.failUnlessIn("Hosts with good shares: 11", s)
        self.failUnlessIn("Corrupt shares: none", s)
        self.failUnlessIn("Wrong Shares: 0", s)
        self.failUnlessIn("Recoverable Versions: 1", s)
        self.failUnlessIn("Unrecoverable Versions: 0", s)

        cr.set_healthy(False)
        cr.set_recoverable(True)
        cr.set_summary("ungroovy")
        html = self.render2(w)
        s = self.remove_tags(html)
        self.failUnlessIn("File Check Results for SI=2k6avp", s) # abbreviated
        self.failUnlessIn("Not Healthy! : ungroovy", s)

        cr.set_healthy(False)
        cr.set_recoverable(False)
        cr.set_summary("rather dead")
        data["list-corrupt-shares"] = [(serverid_1, u.get_storage_index(), 2)]
        cr.set_data(data)
        html = self.render2(w)
        s = self.remove_tags(html)
        self.failUnlessIn("File Check Results for SI=2k6avp", s) # abbreviated
        self.failUnlessIn("Not Recoverable! : rather dead", s)
        self.failUnlessIn("Corrupt shares: Share ID Nickname Node ID sh#2 peer-0 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", s)

        html = self.render2(w)
        s = self.remove_tags(html)
        self.failUnlessIn("File Check Results for SI=2k6avp", s) # abbreviated
        self.failUnlessIn("Not Recoverable! : rather dead", s)

        html = self.render2(w, args={"return_to": ["FOOURL"]})
        self.failUnlessIn('<a href="FOOURL">Return to file/directory.</a>',
                          html)

        d = self.render_json(w)
        def _check_json(jdata):
            j = simplejson.loads(jdata)
            self.failUnlessEqual(j["summary"], "rather dead")
            self.failUnlessEqual(j["storage-index"],
                                 "2k6avpjga3dho3zsjo6nnkt7n4")
            expected = {'needs-rebalancing': False,
                        'count-shares-expected': 9,
                        'healthy': False,
                        'count-unrecoverable-versions': 0,
                        'count-shares-needed': 3,
                        'sharemap': {"shareid1":
                                     ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                                      "77777777777777777777777777777777"]},
                        'count-recoverable-versions': 1,
                        'list-corrupt-shares':
                        [["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                          "2k6avpjga3dho3zsjo6nnkt7n4", 2]],
                        'count-good-share-hosts': 11,
                        'count-wrong-shares': 0,
                        'count-shares-good': 10,
                        'count-corrupt-shares': 0,
                        'servers-responding': [],
                        'recoverable': False,
                        }
            self.failUnlessEqual(j["results"], expected)
        d.addCallback(_check_json)
        d.addCallback(lambda ignored: self.render1(w))
        def _check(html):
            s = self.remove_tags(html)
            self.failUnlessIn("File Check Results for SI=2k6avp", s)
            self.failUnlessIn("Not Recoverable! : rather dead", s)
        d.addCallback(_check)
        return d


    def test_check_and_repair(self):
        c = self.create_fake_client()
        serverid_1 = "\x00"*20
        serverid_f = "\xff"*20
        u = uri.CHKFileURI("\x00"*16, "\x00"*32, 3, 10, 1234)

        pre_cr = check_results.CheckResults(u, u.get_storage_index())
        pre_cr.set_healthy(False)
        pre_cr.set_recoverable(True)
        pre_cr.set_needs_rebalancing(False)
        pre_cr.set_summary("illing")
        data = { "count-shares-needed": 3,
                 "count-shares-expected": 10,
                 "count-shares-good": 6,
                 "count-good-share-hosts": 7,
                 "list-corrupt-shares": [],
                 "count-wrong-shares": 0,
                 "sharemap": {"shareid1": [serverid_1, serverid_f]},
                 "count-recoverable-versions": 1,
                 "count-unrecoverable-versions": 0,
                 "servers-responding": [],
                 }
        pre_cr.set_data(data)

        post_cr = check_results.CheckResults(u, u.get_storage_index())
        post_cr.set_healthy(True)
        post_cr.set_recoverable(True)
        post_cr.set_needs_rebalancing(False)
        post_cr.set_summary("groovy")
        data = { "count-shares-needed": 3,
                 "count-shares-expected": 10,
                 "count-shares-good": 10,
                 "count-good-share-hosts": 11,
                 "list-corrupt-shares": [],
                 "count-wrong-shares": 0,
                 "sharemap": {"shareid1": [serverid_1, serverid_f]},
                 "count-recoverable-versions": 1,
                 "count-unrecoverable-versions": 0,
                 "servers-responding": [],
                 }
        post_cr.set_data(data)

        crr = check_results.CheckAndRepairResults(u.get_storage_index())
        crr.pre_repair_results = pre_cr
        crr.post_repair_results = post_cr
        crr.repair_attempted = False

        w = web_check_results.CheckAndRepairResults(c, crr)
        html = self.render2(w)
        s = self.remove_tags(html)

        self.failUnlessIn("File Check-And-Repair Results for SI=2k6avp", s)
        self.failUnlessIn("Healthy : groovy", s)
        self.failUnlessIn("No repair necessary", s)
        self.failUnlessIn("Post-Repair Checker Results:", s)
        self.failUnlessIn("Share Counts: need 3-of-10, have 10", s)

        crr.repair_attempted = True
        crr.repair_successful = True
        html = self.render2(w)
        s = self.remove_tags(html)

        self.failUnlessIn("File Check-And-Repair Results for SI=2k6avp", s)
        self.failUnlessIn("Healthy : groovy", s)
        self.failUnlessIn("Repair successful", s)
        self.failUnlessIn("Post-Repair Checker Results:", s)

        crr.repair_attempted = True
        crr.repair_successful = False
        post_cr.set_healthy(False)
        post_cr.set_summary("better")
        html = self.render2(w)
        s = self.remove_tags(html)

        self.failUnlessIn("File Check-And-Repair Results for SI=2k6avp", s)
        self.failUnlessIn("Not Healthy! : better", s)
        self.failUnlessIn("Repair unsuccessful", s)
        self.failUnlessIn("Post-Repair Checker Results:", s)

        crr.repair_attempted = True
        crr.repair_successful = False
        post_cr.set_healthy(False)
        post_cr.set_recoverable(False)
        post_cr.set_summary("worse")
        html = self.render2(w)
        s = self.remove_tags(html)

        self.failUnlessIn("File Check-And-Repair Results for SI=2k6avp", s)
        self.failUnlessIn("Not Recoverable! : worse", s)
        self.failUnlessIn("Repair unsuccessful", s)
        self.failUnlessIn("Post-Repair Checker Results:", s)

        d = self.render_json(w)
        def _got_json(data):
            j = simplejson.loads(data)
            self.failUnlessEqual(j["repair-attempted"], True)
            self.failUnlessEqual(j["storage-index"],
                                 "2k6avpjga3dho3zsjo6nnkt7n4")
            self.failUnlessEqual(j["pre-repair-results"]["summary"], "illing")
            self.failUnlessEqual(j["post-repair-results"]["summary"], "worse")
        d.addCallback(_got_json)

        w2 = web_check_results.CheckAndRepairResults(c, None)
        d.addCallback(lambda ignored: self.render_json(w2))
        def _got_lit_results(data):
            j = simplejson.loads(data)
            self.failUnlessEqual(j["repair-attempted"], False)
            self.failUnlessEqual(j["storage-index"], "")
        d.addCallback(_got_lit_results)
        return d

class AddLease(GridTestMixin, unittest.TestCase):
    # test for #875, in which failures in the add-lease call cause
    # false-negatives in the checker

    def test_875(self):
        self.basedir = "checker/AddLease/875"
        self.set_up_grid(num_servers=1)
        c0 = self.g.clients[0]
        c0.DEFAULT_ENCODING_PARAMETERS['happy'] = 1
        self.uris = {}
        DATA = "data" * 100
        d = c0.upload(Data(DATA, convergence=""))
        def _stash_immutable(ur):
            self.imm = c0.create_node_from_uri(ur.uri)
        d.addCallback(_stash_immutable)
        d.addCallback(lambda ign:
            c0.create_mutable_file(MutableData("contents")))
        def _stash_mutable(node):
            self.mut = node
        d.addCallback(_stash_mutable)

        def _check_cr(cr, which):
            self.failUnless(cr.is_healthy(), which)

        # these two should work normally
        d.addCallback(lambda ign: self.imm.check(Monitor(), add_lease=True))
        d.addCallback(_check_cr, "immutable-normal")
        d.addCallback(lambda ign: self.mut.check(Monitor(), add_lease=True))
        d.addCallback(_check_cr, "mutable-normal")

        really_did_break = []
        # now break the server's remote_add_lease call
        def _break_add_lease(ign):
            def broken_add_lease(*args, **kwargs):
                really_did_break.append(1)
                raise KeyError("intentional failure, should be ignored")
            assert self.g.servers_by_number[0].remote_add_lease
            self.g.servers_by_number[0].remote_add_lease = broken_add_lease
        d.addCallback(_break_add_lease)

        # and confirm that the files still look healthy
        d.addCallback(lambda ign: self.mut.check(Monitor(), add_lease=True))
        d.addCallback(_check_cr, "mutable-broken")
        d.addCallback(lambda ign: self.imm.check(Monitor(), add_lease=True))
        d.addCallback(_check_cr, "immutable-broken")

        d.addCallback(lambda ign: self.failUnless(really_did_break))
        return d

class CounterHolder(object):
    def __init__(self):
        self._num_active_block_fetches = 0
        self._max_active_block_fetches = 0

from allmydata.immutable.checker import ValidatedReadBucketProxy
class MockVRBP(ValidatedReadBucketProxy):
    def __init__(self, sharenum, bucket, share_hash_tree, num_blocks, block_size, share_size, counterholder):
        ValidatedReadBucketProxy.__init__(self, sharenum, bucket,
                                          share_hash_tree, num_blocks,
                                          block_size, share_size)
        self.counterholder = counterholder

    def get_block(self, blocknum):
        self.counterholder._num_active_block_fetches += 1
        if self.counterholder._num_active_block_fetches > self.counterholder._max_active_block_fetches:
            self.counterholder._max_active_block_fetches = self.counterholder._num_active_block_fetches
        d = ValidatedReadBucketProxy.get_block(self, blocknum)
        def _mark_no_longer_active(res):
            self.counterholder._num_active_block_fetches -= 1
            return res
        d.addBoth(_mark_no_longer_active)
        return d

class TooParallel(GridTestMixin, unittest.TestCase):
    # bug #1395: immutable verifier was aggressively parallized, checking all
    # blocks of all shares at the same time, blowing our memory budget and
    # crashing with MemoryErrors on >1GB files.

    def test_immutable(self):
        import allmydata.immutable.checker
        origVRBP = allmydata.immutable.checker.ValidatedReadBucketProxy

        self.basedir = "checker/TooParallel/immutable"

        # If any code asks to instantiate a ValidatedReadBucketProxy,
        # we give them a MockVRBP which is configured to use our
        # CounterHolder.
        counterholder = CounterHolder()
        def make_mock_VRBP(*args, **kwargs):
            return MockVRBP(counterholder=counterholder, *args, **kwargs)
        allmydata.immutable.checker.ValidatedReadBucketProxy = make_mock_VRBP

        d = defer.succeed(None)
        def _start(ign):
            self.set_up_grid(num_servers=4)
            self.c0 = self.g.clients[0]
            self.c0.DEFAULT_ENCODING_PARAMETERS = { "k": 1,
                                               "happy": 4,
                                               "n": 4,
                                               "max_segment_size": 5,
                                               }
            self.uris = {}
            DATA = "data" * 100 # 400/5 = 80 blocks
            return self.c0.upload(Data(DATA, convergence=""))
        d.addCallback(_start)
        def _do_check(ur):
            n = self.c0.create_node_from_uri(ur.uri)
            return n.check(Monitor(), verify=True)
        d.addCallback(_do_check)
        def _check(cr):
            # the verifier works on all 4 shares in parallel, but only
            # fetches one block from each share at a time, so we expect to
            # see 4 parallel fetches
            self.failUnlessEqual(counterholder._max_active_block_fetches, 4)
        d.addCallback(_check)
        def _clean_up(res):
            allmydata.immutable.checker.ValidatedReadBucketProxy = origVRBP
            return res
        d.addBoth(_clean_up)
        return d

    test_immutable.timeout = 80
