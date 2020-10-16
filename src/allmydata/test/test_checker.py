"""
Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401


import json
import os.path, shutil

from bs4 import BeautifulSoup

from twisted.trial import unittest
from twisted.internet import defer

# We need to use `nevow.inevow.IRequest` for now for compatibility
# with the code in web/common.py.  Once nevow bits are gone from
# web/common.py, we can use `twisted.web.iweb.IRequest` here.
if PY2:
    from nevow.inevow import IRequest
else:
    from twisted.web.iweb import IRequest

from zope.interface import implementer
from twisted.web.server import Request
from twisted.web.test.requesthelper import DummyChannel
from twisted.web.template import flattenString

from allmydata import check_results, uri
from allmydata import uri as tahoe_uri
from allmydata.interfaces import (
    IServer,
    ICheckResults,
    ICheckAndRepairResults,
)
from allmydata.util import base32
from allmydata.web import check_results as web_check_results
from allmydata.storage_client import StorageFarmBroker, NativeStorageServer
from allmydata.storage.server import storage_index_to_dir
from allmydata.monitor import Monitor
from allmydata.test.no_network import GridTestMixin
from allmydata.immutable.upload import Data
from allmydata.mutable.publish import MutableData

from .common import (
    EMPTY_CLIENT_CONFIG,
)

from .web.common import (
    assert_soup_has_favicon,
    assert_soup_has_tag_with_content,
)

class FakeClient(object):
    def get_storage_broker(self):
        return self.storage_broker

@implementer(IRequest)
class TestRequest(Request, object):
    """
    A minimal Request class to use in tests.

    XXX: We have to have this class because `common.get_arg()` expects
    a `nevow.inevow.IRequest`, which `twisted.web.server.Request`
    isn't.  The request needs to have `args`, `fields`, `prepath`, and
    `postpath` properties so that `allmydata.web.common.get_arg()`
    won't complain.
    """
    def __init__(self, args=None, fields=None):
        super(TestRequest, self).__init__(DummyChannel())
        self.args = args or {}
        self.fields = fields or {}
        self.prepath = [b""]
        self.postpath = [b""]


@implementer(IServer)
class FakeServer(object):

    def get_name(self):
        return "fake name"

    def get_longname(self):
        return "fake longname"

    def get_nickname(self):
        return "fake nickname"


@implementer(ICheckResults)
class FakeCheckResults(object):

    def __init__(self, si=None,
                 healthy=False, recoverable=False,
                 summary="fake summary"):
        self._storage_index = si
        self._is_healthy = healthy
        self._is_recoverable = recoverable
        self._summary = summary

    def get_storage_index(self):
        return self._storage_index

    def get_storage_index_string(self):
        return base32.b2a_or_none(self._storage_index)

    def is_healthy(self):
        return self._is_healthy

    def is_recoverable(self):
        return self._is_recoverable

    def get_summary(self):
        return self._summary

    def get_corrupt_shares(self):
        # returns a list of (IServer, storage_index, sharenum)
        return [(FakeServer(), b"<fake-si>", 0)]


@implementer(ICheckAndRepairResults)
class FakeCheckAndRepairResults(object):

    def __init__(self, si=None,
                 repair_attempted=False,
                 repair_success=False):
        self._storage_index = si
        self._repair_attempted = repair_attempted
        self._repair_success = repair_success

    def get_storage_index(self):
        return self._storage_index

    def get_pre_repair_results(self):
        return FakeCheckResults()

    def get_post_repair_results(self):
        return FakeCheckResults()

    def get_repair_attempted(self):
        return self._repair_attempted

    def get_repair_successful(self):
        return self._repair_success


class WebResultsRendering(unittest.TestCase):

    @staticmethod
    def remove_tags(html):
        return BeautifulSoup(html).get_text(separator=" ")

    def create_fake_client(self):
        sb = StorageFarmBroker(True, None, EMPTY_CLIENT_CONFIG)
        # s.get_name() (the "short description") will be "v0-00000000".
        # s.get_longname() will include the -long suffix.
        servers = [(b"v0-00000000-long", b"\x00"*20, "peer-0"),
                   (b"v0-ffffffff-long", b"\xff"*20, "peer-f"),
                   (b"v0-11111111-long", b"\x11"*20, "peer-11")]
        for (key_s, binary_tubid, nickname) in servers:
            server_id = key_s
            tubid_b32 = base32.b2a(binary_tubid)
            furl = b"pb://%s@nowhere/fake" % tubid_b32
            ann = { "version": 0,
                    "service-name": "storage",
                    "anonymous-storage-FURL": furl,
                    "permutation-seed-base32": "",
                    "nickname": str(nickname),
                    "app-versions": {}, # need #466 and v2 introducer
                    "my-version": "ver",
                    "oldest-supported": "oldest",
                    }
            s = NativeStorageServer(server_id, ann, None, None, None)
            sb.test_add_server(server_id, s)
        c = FakeClient()
        c.storage_broker = sb
        return c

    def render_json(self, resource):
        return resource.render(TestRequest(args={"output": ["json"]}))

    def render_element(self, element, args=None):
        d = flattenString(TestRequest(args), element)
        return unittest.TestCase().successResultOf(d)

    def test_literal(self):
        lcr = web_check_results.LiteralCheckResultsRendererElement()

        html = self.render_element(lcr)
        self.failUnlessIn(b"Literal files are always healthy", html)

        html = self.render_element(lcr, args={"return_to": ["FOOURL"]})
        self.failUnlessIn(b"Literal files are always healthy", html)
        self.failUnlessIn(b'<a href="FOOURL">Return to file.</a>', html)

        c = self.create_fake_client()
        lcr = web_check_results.LiteralCheckResultsRenderer(c)

        js = self.render_json(lcr)
        j = json.loads(js)
        self.failUnlessEqual(j["storage-index"], "")
        self.failUnlessEqual(j["results"]["healthy"], True)


    def test_check(self):
        c = self.create_fake_client()
        sb = c.storage_broker
        serverid_1 = b"\x00"*20
        serverid_f = b"\xff"*20
        server_1 = sb.get_stub_server(serverid_1)
        server_f = sb.get_stub_server(serverid_f)
        u = uri.CHKFileURI(b"\x00"*16, b"\x00"*32, 3, 10, 1234)
        data = { "count_happiness": 8,
                 "count_shares_needed": 3,
                 "count_shares_expected": 9,
                 "count_shares_good": 10,
                 "count_good_share_hosts": 11,
                 "count_recoverable_versions": 1,
                 "count_unrecoverable_versions": 0,
                 "servers_responding": [],
                 "sharemap": {"shareid1": [server_1, server_f]},
                 "count_wrong_shares": 0,
                 "list_corrupt_shares": [],
                 "count_corrupt_shares": 0,
                 "list_incompatible_shares": [],
                 "count_incompatible_shares": 0,
                 "report": [], "share_problems": [], "servermap": None,
                 }
        cr = check_results.CheckResults(u, u.get_storage_index(),
                                        healthy=True, recoverable=True,
                                        summary="groovy",
                                        **data)
        w = web_check_results.CheckResultsRendererElement(c, cr)
        html = self.render_element(w)
        s = self.remove_tags(html)
        self.failUnlessIn("File Check Results for SI=2k6avp", s) # abbreviated
        self.failUnlessIn("Healthy : groovy", s)
        self.failUnlessIn("Share Counts: need 3-of-9, have 10", s)
        self.failUnlessIn("Happiness Level: 8", s)
        self.failUnlessIn("Hosts with good shares: 11", s)
        self.failUnlessIn("Corrupt shares: none", s)
        self.failUnlessIn("Wrong Shares: 0", s)
        self.failUnlessIn("Recoverable Versions: 1", s)
        self.failUnlessIn("Unrecoverable Versions: 0", s)
        self.failUnlessIn("Good Shares (sorted in share order):  Share ID Nickname Node ID shareid1 peer-0 00000000 peer-f ffffffff", s)

        cr = check_results.CheckResults(u, u.get_storage_index(),
                                        healthy=False, recoverable=True,
                                        summary="ungroovy",
                                        **data)
        w = web_check_results.CheckResultsRendererElement(c, cr)
        html = self.render_element(w)
        s = self.remove_tags(html)
        self.failUnlessIn("File Check Results for SI=2k6avp", s) # abbreviated
        self.failUnlessIn("Not Healthy! : ungroovy", s)

        data["count_corrupt_shares"] = 1
        data["list_corrupt_shares"] = [(server_1, u.get_storage_index(), 2)]
        cr = check_results.CheckResults(u, u.get_storage_index(),
                                        healthy=False, recoverable=False,
                                        summary="rather dead",
                                        **data)
        w = web_check_results.CheckResultsRendererElement(c, cr)
        html = self.render_element(w)
        s = self.remove_tags(html)
        self.failUnlessIn("File Check Results for SI=2k6avp", s) # abbreviated
        self.failUnlessIn("Not Recoverable! : rather dead", s)
        self.failUnlessIn("Corrupt shares:  Share ID Nickname Node ID sh#2 peer-0 00000000", s)

        html = self.render_element(w)
        s = self.remove_tags(html)
        self.failUnlessIn("File Check Results for SI=2k6avp", s) # abbreviated
        self.failUnlessIn("Not Recoverable! : rather dead", s)

        html = self.render_element(w, args={"return_to": ["FOOURL"]})
        self.failUnlessIn(b'<a href="FOOURL">Return to file/directory.</a>',
                          html)

        w = web_check_results.CheckResultsRenderer(c, cr)
        d = self.render_json(w)
        def _check_json(jdata):
            j = json.loads(jdata)
            self.failUnlessEqual(j["summary"], "rather dead")
            self.failUnlessEqual(j["storage-index"],
                                 "2k6avpjga3dho3zsjo6nnkt7n4")
            expected = {'count-happiness': 8,
                        'count-shares-expected': 9,
                        'healthy': False,
                        'count-unrecoverable-versions': 0,
                        'count-shares-needed': 3,
                        'sharemap': {"shareid1":
                                     ["v0-00000000-long", "v0-ffffffff-long"]},
                        'count-recoverable-versions': 1,
                        'list-corrupt-shares':
                        [["v0-00000000-long", "2k6avpjga3dho3zsjo6nnkt7n4", 2]],
                        'count-good-share-hosts': 11,
                        'count-wrong-shares': 0,
                        'count-shares-good': 10,
                        'count-corrupt-shares': 1,
                        'servers-responding': [],
                        'recoverable': False,
                        }
            self.failUnlessEqual(j["results"], expected)
        _check_json(d)

        w = web_check_results.CheckResultsRendererElement(c, cr)
        d = self.render_element(w)
        def _check(html):
            s = self.remove_tags(html)
            self.failUnlessIn("File Check Results for SI=2k6avp", s)
            self.failUnlessIn("Not Recoverable! : rather dead", s)
        _check(html)

    def test_check_and_repair(self):
        c = self.create_fake_client()
        sb = c.storage_broker
        serverid_1 = b"\x00"*20
        serverid_f = b"\xff"*20
        u = uri.CHKFileURI(b"\x00"*16, b"\x00"*32, 3, 10, 1234)

        data = { "count_happiness": 5,
                 "count_shares_needed": 3,
                 "count_shares_expected": 10,
                 "count_shares_good": 6,
                 "count_good_share_hosts": 7,
                 "count_recoverable_versions": 1,
                 "count_unrecoverable_versions": 0,
                 "servers_responding": [],
                 "sharemap": {"shareid1": [sb.get_stub_server(serverid_1),
                                           sb.get_stub_server(serverid_f)]},
                 "count_wrong_shares": 0,
                 "list_corrupt_shares": [],
                 "count_corrupt_shares": 0,
                 "list_incompatible_shares": [],
                 "count_incompatible_shares": 0,
                 "report": [], "share_problems": [], "servermap": None,
                 }
        pre_cr = check_results.CheckResults(u, u.get_storage_index(),
                                            healthy=False, recoverable=True,
                                            summary="illing",
                                            **data)

        data = { "count_happiness": 9,
                 "count_shares_needed": 3,
                 "count_shares_expected": 10,
                 "count_shares_good": 10,
                 "count_good_share_hosts": 11,
                 "count_recoverable_versions": 1,
                 "count_unrecoverable_versions": 0,
                 "servers_responding": [],
                 "sharemap": {"shareid1": [sb.get_stub_server(serverid_1),
                                           sb.get_stub_server(serverid_f)]},
                 "count_wrong_shares": 0,
                 "count_corrupt_shares": 0,
                 "list_corrupt_shares": [],
                 "list_incompatible_shares": [],
                 "count_incompatible_shares": 0,
                 "report": [], "share_problems": [], "servermap": None,
                 }
        post_cr = check_results.CheckResults(u, u.get_storage_index(),
                                             healthy=True, recoverable=True,
                                             summary="groovy",
                                             **data)

        crr = check_results.CheckAndRepairResults(u.get_storage_index())
        crr.pre_repair_results = pre_cr
        crr.post_repair_results = post_cr
        crr.repair_attempted = False

        w = web_check_results.CheckAndRepairResultsRendererElement(c, crr)
        html = self.render_element(w)
        s = self.remove_tags(html)

        self.failUnlessIn("File Check-And-Repair Results for SI=2k6avp", s)
        self.failUnlessIn("Healthy : groovy", s)
        self.failUnlessIn("No repair necessary", s)
        self.failUnlessIn("Post-Repair Checker Results:", s)
        self.failUnlessIn("Share Counts: need 3-of-10, have 10", s)

        crr.repair_attempted = True
        crr.repair_successful = True
        html = self.render_element(w)
        s = self.remove_tags(html)

        self.failUnlessIn("File Check-And-Repair Results for SI=2k6avp", s)
        self.failUnlessIn("Healthy : groovy", s)
        self.failUnlessIn("Repair successful", s)
        self.failUnlessIn("Post-Repair Checker Results:", s)

        crr.repair_attempted = True
        crr.repair_successful = False
        post_cr = check_results.CheckResults(u, u.get_storage_index(),
                                             healthy=False, recoverable=True,
                                             summary="better",
                                             **data)
        crr.post_repair_results = post_cr
        html = self.render_element(w)
        s = self.remove_tags(html)

        self.failUnlessIn("File Check-And-Repair Results for SI=2k6avp", s)
        self.failUnlessIn("Not Healthy! : better", s)
        self.failUnlessIn("Repair unsuccessful", s)
        self.failUnlessIn("Post-Repair Checker Results:", s)

        crr.repair_attempted = True
        crr.repair_successful = False
        post_cr = check_results.CheckResults(u, u.get_storage_index(),
                                             healthy=False, recoverable=False,
                                             summary="worse",
                                             **data)
        crr.post_repair_results = post_cr
        html = self.render_element(w)
        s = self.remove_tags(html)

        self.failUnlessIn("File Check-And-Repair Results for SI=2k6avp", s)
        self.failUnlessIn("Not Recoverable! : worse", s)
        self.failUnlessIn("Repair unsuccessful", s)
        self.failUnlessIn("Post-Repair Checker Results:", s)

        w = web_check_results.CheckAndRepairResultsRenderer(c, crr)
        j = json.loads(self.render_json(w))
        self.failUnlessEqual(j["repair-attempted"], True)
        self.failUnlessEqual(j["storage-index"],
                             "2k6avpjga3dho3zsjo6nnkt7n4")
        self.failUnlessEqual(j["pre-repair-results"]["summary"], "illing")
        self.failUnlessEqual(j["post-repair-results"]["summary"], "worse")

        w = web_check_results.CheckAndRepairResultsRenderer(c, None)
        j = json.loads(self.render_json(w))
        self.failUnlessEqual(j["repair-attempted"], False)
        self.failUnlessEqual(j["storage-index"], "")


    def test_deep_check_renderer(self):
        status = check_results.DeepCheckResults(b"fake-root-si")
        status.add_check(
            FakeCheckResults(b"<unhealthy/unrecoverable>", False, False),
            (u"fake", u"unhealthy", u"unrecoverable")
        )
        status.add_check(
            FakeCheckResults(b"<healthy/recoverable>", True, True),
            (u"fake", u"healthy", u"recoverable")
        )
        status.add_check(
            FakeCheckResults(b"<healthy/unrecoverable>", True, False),
            (u"fake", u"healthy", u"unrecoverable")
        )
        status.add_check(
            FakeCheckResults(b"<unhealthy/unrecoverable>", False, True),
            (u"fake", u"unhealthy", u"recoverable")
        )

        monitor = Monitor()
        monitor.set_status(status)

        elem = web_check_results.DeepCheckResultsRendererElement(monitor)
        doc = self.render_element(elem)
        soup = BeautifulSoup(doc, 'html5lib')

        assert_soup_has_favicon(self, soup)

        assert_soup_has_tag_with_content(
            self, soup, u"title",
            u"Tahoe-LAFS - Deep Check Results"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h1",
            "Deep-Check Results for root SI="
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Checked: 4"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Healthy: 2"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Unhealthy: 2"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Unrecoverable: 2"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Corrupt Shares: 4"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"Files/Directories That Had Problems:"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"fake/unhealthy/recoverable: fake summary"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"fake/unhealthy/unrecoverable: fake summary"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"Servers on which corrupt shares were found"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"Corrupt Shares"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"All Results"
        )

    def test_deep_check_and_repair_renderer(self):
        status = check_results.DeepCheckAndRepairResults(b"")

        status.add_check_and_repair(
            FakeCheckAndRepairResults(b"attempted/success", True, True),
            (u"attempted", u"success")
        )
        status.add_check_and_repair(
            FakeCheckAndRepairResults(b"attempted/failure", True, False),
            (u"attempted", u"failure")
        )
        status.add_check_and_repair(
            FakeCheckAndRepairResults(b"unattempted/failure", False, False),
            (u"unattempted", u"failure")
        )

        monitor = Monitor()
        monitor.set_status(status)

        elem = web_check_results.DeepCheckAndRepairResultsRendererElement(monitor)
        doc = self.render_element(elem)
        soup = BeautifulSoup(doc, 'html5lib')

        assert_soup_has_favicon(self, soup)

        assert_soup_has_tag_with_content(
            self, soup, u"title",
            u"Tahoe-LAFS - Deep Check Results"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h1",
            u"Deep-Check-And-Repair Results for root SI="
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Checked: 3"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Healthy (before repair): 0"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Unhealthy (before repair): 3"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Corrupt Shares (before repair): 3"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Repairs Attempted: 2"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Repairs Successful: 1"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            "Repairs Unsuccessful: 1"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Healthy (after repair): 0"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Objects Unhealthy (after repair): 3"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"li",
            u"Corrupt Shares (after repair): 3"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"Files/Directories That Had Problems:"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"Files/Directories That Still Have Problems:"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"Servers on which corrupt shares were found"
        )

        assert_soup_has_tag_with_content(
            self, soup, u"h2",
            u"Remaining Corrupt Shares"
        )


class BalancingAct(GridTestMixin, unittest.TestCase):
    # test for #1115 regarding the 'count-good-share-hosts' metric


    def add_server(self, server_number, readonly=False):
        assert self.g, "I tried to find a grid at self.g, but failed"
        ss = self.g.make_server(server_number, readonly)
        #log.msg("just created a server, number: %s => %s" % (server_number, ss,))
        self.g.add_server(server_number, ss)

    def add_server_with_share(self, server_number, uri, share_number=None,
                              readonly=False):
        self.add_server(server_number, readonly)
        if share_number is not None:
            self.copy_share_to_server(uri, share_number, server_number)

    def copy_share_to_server(self, uri, share_number, server_number):
        ss = self.g.servers_by_number[server_number]
        # Copy share i from the directory associated with the first
        # storage server to the directory associated with this one.
        assert self.g, "I tried to find a grid at self.g, but failed"
        assert self.shares, "I tried to find shares at self.shares, but failed"
        old_share_location = self.shares[share_number][2]
        new_share_location = os.path.join(ss.storedir, "shares")
        si = tahoe_uri.from_string(self.uri).get_storage_index()
        new_share_location = os.path.join(new_share_location,
                                          storage_index_to_dir(si))
        if not os.path.exists(new_share_location):
            os.makedirs(new_share_location)
        new_share_location = os.path.join(new_share_location,
                                          str(share_number))
        if old_share_location != new_share_location:
            shutil.copy(old_share_location, new_share_location)
        shares = self.find_uri_shares(uri)
        # Make sure that the storage server has the share.
        self.failUnless((share_number, ss.my_nodeid, new_share_location)
                        in shares)

    def _pretty_shares_chart(self, uri):
        # Servers are labeled A-Z, shares are labeled 0-9
        letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        assert len(self.g.servers_by_number) < len(letters), \
            "This little printing function is only meant for < 26 servers"
        shares_chart = {}
        names = dict(zip([ss.my_nodeid
                          for _,ss in self.g.servers_by_number.items()],
                         letters))
        for shnum, serverid, _ in self.find_uri_shares(uri):
            shares_chart.setdefault(shnum, []).append(names[serverid])
        return shares_chart

    def test_good_share_hosts(self):
        self.basedir = "checker/BalancingAct/1115"
        self.set_up_grid(num_servers=1)
        c0 = self.g.clients[0]
        c0.encoding_params['happy'] = 1
        c0.encoding_params['n'] = 4
        c0.encoding_params['k'] = 3

        DATA = b"data" * 100
        d = c0.upload(Data(DATA, convergence=b""))
        def _stash_immutable(ur):
            self.imm = c0.create_node_from_uri(ur.get_uri())
            self.uri = self.imm.get_uri()
        d.addCallback(_stash_immutable)
        d.addCallback(lambda ign:
            self.find_uri_shares(self.uri))
        def _store_shares(shares):
            self.shares = shares
        d.addCallback(_store_shares)

        def add_three(_, i):
            # Add a new server with just share 3
            self.add_server_with_share(i, self.uri, 3)
            #print(self._pretty_shares_chart(self.uri))
        for i in range(1,5):
            d.addCallback(add_three, i)

        def _check_and_repair(_):
            return self.imm.check_and_repair(Monitor())
        def _check_counts(crr, shares_good, good_share_hosts):
            prr = crr.get_post_repair_results()
            self.failUnlessEqual(prr.get_share_counter_good(), shares_good)
            self.failUnlessEqual(prr.get_host_counter_good_shares(),
                                 good_share_hosts)

        """
        Initial sharemap:
            0:[A] 1:[A] 2:[A] 3:[A,B,C,D,E]
          4 good shares, but 5 good hosts
        After deleting all instances of share #3 and repairing:
            0:[A], 1:[A,B], 2:[C,A], 3:[E]
