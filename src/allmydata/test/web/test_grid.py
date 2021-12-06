"""
Ported to Python 3.
"""
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os.path, re
from urllib.parse import quote as url_quote
import json
from six.moves import StringIO

from bs4 import BeautifulSoup

from twisted.web import resource
from allmydata import uri, dirnode
from allmydata.util import base32
from allmydata.util.encodingutil import to_bytes
from allmydata.util.consumer import download_to_data
from allmydata.util.netstring import split_netstring
from allmydata.unknown import UnknownNode
from allmydata.storage.shares import get_share_file
from allmydata.scripts.debug import CorruptShareOptions, corrupt_share
from allmydata.immutable import upload
from allmydata.mutable import publish

from ...web.common import (
    render_exception,
)
from .. import common_util as testutil
from ..common import WebErrorMixin, ShouldFailMixin
from ..no_network import GridTestMixin
from .common import (
    assert_soup_has_favicon,
    unknown_immcap,
    unknown_rocap,
    unknown_rwcap,
)

from ..common import (
    AsyncTestCase,
)

from testtools.matchers import (
    Equals,
    Contains,
    Not,
    HasLength,
    EndsWith,
)

from testtools.twistedsupport import flush_logged_errors


DIR_HTML_TAG = '<html lang="en">'

class CompletelyUnhandledError(Exception):
    pass

class ErrorBoom(resource.Resource, object):
    @render_exception
    def render(self, req):
        raise CompletelyUnhandledError("whoops")

