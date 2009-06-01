
import simplejson
from twisted.trial import unittest
from allmydata import check_results, uri
from allmydata.web import check_results as web_check_results
from allmydata.storage_client import StorageFarmBroker, NativeStorageClient
from common_web import WebRenderingMixin

class FakeClient:
    def get_nickname_for_serverid(self, serverid):
        return self.storage_broker.get_nickname_for_serverid(serverid)

class WebResultsRendering(unittest.TestCase, WebRenderingMixin):

    def create_fake_client(self):
        sb = StorageFarmBroker()
        for (peerid, nickname) in [("\x00"*20, "peer-0"),
                                   ("\xff"*20, "peer-f"),
                                   ("\x11"*20, "peer-11")] :
            n = NativeStorageClient(peerid, None, nickname)
            sb.add_server(peerid, n)
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
        cr = check_results.CheckResults(u, u.storage_index)
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
        data["list-corrupt-shares"] = [(serverid_1, u.storage_index, 2)]
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

        pre_cr = check_results.CheckResults(u, u.storage_index)
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

        post_cr = check_results.CheckResults(u, u.storage_index)
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

        crr = check_results.CheckAndRepairResults(u.storage_index)
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