# actually: {0: ['E', 'A'], 1: ['C', 'A'], 2: ['A', 'B'], 3: ['D']}
          Still 4 good shares but now 4 good hosts
            """
        d.addCallback(_check_and_repair)
        d.addCallback(_check_counts, 4, 5)
        d.addCallback(lambda _: self.delete_shares_numbered(self.uri, [3]))
        d.addCallback(_check_and_repair)

        # it can happen that our uploader will choose, e.g., to upload
        # to servers B, C, D, E .. which will mean that all 5 serves
        # now contain our shares (and thus "respond").

        def _check_happy(crr):
            prr = crr.get_post_repair_results()
            self.assertTrue(prr.get_host_counter_good_shares() >= 4)
            return crr
        d.addCallback(_check_happy)
        d.addCallback(lambda _: all([self.g.break_server(sid)
                                     for sid in self.g.get_all_serverids()]))
        d.addCallback(_check_and_repair)
        d.addCallback(_check_counts, 0, 0)
        return d

class AddLease(GridTestMixin, unittest.TestCase):
    # test for #875, in which failures in the add-lease call cause
    # false-negatives in the checker

    def test_875(self):
        self.basedir = "checker/AddLease/875"
        self.set_up_grid(num_servers=1)
        c0 = self.g.clients[0]
        c0.encoding_params['happy'] = 1
        self.uris = {}
        DATA = b"data" * 100
        d = c0.upload(Data(DATA, convergence=b""))
        def _stash_immutable(ur):
            self.imm = c0.create_node_from_uri(ur.get_uri())
        d.addCallback(_stash_immutable)
        d.addCallback(lambda ign:
            c0.create_mutable_file(MutableData(b"contents")))
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
            self.c0.encoding_params = { "k": 1,
                                        "happy": 4,
                                        "n": 4,
                                        "max_segment_size": 5,
                                      }
            self.uris = {}
            DATA = b"data" * 100 # 400/5 = 80 blocks
            return self.c0.upload(Data(DATA, convergence=b""))
        d.addCallback(_start)
        def _do_check(ur):
            n = self.c0.create_node_from_uri(ur.get_uri())
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