class Grid(GridTestMixin, WebErrorMixin, ShouldFailMixin, testutil.ReallyEqualMixin, AsyncTestCase):

    def CHECK(self, ign, which, args, clientnum=0):
        fileurl = self.fileurls[which]
        url = fileurl + "?" + args
        return self.GET_unicode(url, method="POST", clientnum=clientnum)

    def GET_unicode(self, *args, **kwargs):
        """Send an HTTP request, but convert result to Unicode string."""
        d = GridTestMixin.GET(self, *args, **kwargs)
        d.addCallback(str, "utf-8")
        return d

    def test_filecheck(self):
        self.basedir = "web/Grid/filecheck"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.uris = {}
        DATA = b"data" * 100
        d = c0.upload(upload.Data(DATA, convergence=b""))
        def _stash_uri(ur, which):
            self.uris[which] = ur.get_uri()
        d.addCallback(_stash_uri, "good")
        d.addCallback(lambda ign:
                      c0.upload(upload.Data(DATA+b"1", convergence=b"")))
        d.addCallback(_stash_uri, "sick")
        d.addCallback(lambda ign:
                      c0.upload(upload.Data(DATA+b"2", convergence=b"")))
        d.addCallback(_stash_uri, "dead")
        def _stash_mutable_uri(n, which):
            self.uris[which] = n.get_uri()
            assert isinstance(self.uris[which], bytes)
        d.addCallback(lambda ign:
            c0.create_mutable_file(publish.MutableData(DATA+b"3")))
        d.addCallback(_stash_mutable_uri, "corrupt")
        d.addCallback(lambda ign:
                      c0.upload(upload.Data(b"literal", convergence=b"")))
        d.addCallback(_stash_uri, "small")
        d.addCallback(lambda ign: c0.create_immutable_dirnode({}))
        d.addCallback(_stash_mutable_uri, "smalldir")

        def _compute_fileurls(ignored):
            self.fileurls = {}
            for which in self.uris:
                self.fileurls[which] = "uri/" + url_quote(self.uris[which])
        d.addCallback(_compute_fileurls)

        def _clobber_shares(ignored):
            good_shares = self.find_uri_shares(self.uris["good"])
            self.failUnlessReallyEqual(len(good_shares), 10)
            sick_shares = self.find_uri_shares(self.uris["sick"])
            os.unlink(sick_shares[0][2])
            dead_shares = self.find_uri_shares(self.uris["dead"])
            for i in range(1, 10):
                os.unlink(dead_shares[i][2])
            c_shares = self.find_uri_shares(self.uris["corrupt"])
            cso = CorruptShareOptions()
            cso.stdout = StringIO()
            cso.parseOptions([c_shares[0][2]])
            corrupt_share(cso)
        d.addCallback(_clobber_shares)

        d.addCallback(self.CHECK, "good", "t=check")
        def _got_html_good(res):
            self.assertThat(res, Contains("Healthy"))
            self.assertThat(res, Not(Contains("Not Healthy", )))
            soup = BeautifulSoup(res, 'html5lib')
            assert_soup_has_favicon(self, soup)

        d.addCallback(_got_html_good)
        d.addCallback(self.CHECK, "good", "t=check&return_to=somewhere")
        def _got_html_good_return_to(res):
            self.assertThat(res, Contains("Healthy"))
            self.assertThat(res, Not(Contains("Not Healthy")))
            self.assertThat(res, Contains('<a href="somewhere">Return to file'))
        d.addCallback(_got_html_good_return_to)
        d.addCallback(self.CHECK, "good", "t=check&output=json")
        def _got_json_good(res):
            r = json.loads(res)
            self.failUnlessEqual(r["summary"], "Healthy")
            self.failUnless(r["results"]["healthy"])
            self.assertThat(r["results"], Not(Contains("needs-rebalancing",)))
            self.failUnless(r["results"]["recoverable"])
        d.addCallback(_got_json_good)

        d.addCallback(self.CHECK, "small", "t=check")
        def _got_html_small(res):
            self.assertThat(res, Contains("Literal files are always healthy"))
            self.assertThat(res, Not(Contains("Not Healthy")))
        d.addCallback(_got_html_small)
        d.addCallback(self.CHECK, "small", "t=check&return_to=somewhere")
        def _got_html_small_return_to(res):
            self.assertThat(res, Contains("Literal files are always healthy"))
            self.assertThat(res, Not(Contains("Not Healthy")))
            self.assertThat(res, Contains('<a href="somewhere">Return to file'))
        d.addCallback(_got_html_small_return_to)
        d.addCallback(self.CHECK, "small", "t=check&output=json")
        def _got_json_small(res):
            r = json.loads(res)
            self.failUnlessEqual(r["storage-index"], "")
            self.failUnless(r["results"]["healthy"])
        d.addCallback(_got_json_small)

        d.addCallback(self.CHECK, "smalldir", "t=check")
        def _got_html_smalldir(res):
            self.assertThat(res, Contains("Literal files are always healthy"))
            self.assertThat(res, Not(Contains("Not Healthy")))
        d.addCallback(_got_html_smalldir)
        d.addCallback(self.CHECK, "smalldir", "t=check&output=json")
        def _got_json_smalldir(res):
            r = json.loads(res)
            self.failUnlessEqual(r["storage-index"], "")
            self.failUnless(r["results"]["healthy"])
        d.addCallback(_got_json_smalldir)

        d.addCallback(self.CHECK, "sick", "t=check")
        def _got_html_sick(res):
            self.assertThat(res, Contains("Not Healthy"))
        d.addCallback(_got_html_sick)
        d.addCallback(self.CHECK, "sick", "t=check&output=json")
        def _got_json_sick(res):
            r = json.loads(res)
            self.failUnlessEqual(r["summary"],
                                 "Not Healthy: 9 shares (enc 3-of-10)")
            self.assertThat(r["results"]["healthy"], Equals(False))
            self.failUnless(r["results"]["recoverable"])
            self.assertThat(r["results"], Not(Contains("needs-rebalancing")))
        d.addCallback(_got_json_sick)

        d.addCallback(self.CHECK, "dead", "t=check")
        def _got_html_dead(res):
            self.assertThat(res, Contains("Not Healthy"))
        d.addCallback(_got_html_dead)
        d.addCallback(self.CHECK, "dead", "t=check&output=json")
        def _got_json_dead(res):
            r = json.loads(res)
            self.failUnlessEqual(r["summary"],
                                 "Not Healthy: 1 shares (enc 3-of-10)")
            self.assertThat(r["results"]["healthy"], Equals(False))
            self.assertThat(r["results"]["recoverable"], Equals(False))
            self.assertThat(r["results"], Not(Contains("needs-rebalancing")))
        d.addCallback(_got_json_dead)

        d.addCallback(self.CHECK, "corrupt", "t=check&verify=true")
        def _got_html_corrupt(res):
            self.assertThat(res, Contains("Not Healthy! : Unhealthy"))
        d.addCallback(_got_html_corrupt)
        d.addCallback(self.CHECK, "corrupt", "t=check&verify=true&output=json")
        def _got_json_corrupt(res):
            r = json.loads(res)
            self.assertThat(r["summary"], Contains("Unhealthy: 9 shares (enc 3-of-10)"))
            self.assertThat(r["results"]["healthy"], Equals(False))
            self.failUnless(r["results"]["recoverable"])
            self.assertThat(r["results"], Not(Contains("needs-rebalancing")))
            self.failUnlessReallyEqual(r["results"]["count-happiness"], 9)
            self.failUnlessReallyEqual(r["results"]["count-shares-good"], 9)
            self.failUnlessReallyEqual(r["results"]["count-corrupt-shares"], 1)
        d.addCallback(_got_json_corrupt)

        d.addErrback(self.explain_web_error)
        return d

    def test_repair_html(self):
        self.basedir = "web/Grid/repair_html"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.uris = {}
        DATA = b"data" * 100
        d = c0.upload(upload.Data(DATA, convergence=b""))
        def _stash_uri(ur, which):
            self.uris[which] = ur.get_uri()
        d.addCallback(_stash_uri, "good")
        d.addCallback(lambda ign:
                      c0.upload(upload.Data(DATA+b"1", convergence=b"")))
        d.addCallback(_stash_uri, "sick")
        d.addCallback(lambda ign:
                      c0.upload(upload.Data(DATA+b"2", convergence=b"")))
        d.addCallback(_stash_uri, "dead")
        def _stash_mutable_uri(n, which):
            self.uris[which] = n.get_uri()
            assert isinstance(self.uris[which], bytes)
        d.addCallback(lambda ign:
            c0.create_mutable_file(publish.MutableData(DATA+b"3")))
        d.addCallback(_stash_mutable_uri, "corrupt")

        def _compute_fileurls(ignored):
            self.fileurls = {}
            for which in self.uris:
                self.fileurls[which] = "uri/" + url_quote(self.uris[which])
        d.addCallback(_compute_fileurls)

        def _clobber_shares(ignored):
            good_shares = self.find_uri_shares(self.uris["good"])
            self.failUnlessReallyEqual(len(good_shares), 10)
            sick_shares = self.find_uri_shares(self.uris["sick"])
            os.unlink(sick_shares[0][2])
            dead_shares = self.find_uri_shares(self.uris["dead"])
            for i in range(1, 10):
                os.unlink(dead_shares[i][2])
            c_shares = self.find_uri_shares(self.uris["corrupt"])
            cso = CorruptShareOptions()
            cso.stdout = StringIO()
            cso.parseOptions([c_shares[0][2]])
            corrupt_share(cso)
        d.addCallback(_clobber_shares)

        d.addCallback(self.CHECK, "good", "t=check&repair=true")
        def _got_html_good(res):
            self.assertThat(res, Contains("Healthy"))
            self.assertThat(res, Not(Contains("Not Healthy")))
            self.assertThat(res, Contains("No repair necessary", ))
            soup = BeautifulSoup(res, 'html5lib')
            assert_soup_has_favicon(self, soup)

        d.addCallback(_got_html_good)

        d.addCallback(self.CHECK, "sick", "t=check&repair=true")
        def _got_html_sick(res):
            self.assertThat(res, Contains("Healthy : healthy"))
            self.assertThat(res, Not(Contains("Not Healthy")))
            self.assertThat(res, Contains("Repair successful"))
        d.addCallback(_got_html_sick)

        # repair of a dead file will fail, of course, but it isn't yet
        # clear how this should be reported. Right now it shows up as
        # a "410 Gone".
        #
        #d.addCallback(self.CHECK, "dead", "t=check&repair=true")
        #def _got_html_dead(res):
        #    print(res)
        #    self.failUnlessIn("Healthy : healthy", res)
        #    self.failIfIn("Not Healthy", res)
        #    self.failUnlessIn("No repair necessary", res)
        #d.addCallback(_got_html_dead)

        d.addCallback(self.CHECK, "corrupt", "t=check&verify=true&repair=true")
        def _got_html_corrupt(res):
            self.assertThat(res, Contains("Healthy : Healthy"))
            self.assertThat(res, Not(Contains("Not Healthy")))
            self.assertThat(res, Contains("Repair successful"))
        d.addCallback(_got_html_corrupt)

        d.addErrback(self.explain_web_error)
        return d

    def test_repair_json(self):
        self.basedir = "web/Grid/repair_json"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.uris = {}
        DATA = b"data" * 100
        d = c0.upload(upload.Data(DATA+b"1", convergence=b""))
        def _stash_uri(ur, which):
            self.uris[which] = ur.get_uri()
        d.addCallback(_stash_uri, "sick")

        def _compute_fileurls(ignored):
            self.fileurls = {}
            for which in self.uris:
                self.fileurls[which] = "uri/" + url_quote(self.uris[which])
        d.addCallback(_compute_fileurls)

        def _clobber_shares(ignored):
            sick_shares = self.find_uri_shares(self.uris["sick"])
            os.unlink(sick_shares[0][2])
        d.addCallback(_clobber_shares)

        d.addCallback(self.CHECK, "sick", "t=check&repair=true&output=json")
        def _got_json_sick(res):
            r = json.loads(res)
            self.failUnlessReallyEqual(r["repair-attempted"], True)
            self.failUnlessReallyEqual(r["repair-successful"], True)
            self.failUnlessEqual(r["pre-repair-results"]["summary"],
                                 "Not Healthy: 9 shares (enc 3-of-10)")
            self.failIf(r["pre-repair-results"]["results"]["healthy"])
            self.failUnlessEqual(r["post-repair-results"]["summary"], "healthy")
            self.failUnless(r["post-repair-results"]["results"]["healthy"])
        d.addCallback(_got_json_sick)

        d.addErrback(self.explain_web_error)
        return d

    def test_unknown(self, immutable=False):
        self.basedir = "web/Grid/unknown"
        if immutable:
            self.basedir = "web/Grid/unknown-immutable"

        self.set_up_grid(oneshare=True)
        c0 = self.g.clients[0]
        self.uris = {}
        self.fileurls = {}

        # the future cap format may contain slashes, which must be tolerated
        expected_info_url = "uri/%s?t=info" % url_quote(unknown_rwcap,
                                                           safe="")

        if immutable:
            name = u"future-imm"
            future_node = UnknownNode(None, unknown_immcap, deep_immutable=True)
            d = c0.create_immutable_dirnode({name: (future_node, {})})
        else:
            name = u"future"
            future_node = UnknownNode(unknown_rwcap, unknown_rocap)
            d = c0.create_dirnode()

        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.rooturl = "uri/" + url_quote(n.get_uri())
            self.rourl = "uri/" + url_quote(n.get_readonly_uri())
            if not immutable:
                return self.rootnode.set_node(name, future_node)
        d.addCallback(_stash_root_and_create_file)

        # make sure directory listing tolerates unknown nodes
        d.addCallback(lambda ign: self.GET(self.rooturl))
        def _check_directory_html(res, expected_type_suffix):
            pattern = re.compile(br'<td>\?%s</td>[ \t\n\r]*'
                                 b'<td>%s</td>' % (
                                     expected_type_suffix, name.encode("ascii")),
                                 re.DOTALL)
            self.failUnless(re.search(pattern, res), res)
            # find the More Info link for name, should be relative
            mo = re.search(br'<a href="([^"]+)">More Info</a>', res)
            info_url = mo.group(1)
            self.failUnlessReallyEqual(info_url, b"%s?t=info" % (name.encode("ascii"),))
        if immutable:
            d.addCallback(_check_directory_html, b"-IMM")
        else:
            d.addCallback(_check_directory_html, b"")

        d.addCallback(lambda ign: self.GET(self.rooturl+"?t=json"))
        def _check_directory_json(res, expect_rw_uri):
            data = json.loads(res)
            self.failUnlessEqual(data[0], "dirnode")
            f = data[1]["children"][name]
            self.failUnlessEqual(f[0], "unknown")
            if expect_rw_uri:
                self.failUnlessReallyEqual(to_bytes(f[1]["rw_uri"]), unknown_rwcap, data)
            else:
                self.assertThat(f[1], Not(Contains("rw_uri")))
            if immutable:
                self.failUnlessReallyEqual(to_bytes(f[1]["ro_uri"]), unknown_immcap, data)
            else:
                self.failUnlessReallyEqual(to_bytes(f[1]["ro_uri"]), unknown_rocap, data)
            self.assertThat(f[1], Contains("metadata"))
        d.addCallback(_check_directory_json, expect_rw_uri=not immutable)

        def _check_info(res, expect_rw_uri, expect_ro_uri):
            if expect_rw_uri:
                self.assertThat(res, Contains(unknown_rwcap))
            if expect_ro_uri:
                if immutable:
                    self.assertThat(res, Contains(unknown_immcap))
                else:
                    self.assertThat(res, Contains(unknown_rocap))
            else:
                self.assertThat(res, Not(Contains(unknown_rocap)))
            res = str(res, "utf-8")
            self.assertThat(res, Contains("Object Type: <span>unknown</span>"))
            self.assertThat(res, Not(Contains("Raw data as")))
            self.assertThat(res, Not(Contains("Directory writecap")))
            self.assertThat(res, Not(Contains("Checker Operations")))
            self.assertThat(res, Not(Contains("Mutable File Operations")))
            self.assertThat(res, Not(Contains("Directory Operations")))

        # FIXME: these should have expect_rw_uri=not immutable; I don't know
        # why they fail. Possibly related to ticket #922.

        d.addCallback(lambda ign: self.GET(expected_info_url))
        d.addCallback(_check_info, expect_rw_uri=False, expect_ro_uri=False)
        d.addCallback(lambda ign: self.GET("%s/%s?t=info" % (self.rooturl, name)))
        d.addCallback(_check_info, expect_rw_uri=False, expect_ro_uri=True)

        def _check_json(res, expect_rw_uri):
            data = json.loads(res)
            self.failUnlessEqual(data[0], "unknown")
            if expect_rw_uri:
                self.failUnlessReallyEqual(to_bytes(data[1]["rw_uri"]), unknown_rwcap, data)
            else:
                self.assertThat(data[1], Not(Contains("rw_uri")))

            if immutable:
                self.failUnlessReallyEqual(to_bytes(data[1]["ro_uri"]), unknown_immcap, data)
                self.failUnlessReallyEqual(data[1]["mutable"], False)
            elif expect_rw_uri:
                self.failUnlessReallyEqual(to_bytes(data[1]["ro_uri"]), unknown_rocap, data)
                self.failUnlessReallyEqual(data[1]["mutable"], True)
            else:
                self.failUnlessReallyEqual(to_bytes(data[1]["ro_uri"]), unknown_rocap, data)
                self.assertThat(data[1], Not(Contains("mutable")))

            # TODO: check metadata contents
            self.assertThat(data[1], Contains("metadata"))

        d.addCallback(lambda ign: self.GET("%s/%s?t=json" % (self.rooturl, str(name))))
        d.addCallback(_check_json, expect_rw_uri=not immutable)

        # and make sure that a read-only version of the directory can be
        # rendered too. This version will not have unknown_rwcap, whether
        # or not future_node was immutable.
        d.addCallback(lambda ign: self.GET(self.rourl))
        if immutable:
            d.addCallback(_check_directory_html, b"-IMM")
        else:
            d.addCallback(_check_directory_html, b"-RO")

        d.addCallback(lambda ign: self.GET(self.rourl+"?t=json"))
        d.addCallback(_check_directory_json, expect_rw_uri=False)

        d.addCallback(lambda ign: self.GET("%s/%s?t=json" % (self.rourl, str(name))))
        d.addCallback(_check_json, expect_rw_uri=False)

        # TODO: check that getting t=info from the Info link in the ro directory
        # works, and does not include the writecap URI.
        return d

    def test_immutable_unknown(self):
        return self.test_unknown(immutable=True)

    def test_mutant_dirnodes_are_omitted(self):
        self.basedir = "web/Grid/mutant_dirnodes_are_omitted"

        self.set_up_grid(oneshare=True)
        c = self.g.clients[0]
        nm = c.nodemaker
        self.uris = {}
        self.fileurls = {}

        lonely_uri = b"URI:LIT:n5xgk" # LIT for "one"
        mut_write_uri = b"URI:SSK:vfvcbdfbszyrsaxchgevhmmlii:euw4iw7bbnkrrwpzuburbhppuxhc3gwxv26f6imekhz7zyw2ojnq"
        mut_read_uri = b"URI:SSK-RO:e3mdrzfwhoq42hy5ubcz6rp3o4:ybyibhnp3vvwuq2vaw2ckjmesgkklfs6ghxleztqidihjyofgw7q"

        # This method tests mainly dirnode, but we'd have to duplicate code in order to
        # test the dirnode and web layers separately.

        # 'lonely' is a valid LIT child, 'ro' is a mutant child with an SSK-RO readcap,
        # and 'write-in-ro' is a mutant child with an SSK writecap in the ro_uri field.
        # When the directory is read, the mutants should be silently disposed of, leaving
        # their lonely sibling.
        # We don't test the case of a retrieving a cap from the encrypted rw_uri field,
        # because immutable directories don't have a writecap and therefore that field
        # isn't (and can't be) decrypted.
        # TODO: The field still exists in the netstring. Technically we should check what
        # happens if something is put there (_unpack_contents should raise ValueError),
        # but that can wait.

        lonely_child = nm.create_from_cap(lonely_uri)
        mutant_ro_child = nm.create_from_cap(mut_read_uri)
        mutant_write_in_ro_child = nm.create_from_cap(mut_write_uri)

        def _by_hook_or_by_crook():
            return True
        for n in [mutant_ro_child, mutant_write_in_ro_child]:
            n.is_allowed_in_immutable_directory = _by_hook_or_by_crook

        mutant_write_in_ro_child.get_write_uri    = lambda: None
        mutant_write_in_ro_child.get_readonly_uri = lambda: mut_write_uri

        kids = {u"lonely":      (lonely_child, {}),
                u"ro":          (mutant_ro_child, {}),
                u"write-in-ro": (mutant_write_in_ro_child, {}),
                }
        d = c.create_immutable_dirnode(kids)

        def _created(dn):
            self.failUnless(isinstance(dn, dirnode.DirectoryNode))
            self.assertThat(dn.is_mutable(), Equals(False))
            self.failUnless(dn.is_readonly())
            # This checks that if we somehow ended up calling dn._decrypt_rwcapdata, it would fail.
            self.assertThat(hasattr(dn._node, 'get_writekey'), Equals(False))
            rep = str(dn)
            self.assertThat(rep, Contains("RO-IMM"))
            cap = dn.get_cap()
            self.assertThat(cap.to_string(), Contains(b"CHK"))
            self.cap = cap
            self.rootnode = dn
            self.rooturl = "uri/" + url_quote(dn.get_uri())
            return download_to_data(dn._node)
        d.addCallback(_created)

        def _check_data(data):
            # Decode the netstring representation of the directory to check that all children
            # are present. This is a bit of an abstraction violation, but there's not really
            # any other way to do it given that the real DirectoryNode._unpack_contents would
            # strip the mutant children out (which is what we're trying to test, later).
            position = 0
            numkids = 0
            while position < len(data):
                entries, position = split_netstring(data, 1, position)
                entry = entries[0]
                (name_utf8, ro_uri, rwcapdata, metadata_s), subpos = split_netstring(entry, 4)
                name = name_utf8.decode("utf-8")
                self.failUnlessEqual(rwcapdata, b"")
                self.assertThat(kids, Contains(name))
                (expected_child, ign) = kids[name]
                self.failUnlessReallyEqual(ro_uri, expected_child.get_readonly_uri())
                numkids += 1

            self.failUnlessReallyEqual(numkids, 3)
            return self.rootnode.list()
        d.addCallback(_check_data)

        # Now when we use the real directory listing code, the mutants should be absent.
        def _check_kids(children):
            self.failUnlessReallyEqual(sorted(children.keys()), [u"lonely"])
            lonely_node, lonely_metadata = children[u"lonely"]

            self.failUnlessReallyEqual(lonely_node.get_write_uri(), None)
            self.failUnlessReallyEqual(lonely_node.get_readonly_uri(), lonely_uri)
        d.addCallback(_check_kids)

        d.addCallback(lambda ign: nm.create_from_cap(self.cap.to_string()))
        d.addCallback(lambda n: n.list())
        d.addCallback(_check_kids)  # again with dirnode recreated from cap

        # Make sure the lonely child can be listed in HTML...
        d.addCallback(lambda ign: self.GET(self.rooturl))
        def _check_html(res):
            soup = BeautifulSoup(res, 'html5lib')
            self.assertThat(res, Not(Contains(b"URI:SSK")))
            found = False
            for td in soup.find_all(u"td"):
                if td.text != u"FILE":
                    continue
                a = td.findNextSibling()(u"a")[0]
                self.assertThat(a[u"href"], Contains(url_quote(lonely_uri)))
                self.assertThat(a.text, Equals(u"lonely"))
                self.assertThat(a[u"rel"], Equals([u"noreferrer"]))
                self.assertThat(td.findNextSibling().findNextSibling().text, Equals(u"{}".format(len("one"))))
                found = True
                break
            self.assertThat(found, Equals(True))

            infos = list(
                a[u"href"]
                for a in soup.find_all(u"a")
                if a.text == u"More Info"
            )
            self.assertThat(infos, HasLength(1))
            self.assertThat(infos[0], EndsWith(url_quote(lonely_uri) + "?t=info"))
        d.addCallback(_check_html)

        # ... and in JSON.
        d.addCallback(lambda ign: self.GET(self.rooturl+"?t=json"))
        def _check_json(res):
            data = json.loads(res)
            self.failUnlessEqual(data[0], "dirnode")
            listed_children = data[1]["children"]
            self.failUnlessReallyEqual(sorted(listed_children.keys()), [u"lonely"])
            ll_type, ll_data = listed_children[u"lonely"]
            self.failUnlessEqual(ll_type, "filenode")
            self.assertThat(ll_data, Not(Contains("rw_uri")))
            self.failUnlessReallyEqual(to_bytes(ll_data["ro_uri"]), lonely_uri)
        d.addCallback(_check_json)
        return d

    def test_deep_check(self):
        self.basedir = "web/Grid/deep_check"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.uris = {}
        self.fileurls = {}
        DATA = b"data" * 100
        d = c0.create_dirnode()
        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.fileurls["root"] = "uri/" + url_quote(n.get_uri())
            return n.add_file(u"good", upload.Data(DATA, convergence=b""))
        d.addCallback(_stash_root_and_create_file)
        def _stash_uri(fn, which):
            self.uris[which] = fn.get_uri()
            return fn
        d.addCallback(_stash_uri, "good")
        d.addCallback(lambda ign:
                      self.rootnode.add_file(u"small",
                                             upload.Data(b"literal",
                                                        convergence=b"")))
        d.addCallback(_stash_uri, "small")
        d.addCallback(lambda ign:
                      self.rootnode.add_file(u"sick",
                                             upload.Data(DATA+b"1",
                                                        convergence=b"")))
        d.addCallback(_stash_uri, "sick")

        # this tests that deep-check and stream-manifest will ignore
        # UnknownNode instances. Hopefully this will also cover deep-stats.
        future_node = UnknownNode(unknown_rwcap, unknown_rocap)
        d.addCallback(lambda ign: self.rootnode.set_node(u"future", future_node))

        def _clobber_shares(ignored):
            self.delete_shares_numbered(self.uris["sick"], [0,1])
        d.addCallback(_clobber_shares)

        # root
        # root/good
        # root/small
        # root/sick
        # root/future

        d.addCallback(self.CHECK, "root", "t=stream-deep-check")
        def _done(res):
            try:
                units = [json.loads(line)
                         for line in res.splitlines()
                         if line]
            except ValueError:
                print("response is:", res)
                raise
            self.failUnlessReallyEqual(len(units), 5+1)
            # should be parent-first
            u0 = units[0]
            self.failUnlessEqual(u0["path"], [])
            self.failUnlessEqual(u0["type"], "directory")
            self.failUnlessReallyEqual(to_bytes(u0["cap"]), self.rootnode.get_uri())
            u0cr = u0["check-results"]
            self.failUnlessReallyEqual(u0cr["results"]["count-happiness"], 10)
            self.failUnlessReallyEqual(u0cr["results"]["count-shares-good"], 10)

            ugood = [u for u in units
                     if u["type"] == "file" and u["path"] == [u"good"]][0]
            self.failUnlessReallyEqual(to_bytes(ugood["cap"]), self.uris["good"])
            ugoodcr = ugood["check-results"]
            self.failUnlessReallyEqual(ugoodcr["results"]["count-happiness"], 10)
            self.failUnlessReallyEqual(ugoodcr["results"]["count-shares-good"], 10)

            stats = units[-1]
            self.failUnlessEqual(stats["type"], "stats")
            s = stats["stats"]
            self.failUnlessReallyEqual(s["count-immutable-files"], 2)
            self.failUnlessReallyEqual(s["count-literal-files"], 1)
            self.failUnlessReallyEqual(s["count-directories"], 1)
            self.failUnlessReallyEqual(s["count-unknown"], 1)
        d.addCallback(_done)

        d.addCallback(self.CHECK, "root", "t=stream-manifest")
        def _check_manifest(res):
            self.failUnless(res.endswith("\n"))
            units = [json.loads(t) for t in res[:-1].split("\n")]
            self.failUnlessReallyEqual(len(units), 5+1)
            self.failUnlessEqual(units[-1]["type"], "stats")
            first = units[0]
            self.failUnlessEqual(first["path"], [])
            self.failUnlessEqual(to_bytes(first["cap"]), self.rootnode.get_uri())
            self.failUnlessEqual(first["type"], "directory")
            stats = units[-1]["stats"]
            self.failUnlessReallyEqual(stats["count-immutable-files"], 2)
            self.failUnlessReallyEqual(stats["count-literal-files"], 1)
            self.failUnlessReallyEqual(stats["count-mutable-files"], 0)
            self.failUnlessReallyEqual(stats["count-immutable-files"], 2)
            self.failUnlessReallyEqual(stats["count-unknown"], 1)
        d.addCallback(_check_manifest)

        # now add root/subdir and root/subdir/grandchild, then make subdir
        # unrecoverable, then see what happens

        d.addCallback(lambda ign:
                      self.rootnode.create_subdirectory(u"subdir"))
        d.addCallback(_stash_uri, "subdir")
        d.addCallback(lambda subdir_node:
                      subdir_node.add_file(u"grandchild",
                                           upload.Data(DATA+b"2",
                                                       convergence=b"")))
        d.addCallback(_stash_uri, "grandchild")

        d.addCallback(lambda ign:
                      self.delete_shares_numbered(self.uris["subdir"],
                                                  list(range(1, 10))))

        # root
        # root/good
        # root/small
        # root/sick
        # root/future
        # root/subdir [unrecoverable]
        # root/subdir/grandchild

        # how should a streaming-JSON API indicate fatal error?
        # answer: emit ERROR: instead of a JSON string

        d.addCallback(self.CHECK, "root", "t=stream-manifest")
        def _check_broken_manifest(res):
            lines = res.splitlines()
            error_lines = [i
                           for (i,line) in enumerate(lines)
                           if line.startswith("ERROR:")]
            if not error_lines:
                self.fail("no ERROR: in output: %s" % (res,))
            first_error = error_lines[0]
            error_line = lines[first_error]
            error_msg = lines[first_error+1:]
            error_msg_s = "\n".join(error_msg) + "\n"
            self.assertThat(error_line, Contains("ERROR: UnrecoverableFileError(no recoverable versions)"))
            self.failUnless(len(error_msg) > 2, error_msg_s) # some traceback
            units = [json.loads(line) for line in lines[:first_error]]
            self.failUnlessReallyEqual(len(units), 6) # includes subdir
            last_unit = units[-1]
            self.failUnlessEqual(last_unit["path"], ["subdir"])
        d.addCallback(_check_broken_manifest)

        d.addCallback(self.CHECK, "root", "t=stream-deep-check")
        def _check_broken_deepcheck(res):
            lines = res.splitlines()
            error_lines = [i
                           for (i,line) in enumerate(lines)
                           if line.startswith("ERROR:")]
            if not error_lines:
                self.fail("no ERROR: in output: %s" % (res,))
            first_error = error_lines[0]
            error_line = lines[first_error]
            error_msg = lines[first_error+1:]
            error_msg_s = "\n".join(error_msg) + "\n"
            self.assertThat(error_line, Contains("ERROR: UnrecoverableFileError(no recoverable versions)"))
            self.failUnless(len(error_msg) > 2, error_msg_s) # some traceback
            units = [json.loads(line) for line in lines[:first_error]]
            self.failUnlessReallyEqual(len(units), 6) # includes subdir
            last_unit = units[-1]
            self.failUnlessEqual(last_unit["path"], ["subdir"])
            r = last_unit["check-results"]["results"]
            self.failUnlessReallyEqual(r["count-recoverable-versions"], 0)
            self.failUnlessReallyEqual(r["count-happiness"], 1)
            self.failUnlessReallyEqual(r["count-shares-good"], 1)
            self.failUnlessReallyEqual(r["recoverable"], False)
        d.addCallback(_check_broken_deepcheck)

        d.addErrback(self.explain_web_error)
        return d

    def test_deep_check_and_repair(self):
        self.basedir = "web/Grid/deep_check_and_repair"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.uris = {}
        self.fileurls = {}
        DATA = b"data" * 100
        d = c0.create_dirnode()
        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.fileurls["root"] = "uri/" + url_quote(n.get_uri())
            return n.add_file(u"good", upload.Data(DATA, convergence=b""))
        d.addCallback(_stash_root_and_create_file)
        def _stash_uri(fn, which):
            self.uris[which] = fn.get_uri()
        d.addCallback(_stash_uri, "good")
        d.addCallback(lambda ign:
                      self.rootnode.add_file(u"small",
                                             upload.Data(b"literal",
                                                        convergence=b"")))
        d.addCallback(_stash_uri, "small")
        d.addCallback(lambda ign:
                      self.rootnode.add_file(u"sick",
                                             upload.Data(DATA+b"1",
                                                        convergence=b"")))
        d.addCallback(_stash_uri, "sick")
        #d.addCallback(lambda ign:
        #              self.rootnode.add_file(u"dead",
        #                                     upload.Data(DATA+b"2",
        #                                                convergence=b"")))
        #d.addCallback(_stash_uri, "dead")

        #d.addCallback(lambda ign: c0.create_mutable_file("mutable"))
        #d.addCallback(lambda fn: self.rootnode.set_node(u"corrupt", fn))
        #d.addCallback(_stash_uri, "corrupt")

        def _clobber_shares(ignored):
            good_shares = self.find_uri_shares(self.uris["good"])
            self.failUnlessReallyEqual(len(good_shares), 10)
            sick_shares = self.find_uri_shares(self.uris["sick"])
            os.unlink(sick_shares[0][2])
            #dead_shares = self.find_uri_shares(self.uris["dead"])
            #for i in range(1, 10):
            #    os.unlink(dead_shares[i][2])

            #c_shares = self.find_uri_shares(self.uris["corrupt"])
            #cso = CorruptShareOptions()
            #cso.stdout = StringIO()
            #cso.parseOptions([c_shares[0][2]])
            #corrupt_share(cso)
        d.addCallback(_clobber_shares)

        # root
        # root/good   CHK, 10 shares
        # root/small  LIT
        # root/sick   CHK, 9 shares

        d.addCallback(self.CHECK, "root", "t=stream-deep-check&repair=true")
        def _done(res):
            units = [json.loads(line)
                     for line in res.splitlines()
                     if line]
            self.failUnlessReallyEqual(len(units), 4+1)
            # should be parent-first
            u0 = units[0]
            self.failUnlessEqual(u0["path"], [])
            self.failUnlessEqual(u0["type"], "directory")
            self.failUnlessReallyEqual(to_bytes(u0["cap"]), self.rootnode.get_uri())
            u0crr = u0["check-and-repair-results"]
            self.failUnlessReallyEqual(u0crr["repair-attempted"], False)
            self.failUnlessReallyEqual(u0crr["pre-repair-results"]["results"]["count-happiness"], 10)
            self.failUnlessReallyEqual(u0crr["pre-repair-results"]["results"]["count-shares-good"], 10)

            ugood = [u for u in units
                     if u["type"] == "file" and u["path"] == [u"good"]][0]
            self.failUnlessEqual(to_bytes(ugood["cap"]), self.uris["good"])
            ugoodcrr = ugood["check-and-repair-results"]
            self.failUnlessReallyEqual(ugoodcrr["repair-attempted"], False)
            self.failUnlessReallyEqual(ugoodcrr["pre-repair-results"]["results"]["count-happiness"], 10)
            self.failUnlessReallyEqual(ugoodcrr["pre-repair-results"]["results"]["count-shares-good"], 10)

            usick = [u for u in units
                     if u["type"] == "file" and u["path"] == [u"sick"]][0]
            self.failUnlessReallyEqual(to_bytes(usick["cap"]), self.uris["sick"])
            usickcrr = usick["check-and-repair-results"]
            self.failUnlessReallyEqual(usickcrr["repair-attempted"], True)
            self.failUnlessReallyEqual(usickcrr["repair-successful"], True)
            self.failUnlessReallyEqual(usickcrr["pre-repair-results"]["results"]["count-happiness"], 9)
            self.failUnlessReallyEqual(usickcrr["pre-repair-results"]["results"]["count-shares-good"], 9)
            self.failUnlessReallyEqual(usickcrr["post-repair-results"]["results"]["count-happiness"], 10)
            self.failUnlessReallyEqual(usickcrr["post-repair-results"]["results"]["count-shares-good"], 10)

            stats = units[-1]
            self.failUnlessEqual(stats["type"], "stats")
            s = stats["stats"]
            self.failUnlessReallyEqual(s["count-immutable-files"], 2)
            self.failUnlessReallyEqual(s["count-literal-files"], 1)
            self.failUnlessReallyEqual(s["count-directories"], 1)
        d.addCallback(_done)

        d.addErrback(self.explain_web_error)
        return d

    def _count_leases(self, ignored, which):
        u = self.uris[which]
        shares = self.find_uri_shares(u)
        lease_counts = []
        for shnum, serverid, fn in shares:
            sf = get_share_file(fn)
            num_leases = len(list(sf.get_leases()))
            lease_counts.append( (fn, num_leases) )
        return lease_counts

    def _assert_leasecount(self, lease_counts, expected):
        for (fn, num_leases) in lease_counts:
            if num_leases != expected:
                self.fail("expected %d leases, have %d, on %s" %
                          (expected, num_leases, fn))

    def test_add_lease(self):
        self.basedir = "web/Grid/add_lease"
        self.set_up_grid(num_clients=2, oneshare=True)
        c0 = self.g.clients[0]
        self.uris = {}
        DATA = b"data" * 100
        d = c0.upload(upload.Data(DATA, convergence=b""))
        def _stash_uri(ur, which):
            self.uris[which] = ur.get_uri()
        d.addCallback(_stash_uri, "one")
        d.addCallback(lambda ign:
                      c0.upload(upload.Data(DATA+b"1", convergence=b"")))
        d.addCallback(_stash_uri, "two")
        def _stash_mutable_uri(n, which):
            self.uris[which] = n.get_uri()
            assert isinstance(self.uris[which], bytes)
        d.addCallback(lambda ign:
            c0.create_mutable_file(publish.MutableData(DATA+b"2")))
        d.addCallback(_stash_mutable_uri, "mutable")

        def _compute_fileurls(ignored):
            self.fileurls = {}
            for which in self.uris:
                self.fileurls[which] = "uri/" + url_quote(self.uris[which])
        d.addCallback(_compute_fileurls)

        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "two")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 1)

        d.addCallback(self.CHECK, "one", "t=check") # no add-lease
        def _got_html_good(res):
            self.assertThat(res, Contains("Healthy"))
            self.assertThat(res, Not(Contains("Not Healthy")))
        d.addCallback(_got_html_good)

        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "two")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 1)

        # this CHECK uses the original client, which uses the same
        # lease-secrets, so it will just renew the original lease
        d.addCallback(self.CHECK, "one", "t=check&add-lease=true")
        d.addCallback(_got_html_good)

        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "two")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 1)

        # this CHECK uses an alternate client, which adds a second lease
        d.addCallback(self.CHECK, "one", "t=check&add-lease=true", clientnum=1)
        d.addCallback(_got_html_good)

        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 2)
        d.addCallback(self._count_leases, "two")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 1)

        d.addCallback(self.CHECK, "mutable", "t=check&add-lease=true")
        d.addCallback(_got_html_good)

        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 2)
        d.addCallback(self._count_leases, "two")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 1)

        d.addCallback(self.CHECK, "mutable", "t=check&add-lease=true",
                      clientnum=1)
        d.addCallback(_got_html_good)

        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 2)
        d.addCallback(self._count_leases, "two")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 2)

        d.addErrback(self.explain_web_error)
        return d

    def test_deep_add_lease(self):
        self.basedir = "web/Grid/deep_add_lease"
        self.set_up_grid(num_clients=2, oneshare=True)
        c0 = self.g.clients[0]
        self.uris = {}
        self.fileurls = {}
        DATA = b"data" * 100
        d = c0.create_dirnode()
        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.uris["root"] = n.get_uri()
            self.fileurls["root"] = "uri/" + url_quote(n.get_uri())
            return n.add_file(u"one", upload.Data(DATA, convergence=b""))
        d.addCallback(_stash_root_and_create_file)
        def _stash_uri(fn, which):
            self.uris[which] = fn.get_uri()
        d.addCallback(_stash_uri, "one")
        d.addCallback(lambda ign:
                      self.rootnode.add_file(u"small",
                                             upload.Data(b"literal",
                                                        convergence=b"")))
        d.addCallback(_stash_uri, "small")

        d.addCallback(lambda ign:
            c0.create_mutable_file(publish.MutableData(b"mutable")))
        d.addCallback(lambda fn: self.rootnode.set_node(u"mutable", fn))
        d.addCallback(_stash_uri, "mutable")

        d.addCallback(self.CHECK, "root", "t=stream-deep-check") # no add-lease
        def _done(res):
            units = [json.loads(line)
                     for line in res.splitlines()
                     if line]
            # root, one, small, mutable,   stats
            self.failUnlessReallyEqual(len(units), 4+1)
        d.addCallback(_done)

        d.addCallback(self._count_leases, "root")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 1)

        d.addCallback(self.CHECK, "root", "t=stream-deep-check&add-lease=true")
        d.addCallback(_done)

        d.addCallback(self._count_leases, "root")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 1)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 1)

        d.addCallback(self.CHECK, "root", "t=stream-deep-check&add-lease=true",
                      clientnum=1)
        d.addCallback(_done)

        d.addCallback(self._count_leases, "root")
        d.addCallback(self._assert_leasecount, 2)
        d.addCallback(self._count_leases, "one")
        d.addCallback(self._assert_leasecount, 2)
        d.addCallback(self._count_leases, "mutable")
        d.addCallback(self._assert_leasecount, 2)

        d.addErrback(self.explain_web_error)
        return d


    def test_exceptions(self):
        self.basedir = "web/Grid/exceptions"
        self.set_up_grid(num_clients=1, num_servers=2)
        c0 = self.g.clients[0]
        c0.encoding_params['happy'] = 2
        self.fileurls = {}
        DATA = b"data" * 100
        d = c0.create_dirnode()
        def _stash_root(n):
            self.fileurls["root"] = "uri/" + url_quote(n.get_uri())
            self.fileurls["imaginary"] = self.fileurls["root"] + "/imaginary"
            return n
        d.addCallback(_stash_root)
        d.addCallback(lambda ign: c0.upload(upload.Data(DATA, convergence=b"")))
        def _stash_bad(ur):
            self.fileurls["1share"] = "uri/" + url_quote(ur.get_uri())
            self.delete_shares_numbered(ur.get_uri(), list(range(1,10)))

            u = uri.from_string(ur.get_uri())
            u.key = testutil.flip_bit(u.key, 0)
            baduri = u.to_string()
            self.fileurls["0shares"] = "uri/" + url_quote(baduri)
        d.addCallback(_stash_bad)
        d.addCallback(lambda ign: c0.create_dirnode())
        def _mangle_dirnode_1share(n):
            u = n.get_uri()
            url = self.fileurls["dir-1share"] = "uri/" + url_quote(u)
            self.fileurls["dir-1share-json"] = url + "?t=json"
            self.delete_shares_numbered(u, list(range(1,10)))
        d.addCallback(_mangle_dirnode_1share)
        d.addCallback(lambda ign: c0.create_dirnode())
        def _mangle_dirnode_0share(n):
            u = n.get_uri()
            url = self.fileurls["dir-0share"] = "uri/" + url_quote(u)
            self.fileurls["dir-0share-json"] = url + "?t=json"
            self.delete_shares_numbered(u, list(range(0,10)))
        d.addCallback(_mangle_dirnode_0share)

        # NotEnoughSharesError should be reported sensibly, with a
        # text/plain explanation of the problem, and perhaps some
        # information on which shares *could* be found.

        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET unrecoverable",
                                           410, "Gone", "NoSharesError",
                                           self.GET, self.fileurls["0shares"]))
        def _check_zero_shares(body):
            body = str(body, "utf-8")
            self.assertThat(body, Not(Contains("<html>")))
            body = " ".join(body.strip().split())
            exp = ("NoSharesError: no shares could be found. "
                   "Zero shares usually indicates a corrupt URI, or that "
                   "no servers were connected, but it might also indicate "
                   "severe corruption. You should perform a filecheck on "
                   "this object to learn more. The full error message is: "
                   "no shares (need 3). Last failure: None")
            self.assertEqual(exp, body)
        d.addCallback(_check_zero_shares)


        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET 1share",
                                           410, "Gone", "NotEnoughSharesError",
                                           self.GET, self.fileurls["1share"]))
        def _check_one_share(body):
            body = str(body, "utf-8")
            self.assertThat(body, Not(Contains("<html>")))
            body = " ".join(body.strip().split())
            msgbase = ("NotEnoughSharesError: This indicates that some "
                       "servers were unavailable, or that shares have been "
                       "lost to server departure, hard drive failure, or disk "
                       "corruption. You should perform a filecheck on "
                       "this object to learn more. The full error message is:"
                       )
            msg1 = msgbase + (" ran out of shares:"
                              " complete=sh0"
                              " pending="
                              " overdue= unused= need 3. Last failure: None")
            msg2 = msgbase + (" ran out of shares:"
                              " complete="
                              " pending=Share(sh0-on-ysbz4st7)"
                              " overdue= unused= need 3. Last failure: None")
            self.failUnless(body == msg1 or body == msg2, body)
        d.addCallback(_check_one_share)

        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET imaginary",
                                           404, "Not Found", None,
                                           self.GET, self.fileurls["imaginary"]))
        def _missing_child(body):
            body = str(body, "utf-8")
            self.assertThat(body, Contains("No such child: imaginary"))
        d.addCallback(_missing_child)

        d.addCallback(lambda ignored: self.GET_unicode(self.fileurls["dir-0share"]))
        def _check_0shares_dir_html(body):
            self.assertThat(body, Contains(DIR_HTML_TAG))
            # we should see the regular page, but without the child table or
            # the dirops forms
            body = " ".join(body.strip().split())
            self.assertThat(body, Contains('href="?t=info">More info on this directory'))
            exp = ("UnrecoverableFileError: the directory (or mutable file) "
                   "could not be retrieved, because there were insufficient "
                   "good shares. This might indicate that no servers were "
                   "connected, insufficient servers were connected, the URI "
                   "was corrupt, or that shares have been lost due to server "
                   "departure, hard drive failure, or disk corruption. You "
                   "should perform a filecheck on this object to learn more.")
            self.assertThat(body, Contains(exp))
            self.assertThat(body, Contains("No upload forms: directory is unreadable"))
        d.addCallback(_check_0shares_dir_html)

        d.addCallback(lambda ignored: self.GET_unicode(self.fileurls["dir-1share"]))
        def _check_1shares_dir_html(body):
            # at some point, we'll split UnrecoverableFileError into 0-shares
            # and some-shares like we did for immutable files (since there
            # are different sorts of advice to offer in each case). For now,
            # they present the same way.
            self.assertThat(body, Contains(DIR_HTML_TAG))
            body = " ".join(body.strip().split())
            self.assertThat(body, Contains('href="?t=info">More info on this directory'))
            exp = ("UnrecoverableFileError: the directory (or mutable file) "
                   "could not be retrieved, because there were insufficient "
                   "good shares. This might indicate that no servers were "
                   "connected, insufficient servers were connected, the URI "
                   "was corrupt, or that shares have been lost due to server "
                   "departure, hard drive failure, or disk corruption. You "
                   "should perform a filecheck on this object to learn more.")
            self.assertThat(body, Contains(exp))
            self.assertThat(body, Contains("No upload forms: directory is unreadable"))
        d.addCallback(_check_1shares_dir_html)

        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET dir-0share-json",
                                           410, "Gone", "UnrecoverableFileError",
                                           self.GET,
                                           self.fileurls["dir-0share-json"]))
        def _check_unrecoverable_file(body):
            body = str(body, "utf-8")
            self.assertThat(body, Not(Contains("<html>")))
            body = " ".join(body.strip().split())
            exp = ("UnrecoverableFileError: the directory (or mutable file) "
                   "could not be retrieved, because there were insufficient "
                   "good shares. This might indicate that no servers were "
                   "connected, insufficient servers were connected, the URI "
                   "was corrupt, or that shares have been lost due to server "
                   "departure, hard drive failure, or disk corruption. You "
                   "should perform a filecheck on this object to learn more.")
            self.assertThat(body, Contains(exp))
        d.addCallback(_check_unrecoverable_file)

        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET dir-1share-json",
                                           410, "Gone", "UnrecoverableFileError",
                                           self.GET,
                                           self.fileurls["dir-1share-json"]))
        d.addCallback(_check_unrecoverable_file)

        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET imaginary",
                                           404, "Not Found", None,
                                           self.GET, self.fileurls["imaginary"]))

        # attach a webapi child that throws a random error, to test how it
        # gets rendered.
        w = c0.getServiceNamed("webish")
        w.root.putChild(b"ERRORBOOM", ErrorBoom())

        # "Accept: */*" :        should get a text/html stack trace
        # "Accept: text/plain" : should get a text/plain stack trace
        # "Accept: text/plain, application/octet-stream" : text/plain (CLI)
        # no Accept header:      should get a text/html stack trace

        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET errorboom_html",
                                           500, "Internal Server Error", None,
                                           self.GET, "ERRORBOOM",
                                           headers={"accept": "*/*"}))
        def _internal_error_html1(body):
            body = str(body, "utf-8")
            self.assertThat("expected HTML, not '%s'" % body, Contains("<html>"))
        d.addCallback(_internal_error_html1)

        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET errorboom_text",
                                           500, "Internal Server Error", None,
                                           self.GET, "ERRORBOOM",
                                           headers={"accept": "text/plain"}))
        def _internal_error_text2(body):
            body = str(body, "utf-8")
            self.assertThat(body, Not(Contains("<html>")))
            self.failUnless(body.startswith("Traceback "), body)

        d.addCallback(_internal_error_text2)

        CLI_accepts = "text/plain, application/octet-stream"
        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET errorboom_text",
                                           500, "Internal Server Error", None,
                                           self.GET, "ERRORBOOM",
                                           headers={"accept": CLI_accepts}))
        def _internal_error_text3(body):
            body = str(body, "utf-8")
            self.assertThat(body, Not(Contains("<html>")))
            self.failUnless(body.startswith("Traceback "), body)
        d.addCallback(_internal_error_text3)

        d.addCallback(lambda ignored:
                      self.shouldHTTPError("GET errorboom_text",
                                           500, "Internal Server Error", None,
                                           self.GET, "ERRORBOOM"))
        def _internal_error_html4(body):
            self.assertThat(body, Contains(b"<html>"))
        d.addCallback(_internal_error_html4)

        def _flush_errors(res):
            # Trial: please ignore the CompletelyUnhandledError in the logs
            flush_logged_errors(CompletelyUnhandledError)
            return res
        d.addBoth(_flush_errors)

        return d

    def test_blacklist(self):
        # download from a blacklisted URI, get an error
        self.basedir = "web/Grid/blacklist"
        self.set_up_grid(oneshare=True)
        c0 = self.g.clients[0]
        fn = c0.config.get_config_path("access.blacklist")
        self.uris = {}
        DATA = b"off-limits " * 50

        d = c0.upload(upload.Data(DATA, convergence=b""))
        def _stash_uri_and_create_dir(ur):
            self.uri = ur.get_uri()
            self.url = b"uri/"+self.uri
            u = uri.from_string_filenode(self.uri)
            self.si = u.get_storage_index()
            childnode = c0.create_node_from_uri(self.uri, None)
            return c0.create_dirnode({u"blacklisted.txt": (childnode,{}) })
        d.addCallback(_stash_uri_and_create_dir)
        def _stash_dir(node):
            self.dir_node = node
            self.dir_uri = node.get_uri()
            self.dir_url = b"uri/"+self.dir_uri
        d.addCallback(_stash_dir)
        d.addCallback(lambda ign: self.GET_unicode(self.dir_url, followRedirect=True))
        def _check_dir_html(body):
            self.assertThat(body, Contains(DIR_HTML_TAG))
            self.assertThat(body, Contains("blacklisted.txt</a>"))
        d.addCallback(_check_dir_html)
        d.addCallback(lambda ign: self.GET(self.url))
        d.addCallback(lambda body: self.failUnlessEqual(DATA, body))

        def _blacklist(ign):
            f = open(fn, "w")
            f.write(" # this is a comment\n")
            f.write(" \n")
            f.write("\n") # also exercise blank lines
            f.write("%s off-limits to you\n" % (str(base32.b2a(self.si), "ascii"),))
            f.close()
            # clients should be checking the blacklist each time, so we don't
            # need to restart the client
        d.addCallback(_blacklist)
        d.addCallback(lambda ign: self.shouldHTTPError("get_from_blacklisted_uri",
                                                       403, "Forbidden",
                                                       "Access Prohibited: off-limits",
                                                       self.GET, self.url))

        # We should still be able to list the parent directory, in HTML...
        d.addCallback(lambda ign: self.GET_unicode(self.dir_url, followRedirect=True))
        def _check_dir_html2(body):
            self.assertThat(body, Contains(DIR_HTML_TAG))
            self.assertThat(body, Contains("blacklisted.txt</strike>"))
        d.addCallback(_check_dir_html2)

        # ... and in JSON (used by CLI).
        d.addCallback(lambda ign: self.GET(self.dir_url+b"?t=json", followRedirect=True))
        def _check_dir_json(res):
            data = json.loads(res)
            self.failUnless(isinstance(data, list), data)
            self.failUnlessEqual(data[0], "dirnode")
            self.failUnless(isinstance(data[1], dict), data)
            self.assertThat(data[1], Contains("children"))
            self.assertThat(data[1]["children"], Contains("blacklisted.txt"))
            childdata = data[1]["children"]["blacklisted.txt"]
            self.failUnless(isinstance(childdata, list), data)
            self.failUnlessEqual(childdata[0], "filenode")
            self.failUnless(isinstance(childdata[1], dict), data)
        d.addCallback(_check_dir_json)

        def _unblacklist(ign):
            open(fn, "w").close()
            # the Blacklist object watches mtime to tell when the file has
            # changed, but on windows this test will run faster than the
            # filesystem's mtime resolution. So we edit Blacklist.last_mtime
            # to force a reload.
            self.g.clients[0].blacklist.last_mtime -= 2.0
        d.addCallback(_unblacklist)

        # now a read should work
        d.addCallback(lambda ign: self.GET(self.url))
        d.addCallback(lambda body: self.failUnlessEqual(DATA, body))

        # read again to exercise the blacklist-is-unchanged logic
        d.addCallback(lambda ign: self.GET(self.url))
        d.addCallback(lambda body: self.failUnlessEqual(DATA, body))

        # now add a blacklisted directory, and make sure files under it are
        # refused too
        def _add_dir(ign):
            childnode = c0.create_node_from_uri(self.uri, None)
            return c0.create_dirnode({u"child": (childnode,{}) })
        d.addCallback(_add_dir)
        def _get_dircap(dn):
            self.dir_si_b32 = base32.b2a(dn.get_storage_index())
            self.dir_url_base = b"uri/"+dn.get_write_uri()
            self.dir_url_json1 = b"uri/"+dn.get_write_uri()+b"?t=json"
            self.dir_url_json2 = b"uri/"+dn.get_write_uri()+b"?t=json"
            self.dir_url_json_ro = b"uri/"+dn.get_readonly_uri()+b"?t=json"
            self.child_url = b"uri/"+dn.get_readonly_uri()+b"/child"
        d.addCallback(_get_dircap)
        d.addCallback(lambda ign: self.GET(self.dir_url_base, followRedirect=True))
        d.addCallback(lambda body: self.assertThat(str(body, "utf-8"), Contains(DIR_HTML_TAG)))
        d.addCallback(lambda ign: self.GET(self.dir_url_json1))
        d.addCallback(lambda res: json.loads(res))  # just check it decodes
        d.addCallback(lambda ign: self.GET(self.dir_url_json2))
        d.addCallback(lambda res: json.loads(res))  # just check it decodes
        d.addCallback(lambda ign: self.GET(self.dir_url_json_ro))
        d.addCallback(lambda res: json.loads(res))  # just check it decodes
        d.addCallback(lambda ign: self.GET(self.child_url))
        d.addCallback(lambda body: self.failUnlessEqual(DATA, body))

        def _block_dir(ign):
            f = open(fn, "wb")
            f.write(b"%s %s\n" % (self.dir_si_b32, b"dir-off-limits to you"))
            f.close()
            self.g.clients[0].blacklist.last_mtime -= 2.0
        d.addCallback(_block_dir)
        d.addCallback(lambda ign: self.shouldHTTPError("get_from_blacklisted_dir base",
                                                       403, "Forbidden",
                                                       "Access Prohibited: dir-off-limits",
                                                       self.GET, self.dir_url_base))
        d.addCallback(lambda ign: self.shouldHTTPError("get_from_blacklisted_dir json1",
                                                       403, "Forbidden",
                                                       "Access Prohibited: dir-off-limits",
                                                       self.GET, self.dir_url_json1))
        d.addCallback(lambda ign: self.shouldHTTPError("get_from_blacklisted_dir json2",
                                                       403, "Forbidden",
                                                       "Access Prohibited: dir-off-limits",
                                                       self.GET, self.dir_url_json2))
        d.addCallback(lambda ign: self.shouldHTTPError("get_from_blacklisted_dir json_ro",
                                                       403, "Forbidden",
                                                       "Access Prohibited: dir-off-limits",
                                                       self.GET, self.dir_url_json_ro))
        d.addCallback(lambda ign: self.shouldHTTPError("get_from_blacklisted_dir child",
                                                       403, "Forbidden",
                                                       "Access Prohibited: dir-off-limits",
                                                       self.GET, self.child_url))
        return d


