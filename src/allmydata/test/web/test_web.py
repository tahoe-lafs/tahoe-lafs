from __future__ import print_function

import os.path, re, urllib, time, cgi
import json
import treq
import mock

from bs4 import BeautifulSoup

from twisted.application import service
from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet.defer import inlineCallbacks, returnValue, maybeDeferred
from twisted.internet.task import Clock
from twisted.web import client, error, http
from twisted.python import failure, log

from nevow.context import WebContext
from nevow.inevow import (
    ICanHandleException,
    IRequest,
    IData,
)
from nevow.util import escapeToXML
from nevow.loaders import stan
from nevow.testutil import FakeRequest
from nevow.appserver import (
    processingFailed,
    DefaultExceptionHandler,
)

from allmydata import interfaces, uri, webish
from allmydata.storage_client import StorageFarmBroker, StubServer
from allmydata.immutable import upload
from allmydata.immutable.downloader.status import DownloadStatus
from allmydata.dirnode import DirectoryNode
from allmydata.nodemaker import NodeMaker
from allmydata.frontends.magic_folder import QueuedItem
from allmydata.web import status
from allmydata.web.common import WebError, MultiFormatPage
from allmydata.util import fileutil, base32, hashutil
from allmydata.util.consumer import download_to_data
from allmydata.util.encodingutil import to_str
from ...util.connection_status import ConnectionStatus
from ..common import (
    EMPTY_CLIENT_CONFIG,
    FakeCHKFileNode,
    FakeMutableFileNode,
    create_chk_filenode,
    WebErrorMixin,
    make_mutable_file_uri,
    create_mutable_filenode,
)
from .common import (
    assert_soup_has_favicon,
    assert_soup_has_text,
)
from allmydata.interfaces import IMutableFileNode, SDMF_VERSION, MDMF_VERSION
from allmydata.mutable import servermap, publish, retrieve
from .. import common_util as testutil
from ..common_web import (
    do_http,
    Error,
)
from allmydata.client import _Client, SecretHolder
from .common import unknown_rwcap, unknown_rocap, unknown_immcap, FAVICON_MARKUP
from ..status import FakeStatus

# create a fake uploader/downloader, and a couple of fake dirnodes, then
# create a webserver that works against them

class FakeStatsProvider(object):
    def get_stats(self):
        stats = {'stats': {}, 'counters': {}}
        return stats

class FakeNodeMaker(NodeMaker):
    encoding_params = {
        'k': 3,
        'n': 10,
        'happy': 7,
        'max_segment_size':128*1024 # 1024=KiB
    }
    def _create_lit(self, cap):
        return FakeCHKFileNode(cap, self.all_contents)
    def _create_immutable(self, cap):
        return FakeCHKFileNode(cap, self.all_contents)
    def _create_mutable(self, cap):
        return FakeMutableFileNode(None, None,
                                   self.encoding_params, None,
                                   self.all_contents).init_from_cap(cap)
    def create_mutable_file(self, contents="", keysize=None,
                            version=SDMF_VERSION):
        n = FakeMutableFileNode(None, None, self.encoding_params, None,
                                self.all_contents)
        return n.create(contents, version=version)

class FakeUploader(service.Service):
    name = "uploader"
    helper_furl = None
    helper_connected = False

    def upload(self, uploadable, **kw):
        d = uploadable.get_size()
        d.addCallback(lambda size: uploadable.read(size))
        def _got_data(datav):
            data = "".join(datav)
            n = create_chk_filenode(data, self.all_contents)
            ur = upload.UploadResults(file_size=len(data),
                                      ciphertext_fetched=0,
                                      preexisting_shares=0,
                                      pushed_shares=10,
                                      sharemap={},
                                      servermap={},
                                      timings={},
                                      uri_extension_data={},
                                      uri_extension_hash="fake",
                                      verifycapstr="fakevcap")
            ur.set_uri(n.get_uri())
            return ur
        d.addCallback(_got_data)
        return d

    def get_helper_info(self):
        return (self.helper_furl, self.helper_connected)


def create_test_queued_item(relpath_u, history=[]):
    progress = mock.Mock()
    progress.progress = 100.0
    item = QueuedItem(relpath_u, progress, 1234)
    for the_status, timestamp in history:
        item.set_status(the_status, current_time=timestamp)
    return item


class FakeMagicFolder(object):
    def __init__(self):
        self.uploader = FakeStatus()
        self.downloader = FakeStatus()

    def get_public_status(self):
        return (
            True,
            [
                'a magic-folder status message'
            ],
        )


def build_one_ds():
    ds = DownloadStatus("storage_index", 1234)
    now = time.time()

    serverA = StubServer(hashutil.tagged_hash("foo", "serverid_a")[:20])
    serverB = StubServer(hashutil.tagged_hash("foo", "serverid_b")[:20])
    storage_index = hashutil.storage_index_hash("SI")
    e0 = ds.add_segment_request(0, now)
    e0.activate(now+0.5)
    e0.deliver(now+1, 0, 100, 0.5) # when, start,len, decodetime
    e1 = ds.add_segment_request(1, now+2)
    e1.error(now+3)
    # two outstanding requests
    e2 = ds.add_segment_request(2, now+4)
    e3 = ds.add_segment_request(3, now+5)
    del e2,e3 # hush pyflakes

    # simulate a segment which gets delivered faster than a system clock tick (ticket #1166)
    e = ds.add_segment_request(4, now)
    e.activate(now)
    e.deliver(now, 0, 140, 0.5)

    e = ds.add_dyhb_request(serverA, now)
    e.finished([1,2], now+1)
    e = ds.add_dyhb_request(serverB, now+2) # left unfinished

    e = ds.add_read_event(0, 120, now)
    e.update(60, 0.5, 0.1) # bytes, decrypttime, pausetime
    e.finished(now+1)
    e = ds.add_read_event(120, 30, now+2) # left unfinished

    e = ds.add_block_request(serverA, 1, 100, 20, now)
    e.finished(20, now+1)
    e = ds.add_block_request(serverB, 1, 120, 30, now+1) # left unfinished

    # make sure that add_read_event() can come first too
    ds1 = DownloadStatus(storage_index, 1234)
    e = ds1.add_read_event(0, 120, now)
    e.update(60, 0.5, 0.1) # bytes, decrypttime, pausetime
    e.finished(now+1)

    return ds

class FakeHistory(object):
    _all_upload_status = [upload.UploadStatus()]
    _all_download_status = [build_one_ds()]
    _all_mapupdate_statuses = [servermap.UpdateStatus()]
    _all_publish_statuses = [publish.PublishStatus()]
    _all_retrieve_statuses = [retrieve.RetrieveStatus()]

    def list_all_upload_statuses(self):
        return self._all_upload_status
    def list_all_download_statuses(self):
        return self._all_download_status
    def list_all_mapupdate_statuses(self):
        return self._all_mapupdate_statuses
    def list_all_publish_statuses(self):
        return self._all_publish_statuses
    def list_all_retrieve_statuses(self):
        return self._all_retrieve_statuses
    def list_all_helper_statuses(self):
        return []

class FakeDisplayableServer(StubServer):
    def __init__(self, serverid, nickname, connected,
                 last_connect_time, last_loss_time, last_rx_time):
        StubServer.__init__(self, serverid)
        self.announcement = {"my-version": "tahoe-lafs-fake",
                             "service-name": "storage",
                             "nickname": nickname}
        self.connected = connected
        self.last_loss_time = last_loss_time
        self.last_rx_time = last_rx_time
        self.last_connect_time = last_connect_time

    def on_status_changed(self, cb): # TODO: try to remove me
        cb(self)
    def is_connected(self): # TODO: remove me
        return self.connected
    def get_version(self):
        return {
            "application-version": "1.0"
        }
    def get_permutation_seed(self):
        return ""
    def get_announcement(self):
        return self.announcement
    def get_nickname(self):
        return self.announcement["nickname"]
    def get_available_space(self):
        return 123456
    def get_connection_status(self):
        return ConnectionStatus(self.connected, "summary", {},
                                self.last_connect_time, self.last_rx_time)

class FakeBucketCounter(object):
    def get_state(self):
        return {"last-complete-bucket-count": 0}
    def get_progress(self):
        return {"estimated-time-per-cycle": 0,
                "cycle-in-progress": False,
                "remaining-wait-time": 0}

class FakeLeaseChecker(object):
    def __init__(self):
        self.expiration_enabled = False
        self.mode = "age"
        self.override_lease_duration = None
        self.sharetypes_to_expire = {}
    def get_state(self):
        return {"history": None}
    def get_progress(self):
        return {"estimated-time-per-cycle": 0,
                "cycle-in-progress": False,
                "remaining-wait-time": 0}

class FakeStorageServer(service.MultiService):
    name = 'storage'
    def __init__(self, nodeid, nickname):
        service.MultiService.__init__(self)
        self.my_nodeid = nodeid
        self.nickname = nickname
        self.bucket_counter = FakeBucketCounter()
        self.lease_checker = FakeLeaseChecker()
    def get_stats(self):
        return {"storage_server.accepting_immutable_shares": False}
    def on_status_changed(self, cb):
        cb(self)

class FakeClient(_Client):
    def __init__(self):
        # don't upcall to Client.__init__, since we only want to initialize a
        # minimal subset
        service.MultiService.__init__(self)
        self._magic_folders = dict()
        self.all_contents = {}
        self.nodeid = "fake_nodeid"
        self.nickname = u"fake_nickname \u263A"
        self.introducer_furls = []
        self.introducer_clients = []
        self.stats_provider = FakeStatsProvider()
        self._secret_holder = SecretHolder("lease secret", "convergence secret")
        self.helper = None
        self.convergence = "some random string"
        self.storage_broker = StorageFarmBroker(
            permute_peers=True,
            tub_maker=None,
            node_config=EMPTY_CLIENT_CONFIG,
        )
        # fake knowledge of another server
        self.storage_broker.test_add_server("other_nodeid",
            FakeDisplayableServer(
                serverid="other_nodeid", nickname=u"other_nickname \u263B", connected = True,
                last_connect_time = 10, last_loss_time = 20, last_rx_time = 30))
        self.storage_broker.test_add_server("disconnected_nodeid",
            FakeDisplayableServer(
                serverid="disconnected_nodeid", nickname=u"disconnected_nickname \u263B", connected = False,
                last_connect_time = None, last_loss_time = 25, last_rx_time = 35))
        self.introducer_client = None
        self.history = FakeHistory()
        self.uploader = FakeUploader()
        self.uploader.all_contents = self.all_contents
        self.uploader.setServiceParent(self)
        self.blacklist = None
        self.nodemaker = FakeNodeMaker(None, self._secret_holder, None,
                                       self.uploader, None,
                                       None, None, None)
        self.nodemaker.all_contents = self.all_contents
        self.mutable_file_default = SDMF_VERSION
        self.addService(FakeStorageServer(self.nodeid, self.nickname))

    def get_long_nodeid(self):
        return "v0-nodeid"
    def get_long_tubid(self):
        return "tubid"

    def get_auth_token(self):
        return 'a fake debug auth token'

    def startService(self):
        return service.MultiService.startService(self)
    def stopService(self):
        return service.MultiService.stopService(self)

    MUTABLE_SIZELIMIT = FakeMutableFileNode.MUTABLE_SIZELIMIT

class WebMixin(testutil.TimezoneMixin):
    def setUp(self):
        self.setTimezone('UTC-13:00')
        self.s = FakeClient()
        self.s.startService()
        self.staticdir = self.mktemp()
        self.clock = Clock()
        self.fakeTime = 86460 # 1d 0h 1m 0s
        self.ws = webish.WebishServer(self.s, "0", staticdir=self.staticdir,
                                      clock=self.clock, now_fn=lambda:self.fakeTime)
        self.ws.setServiceParent(self.s)
        self.webish_port = self.ws.getPortnum()
        self.webish_url = self.ws.getURL()
        assert self.webish_url.endswith("/")
        self.webish_url = self.webish_url[:-1] # these tests add their own /

        l = [ self.s.create_dirnode() for x in range(6) ]
        d = defer.DeferredList(l)
        def _then(res):
            self.public_root = res[0][1]
            assert interfaces.IDirectoryNode.providedBy(self.public_root), res
            self.public_url = "/uri/" + self.public_root.get_uri()
            self.private_root = res[1][1]

            foo = res[2][1]
            self._foo_node = foo
            self._foo_uri = foo.get_uri()
            self._foo_readonly_uri = foo.get_readonly_uri()
            self._foo_verifycap = foo.get_verify_cap().to_string()
            # NOTE: we ignore the deferred on all set_uri() calls, because we
            # know the fake nodes do these synchronously
            self.public_root.set_uri(u"foo", foo.get_uri(),
                                     foo.get_readonly_uri())

            self.BAR_CONTENTS, n, self._bar_txt_uri = self.makefile(0)
            foo.set_uri(u"bar.txt", self._bar_txt_uri, self._bar_txt_uri)
            self._bar_txt_verifycap = n.get_verify_cap().to_string()

            # sdmf
            # XXX: Do we ever use this?
            self.BAZ_CONTENTS, n, self._baz_txt_uri, self._baz_txt_readonly_uri = self.makefile_mutable(0)

            foo.set_uri(u"baz.txt", self._baz_txt_uri, self._baz_txt_readonly_uri)

            # mdmf
            self.QUUX_CONTENTS, n, self._quux_txt_uri, self._quux_txt_readonly_uri = self.makefile_mutable(0, mdmf=True)
            assert self._quux_txt_uri.startswith("URI:MDMF")
            foo.set_uri(u"quux.txt", self._quux_txt_uri, self._quux_txt_readonly_uri)

            foo.set_uri(u"empty", res[3][1].get_uri(),
                        res[3][1].get_readonly_uri())
            sub_uri = res[4][1].get_uri()
            self._sub_uri = sub_uri
            foo.set_uri(u"sub", sub_uri, sub_uri)
            sub = self.s.create_node_from_uri(sub_uri)
            self._sub_node = sub

            _ign, n, blocking_uri = self.makefile(1)
            foo.set_uri(u"blockingfile", blocking_uri, blocking_uri)

            # filenode to test for html encoding issues
            self._htmlname_unicode = u"<&weirdly'named\"file>>>_<iframe />.txt"
            self._htmlname_raw = self._htmlname_unicode.encode('utf-8')
            self._htmlname_urlencoded = urllib.quote(self._htmlname_raw, '')
            self._htmlname_escaped = escapeToXML(self._htmlname_raw)
            self._htmlname_escaped_attr = cgi.escape(self._htmlname_raw, quote=True)
            self._htmlname_escaped_double = escapeToXML(cgi.escape(self._htmlname_raw, quote=True))
            self.HTMLNAME_CONTENTS, n, self._htmlname_txt_uri = self.makefile(0)
            foo.set_uri(self._htmlname_unicode, self._htmlname_txt_uri, self._htmlname_txt_uri)

            unicode_filename = u"n\u00fc.txt" # n u-umlaut . t x t
            # ok, unicode calls it LATIN SMALL LETTER U WITH DIAERESIS but I
            # still think of it as an umlaut
            foo.set_uri(unicode_filename, self._bar_txt_uri, self._bar_txt_uri)

            self.SUBBAZ_CONTENTS, n, baz_file = self.makefile(2)
            self._baz_file_uri = baz_file
            sub.set_uri(u"baz.txt", baz_file, baz_file)

            _ign, n, self._bad_file_uri = self.makefile(3)
            # this uri should not be downloadable
            del self.s.all_contents[self._bad_file_uri]

            rodir = res[5][1]
            self.public_root.set_uri(u"reedownlee", rodir.get_readonly_uri(),
                                     rodir.get_readonly_uri())
            rodir.set_uri(u"nor", baz_file, baz_file)

            # public/
            # public/foo/
            # public/foo/bar.txt
            # public/foo/baz.txt
            # public/foo/quux.txt
            # public/foo/blockingfile
            # public/foo/<&weirdly'named\"file>>>_<iframe />.txt
            # public/foo/empty/
            # public/foo/sub/
            # public/foo/sub/baz.txt
            # public/reedownlee/
            # public/reedownlee/nor
            self.NEWFILE_CONTENTS = "newfile contents\n"

            return foo.get_metadata_for(u"bar.txt")
        d.addCallback(_then)
        def _got_metadata(metadata):
            self._bar_txt_metadata = metadata
        d.addCallback(_got_metadata)
        return d

    def get_all_contents(self):
        return self.s.all_contents

    def makefile(self, number):
        contents = "contents of file %s\n" % number
        n = create_chk_filenode(contents, self.get_all_contents())
        return contents, n, n.get_uri()

    def makefile_mutable(self, number, mdmf=False):
        contents = "contents of mutable file %s\n" % number
        n = create_mutable_filenode(contents, mdmf, self.s.all_contents)
        return contents, n, n.get_uri(), n.get_readonly_uri()

    def tearDown(self):
        return self.s.stopService()

    def failUnlessIsBarDotTxt(self, res):
        self.failUnlessReallyEqual(res, self.BAR_CONTENTS, res)

    def failUnlessIsQuuxDotTxt(self, res):
        self.failUnlessReallyEqual(res, self.QUUX_CONTENTS, res)

    def failUnlessIsBazDotTxt(self, res):
        self.failUnlessReallyEqual(res, self.BAZ_CONTENTS, res)

    def failUnlessIsSubBazDotTxt(self, res):
        self.failUnlessReallyEqual(res, self.SUBBAZ_CONTENTS, res)

    def failUnlessIsBarJSON(self, res):
        data = json.loads(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "filenode")
        self.failUnless(isinstance(data[1], dict))
        self.failIf(data[1]["mutable"])
        self.failIfIn("rw_uri", data[1]) # immutable
        self.failUnlessReallyEqual(to_str(data[1]["ro_uri"]), self._bar_txt_uri)
        self.failUnlessReallyEqual(to_str(data[1]["verify_uri"]), self._bar_txt_verifycap)
        self.failUnlessReallyEqual(data[1]["size"], len(self.BAR_CONTENTS))

    def failUnlessIsQuuxJSON(self, res, readonly=False):
        data = json.loads(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "filenode")
        self.failUnless(isinstance(data[1], dict))
        metadata = data[1]
        return self.failUnlessIsQuuxDotTxtMetadata(metadata, readonly)

    def failUnlessIsQuuxDotTxtMetadata(self, metadata, readonly):
        self.failUnless(metadata['mutable'])
        if readonly:
            self.failIfIn("rw_uri", metadata)
        else:
            self.failUnlessIn("rw_uri", metadata)
            self.failUnlessEqual(metadata['rw_uri'], self._quux_txt_uri)
        self.failUnlessIn("ro_uri", metadata)
        self.failUnlessEqual(metadata['ro_uri'], self._quux_txt_readonly_uri)
        self.failUnlessReallyEqual(metadata['size'], len(self.QUUX_CONTENTS))

    def failUnlessIsFooJSON(self, res):
        data = json.loads(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "dirnode", res)
        self.failUnless(isinstance(data[1], dict))
        self.failUnless(data[1]["mutable"])
        self.failUnlessIn("rw_uri", data[1]) # mutable
        self.failUnlessReallyEqual(to_str(data[1]["rw_uri"]), self._foo_uri)
        self.failUnlessReallyEqual(to_str(data[1]["ro_uri"]), self._foo_readonly_uri)
        self.failUnlessReallyEqual(to_str(data[1]["verify_uri"]), self._foo_verifycap)

        kidnames = sorted([unicode(n) for n in data[1]["children"]])
        self.failUnlessEqual(kidnames,
                             [self._htmlname_unicode, u"bar.txt", u"baz.txt",
                              u"blockingfile", u"empty", u"n\u00fc.txt", u"quux.txt", u"sub"])
        kids = dict( [(unicode(name),value)
                      for (name,value)
                      in data[1]["children"].iteritems()] )
        self.failUnlessEqual(kids[u"sub"][0], "dirnode")
        self.failUnlessIn("metadata", kids[u"sub"][1])
        self.failUnlessIn("tahoe", kids[u"sub"][1]["metadata"])
        tahoe_md = kids[u"sub"][1]["metadata"]["tahoe"]
        self.failUnlessIn("linkcrtime", tahoe_md)
        self.failUnlessIn("linkmotime", tahoe_md)
        self.failUnlessEqual(kids[u"bar.txt"][0], "filenode")
        self.failUnlessReallyEqual(kids[u"bar.txt"][1]["size"], len(self.BAR_CONTENTS))
        self.failUnlessReallyEqual(to_str(kids[u"bar.txt"][1]["ro_uri"]), self._bar_txt_uri)
        self.failUnlessReallyEqual(to_str(kids[u"bar.txt"][1]["verify_uri"]),
                                   self._bar_txt_verifycap)
        self.failUnlessIn("metadata", kids[u"bar.txt"][1])
        self.failUnlessIn("tahoe", kids[u"bar.txt"][1]["metadata"])
        self.failUnlessReallyEqual(kids[u"bar.txt"][1]["metadata"]["tahoe"]["linkcrtime"],
                                   self._bar_txt_metadata["tahoe"]["linkcrtime"])
        self.failUnlessReallyEqual(to_str(kids[u"n\u00fc.txt"][1]["ro_uri"]),
                                   self._bar_txt_uri)
        self.failUnlessIn("quux.txt", kids)
        self.failUnlessReallyEqual(to_str(kids[u"quux.txt"][1]["rw_uri"]),
                                   self._quux_txt_uri)
        self.failUnlessReallyEqual(to_str(kids[u"quux.txt"][1]["ro_uri"]),
                                   self._quux_txt_readonly_uri)

    @inlineCallbacks
    def GET(self, urlpath, followRedirect=False, return_response=False,
            **kwargs):
        # if return_response=True, this fires with (data, statuscode,
        # respheaders) instead of just data.

        # treq can accept unicode URLs, unlike the old client.getPage
        url = self.webish_url + urlpath
        response = yield treq.request("get", url, persistent=False,
                                      allow_redirects=followRedirect,
                                      **kwargs)
        data = yield response.content()
        if return_response:
            # we emulate the old HTTPClientGetFactory-based response, which
            # wanted a tuple of (bytestring of data, bytestring of response
            # code like "200" or "404", and a
            # twisted.web.http_headers.Headers instance). Fortunately treq's
            # response.headers has one.
            returnValue( (data, str(response.code), response.headers) )
        if 400 <= response.code < 600:
            raise Error(response.code, response=data)
        returnValue(data)

    @inlineCallbacks
    def HEAD(self, urlpath, return_response=False, headers={}):
        url = self.webish_url + urlpath
        response = yield treq.request("head", url, persistent=False,
                                      headers=headers)
        if 400 <= response.code < 600:
            raise Error(response.code, response="")
        returnValue( ("", response.code, response.headers) )

    def PUT(self, urlpath, data, headers={}):
        url = self.webish_url + urlpath
        return do_http("put", url, data=data, headers=headers)

    def DELETE(self, urlpath):
        url = self.webish_url + urlpath
        return do_http("delete", url)

    def build_form(self, **fields):
        sepbase = "boogabooga"
        sep = "--" + sepbase
        form = []
        form.append(sep)
        form.append('Content-Disposition: form-data; name="_charset"')
        form.append('')
        form.append('UTF-8')
        form.append(sep)
        for name, value in fields.iteritems():
            if isinstance(value, tuple):
                filename, value = value
                form.append('Content-Disposition: form-data; name="%s"; '
                            'filename="%s"' % (name, filename.encode("utf-8")))
            else:
                form.append('Content-Disposition: form-data; name="%s"' % name)
            form.append('')
            if isinstance(value, unicode):
                value = value.encode("utf-8")
            else:
                value = str(value)
            assert isinstance(value, str)
            form.append(value)
            form.append(sep)
        form[-1] += "--"
        body = ""
        headers = {}
        if fields:
            body = "\r\n".join(form) + "\r\n"
            headers["content-type"] = "multipart/form-data; boundary=%s" % sepbase
        return (body, headers)

    def POST(self, urlpath, **fields):
        body, headers = self.build_form(**fields)
        return self.POST2(urlpath, body, headers)

    def POST2(self, urlpath, body="", headers={}, followRedirect=False):
        url = self.webish_url + urlpath
        return do_http("POST", url, allow_redirects=followRedirect,
                       headers=headers, data=body)

    def shouldFail(self, res, expected_failure, which,
                   substring=None, response_substring=None):
        if isinstance(res, failure.Failure):
            res.trap(expected_failure)
            if substring:
                self.failUnlessIn(substring, str(res), which)
            if response_substring:
                self.failUnlessIn(response_substring, res.value.response, which)
        else:
            self.fail("%s was supposed to raise %s, not get '%s'" %
                      (which, expected_failure, res))

    def shouldFail2(self, expected_failure, which, substring,
                    response_substring,
                    callable, *args, **kwargs):
        assert substring is None or isinstance(substring, str)
        assert response_substring is None or isinstance(response_substring, str)
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnlessIn(substring, str(res),
                                      "'%s' not in '%s' (response is '%s') for test '%s'" % \
                                      (substring, str(res),
                                       getattr(res.value, "response", ""),
                                       which))
                if response_substring:
                    self.failUnlessIn(response_substring, res.value.response,
                                      "'%s' not in '%s' for test '%s'" % \
                                      (response_substring, res.value.response,
                                       which))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d

    def should404(self, res, which):
        if isinstance(res, failure.Failure):
            res.trap(error.Error)
            self.failUnlessReallyEqual(res.value.status, "404")
        else:
            self.fail("%s was supposed to Error(404), not get '%s'" %
                      (which, res))

    def should302(self, res, which):
        if isinstance(res, failure.Failure):
            res.trap(error.Error)
            self.failUnlessReallyEqual(res.value.status, "302")
        else:
            self.fail("%s was supposed to Error(302), not get '%s'" %
                      (which, res))



class MultiFormatPageTests(unittest.TestCase):
    """
    Tests for ``MultiFormatPage``.
    """
    def resource(self):
        """
        Create and return an instance of a ``MultiFormatPage`` subclass with two
        formats: ``a`` and ``b``.
        """
        class Content(MultiFormatPage):
            docFactory = stan("doc factory")

            def render_A(self, req):
                return "a"

            def render_B(self, req):
                return "b"
        return Content()


    def render(self, resource, **query_args):
        """
        Render a Nevow ``Page`` against a request with the given query arguments.

        :param resource: The Nevow resource to render.

        :param query_args: The query arguments to put into the request being
            rendered.  A mapping from ``bytes`` to ``list`` of ``bytes``.

        :return: The rendered response body as ``bytes``.
        """
        ctx = WebContext(tag=resource)
        req = FakeRequest(args=query_args)
        ctx.remember(DefaultExceptionHandler(), ICanHandleException)
        ctx.remember(req, IRequest)
        ctx.remember(None, IData)

        d = maybeDeferred(resource.renderHTTP, ctx)
        d.addErrback(processingFailed, req, ctx)
        res = self.successResultOf(d)
        if isinstance(res, bytes):
            return req.v + res
        return req.v


    def test_select_format(self):
        """
        The ``formatArgument`` attribute of a ``MultiFormatPage`` subclass
        identifies the query argument which selects the result format.
        """
        resource = self.resource()
        resource.formatArgument = "foo"
        self.assertEqual("a", self.render(resource, foo=["a"]))


    def test_default_format_argument(self):
        """
        If a ``MultiFormatPage`` subclass does not set ``formatArgument`` then the
        ``t`` argument is used.
        """
        resource = self.resource()
        self.assertEqual("a", self.render(resource, t=["a"]))


    def test_no_format(self):
        """
        If no value is given for the format argument and no default format has
        been defined, the base Nevow rendering behavior is used
        (``renderHTTP``).
        """
        resource = self.resource()
        self.assertEqual("doc factory", self.render(resource))


    def test_default_format(self):
        """
        If no value is given for the format argument and the ``MultiFormatPage``
        subclass defines a ``formatDefault``, that value is used as the format
        to render.
        """
        resource = self.resource()
        resource.formatDefault = "b"
        self.assertEqual("b", self.render(resource))


    def test_explicit_none_format_renderer(self):
        """
        If a format is selected which has a renderer set to ``None``, the base
        Nevow rendering behavior is used (``renderHTTP``).
        """
        resource = self.resource()
        resource.render_FOO = None
        self.assertEqual("doc factory", self.render(resource, t=["foo"]))


    def test_unknown_format(self):
        """
        If a format is selected for which there is no renderer, an error is
        returned.
        """
        resource = self.resource()
        self.assertIn(
            "<title>Exception</title>",
            self.render(resource, t=["foo"]),
        )
        self.flushLoggedErrors(WebError)



class Web(WebMixin, WebErrorMixin, testutil.StallMixin, testutil.ReallyEqualMixin, unittest.TestCase):
    maxDiff = None

    def test_create(self):
        pass

    def test_frame_options(self):
        """
        All pages deny the ability to be loaded in frames.
        """
        d = self.GET("/", return_response=True)
        def responded(result):
            _, _, headers = result
            self.assertEqual(
                [b"DENY"],
                headers.getRawHeaders(b"X-Frame-Options"),
            )
        d.addCallback(responded)
        return d

    def test_welcome_json(self):
        """
        There is a JSON version of the welcome page which can be selected with the
        ``t`` query argument.
        """
        d = self.GET("/?t=json")
        def _check(res):
            decoded = json.loads(res)
            expected = {
                u'introducers': {
                    u'statuses': [],
                },
                u'servers': sorted([
                    {u"nodeid": u'other_nodeid',
                     u'available_space': 123456,
                     u'connection_status': u'summary',
                     u'last_received_data': 30,
                     u'nickname': u'other_nickname \u263b',
                     u'version': u'1.0',
                    },
                    {u"nodeid": u'disconnected_nodeid',
                     u'available_space': 123456,
                     u'connection_status': u'summary',
                     u'last_received_data': 35,
                     u'nickname': u'disconnected_nickname \u263b',
                     u'version': u'1.0',
                    },
                ]),
            }
            self.assertEqual(expected, decoded)
        d.addCallback(_check)
        return d

    def test_welcome(self):
        d = self.GET("/")
        def _check(res):
            # TODO: replace this with a parser
            self.failUnlessIn('<title>Tahoe-LAFS - Welcome</title>', res)
            self.failUnlessIn(FAVICON_MARKUP, res)
            self.failUnlessIn('<a href="status">Recent and Active Operations</a>', res)
            self.failUnlessIn('<a href="statistics">Operational Statistics</a>', res)
            self.failUnless(re.search('<input (type="hidden" |name="t" |value="report-incident" ){3}/>',res), res)
            self.failUnlessIn('Page rendered at', res)
            self.failUnlessIn('Tahoe-LAFS code imported from:', res)
            res_u = res.decode('utf-8')
            self.failUnlessIn(u'<td>fake_nickname \u263A</td>', res_u)
            self.failUnlessIn(u'<div class="nickname">other_nickname \u263B</div>', res_u)
            self.failUnlessIn(u'Connected to <span>1</span>\n              of <span>2</span> known storage servers', res_u)
            def timestamp(t):
                return (u'"%s"' % (t,)) if self.have_working_tzset() else u'"[^"]*"'

            # TODO: use a real parser to make sure these two nodes are siblings
            self.failUnless(re.search(
                u'<div class="status-indicator"><img (src="img/connected-yes.png" |alt="Connected" ){2}/></div>'
                u'\s+'
                u'<div class="nickname">other_nickname \u263B</div>',
                res_u), repr(res_u))
            self.failUnless(re.search(
                u'<a( class="timestamp"| title=%s){2}>\s+1d\u00A00h\u00A00m\u00A050s\s+</a>'
                % timestamp(u'1970-01-01 13:00:10'), res_u), repr(res_u))

            # same for these two nodes
            self.failUnless(re.search(
                u'<div class="status-indicator"><img (src="img/connected-no.png" |alt="Disconnected" ){2}/></div>'
                u'\s+'
                u'<div class="nickname">disconnected_nickname \u263B</div>',
                res_u), repr(res_u))
            self.failUnless(re.search(
                u'<a( class="timestamp"| title="N/A"){2}>\s+N/A\s+</a>',
                res_u), repr(res_u))

            self.failUnless(re.search(
                u'<td class="service-last-received-data"><a( class="timestamp"| title=%s){2}>'
                u'1d\u00A00h\u00A00m\u00A030s</a></td>'
                % timestamp(u'1970-01-01 13:00:30'), res_u), repr(res_u))
            self.failUnless(re.search(
                u'<td class="service-last-received-data"><a( class="timestamp"| title=%s){2}>'
                u'1d\u00A00h\u00A00m\u00A025s</a></td>'
                % timestamp(u'1970-01-01 13:00:35'), res_u), repr(res_u))

            self.failUnlessIn(u'\u00A9 <a href="https://tahoe-lafs.org/">Tahoe-LAFS Software Foundation', res_u)
            self.failUnlessIn('<td><h3>Available</h3></td>', res)
            self.failUnlessIn('123.5kB', res)

            self.s.basedir = 'web/test_welcome'
            fileutil.make_dirs("web/test_welcome")
            fileutil.make_dirs("web/test_welcome/private")
            return self.GET("/")
        d.addCallback(_check)
        return d

    def test_introducer_status(self):
        class MockIntroducerClient(object):
            def __init__(self, connected):
                self.connected = connected
            def connection_status(self):
                return ConnectionStatus(self.connected, "summary", {}, 0, 0)

        d = defer.succeed(None)

        # introducer not connected, unguessable furl
        def _set_introducer_not_connected_unguessable(ign):
            self.s.introducer_furls = [ "pb://someIntroducer/secret" ]
            self.s.introducer_clients = [ MockIntroducerClient(False) ]
            return self.GET("/")
        d.addCallback(_set_introducer_not_connected_unguessable)
        def _check_introducer_not_connected_unguessable(res):
            html = res.replace('\n', ' ')
            self.failIfIn('pb://someIntroducer/secret', html)
            self.failUnless(re.search('<img (alt="Disconnected" |src="img/connected-no.png" ){2}/></div>[ ]*<div>No introducers connected</div>', html), res)

        d.addCallback(_check_introducer_not_connected_unguessable)

        # introducer connected, unguessable furl
        def _set_introducer_connected_unguessable(ign):
            self.s.introducer_furls = [ "pb://someIntroducer/secret" ]
            self.s.introducer_clients = [ MockIntroducerClient(True) ]
            return self.GET("/")
        d.addCallback(_set_introducer_connected_unguessable)
        def _check_introducer_connected_unguessable(res):
            html = res.replace('\n', ' ')
            self.failUnlessIn('<div class="connection-status" title="(no other hints)">summary</div>', html)
            self.failIfIn('pb://someIntroducer/secret', html)
            self.failUnless(re.search('<img (src="img/connected-yes.png" |alt="Connected" ){2}/></div>[ ]*<div>1 introducer connected</div>', html), res)
        d.addCallback(_check_introducer_connected_unguessable)

        # introducer connected, guessable furl
        def _set_introducer_connected_guessable(ign):
            self.s.introducer_furls = [ "pb://someIntroducer/introducer" ]
            self.s.introducer_clients = [ MockIntroducerClient(True) ]
            return self.GET("/")
        d.addCallback(_set_introducer_connected_guessable)
        def _check_introducer_connected_guessable(res):
            html = res.replace('\n', ' ')
            self.failUnlessIn('<div class="connection-status" title="(no other hints)">summary</div>', html)
            self.failUnless(re.search('<img (src="img/connected-yes.png" |alt="Connected" ){2}/></div>[ ]*<div>1 introducer connected</div>', html), res)
        d.addCallback(_check_introducer_connected_guessable)
        return d

    def test_helper_status(self):
        d = defer.succeed(None)

        # set helper furl to None
        def _set_no_helper(ign):
            self.s.uploader.helper_furl = None
            return self.GET("/")
        d.addCallback(_set_no_helper)
        def _check_no_helper(res):
            html = res.replace('\n', ' ')
            self.failUnless(re.search('<img (src="img/connected-not-configured.png" |alt="Not Configured" ){2}/>', html), res)
        d.addCallback(_check_no_helper)

        # enable helper, not connected
        def _set_helper_not_connected(ign):
            self.s.uploader.helper_furl = "pb://someHelper/secret"
            self.s.uploader.helper_connected = False
            return self.GET("/")
        d.addCallback(_set_helper_not_connected)
        def _check_helper_not_connected(res):
            html = res.replace('\n', ' ')
            self.failUnlessIn('<div class="furl">pb://someHelper/[censored]</div>', html)
            self.failIfIn('pb://someHelper/secret', html)
            self.failUnless(re.search('<img (src="img/connected-no.png" |alt="Disconnected" ){2}/>', html), res)
        d.addCallback(_check_helper_not_connected)

        # enable helper, connected
        def _set_helper_connected(ign):
            self.s.uploader.helper_furl = "pb://someHelper/secret"
            self.s.uploader.helper_connected = True
            return self.GET("/")
        d.addCallback(_set_helper_connected)
        def _check_helper_connected(res):
            html = res.replace('\n', ' ')
            self.failUnlessIn('<div class="furl">pb://someHelper/[censored]</div>', html)
            self.failIfIn('pb://someHelper/secret', html)
            self.failUnless(re.search('<img (src="img/connected-yes.png" |alt="Connected" ){2}/>', html), res)
        d.addCallback(_check_helper_connected)
        return d

    def test_storage(self):
        d = self.GET("/storage")
        def _check(res):
            self.failUnlessIn('Storage Server Status', res)
            self.failUnlessIn(FAVICON_MARKUP, res)
            res_u = res.decode('utf-8')
            self.failUnlessIn(u'<li>Server Nickname: <span class="nickname mine">fake_nickname \u263A</span></li>', res_u)
        d.addCallback(_check)
        return d

    @defer.inlineCallbacks
    def test_magicfolder_status_bad_token(self):
        with self.assertRaises(Error):
            yield self.POST(
                '/magic_folder?t=json',
                t='json',
                name='default',
                token='not the token you are looking for',
            )

    @defer.inlineCallbacks
    def test_magicfolder_status_wrong_folder(self):
        with self.assertRaises(Exception) as ctx:
            yield self.POST(
                '/magic_folder?t=json',
                t='json',
                name='a non-existent magic-folder',
                token=self.s.get_auth_token(),
            )
        self.assertIn(
            "Not Found",
            str(ctx.exception)
        )

    @defer.inlineCallbacks
    def test_magicfolder_status_success(self):
        self.s._magic_folders['default'] = mf = FakeMagicFolder()
        mf.uploader.status = [
            create_test_queued_item(u"rel/uppath", [('done', 12345)])
        ]
        mf.downloader.status = [
            create_test_queued_item(u"rel/downpath", [('done', 23456)])
        ]
        data = yield self.POST(
            '/magic_folder?t=json',
            t='json',
            name='default',
            token=self.s.get_auth_token(),
        )
        data = json.loads(data)
        self.assertEqual(
            data,
            [
                {
                    "status": "done",
                    "path": "rel/uppath",
                    "kind": "upload",
                    "percent_done": 100.0,
                    "done_at": 12345,
                    "size": 1234,
                },
                {
                    "status": "done",
                    "path": "rel/downpath",
                    "kind": "download",
                    "percent_done": 100.0,
                    "done_at": 23456,
                    "size": 1234,
                },
            ]
        )

    @defer.inlineCallbacks
    def test_magicfolder_root_success(self):
        self.s._magic_folders['default'] = mf = FakeMagicFolder()
        mf.uploader.status = [
            create_test_queued_item(u"rel/path", [('done', 12345)])
        ]
        data = yield self.GET(
            '/',
        )
        del data

    def test_status(self):
        h = self.s.get_history()
        dl_num = h.list_all_download_statuses()[0].get_counter()
        ul_num = h.list_all_upload_statuses()[0].get_counter()
        mu_num = h.list_all_mapupdate_statuses()[0].get_counter()
        pub_num = h.list_all_publish_statuses()[0].get_counter()
        ret_num = h.list_all_retrieve_statuses()[0].get_counter()
        d = self.GET("/status", followRedirect=True)
        def _check(res):
            self.failUnlessIn('Recent and Active Operations', res)
            self.failUnlessIn('"down-%d"' % dl_num, res)
            self.failUnlessIn('"up-%d"' % ul_num, res)
            self.failUnlessIn('"mapupdate-%d"' % mu_num, res)
            self.failUnlessIn('"publish-%d"' % pub_num, res)
            self.failUnlessIn('"retrieve-%d"' % ret_num, res)
        d.addCallback(_check)
        d.addCallback(lambda res: self.GET("/status/?t=json"))
        def _check_json(res):
            data = json.loads(res)
            self.failUnless(isinstance(data, dict))
            #active = data["active"]
            # TODO: test more. We need a way to fake an active operation
            # here.
        d.addCallback(_check_json)

        d.addCallback(lambda res: self.GET("/status/down-%d" % dl_num))
        def _check_dl(res):
            self.failUnlessIn("File Download Status", res)
        d.addCallback(_check_dl)
        d.addCallback(lambda res: self.GET("/status/down-%d/event_json" % dl_num))
        def _check_dl_json(res):
            data = json.loads(res)
            self.failUnless(isinstance(data, dict))
            self.failUnlessIn("read", data)
            self.failUnlessEqual(data["read"][0]["length"], 120)
            self.failUnlessEqual(data["segment"][0]["segment_length"], 100)
            self.failUnlessEqual(data["segment"][2]["segment_number"], 2)
            self.failUnlessEqual(data["segment"][2]["finish_time"], None)
            phwr_id = base32.b2a(hashutil.tagged_hash("foo", "serverid_a")[:20])
            cmpu_id = base32.b2a(hashutil.tagged_hash("foo", "serverid_b")[:20])
            # serverids[] keys are strings, since that's what JSON does, but
            # we'd really like them to be ints
            self.failUnlessEqual(data["serverids"]["0"], "phwrsjte")
            self.failUnless(data["serverids"].has_key("1"),
                            str(data["serverids"]))
            self.failUnlessEqual(data["serverids"]["1"], "cmpuvkjm",
                                 str(data["serverids"]))
            self.failUnlessEqual(data["server_info"][phwr_id]["short"],
                                 "phwrsjte")
            self.failUnlessEqual(data["server_info"][cmpu_id]["short"],
                                 "cmpuvkjm")
            self.failUnlessIn("dyhb", data)
            self.failUnlessIn("misc", data)
        d.addCallback(_check_dl_json)
        d.addCallback(lambda res: self.GET("/status/up-%d" % ul_num))
        def _check_ul(res):
            self.failUnlessIn("File Upload Status", res)
        d.addCallback(_check_ul)
        d.addCallback(lambda res: self.GET("/status/mapupdate-%d" % mu_num))
        def _check_mapupdate(res):
            self.failUnlessIn("Mutable File Servermap Update Status", res)
        d.addCallback(_check_mapupdate)
        d.addCallback(lambda res: self.GET("/status/publish-%d" % pub_num))
        def _check_publish(res):
            self.failUnlessIn("Mutable File Publish Status", res)
        d.addCallback(_check_publish)
        d.addCallback(lambda res: self.GET("/status/retrieve-%d" % ret_num))
        def _check_retrieve(res):
            self.failUnlessIn("Mutable File Retrieve Status", res)
        d.addCallback(_check_retrieve)

        return d

    def test_status_numbers(self):
        drrm = status.DownloadResultsRendererMixin()
        self.failUnlessReallyEqual(drrm.render_time(None, None), "")
        self.failUnlessReallyEqual(drrm.render_time(None, 2.5), "2.50s")
        self.failUnlessReallyEqual(drrm.render_time(None, 0.25), "250ms")
        self.failUnlessReallyEqual(drrm.render_time(None, 0.0021), "2.1ms")
        self.failUnlessReallyEqual(drrm.render_time(None, 0.000123), "123us")
        self.failUnlessReallyEqual(drrm.render_rate(None, None), "")
        self.failUnlessReallyEqual(drrm.render_rate(None, 2500000), "2.50MBps")
        self.failUnlessReallyEqual(drrm.render_rate(None, 30100), "30.1kBps")
        self.failUnlessReallyEqual(drrm.render_rate(None, 123), "123Bps")

        urrm = status.UploadResultsRendererMixin()
        self.failUnlessReallyEqual(urrm.render_time(None, None), "")
        self.failUnlessReallyEqual(urrm.render_time(None, 2.5), "2.50s")
        self.failUnlessReallyEqual(urrm.render_time(None, 0.25), "250ms")
        self.failUnlessReallyEqual(urrm.render_time(None, 0.0021), "2.1ms")
        self.failUnlessReallyEqual(urrm.render_time(None, 0.000123), "123us")
        self.failUnlessReallyEqual(urrm.render_rate(None, None), "")
        self.failUnlessReallyEqual(urrm.render_rate(None, 2500000), "2.50MBps")
        self.failUnlessReallyEqual(urrm.render_rate(None, 30100), "30.1kBps")
        self.failUnlessReallyEqual(urrm.render_rate(None, 123), "123Bps")

    def test_GET_FILEURL(self):
        d = self.GET(self.public_url + "/foo/bar.txt")
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_FILEURL_range(self):
        headers = {"range": "bytes=1-10"}
        d = self.GET(self.public_url + "/foo/bar.txt", headers=headers,
                     return_response=True)
        def _got(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(int(status), 206)
            self.failUnless(headers.hasHeader("content-range"))
            self.failUnlessReallyEqual(headers.getRawHeaders("content-range")[0],
                                       "bytes 1-10/%d" % len(self.BAR_CONTENTS))
            self.failUnlessReallyEqual(res, self.BAR_CONTENTS[1:11])
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_partial_range(self):
        headers = {"range": "bytes=5-"}
        length  = len(self.BAR_CONTENTS)
        d = self.GET(self.public_url + "/foo/bar.txt", headers=headers,
                     return_response=True)
        def _got(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(int(status), 206)
            self.failUnless(headers.hasHeader("content-range"))
            self.failUnlessReallyEqual(headers.getRawHeaders("content-range")[0],
                                       "bytes 5-%d/%d" % (length-1, length))
            self.failUnlessReallyEqual(res, self.BAR_CONTENTS[5:])
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_partial_end_range(self):
        headers = {"range": "bytes=-5"}
        length  = len(self.BAR_CONTENTS)
        d = self.GET(self.public_url + "/foo/bar.txt", headers=headers,
                     return_response=True)
        def _got(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(int(status), 206)
            self.failUnless(headers.hasHeader("content-range"))
            self.failUnlessReallyEqual(headers.getRawHeaders("content-range")[0],
                                       "bytes %d-%d/%d" % (length-5, length-1, length))
            self.failUnlessReallyEqual(res, self.BAR_CONTENTS[-5:])
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_partial_range_overrun(self):
        headers = {"range": "bytes=100-200"}
        d = self.shouldFail2(error.Error, "test_GET_FILEURL_range_overrun",
                             "416 Requested Range not satisfiable",
                             "First beyond end of file",
                             self.GET, self.public_url + "/foo/bar.txt",
                             headers=headers)
        return d

    def test_HEAD_FILEURL_range(self):
        headers = {"range": "bytes=1-10"}
        d = self.HEAD(self.public_url + "/foo/bar.txt", headers=headers,
                     return_response=True)
        def _got(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(res, "")
            self.failUnlessReallyEqual(int(status), 206)
            self.failUnless(headers.hasHeader("content-range"))
            self.failUnlessReallyEqual(headers.getRawHeaders("content-range")[0],
                                       "bytes 1-10/%d" % len(self.BAR_CONTENTS))
        d.addCallback(_got)
        return d

    def test_HEAD_FILEURL_partial_range(self):
        headers = {"range": "bytes=5-"}
        length  = len(self.BAR_CONTENTS)
        d = self.HEAD(self.public_url + "/foo/bar.txt", headers=headers,
                     return_response=True)
        def _got(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(int(status), 206)
            self.failUnless(headers.hasHeader("content-range"))
            self.failUnlessReallyEqual(headers.getRawHeaders("content-range")[0],
                                       "bytes 5-%d/%d" % (length-1, length))
        d.addCallback(_got)
        return d

    def test_HEAD_FILEURL_partial_end_range(self):
        headers = {"range": "bytes=-5"}
        length  = len(self.BAR_CONTENTS)
        d = self.HEAD(self.public_url + "/foo/bar.txt", headers=headers,
                     return_response=True)
        def _got(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(int(status), 206)
            self.failUnless(headers.hasHeader("content-range"))
            self.failUnlessReallyEqual(headers.getRawHeaders("content-range")[0],
                                       "bytes %d-%d/%d" % (length-5, length-1, length))
        d.addCallback(_got)
        return d

    def test_HEAD_FILEURL_partial_range_overrun(self):
        headers = {"range": "bytes=100-200"}
        d = self.shouldFail2(error.Error, "test_HEAD_FILEURL_range_overrun",
                             "416 Requested Range not satisfiable",
                             "",
                             self.HEAD, self.public_url + "/foo/bar.txt",
                             headers=headers)
        return d

    def test_GET_FILEURL_range_bad(self):
        headers = {"range": "BOGUS=fizbop-quarnak"}
        d = self.GET(self.public_url + "/foo/bar.txt", headers=headers,
                     return_response=True)
        def _got(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(int(status), 200)
            self.failUnless(not headers.hasHeader("content-range"))
            self.failUnlessReallyEqual(res, self.BAR_CONTENTS)
        d.addCallback(_got)
        return d

    def test_HEAD_FILEURL(self):
        d = self.HEAD(self.public_url + "/foo/bar.txt", return_response=True)
        def _got(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(res, "")
            self.failUnlessReallyEqual(headers.getRawHeaders("content-length")[0],
                                       str(len(self.BAR_CONTENTS)))
            self.failUnlessReallyEqual(headers.getRawHeaders("content-type"),
                                       ["text/plain"])
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_named(self):
        base = "/file/%s" % urllib.quote(self._bar_txt_uri)
        base2 = "/named/%s" % urllib.quote(self._bar_txt_uri)
        d = self.GET(base + "/@@name=/blah.txt")
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(base + "/blah.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(base + "/ignore/lots/blah.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(base2 + "/@@name=/blah.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        save_url = base + "?save=true&filename=blah.txt"
        d.addCallback(lambda res: self.GET(save_url))
        d.addCallback(self.failUnlessIsBarDotTxt) # TODO: check headers
        u_filename = u"n\u00e9wer.txt" # n e-acute w e r . t x t
        u_fn_e = urllib.quote(u_filename.encode("utf-8"))
        u_url = base + "?save=true&filename=" + u_fn_e
        d.addCallback(lambda res: self.GET(u_url))
        d.addCallback(self.failUnlessIsBarDotTxt) # TODO: check headers
        return d

    def test_PUT_FILEURL_named_bad(self):
        base = "/file/%s" % urllib.quote(self._bar_txt_uri)
        d = self.shouldFail2(error.Error, "test_PUT_FILEURL_named_bad",
                             "400 Bad Request",
                             "/file can only be used with GET or HEAD",
                             self.PUT, base + "/@@name=/blah.txt", "")
        return d


    def test_GET_DIRURL_named_bad(self):
        base = "/file/%s" % urllib.quote(self._foo_uri)
        d = self.shouldFail2(error.Error, "test_PUT_DIRURL_named_bad",
                             "400 Bad Request",
                             "is not a file-cap",
                             self.GET, base + "/@@name=/blah.txt")
        return d

    def test_GET_slash_file_bad(self):
        d = self.shouldFail2(error.Error, "test_GET_slash_file_bad",
                             "404 Not Found",
                             "/file must be followed by a file-cap and a name",
                             self.GET, "/file")
        return d

    def test_GET_unhandled_URI_named(self):
        contents, n, newuri = self.makefile(12)
        verifier_cap = n.get_verify_cap().to_string()
        base = "/file/%s" % urllib.quote(verifier_cap)
        # client.create_node_from_uri() can't handle verify-caps
        d = self.shouldFail2(error.Error, "GET_unhandled_URI_named",
                             "400 Bad Request", "is not a file-cap",
                             self.GET, base)
        return d

    def test_GET_unhandled_URI(self):
        contents, n, newuri = self.makefile(12)
        verifier_cap = n.get_verify_cap().to_string()
        base = "/uri/%s" % urllib.quote(verifier_cap)
        # client.create_node_from_uri() can't handle verify-caps
        d = self.shouldFail2(error.Error, "test_GET_unhandled_URI",
                             "400 Bad Request",
                             "GET unknown URI type: can only do t=info",
                             self.GET, base)
        return d

    def test_GET_FILE_URI(self):
        base = "/uri/%s" % urllib.quote(self._bar_txt_uri)
        d = self.GET(base)
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_FILE_URI_mdmf(self):
        base = "/uri/%s" % urllib.quote(self._quux_txt_uri)
        d = self.GET(base)
        d.addCallback(self.failUnlessIsQuuxDotTxt)
        return d

    def test_GET_FILE_URI_mdmf_extensions(self):
        base = "/uri/%s" % urllib.quote("%s:RANDOMSTUFF" % self._quux_txt_uri)
        d = self.GET(base)
        d.addCallback(self.failUnlessIsQuuxDotTxt)
        return d

    def test_GET_FILE_URI_mdmf_readonly(self):
        base = "/uri/%s" % urllib.quote(self._quux_txt_readonly_uri)
        d = self.GET(base)
        d.addCallback(self.failUnlessIsQuuxDotTxt)
        return d

    def test_GET_FILE_URI_badchild(self):
        base = "/uri/%s/boguschild" % urllib.quote(self._bar_txt_uri)
        errmsg = "Files have no children, certainly not named 'boguschild'"
        d = self.shouldFail2(error.Error, "test_GET_FILE_URI_badchild",
                             "400 Bad Request", errmsg,
                             self.GET, base)
        return d

    def test_PUT_FILE_URI_badchild(self):
        base = "/uri/%s/boguschild" % urllib.quote(self._bar_txt_uri)
        errmsg = "Cannot create directory 'boguschild', because its parent is a file, not a directory"
        d = self.shouldFail2(error.Error, "test_GET_FILE_URI_badchild",
                             "400 Bad Request", errmsg,
                             self.PUT, base, "")
        return d

    def test_PUT_FILE_URI_mdmf(self):
        base = "/uri/%s" % urllib.quote(self._quux_txt_uri)
        self._quux_new_contents = "new_contents"
        d = self.GET(base)
        d.addCallback(lambda res:
            self.failUnlessIsQuuxDotTxt(res))
        d.addCallback(lambda ignored:
            self.PUT(base, self._quux_new_contents))
        d.addCallback(lambda ignored:
            self.GET(base))
        d.addCallback(lambda res:
            self.failUnlessReallyEqual(res, self._quux_new_contents))
        return d

    def test_PUT_FILE_URI_mdmf_extensions(self):
        base = "/uri/%s" % urllib.quote("%s:EXTENSIONSTUFF" % self._quux_txt_uri)
        self._quux_new_contents = "new_contents"
        d = self.GET(base)
        d.addCallback(lambda res: self.failUnlessIsQuuxDotTxt(res))
        d.addCallback(lambda ignored: self.PUT(base, self._quux_new_contents))
        d.addCallback(lambda ignored: self.GET(base))
        d.addCallback(lambda res: self.failUnlessEqual(self._quux_new_contents,
                                                       res))
        return d

    def test_PUT_FILE_URI_mdmf_readonly(self):
        # We're not allowed to PUT things to a readonly cap.
        base = "/uri/%s" % self._quux_txt_readonly_uri
        d = self.GET(base)
        d.addCallback(lambda res:
            self.failUnlessIsQuuxDotTxt(res))
        # What should we get here? We get a 500 error now; that's not right.
        d.addCallback(lambda ignored:
            self.shouldFail2(error.Error, "test_PUT_FILE_URI_mdmf_readonly",
                             "400 Bad Request", "read-only cap",
                             self.PUT, base, "new data"))
        return d

    def test_PUT_FILE_URI_sdmf_readonly(self):
        # We're not allowed to put things to a readonly cap.
        base = "/uri/%s" % self._baz_txt_readonly_uri
        d = self.GET(base)
        d.addCallback(lambda res:
            self.failUnlessIsBazDotTxt(res))
        d.addCallback(lambda ignored:
            self.shouldFail2(error.Error, "test_PUT_FILE_URI_sdmf_readonly",
                             "400 Bad Request", "read-only cap",
                             self.PUT, base, "new_data"))
        return d

    def test_GET_etags(self):

        def _check_etags(uri):
            d1 = _get_etag(uri)
            d2 = _get_etag(uri, 'json')
            d = defer.DeferredList([d1, d2], consumeErrors=True)
            def _check(results):
                # All deferred must succeed
                self.failUnless(all([r[0] for r in results]))
                # the etag for the t=json form should be just like the etag
                # fo the default t='' form, but with a 'json' suffix
                self.failUnlessEqual(results[0][1] + 'json', results[1][1])
            d.addCallback(_check)
            return d

        def _get_etag(uri, t=''):
            targetbase = "/uri/%s?t=%s" % (urllib.quote(uri.strip()), t)
            d = self.GET(targetbase, return_response=True, followRedirect=True)
            def _just_the_etag(result):
                data, response, headers = result
                etag = headers.getRawHeaders('etag')[0]
                if uri.startswith('URI:DIR'):
                    self.failUnless(etag.startswith('DIR:'), etag)
                return etag
            return d.addCallback(_just_the_etag)

        # Check that etags work with immutable directories
        (newkids, caps) = self._create_immutable_children()
        d = self.POST2(self.public_url + "/foo/newdir?t=mkdir-immutable",
                      json.dumps(newkids))
        def _stash_immdir_uri(uri):
            self._immdir_uri = uri
            return uri
        d.addCallback(_stash_immdir_uri)
        d.addCallback(_check_etags)

        # Check that etags work with immutable files
        d.addCallback(lambda _: _check_etags(self._bar_txt_uri))

        # use the ETag on GET
        def _check_match(ign):
            uri = "/uri/%s" % self._bar_txt_uri
            d = self.GET(uri, return_response=True)
            # extract the ETag
            d.addCallback(lambda data_code_headers:
                          data_code_headers[2].getRawHeaders('etag')[0])
            # do a GET that's supposed to match the ETag
            d.addCallback(lambda etag:
                          self.GET(uri, return_response=True,
                                   headers={"If-None-Match": etag}))
            # make sure it short-circuited (304 instead of 200)
            d.addCallback(lambda data_code_headers:
                          self.failUnlessEqual(int(data_code_headers[1]), http.NOT_MODIFIED))
            return d
        d.addCallback(_check_match)

        def _no_etag(uri, t):
            target = "/uri/%s?t=%s" % (uri, t)
            d = self.GET(target, return_response=True, followRedirect=True)
            d.addCallback(lambda data_code_headers:
                          self.failIf(data_code_headers[2].hasHeader("etag"), target))
            return d
        def _yes_etag(uri, t):
            target = "/uri/%s?t=%s" % (uri, t)
            d = self.GET(target, return_response=True, followRedirect=True)
            d.addCallback(lambda data_code_headers:
                          self.failUnless(data_code_headers[2].hasHeader("etag"), target))
            return d

        d.addCallback(lambda ign: _yes_etag(self._bar_txt_uri, ""))
        d.addCallback(lambda ign: _yes_etag(self._bar_txt_uri, "json"))
        d.addCallback(lambda ign: _yes_etag(self._bar_txt_uri, "uri"))
        d.addCallback(lambda ign: _yes_etag(self._bar_txt_uri, "readonly-uri"))
        d.addCallback(lambda ign: _no_etag(self._bar_txt_uri, "info"))

        d.addCallback(lambda ign: _yes_etag(self._immdir_uri, ""))
        d.addCallback(lambda ign: _yes_etag(self._immdir_uri, "json"))
        d.addCallback(lambda ign: _yes_etag(self._immdir_uri, "uri"))
        d.addCallback(lambda ign: _yes_etag(self._immdir_uri, "readonly-uri"))
        d.addCallback(lambda ign: _no_etag(self._immdir_uri, "info"))
        d.addCallback(lambda ign: _no_etag(self._immdir_uri, "rename-form"))

        return d

    # TODO: version of this with a Unicode filename
    def test_GET_FILEURL_save(self):
        d = self.GET(self.public_url + "/foo/bar.txt?filename=bar.txt&save=true",
                     return_response=True)
        def _got(res_and_status_and_headers):
            (res, statuscode, headers) = res_and_status_and_headers
            content_disposition = headers.getRawHeaders("content-disposition")[0]
            self.failUnless(content_disposition == 'attachment; filename="bar.txt"', content_disposition)
            self.failUnlessIsBarDotTxt(res)
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_missing(self):
        d = self.GET(self.public_url + "/foo/missing")
        d.addBoth(self.should404, "test_GET_FILEURL_missing")
        return d

    def test_GET_FILEURL_info_mdmf(self):
        d = self.GET("/uri/%s?t=info" % self._quux_txt_uri)
        def _got(res):
            self.failUnlessIn("mutable file (mdmf)", res)
            self.failUnlessIn(self._quux_txt_uri, res)
            self.failUnlessIn(self._quux_txt_readonly_uri, res)
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_info_mdmf_readonly(self):
        d = self.GET("/uri/%s?t=info" % self._quux_txt_readonly_uri)
        def _got(res):
            self.failUnlessIn("mutable file (mdmf)", res)
            self.failIfIn(self._quux_txt_uri, res)
            self.failUnlessIn(self._quux_txt_readonly_uri, res)
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_info_sdmf(self):
        d = self.GET("/uri/%s?t=info" % self._baz_txt_uri)
        def _got(res):
            self.failUnlessIn("mutable file (sdmf)", res)
            self.failUnlessIn(self._baz_txt_uri, res)
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_info_mdmf_extensions(self):
        d = self.GET("/uri/%s:STUFF?t=info" % self._quux_txt_uri)
        def _got(res):
            self.failUnlessIn("mutable file (mdmf)", res)
            self.failUnlessIn(self._quux_txt_uri, res)
            self.failUnlessIn(self._quux_txt_readonly_uri, res)
        d.addCallback(_got)
        return d

    def test_PUT_overwrite_only_files(self):
        # create a directory, put a file in that directory.
        contents, n, filecap = self.makefile(8)
        d = self.PUT(self.public_url + "/foo/dir?t=mkdir", "")
        d.addCallback(lambda res:
            self.PUT(self.public_url + "/foo/dir/file1.txt",
                     self.NEWFILE_CONTENTS))
        # try to overwrite the file with replace=only-files
        # (this should work)
        d.addCallback(lambda res:
            self.PUT(self.public_url + "/foo/dir/file1.txt?t=uri&replace=only-files",
                     filecap))
        d.addCallback(lambda res:
            self.shouldFail2(error.Error, "PUT_bad_t", "409 Conflict",
                 "There was already a child by that name, and you asked me "
                 "to not replace it",
                 self.PUT, self.public_url + "/foo/dir?t=uri&replace=only-files",
                 filecap))
        return d

    def test_PUT_NEWFILEURL(self):
        d = self.PUT(self.public_url + "/foo/new.txt", self.NEWFILE_CONTENTS)
        # TODO: we lose the response code, so we can't check this
        #self.failUnlessReallyEqual(responsecode, 201)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_not_mutable(self):
        d = self.PUT(self.public_url + "/foo/new.txt?mutable=false",
                     self.NEWFILE_CONTENTS)
        # TODO: we lose the response code, so we can't check this
        #self.failUnlessReallyEqual(responsecode, 201)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_unlinked_mdmf(self):
        # this should get us a few segments of an MDMF mutable file,
        # which we can then test for.
        contents = self.NEWFILE_CONTENTS * 300000
        d = self.PUT("/uri?format=mdmf",
                     contents)
        def _got_filecap(filecap):
            self.failUnless(filecap.startswith("URI:MDMF"))
            return filecap
        d.addCallback(_got_filecap)
        d.addCallback(lambda filecap: self.GET("/uri/%s?t=json" % filecap))
        d.addCallback(lambda json: self.failUnlessIn("MDMF", json))
        return d

    def test_PUT_NEWFILEURL_unlinked_sdmf(self):
        contents = self.NEWFILE_CONTENTS * 300000
        d = self.PUT("/uri?format=sdmf",
                     contents)
        d.addCallback(lambda filecap: self.GET("/uri/%s?t=json" % filecap))
        d.addCallback(lambda json: self.failUnlessIn("SDMF", json))
        return d

    @inlineCallbacks
    def test_PUT_NEWFILEURL_unlinked_bad_format(self):
        contents = self.NEWFILE_CONTENTS * 300000
        yield self.assertHTTPError(self.webish_url + "/uri?format=foo", 400,
                                   "Unknown format: foo",
                                   method="put", data=contents)

    def test_PUT_NEWFILEURL_range_bad(self):
        headers = {"content-range": "bytes 1-10/%d" % len(self.NEWFILE_CONTENTS)}
        target = self.public_url + "/foo/new.txt"
        d = self.shouldFail2(error.Error, "test_PUT_NEWFILEURL_range_bad",
                             "501 Not Implemented",
                             "Content-Range in PUT not yet supported",
                             # (and certainly not for immutable files)
                             self.PUT, target, self.NEWFILE_CONTENTS[1:11],
                             headers=headers)
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"new.txt"))
        return d

    def test_PUT_NEWFILEURL_mutable(self):
        d = self.PUT(self.public_url + "/foo/new.txt?mutable=true",
                     self.NEWFILE_CONTENTS)
        # TODO: we lose the response code, so we can't check this
        #self.failUnlessReallyEqual(responsecode, 201)
        def _check_uri(res):
            u = uri.from_string_mutable_filenode(res)
            self.failUnless(u.is_mutable())
            self.failIf(u.is_readonly())
            return res
        d.addCallback(_check_uri)
        d.addCallback(self.failUnlessURIMatchesRWChild, self._foo_node, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessMutableChildContentsAre(self._foo_node,
                                                             u"new.txt",
                                                             self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_mutable_toobig(self):
        # It is okay to upload large mutable files, so we should be able
        # to do that.
        d = self.PUT(self.public_url + "/foo/new.txt?mutable=true",
                     "b" * (self.s.MUTABLE_SIZELIMIT + 1))
        return d

    def test_PUT_NEWFILEURL_replace(self):
        d = self.PUT(self.public_url + "/foo/bar.txt", self.NEWFILE_CONTENTS)
        # TODO: we lose the response code, so we can't check this
        #self.failUnlessReallyEqual(responsecode, 200)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node, u"bar.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"bar.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_bad_t(self):
        d = self.shouldFail2(error.Error, "PUT_bad_t", "400 Bad Request",
                             "PUT to a file: bad t=bogus",
                             self.PUT, self.public_url + "/foo/bar.txt?t=bogus",
                             "contents")
        return d

    def test_PUT_NEWFILEURL_no_replace(self):
        d = self.PUT(self.public_url + "/foo/bar.txt?replace=false",
                     self.NEWFILE_CONTENTS)
        d.addBoth(self.shouldFail, error.Error, "PUT_NEWFILEURL_no_replace",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        return d

    def test_PUT_NEWFILEURL_mkdirs(self):
        d = self.PUT(self.public_url + "/foo/newdir/new.txt", self.NEWFILE_CONTENTS)
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesROChild, fn, u"newdir/new.txt")
        d.addCallback(lambda res: self.failIfNodeHasChild(fn, u"new.txt"))
        d.addCallback(lambda res: self.failUnlessNodeHasChild(fn, u"newdir"))
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, u"newdir/new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_blocked(self):
        d = self.PUT(self.public_url + "/foo/blockingfile/new.txt",
                     self.NEWFILE_CONTENTS)
        d.addBoth(self.shouldFail, error.Error, "PUT_NEWFILEURL_blocked",
                  "409 Conflict",
                  "Unable to create directory 'blockingfile': a file was in the way")
        return d

    def test_PUT_NEWFILEURL_emptyname(self):
        # an empty pathname component (i.e. a double-slash) is disallowed
        d = self.shouldFail2(error.Error, "test_PUT_NEWFILEURL_emptyname",
                             "400 Bad Request",
                             "The webapi does not allow empty pathname components",
                             self.PUT, self.public_url + "/foo//new.txt", "")
        return d

    def test_DELETE_FILEURL(self):
        d = self.DELETE(self.public_url + "/foo/bar.txt")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        return d

    def test_DELETE_FILEURL_missing(self):
        d = self.DELETE(self.public_url + "/foo/missing")
        d.addBoth(self.should404, "test_DELETE_FILEURL_missing")
        return d

    def test_DELETE_FILEURL_missing2(self):
        d = self.DELETE(self.public_url + "/missing/missing")
        d.addBoth(self.should404, "test_DELETE_FILEURL_missing2")
        return d

    def failUnlessHasBarDotTxtMetadata(self, res):
        data = json.loads(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessIn("metadata", data[1])
        self.failUnlessIn("tahoe", data[1]["metadata"])
        self.failUnlessIn("linkcrtime", data[1]["metadata"]["tahoe"])
        self.failUnlessIn("linkmotime", data[1]["metadata"]["tahoe"])
        self.failUnlessReallyEqual(data[1]["metadata"]["tahoe"]["linkcrtime"],
                                   self._bar_txt_metadata["tahoe"]["linkcrtime"])

    def test_GET_FILEURL_json(self):
        # twisted.web.http.parse_qs ignores any query args without an '=', so
        # I can't do "GET /path?json", I have to do "GET /path/t=json"
        # instead. This may make it tricky to emulate the S3 interface
        # completely.
        d = self.GET(self.public_url + "/foo/bar.txt?t=json")
        def _check1(data):
            self.failUnlessIsBarJSON(data)
            self.failUnlessHasBarDotTxtMetadata(data)
            return
        d.addCallback(_check1)
        return d

    def test_GET_FILEURL_json_mutable_type(self):
        # The JSON should include format, which says whether the
        # file is SDMF or MDMF
        d = self.PUT("/uri?format=mdmf",
                     self.NEWFILE_CONTENTS * 300000)
        d.addCallback(lambda filecap: self.GET("/uri/%s?t=json" % filecap))
        def _got_json(raw, version):
            data = json.loads(raw)
            assert "filenode" == data[0]
            data = data[1]
            assert isinstance(data, dict)

            self.failUnlessIn("format", data)
            self.failUnlessEqual(data["format"], version)

        d.addCallback(_got_json, "MDMF")
        # Now make an SDMF file and check that it is reported correctly.
        d.addCallback(lambda ignored:
            self.PUT("/uri?format=sdmf",
                      self.NEWFILE_CONTENTS * 300000))
        d.addCallback(lambda filecap: self.GET("/uri/%s?t=json" % filecap))
        d.addCallback(_got_json, "SDMF")
        return d

    def test_GET_FILEURL_json_mdmf(self):
        d = self.GET("/uri/%s?t=json" % urllib.quote(self._quux_txt_uri))
        d.addCallback(self.failUnlessIsQuuxJSON)
        return d

    def test_GET_FILEURL_json_missing(self):
        d = self.GET(self.public_url + "/foo/missing?json")
        d.addBoth(self.should404, "test_GET_FILEURL_json_missing")
        return d

    def test_GET_FILEURL_uri(self):
        d = self.GET(self.public_url + "/foo/bar.txt?t=uri")
        def _check(res):
            self.failUnlessReallyEqual(res, self._bar_txt_uri)
        d.addCallback(_check)
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/bar.txt?t=readonly-uri"))
        def _check2(res):
            # for now, for files, uris and readonly-uris are the same
            self.failUnlessReallyEqual(res, self._bar_txt_uri)
        d.addCallback(_check2)
        return d

    @inlineCallbacks
    def test_GET_FILEURL_badtype(self):
        url = self.webish_url + self.public_url + "/foo/bar.txt?t=bogus"
        yield self.assertHTTPError(url, 400, "bad t=bogus")

    def test_CSS_FILE(self):
        d = self.GET("/tahoe.css", followRedirect=True)
        def _check(res):
            CSS_STYLE=re.compile('toolbar\s{.+text-align:\scenter.+toolbar-item.+display:\sinline',re.DOTALL)
            self.failUnless(CSS_STYLE.search(res), res)
        d.addCallback(_check)
        return d

    def test_GET_FILEURL_uri_missing(self):
        d = self.GET(self.public_url + "/foo/missing?t=uri")
        d.addBoth(self.should404, "test_GET_FILEURL_uri_missing")
        return d

    def _check_upload_and_mkdir_forms(self, html):
        # We should have a form to create a file, with radio buttons that allow
        # the user to toggle whether it is a CHK/LIT (default), SDMF, or MDMF file.
        self.failUnless(re.search('<input (name="t" |value="upload" |type="hidden" ){3}/>', html), html)
        self.failUnless(re.search('<input [^/]*id="upload-chk"', html), html)
        self.failUnless(re.search('<input [^/]*id="upload-sdmf"', html), html)
        self.failUnless(re.search('<input [^/]*id="upload-mdmf"', html), html)

        # We should also have the ability to create a mutable directory, with
        # radio buttons that allow the user to toggle whether it is an SDMF (default)
        # or MDMF directory.
        self.failUnless(re.search('<input (name="t" |value="mkdir" |type="hidden" ){3}/>', html), html)
        self.failUnless(re.search('<input [^/]*id="mkdir-sdmf"', html), html)
        self.failUnless(re.search('<input [^/]*id="mkdir-mdmf"', html), html)

        self.failUnlessIn(FAVICON_MARKUP, html)

    def test_GET_DIRECTORY_html(self):
        d = self.GET(self.public_url + "/foo", followRedirect=True)
        def _check(html):
            self.failUnlessIn('<li class="toolbar-item"><a href="../../..">Return to Welcome page</a></li>', html)
            self._check_upload_and_mkdir_forms(html)
            self.failUnlessIn("quux", html)
        d.addCallback(_check)
        return d

    def test_GET_DIRECTORY_html_filenode_encoding(self):
        d = self.GET(self.public_url + "/foo", followRedirect=True)
        def _check(html):
            # Check if encoded entries are there
            self.failUnlessIn('@@named=/' + self._htmlname_urlencoded + '" rel="noreferrer">'
                              + self._htmlname_escaped + '</a>', html)
            self.failUnlessIn('value="' + self._htmlname_escaped_attr + '"', html)
            self.failIfIn(self._htmlname_escaped_double, html)
            # Make sure that Nevow escaping actually works by checking for unsafe characters
            # and that '&' is escaped.
            for entity in '<>':
                self.failUnlessIn(entity, self._htmlname_raw)
                self.failIfIn(entity, self._htmlname_escaped)
            self.failUnlessIn('&', re.sub(r'&(amp|lt|gt|quot|apos);', '', self._htmlname_raw))
            self.failIfIn('&', re.sub(r'&(amp|lt|gt|quot|apos);', '', self._htmlname_escaped))
        d.addCallback(_check)
        return d

    def test_GET_root_html(self):
        d = self.GET("/")
        d.addCallback(self._check_upload_and_mkdir_forms)
        return d

    def test_GET_DIRURL(self):
        # the addSlash means we get a redirect here
        # from /uri/$URI/foo/ , we need ../../../ to get back to the root
        ROOT = "../../.."
        d = self.GET(self.public_url + "/foo", followRedirect=True)
        def _check(res):
            self.failUnlessIn('<a href="%s">Return to Welcome page' % ROOT, res)

            # the FILE reference points to a URI, but it should end in bar.txt
            bar_url = ("%s/file/%s/@@named=/bar.txt" %
                       (ROOT, urllib.quote(self._bar_txt_uri)))
            get_bar = "".join([r'<td>FILE</td>',
                               r'\s+<td>',
                               r'<a href="%s" rel="noreferrer">bar.txt</a>' % bar_url,
                               r'</td>',
                               r'\s+<td align="right">%d</td>' % len(self.BAR_CONTENTS),
                               ])
            self.failUnless(re.search(get_bar, res), res)
            for label in ['unlink', 'rename/relink']:
                for line in res.split("\n"):
                    # find the line that contains the relevant button for bar.txt
                    if ("form action" in line and
                        ('value="%s"' % (label,)) in line and
                        'value="bar.txt"' in line):
                        # the form target should use a relative URL
                        foo_url = urllib.quote("%s/uri/%s/" % (ROOT, self._foo_uri))
                        self.failUnlessIn('action="%s"' % foo_url, line)
                        # and the when_done= should too
                        #done_url = urllib.quote(???)
                        #self.failUnlessIn('name="when_done" value="%s"' % done_url, line)

                        # 'unlink' needs to use POST because it directly has a side effect
                        if label == 'unlink':
                            self.failUnlessIn('method="post"', line)
                        break
                else:
                    self.fail("unable to find '%s bar.txt' line" % (label,))

            # the DIR reference just points to a URI
            sub_url = ("%s/uri/%s/" % (ROOT, urllib.quote(self._sub_uri)))
            get_sub = ((r'<td>DIR</td>')
                       +r'\s+<td><a href="%s">sub</a></td>' % sub_url)
            self.failUnless(re.search(get_sub, res), res)
        d.addCallback(_check)

        # look at a readonly directory
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/reedownlee", followRedirect=True))
        def _check2(res):
            self.failUnlessIn("(read-only)", res)
            self.failIfIn("Upload a file", res)
        d.addCallback(_check2)

        # and at a directory that contains a readonly directory
        d.addCallback(lambda res:
                      self.GET(self.public_url, followRedirect=True))
        def _check3(res):
            self.failUnless(re.search('<td>DIR-RO</td>'
                                      r'\s+<td><a href="[\.\/]+/uri/URI%3ADIR2-RO%3A[^"]+">reedownlee</a></td>', res), res)
        d.addCallback(_check3)

        # and an empty directory
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/empty/"))
        def _check4(res):
            self.failUnlessIn("directory is empty", res)
            MKDIR_BUTTON_RE=re.compile('<input (type="hidden" |name="t" |value="mkdir" ){3}/>.*<legend class="freeform-form-label">Create a new directory in this directory</legend>.*<input (type="submit" |class="btn" |value="Create" ){3}/>', re.I)
            self.failUnless(MKDIR_BUTTON_RE.search(res), res)
        d.addCallback(_check4)

        # and at a literal directory
        tiny_litdir_uri = "URI:DIR2-LIT:gqytunj2onug64tufqzdcosvkjetutcjkq5gw4tvm5vwszdgnz5hgyzufqydulbshj5x2lbm" # contains one child which is itself also LIT
        d.addCallback(lambda res:
                      self.GET("/uri/" + tiny_litdir_uri + "/", followRedirect=True))
        def _check5(res):
            self.failUnlessIn('(immutable)', res)
            self.failUnless(re.search('<td>FILE</td>'
                                      r'\s+<td><a href="[\.\/]+/file/URI%3ALIT%3Akrugkidfnzsc4/@@named=/short" rel="noreferrer">short</a></td>', res), res)
        d.addCallback(_check5)
        return d

    @inlineCallbacks
    def test_GET_DIRURL_badtype(self):
        url = self.webish_url + self.public_url + "/foo?t=bogus"
        yield self.assertHTTPError(url, 400, "bad t=bogus")

    def test_GET_DIRURL_json(self):
        d = self.GET(self.public_url + "/foo?t=json")
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    def test_GET_DIRURL_json_format(self):
        d = self.PUT(self.public_url + \
                     "/foo/sdmf.txt?format=sdmf",
                     self.NEWFILE_CONTENTS * 300000)
        d.addCallback(lambda ignored:
            self.PUT(self.public_url + \
                     "/foo/mdmf.txt?format=mdmf",
                     self.NEWFILE_CONTENTS * 300000))
        # Now we have an MDMF and SDMF file in the directory. If we GET
        # its JSON, we should see their encodings.
        d.addCallback(lambda ignored:
            self.GET(self.public_url + "/foo?t=json"))
        def _got_json(raw):
            data = json.loads(raw)
            assert data[0] == "dirnode"

            data = data[1]
            kids = data['children']

            mdmf_data = kids['mdmf.txt'][1]
            self.failUnlessIn("format", mdmf_data)
            self.failUnlessEqual(mdmf_data["format"], "MDMF")

            sdmf_data = kids['sdmf.txt'][1]
            self.failUnlessIn("format", sdmf_data)
            self.failUnlessEqual(sdmf_data["format"], "SDMF")
        d.addCallback(_got_json)
        return d


    def test_POST_DIRURL_manifest_no_ophandle(self):
        d = self.shouldFail2(error.Error,
                             "test_POST_DIRURL_manifest_no_ophandle",
                             "400 Bad Request",
                             "slow operation requires ophandle=",
                             self.POST, self.public_url, t="start-manifest")
        return d

    def test_POST_DIRURL_manifest(self):
        d = defer.succeed(None)
        def getman(ignored, output):
            url = self.webish_url + self.public_url + "/foo/?t=start-manifest&ophandle=125"
            d = do_http("post", url, allow_redirects=True,
                        browser_like_redirects=True)
            d.addCallback(self.wait_for_operation, "125")
            d.addCallback(self.get_operation_results, "125", output)
            return d
        d.addCallback(getman, None)
        def _got_html(manifest):
            soup = BeautifulSoup(manifest, 'html5lib')
            assert_soup_has_text(self, soup, "Manifest of SI=")
            assert_soup_has_text(self, soup, "sub")
            assert_soup_has_text(self, soup, self._sub_uri)
            assert_soup_has_text(self, soup, "sub/baz.txt")
            assert_soup_has_favicon(self, soup)
        d.addCallback(_got_html)

        # both t=status and unadorned GET should be identical
        d.addCallback(lambda res: self.GET("/operations/125"))
        d.addCallback(_got_html)

        d.addCallback(getman, "html")
        d.addCallback(_got_html)
        d.addCallback(getman, "text")
        def _got_text(manifest):
            self.failUnlessIn("\nsub " + self._sub_uri + "\n", manifest)
            self.failUnlessIn("\nsub/baz.txt URI:CHK:", manifest)
        d.addCallback(_got_text)
        d.addCallback(getman, "JSON")
        def _got_json(res):
            data = res["manifest"]
            got = {}
            for (path_list, cap) in data:
                got[tuple(path_list)] = cap
            self.failUnlessReallyEqual(to_str(got[(u"sub",)]), self._sub_uri)
            self.failUnlessIn((u"sub", u"baz.txt"), got)
            self.failUnlessIn("finished", res)
            self.failUnlessIn("origin", res)
            self.failUnlessIn("storage-index", res)
            self.failUnlessIn("verifycaps", res)
            self.failUnlessIn("stats", res)
        d.addCallback(_got_json)
        return d

    def test_POST_DIRURL_deepsize_no_ophandle(self):
        d = self.shouldFail2(error.Error,
                             "test_POST_DIRURL_deepsize_no_ophandle",
                             "400 Bad Request",
                             "slow operation requires ophandle=",
                             self.POST, self.public_url, t="start-deep-size")
        return d

    def test_POST_DIRURL_deepsize(self):
        url = self.webish_url + self.public_url + "/foo/?t=start-deep-size&ophandle=126"
        d = do_http("post", url, allow_redirects=True,
                    browser_like_redirects=True)
        d.addCallback(self.wait_for_operation, "126")
        d.addCallback(self.get_operation_results, "126", "json")
        def _got_json(data):
            self.failUnlessReallyEqual(data["finished"], True)
            size = data["size"]
            self.failUnless(size > 1000)
        d.addCallback(_got_json)
        d.addCallback(self.get_operation_results, "126", "text")
        def _got_text(res):
            mo = re.search(r'^size: (\d+)$', res, re.M)
            self.failUnless(mo, res)
            size = int(mo.group(1))
            # with directories, the size varies.
            self.failUnless(size > 1000)
        d.addCallback(_got_text)
        return d

    def test_POST_DIRURL_deepstats_no_ophandle(self):
        d = self.shouldFail2(error.Error,
                             "test_POST_DIRURL_deepstats_no_ophandle",
                             "400 Bad Request",
                             "slow operation requires ophandle=",
                             self.POST, self.public_url, t="start-deep-stats")
        return d

    def test_POST_DIRURL_deepstats(self):
        url = self.webish_url + self.public_url + "/foo/?t=start-deep-stats&ophandle=127"
        d = do_http("post", url,
                    allow_redirects=True, browser_like_redirects=True)
        d.addCallback(self.wait_for_operation, "127")
        d.addCallback(self.get_operation_results, "127", "json")
        def _got_json(stats):
            expected = {"count-immutable-files": 4,
                        "count-mutable-files": 2,
                        "count-literal-files": 0,
                        "count-files": 6,
                        "count-directories": 3,
                        "size-immutable-files": 76,
                        "size-literal-files": 0,
                        #"size-directories": 1912, # varies
                        #"largest-directory": 1590,
                        "largest-directory-children": 8,
                        "largest-immutable-file": 19,
                        "api-version": 1,
                        }
            for k,v in expected.iteritems():
                self.failUnlessReallyEqual(stats[k], v,
                                           "stats[%s] was %s, not %s" %
                                           (k, stats[k], v))
            self.failUnlessReallyEqual(stats["size-files-histogram"],
                                       [ [11, 31, 4] ])
        d.addCallback(_got_json)
        return d

    def test_POST_DIRURL_stream_manifest(self):
        d = self.POST(self.public_url + "/foo/?t=stream-manifest")
        def _check(res):
            self.failUnless(res.endswith("\n"))
            units = [json.loads(t) for t in res[:-1].split("\n")]
            self.failUnlessReallyEqual(len(units), 10)
            self.failUnlessEqual(units[-1]["type"], "stats")
            first = units[0]
            self.failUnlessEqual(first["path"], [])
            self.failUnlessReallyEqual(to_str(first["cap"]), self._foo_uri)
            self.failUnlessEqual(first["type"], "directory")
            baz = [u for u in units[:-1] if to_str(u["cap"]) == self._baz_file_uri][0]
            self.failUnlessEqual(baz["path"], ["sub", "baz.txt"])
            self.failIfEqual(baz["storage-index"], None)
            self.failIfEqual(baz["verifycap"], None)
            self.failIfEqual(baz["repaircap"], None)
            # XXX: Add quux and baz to this test.
            return
        d.addCallback(_check)
        return d

    def test_GET_DIRURL_uri(self):
        d = self.GET(self.public_url + "/foo?t=uri")
        def _check(res):
            self.failUnlessReallyEqual(to_str(res), self._foo_uri)
        d.addCallback(_check)
        return d

    def test_GET_DIRURL_readonly_uri(self):
        d = self.GET(self.public_url + "/foo?t=readonly-uri")
        def _check(res):
            self.failUnlessReallyEqual(to_str(res), self._foo_readonly_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWDIRURL(self):
        d = self.PUT(self.public_url + "/foo/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_PUT_NEWDIRURL_mdmf(self):
        d = self.PUT(self.public_url + "/foo/newdir?t=mkdir&format=mdmf", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(lambda node:
            self.failUnlessEqual(node._node.get_version(), MDMF_VERSION))
        return d

    def test_PUT_NEWDIRURL_sdmf(self):
        d = self.PUT(self.public_url + "/foo/newdir?t=mkdir&format=sdmf",
                     "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(lambda node:
            self.failUnlessEqual(node._node.get_version(), SDMF_VERSION))
        return d

    @inlineCallbacks
    def test_PUT_NEWDIRURL_bad_format(self):
        url = (self.webish_url + self.public_url +
               "/foo/newdir=?t=mkdir&format=foo")
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="put", data="")

    def test_POST_NEWDIRURL(self):
        d = self.POST2(self.public_url + "/foo/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_NEWDIRURL_mdmf(self):
        d = self.POST2(self.public_url + "/foo/newdir?t=mkdir&format=mdmf", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(lambda node:
            self.failUnlessEqual(node._node.get_version(), MDMF_VERSION))
        return d

    def test_POST_NEWDIRURL_sdmf(self):
        d = self.POST2(self.public_url + "/foo/newdir?t=mkdir&format=sdmf", "")
        d.addCallback(lambda res:
            self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(lambda node:
            self.failUnlessEqual(node._node.get_version(), SDMF_VERSION))
        return d

    @inlineCallbacks
    def test_POST_NEWDIRURL_bad_format(self):
        url = (self.webish_url + self.public_url +
               "/foo/newdir?t=mkdir&format=foo")
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="post", data="")

    def test_POST_NEWDIRURL_emptyname(self):
        # an empty pathname component (i.e. a double-slash) is disallowed
        d = self.shouldFail2(error.Error, "POST_NEWDIRURL_emptyname",
                             "400 Bad Request",
                             "The webapi does not allow empty pathname components, i.e. a double slash",
                             self.POST, self.public_url + "//?t=mkdir")
        return d

    def _do_POST_NEWDIRURL_initial_children_test(self, version=None):
        (newkids, caps) = self._create_initial_children()
        query = "/foo/newdir?t=mkdir-with-children"
        if version == MDMF_VERSION:
            query += "&format=mdmf"
        elif version == SDMF_VERSION:
            query += "&format=sdmf"
        else:
            version = SDMF_VERSION # for later
        d = self.POST2(self.public_url + query,
                       json.dumps(newkids))
        def _check(uri):
            n = self.s.create_node_from_uri(uri.strip())
            d2 = self.failUnlessNodeKeysAre(n, newkids.keys())
            self.failUnlessEqual(n._node.get_version(), version)
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"child-imm",
                                                       caps['filecap1']))
            d2.addCallback(lambda ign:
                           self.failUnlessRWChildURIIs(n, u"child-mutable",
                                                       caps['filecap2']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"child-mutable-ro",
                                                       caps['filecap3']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"unknownchild-ro",
                                                       caps['unknown_rocap']))
            d2.addCallback(lambda ign:
                           self.failUnlessRWChildURIIs(n, u"unknownchild-rw",
                                                       caps['unknown_rwcap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"unknownchild-imm",
                                                       caps['unknown_immcap']))
            d2.addCallback(lambda ign:
                           self.failUnlessRWChildURIIs(n, u"dirchild",
                                                       caps['dircap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"dirchild-lit",
                                                       caps['litdircap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"dirchild-empty",
                                                       caps['emptydircap']))
            return d2
        d.addCallback(_check)
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, newkids.keys())
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"child-imm", caps['filecap1'])
        return d

    def test_POST_NEWDIRURL_initial_children(self):
        return self._do_POST_NEWDIRURL_initial_children_test()

    def test_POST_NEWDIRURL_initial_children_mdmf(self):
        return self._do_POST_NEWDIRURL_initial_children_test(MDMF_VERSION)

    def test_POST_NEWDIRURL_initial_children_sdmf(self):
        return self._do_POST_NEWDIRURL_initial_children_test(SDMF_VERSION)

    @inlineCallbacks
    def test_POST_NEWDIRURL_initial_children_bad_format(self):
        (newkids, caps) = self._create_initial_children()
        url = (self.webish_url + self.public_url +
               "/foo/newdir?t=mkdir-with-children&format=foo")
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="post", data=json.dumps(newkids))

    def test_POST_NEWDIRURL_immutable(self):
        (newkids, caps) = self._create_immutable_children()
        d = self.POST2(self.public_url + "/foo/newdir?t=mkdir-immutable",
                       json.dumps(newkids))
        def _check(uri):
            n = self.s.create_node_from_uri(uri.strip())
            d2 = self.failUnlessNodeKeysAre(n, newkids.keys())
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"child-imm",
                                                       caps['filecap1']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"unknownchild-imm",
                                                       caps['unknown_immcap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"dirchild-imm",
                                                       caps['immdircap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"dirchild-lit",
                                                       caps['litdircap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"dirchild-empty",
                                                       caps['emptydircap']))
            return d2
        d.addCallback(_check)
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, newkids.keys())
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"child-imm", caps['filecap1'])
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"unknownchild-imm", caps['unknown_immcap'])
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"dirchild-imm", caps['immdircap'])
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"dirchild-lit", caps['litdircap'])
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"dirchild-empty", caps['emptydircap'])
        d.addErrback(self.explain_web_error)
        return d

    def test_POST_NEWDIRURL_immutable_bad(self):
        (newkids, caps) = self._create_initial_children()
        d = self.shouldFail2(error.Error, "test_POST_NEWDIRURL_immutable_bad",
                             "400 Bad Request",
                             "needed to be immutable but was not",
                             self.POST2,
                             self.public_url + "/foo/newdir?t=mkdir-immutable",
                             json.dumps(newkids))
        return d

    def test_PUT_NEWDIRURL_exists(self):
        d = self.PUT(self.public_url + "/foo/sub?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"sub"))
        d.addCallback(lambda res: self._foo_node.get(u"sub"))
        d.addCallback(self.failUnlessNodeKeysAre, [u"baz.txt"])
        return d

    def test_PUT_NEWDIRURL_blocked(self):
        d = self.shouldFail2(error.Error, "PUT_NEWDIRURL_blocked",
                             "409 Conflict", "Unable to create directory 'bar.txt': a file was in the way",
                             self.PUT,
                             self.public_url + "/foo/bar.txt/sub?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"sub"))
        d.addCallback(lambda res: self._foo_node.get(u"sub"))
        d.addCallback(self.failUnlessNodeKeysAre, [u"baz.txt"])
        return d

    def test_PUT_NEWDIRURL_mkdirs(self):
        d = self.PUT(self.public_url + "/foo/subdir/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"subdir"))
        d.addCallback(lambda res:
                      self._foo_node.get_child_at_path(u"subdir/newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_PUT_NEWDIRURL_mkdirs_mdmf(self):
        d = self.PUT(self.public_url + "/foo/subdir/newdir?t=mkdir&format=mdmf", "")
        d.addCallback(lambda ignored:
            self.failUnlessNodeHasChild(self._foo_node, u"subdir"))
        d.addCallback(lambda ignored:
            self.failIfNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda ignored:
            self._foo_node.get_child_at_path(u"subdir"))
        def _got_subdir(subdir):
            # XXX: What we want?
            #self.failUnlessEqual(subdir._node.get_version(), MDMF_VERSION)
            self.failUnlessNodeHasChild(subdir, u"newdir")
            return subdir.get_child_at_path(u"newdir")
        d.addCallback(_got_subdir)
        d.addCallback(lambda newdir:
            self.failUnlessEqual(newdir._node.get_version(), MDMF_VERSION))
        return d

    def test_PUT_NEWDIRURL_mkdirs_sdmf(self):
        d = self.PUT(self.public_url + "/foo/subdir/newdir?t=mkdir&format=sdmf", "")
        d.addCallback(lambda ignored:
            self.failUnlessNodeHasChild(self._foo_node, u"subdir"))
        d.addCallback(lambda ignored:
            self.failIfNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda ignored:
            self._foo_node.get_child_at_path(u"subdir"))
        def _got_subdir(subdir):
            # XXX: What we want?
            #self.failUnlessEqual(subdir._node.get_version(), MDMF_VERSION)
            self.failUnlessNodeHasChild(subdir, u"newdir")
            return subdir.get_child_at_path(u"newdir")
        d.addCallback(_got_subdir)
        d.addCallback(lambda newdir:
            self.failUnlessEqual(newdir._node.get_version(), SDMF_VERSION))
        return d

    @inlineCallbacks
    def test_PUT_NEWDIRURL_mkdirs_bad_format(self):
        url = (self.webish_url + self.public_url +
               "/foo/subdir/newdir?t=mkdir&format=foo")
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="put", data="")

    def test_DELETE_DIRURL(self):
        d = self.DELETE(self.public_url + "/foo")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self.public_root, u"foo"))
        return d

    def test_DELETE_DIRURL_missing(self):
        d = self.DELETE(self.public_url + "/foo/missing")
        d.addBoth(self.should404, "test_DELETE_DIRURL_missing")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self.public_root, u"foo"))
        return d

    def test_DELETE_DIRURL_missing2(self):
        d = self.DELETE(self.public_url + "/missing")
        d.addBoth(self.should404, "test_DELETE_DIRURL_missing2")
        return d

    def dump_root(self):
        print("NODEWALK")
        w = webish.DirnodeWalkerMixin()
        def visitor(childpath, childnode, metadata):
            print(childpath)
        d = w.walk(self.public_root, visitor)
        return d

    def failUnlessNodeKeysAre(self, node, expected_keys):
        for k in expected_keys:
            assert isinstance(k, unicode)
        d = node.list()
        def _check(children):
            self.failUnlessReallyEqual(sorted(children.keys()), sorted(expected_keys))
        d.addCallback(_check)
        return d
    def failUnlessNodeHasChild(self, node, name):
        assert isinstance(name, unicode)
        d = node.list()
        def _check(children):
            self.failUnlessIn(name, children)
        d.addCallback(_check)
        return d
    def failIfNodeHasChild(self, node, name):
        assert isinstance(name, unicode)
        d = node.list()
        def _check(children):
            self.failIfIn(name, children)
        d.addCallback(_check)
        return d

    def failUnlessChildContentsAre(self, node, name, expected_contents):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        d.addCallback(lambda node: download_to_data(node))
        def _check(contents):
            self.failUnlessReallyEqual(contents, expected_contents)
        d.addCallback(_check)
        return d

    def failUnlessMutableChildContentsAre(self, node, name, expected_contents):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        d.addCallback(lambda node: node.download_best_version())
        def _check(contents):
            self.failUnlessReallyEqual(contents, expected_contents)
        d.addCallback(_check)
        return d

    def failUnlessRWChildURIIs(self, node, name, expected_uri):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        def _check(child):
            self.failUnless(child.is_unknown() or not child.is_readonly())
            self.failUnlessReallyEqual(child.get_uri(), expected_uri.strip())
            self.failUnlessReallyEqual(child.get_write_uri(), expected_uri.strip())
            expected_ro_uri = self._make_readonly(expected_uri)
            if expected_ro_uri:
                self.failUnlessReallyEqual(child.get_readonly_uri(), expected_ro_uri.strip())
        d.addCallback(_check)
        return d

    def failUnlessROChildURIIs(self, node, name, expected_uri):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        def _check(child):
            self.failUnless(child.is_unknown() or child.is_readonly())
            self.failUnlessReallyEqual(child.get_write_uri(), None)
            self.failUnlessReallyEqual(child.get_uri(), expected_uri.strip())
            self.failUnlessReallyEqual(child.get_readonly_uri(), expected_uri.strip())
        d.addCallback(_check)
        return d

    def failUnlessURIMatchesRWChild(self, got_uri, node, name):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        def _check(child):
            self.failUnless(child.is_unknown() or not child.is_readonly())
            self.failUnlessReallyEqual(child.get_uri(), got_uri.strip())
            self.failUnlessReallyEqual(child.get_write_uri(), got_uri.strip())
            expected_ro_uri = self._make_readonly(got_uri)
            if expected_ro_uri:
                self.failUnlessReallyEqual(child.get_readonly_uri(), expected_ro_uri.strip())
        d.addCallback(_check)
        return d

    def failUnlessURIMatchesROChild(self, got_uri, node, name):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        def _check(child):
            self.failUnless(child.is_unknown() or child.is_readonly())
            self.failUnlessReallyEqual(child.get_write_uri(), None)
            self.failUnlessReallyEqual(got_uri.strip(), child.get_uri())
            self.failUnlessReallyEqual(got_uri.strip(), child.get_readonly_uri())
        d.addCallback(_check)
        return d

    def failUnlessCHKURIHasContents(self, got_uri, contents):
        self.failUnless(self.get_all_contents()[got_uri] == contents)

    def test_POST_upload(self):
        d = self.POST(self.public_url + "/foo", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesROChild, fn, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, u"new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_unicode(self):
        filename = u"n\u00e9wer.txt" # n e-acute w e r . t x t
        d = self.POST(self.public_url + "/foo", t="upload",
                      file=(filename, self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesROChild, fn, filename)
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, filename,
                                                      self.NEWFILE_CONTENTS))
        target_url = self.public_url + u"/foo/" + filename
        d.addCallback(lambda res: self.GET(target_url))
        d.addCallback(lambda contents: self.failUnlessReallyEqual(contents,
                                                                  self.NEWFILE_CONTENTS,
                                                                  contents))
        return d

    def test_POST_upload_unicode_named(self):
        filename = u"n\u00e9wer.txt" # n e-acute w e r . t x t
        d = self.POST(self.public_url + "/foo", t="upload",
                      name=filename,
                      file=("overridden", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesROChild, fn, filename)
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, filename,
                                                      self.NEWFILE_CONTENTS))
        target_url = self.public_url + u"/foo/" + filename
        d.addCallback(lambda res: self.GET(target_url))
        d.addCallback(lambda contents: self.failUnlessReallyEqual(contents,
                                                                  self.NEWFILE_CONTENTS,
                                                                  contents))
        return d

    def test_POST_upload_no_link(self):
        d = self.POST("/uri", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        def _check_upload_results(page):
            # this should be a page which describes the results of the upload
            # that just finished.
            self.failUnlessIn("Upload Results:", page)
            self.failUnlessIn("URI:", page)
            uri_re = re.compile("URI: <tt><span>(.*)</span>")
            mo = uri_re.search(page)
            self.failUnless(mo, page)
            new_uri = mo.group(1)
            return new_uri
        d.addCallback(_check_upload_results)
        d.addCallback(self.failUnlessCHKURIHasContents, self.NEWFILE_CONTENTS)
        return d

    @inlineCallbacks
    def test_POST_upload_no_link_whendone(self):
        body, headers = self.build_form(t="upload", when_done="/",
                                        file=("new.txt", self.NEWFILE_CONTENTS))
        yield self.shouldRedirectTo(self.webish_url + "/uri",
                                    self.webish_url + "/",
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)

    @inlineCallbacks
    def test_POST_upload_no_link_whendone_results(self):
        # We encode "uri" as "%75ri" to exercise a case affected by ticket #1860
        body, headers = self.build_form(t="upload",
                                        when_done="/%75ri/%(uri)s",
                                        file=("new.txt", self.NEWFILE_CONTENTS),
                                        )
        redir_url = yield self.shouldRedirectTo(self.webish_url + "/uri", None,
                                                method="post",
                                                data=body, headers=headers,
                                                code=http.FOUND)
        res = yield do_http("get", redir_url)
        self.failUnlessReallyEqual(res, self.NEWFILE_CONTENTS)

    def test_POST_upload_no_link_mutable(self):
        d = self.POST("/uri", t="upload", mutable="true",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        def _check(filecap):
            filecap = filecap.strip()
            self.failUnless(filecap.startswith("URI:SSK:"), filecap)
            self.filecap = filecap
            u = uri.WriteableSSKFileURI.init_from_string(filecap)
            self.failUnlessIn(u.get_storage_index(), self.get_all_contents())
            n = self.s.create_node_from_uri(filecap)
            return n.download_best_version()
        d.addCallback(_check)
        def _check2(data):
            self.failUnlessReallyEqual(data, self.NEWFILE_CONTENTS)
            return self.GET("/uri/%s" % urllib.quote(self.filecap))
        d.addCallback(_check2)
        def _check3(data):
            self.failUnlessReallyEqual(data, self.NEWFILE_CONTENTS)
            return self.GET("/file/%s" % urllib.quote(self.filecap))
        d.addCallback(_check3)
        def _check4(data):
            self.failUnlessReallyEqual(data, self.NEWFILE_CONTENTS)
        d.addCallback(_check4)
        return d

    def test_POST_upload_no_link_mutable_toobig(self):
        # The SDMF size limit is no longer in place, so we should be
        # able to upload mutable files that are as large as we want them
        # to be.
        d = self.POST("/uri", t="upload", mutable="true",
                      file=("new.txt", "b" * (self.s.MUTABLE_SIZELIMIT + 1)))
        return d


    def test_POST_upload_format_unlinked(self):
        def _check_upload_unlinked(ign, format, uri_prefix):
            filename = format + ".txt"
            d = self.POST("/uri?t=upload&format=" + format,
                          file=(filename, self.NEWFILE_CONTENTS * 300000))
            def _got_results(results):
                if format.upper() in ("SDMF", "MDMF"):
                    # webapi.rst says this returns a filecap
                    filecap = results
                else:
                    # for immutable, it returns an "upload results page", and
                    # the filecap is buried inside
                    line = [l for l in results.split("\n") if "URI: " in l][0]
                    mo = re.search(r'<span>([^<]+)</span>', line)
                    filecap = mo.group(1)
                self.failUnless(filecap.startswith(uri_prefix),
                                (uri_prefix, filecap))
                return self.GET("/uri/%s?t=json" % filecap)
            d.addCallback(_got_results)
            def _got_json(raw):
                data = json.loads(raw)
                data = data[1]
                self.failUnlessIn("format", data)
                self.failUnlessEqual(data["format"], format.upper())
            d.addCallback(_got_json)
            return d
        d = defer.succeed(None)
        d.addCallback(_check_upload_unlinked, "chk", "URI:CHK")
        d.addCallback(_check_upload_unlinked, "CHK", "URI:CHK")
        d.addCallback(_check_upload_unlinked, "sdmf", "URI:SSK")
        d.addCallback(_check_upload_unlinked, "mdmf", "URI:MDMF")
        return d

    @inlineCallbacks
    def test_POST_upload_bad_format_unlinked(self):
        url = self.webish_url + "/uri?t=upload&format=foo"
        body, headers = self.build_form(file=("foo.txt", self.NEWFILE_CONTENTS * 300000))
        yield self.assertHTTPError(url, 400,
                                   "Unknown format: foo",
                                   method="post", data=body, headers=headers)

    def test_POST_upload_format(self):
        def _check_upload(ign, format, uri_prefix, fn=None):
            filename = format + ".txt"
            d = self.POST(self.public_url +
                          "/foo?t=upload&format=" + format,
                          file=(filename, self.NEWFILE_CONTENTS * 300000))
            def _got_filecap(filecap):
                if fn is not None:
                    filenameu = unicode(filename)
                    self.failUnlessURIMatchesRWChild(filecap, fn, filenameu)
                self.failUnless(filecap.startswith(uri_prefix))
                return self.GET(self.public_url + "/foo/%s?t=json" % filename)
            d.addCallback(_got_filecap)
            def _got_json(raw):
                data = json.loads(raw)
                data = data[1]
                self.failUnlessIn("format", data)
                self.failUnlessEqual(data["format"], format.upper())
            d.addCallback(_got_json)
            return d

        d = defer.succeed(None)
        d.addCallback(_check_upload, "chk", "URI:CHK")
        d.addCallback(_check_upload, "sdmf", "URI:SSK", self._foo_node)
        d.addCallback(_check_upload, "mdmf", "URI:MDMF")
        d.addCallback(_check_upload, "MDMF", "URI:MDMF")
        return d

    @inlineCallbacks
    def test_POST_upload_bad_format(self):
        url = self.webish_url + self.public_url + "/foo?t=upload&format=foo"
        body, headers = self.build_form(file=("foo.txt", self.NEWFILE_CONTENTS * 300000))
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="post", data=body, headers=headers)

    def test_POST_upload_mutable(self):
        # this creates a mutable file
        d = self.POST(self.public_url + "/foo", t="upload", mutable="true",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesRWChild, fn, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessMutableChildContentsAre(fn, u"new.txt",
                                                             self.NEWFILE_CONTENTS))
        d.addCallback(lambda res: self._foo_node.get(u"new.txt"))
        def _got(newnode):
            self.failUnless(IMutableFileNode.providedBy(newnode))
            self.failUnless(newnode.is_mutable())
            self.failIf(newnode.is_readonly())
            self._mutable_node = newnode
            self._mutable_uri = newnode.get_uri()
        d.addCallback(_got)

        # now upload it again and make sure that the URI doesn't change
        NEWER_CONTENTS = self.NEWFILE_CONTENTS + "newer\n"
        d.addCallback(lambda res:
                      self.POST(self.public_url + "/foo", t="upload",
                                mutable="true",
                                file=("new.txt", NEWER_CONTENTS)))
        d.addCallback(self.failUnlessURIMatchesRWChild, fn, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessMutableChildContentsAre(fn, u"new.txt",
                                                             NEWER_CONTENTS))
        d.addCallback(lambda res: self._foo_node.get(u"new.txt"))
        def _got2(newnode):
            self.failUnless(IMutableFileNode.providedBy(newnode))
            self.failUnless(newnode.is_mutable())
            self.failIf(newnode.is_readonly())
            self.failUnlessReallyEqual(self._mutable_uri, newnode.get_uri())
        d.addCallback(_got2)

        # upload a second time, using PUT instead of POST
        NEW2_CONTENTS = NEWER_CONTENTS + "overwrite with PUT\n"
        d.addCallback(lambda res:
                      self.PUT(self.public_url + "/foo/new.txt", NEW2_CONTENTS))
        d.addCallback(self.failUnlessURIMatchesRWChild, fn, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessMutableChildContentsAre(fn, u"new.txt",
                                                             NEW2_CONTENTS))

        # finally list the directory, since mutable files are displayed
        # slightly differently

        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/",
                               followRedirect=True))
        def _check_page(res):
            # TODO: assert more about the contents
            self.failUnlessIn("SSK", res)
            return res
        d.addCallback(_check_page)

        d.addCallback(lambda res: self._foo_node.get(u"new.txt"))
        def _got3(newnode):
            self.failUnless(IMutableFileNode.providedBy(newnode))
            self.failUnless(newnode.is_mutable())
            self.failIf(newnode.is_readonly())
            self.failUnlessReallyEqual(self._mutable_uri, newnode.get_uri())
        d.addCallback(_got3)

        # look at the JSON form of the enclosing directory
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/?t=json",
                               followRedirect=True))
        def _check_page_json(res):
            parsed = json.loads(res)
            self.failUnlessEqual(parsed[0], "dirnode")
            children = dict( [(unicode(name),value)
                              for (name,value)
                              in parsed[1]["children"].iteritems()] )
            self.failUnlessIn(u"new.txt", children)
            new_json = children[u"new.txt"]
            self.failUnlessEqual(new_json[0], "filenode")
            self.failUnless(new_json[1]["mutable"])
            self.failUnlessReallyEqual(to_str(new_json[1]["rw_uri"]), self._mutable_uri)
            ro_uri = self._mutable_node.get_readonly().to_string()
            self.failUnlessReallyEqual(to_str(new_json[1]["ro_uri"]), ro_uri)
        d.addCallback(_check_page_json)

        # and the JSON form of the file
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/new.txt?t=json"))
        def _check_file_json(res):
            parsed = json.loads(res)
            self.failUnlessEqual(parsed[0], "filenode")
            self.failUnless(parsed[1]["mutable"])
            self.failUnlessReallyEqual(to_str(parsed[1]["rw_uri"]), self._mutable_uri)
            ro_uri = self._mutable_node.get_readonly().to_string()
            self.failUnlessReallyEqual(to_str(parsed[1]["ro_uri"]), ro_uri)
        d.addCallback(_check_file_json)

        # and look at t=uri and t=readonly-uri
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/new.txt?t=uri"))
        d.addCallback(lambda res: self.failUnlessReallyEqual(res, self._mutable_uri))
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/new.txt?t=readonly-uri"))
        def _check_ro_uri(res):
            ro_uri = self._mutable_node.get_readonly().to_string()
            self.failUnlessReallyEqual(res, ro_uri)
        d.addCallback(_check_ro_uri)

        # make sure we can get to it from /uri/URI
        d.addCallback(lambda res:
                      self.GET("/uri/%s" % urllib.quote(self._mutable_uri)))
        d.addCallback(lambda res:
                      self.failUnlessReallyEqual(res, NEW2_CONTENTS))

        # and that HEAD computes the size correctly
        d.addCallback(lambda res:
                      self.HEAD(self.public_url + "/foo/new.txt",
                                return_response=True))
        def _got_headers(res_and_status_and_headers):
            (res, status, headers) = res_and_status_and_headers
            self.failUnlessReallyEqual(res, "")
            self.failUnlessReallyEqual(headers.getRawHeaders("content-length")[0],
                                       str(len(NEW2_CONTENTS)))
            self.failUnlessReallyEqual(headers.getRawHeaders("content-type"),
                                       ["text/plain"])
        d.addCallback(_got_headers)

        # make sure that outdated size limits aren't enforced anymore.
        d.addCallback(lambda ignored:
            self.POST(self.public_url + "/foo", t="upload",
                      mutable="true",
                      file=("new.txt",
                            "b" * (self.s.MUTABLE_SIZELIMIT+1))))
        d.addErrback(self.dump_error)
        return d

    def test_POST_upload_mutable_toobig(self):
        # SDMF had a size limti that was removed a while ago. MDMF has
        # never had a size limit. Test to make sure that we do not
        # encounter errors when trying to upload large mutable files,
        # since there should be no coded prohibitions regarding large
        # mutable files.
        d = self.POST(self.public_url + "/foo",
                      t="upload", mutable="true",
                      file=("new.txt", "b" * (self.s.MUTABLE_SIZELIMIT + 1)))
        return d

    def dump_error(self, f):
        # if the web server returns an error code (like 400 Bad Request),
        # web.client.getPage puts the HTTP response body into the .response
        # attribute of the exception object that it gives back. It does not
        # appear in the Failure's repr(), so the ERROR that trial displays
        # will be rather terse and unhelpful. addErrback this method to the
        # end of your chain to get more information out of these errors.
        if f.check(error.Error):
            print("web.error.Error:")
            print(f)
            print(f.value.response)
        return f

    def test_POST_upload_replace(self):
        d = self.POST(self.public_url + "/foo", t="upload",
                      file=("bar.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesROChild, fn, u"bar.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, u"bar.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_no_replace_ok(self):
        d = self.POST(self.public_url + "/foo?replace=false", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/new.txt"))
        d.addCallback(lambda res: self.failUnlessReallyEqual(res,
                                                             self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_no_replace_queryarg(self):
        d = self.POST(self.public_url + "/foo?replace=false", t="upload",
                      file=("bar.txt", self.NEWFILE_CONTENTS))
        d.addBoth(self.shouldFail, error.Error,
                  "POST_upload_no_replace_queryarg",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_POST_upload_no_replace_field(self):
        d = self.POST(self.public_url + "/foo", t="upload", replace="false",
                      file=("bar.txt", self.NEWFILE_CONTENTS))
        d.addBoth(self.shouldFail, error.Error, "POST_upload_no_replace_field",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    @inlineCallbacks
    def test_POST_upload_whendone(self):
        body, headers = self.build_form(t="upload", when_done="/THERE",
                                        file=("new.txt", self.NEWFILE_CONTENTS))
        yield self.shouldRedirectTo(self.webish_url + self.public_url + "/foo",
                                    self.webish_url + "/THERE",
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)
        fn = self._foo_node
        yield self.failUnlessChildContentsAre(fn, u"new.txt",
                                              self.NEWFILE_CONTENTS)

    def test_POST_upload_named(self):
        fn = self._foo_node
        d = self.POST(self.public_url + "/foo", t="upload",
                      name="new.txt", file=self.NEWFILE_CONTENTS)
        d.addCallback(self.failUnlessURIMatchesROChild, fn, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, u"new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_named_badfilename(self):
        d = self.POST(self.public_url + "/foo", t="upload",
                      name="slashes/are/bad.txt", file=self.NEWFILE_CONTENTS)
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_upload_named_badfilename",
                  "400 Bad Request",
                  "name= may not contain a slash",
                  )
        # make sure that nothing was added
        d.addCallback(lambda res:
                      self.failUnlessNodeKeysAre(self._foo_node,
                                                 [self._htmlname_unicode,
                                                  u"bar.txt", u"baz.txt", u"blockingfile",
                                                  u"empty", u"n\u00fc.txt", u"quux.txt",
                                                  u"sub"]))
        return d

    @inlineCallbacks
    def test_POST_FILEURL_check(self):
        bar_url = self.public_url + "/foo/bar.txt"
        res = yield self.POST(bar_url, t="check")
        self.failUnlessIn("Healthy :", res)

        redir_url = "http://allmydata.org/TARGET"
        body, headers = self.build_form(t="check", when_done=redir_url)
        yield self.shouldRedirectTo(self.webish_url + bar_url, redir_url,
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)

        res = yield self.POST(bar_url, t="check", return_to=redir_url)
        self.failUnlessIn("Healthy :", res)
        self.failUnlessIn("Return to file", res)
        self.failUnlessIn(redir_url, res)

        res = yield self.POST(bar_url, t="check", output="JSON")
        data = json.loads(res)
        self.failUnlessIn("storage-index", data)
        self.failUnless(data["results"]["healthy"])

    @inlineCallbacks
    def test_POST_FILEURL_check_and_repair(self):
        bar_url = self.public_url + "/foo/bar.txt"
        res = yield self.POST(bar_url, t="check", repair="true")
        self.failUnlessIn("Healthy :", res)

        redir_url = "http://allmydata.org/TARGET"
        body, headers = self.build_form(t="check", repair="true",
                                        when_done=redir_url)
        yield self.shouldRedirectTo(self.webish_url + bar_url, redir_url,
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)

        res = yield self.POST(bar_url, t="check", return_to=redir_url)
        self.failUnlessIn("Healthy :", res)
        self.failUnlessIn("Return to file", res)
        self.failUnlessIn(redir_url, res)

    @inlineCallbacks
    def test_POST_DIRURL_check(self):
        foo_url = self.public_url + "/foo/"
        res = yield self.POST(foo_url, t="check")
        self.failUnlessIn("Healthy :", res)

        redir_url = "http://allmydata.org/TARGET"
        body, headers = self.build_form(t="check", when_done=redir_url)
        yield self.shouldRedirectTo(self.webish_url + foo_url, redir_url,
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)

        res = yield self.POST(foo_url, t="check", return_to=redir_url)
        self.failUnlessIn("Healthy :", res)
        self.failUnlessIn("Return to file/directory", res)
        self.failUnlessIn(redir_url, res)

        res = yield self.POST(foo_url, t="check", output="JSON")
        data = json.loads(res)
        self.failUnlessIn("storage-index", data)
        self.failUnless(data["results"]["healthy"])

    @inlineCallbacks
    def test_POST_DIRURL_check_and_repair(self):
        foo_url = self.public_url + "/foo/"
        res = yield self.POST(foo_url, t="check", repair="true")
        self.failUnlessIn("Healthy :", res)

        redir_url = "http://allmydata.org/TARGET"
        body, headers = self.build_form(t="check", repair="true",
                                        when_done=redir_url)
        yield self.shouldRedirectTo(self.webish_url + foo_url, redir_url,
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)
        res = yield self.POST(foo_url, t="check", return_to=redir_url)
        self.failUnlessIn("Healthy :", res)
        self.failUnlessIn("Return to file/directory", res)
        self.failUnlessIn(redir_url, res)

    def test_POST_FILEURL_mdmf_check(self):
        quux_url = "/uri/%s" % urllib.quote(self._quux_txt_uri)
        d = self.POST(quux_url, t="check")
        def _check(res):
            self.failUnlessIn("Healthy", res)
        d.addCallback(_check)
        quux_extension_url = "/uri/%s" % urllib.quote("%s:3:131073" % self._quux_txt_uri)
        d.addCallback(lambda ignored:
                      self.POST(quux_extension_url, t="check"))
        d.addCallback(_check)
        return d

    def test_POST_FILEURL_mdmf_check_and_repair(self):
        quux_url = "/uri/%s" % urllib.quote(self._quux_txt_uri)
        d = self.POST(quux_url, t="check", repair="true")
        def _check(res):
            self.failUnlessIn("Healthy", res)
        d.addCallback(_check)
        quux_extension_url = "/uri/%s" % urllib.quote("%s:3:131073" % self._quux_txt_uri)
        d.addCallback(lambda ignored:
                      self.POST(quux_extension_url, t="check", repair="true"))
        d.addCallback(_check)
        return d

    def wait_for_operation(self, ignored, ophandle):
        url = "/operations/" + ophandle
        url += "?t=status&output=JSON"
        d = self.GET(url)
        def _got(res):
            data = json.loads(res)
            if not data["finished"]:
                d = self.stall(delay=1.0)
                d.addCallback(self.wait_for_operation, ophandle)
                return d
            return data
        d.addCallback(_got)
        return d

    def get_operation_results(self, ignored, ophandle, output=None):
        url = "/operations/" + ophandle
        url += "?t=status"
        if output:
            url += "&output=" + output
        d = self.GET(url)
        def _got(res):
            if output and output.lower() == "json":
                return json.loads(res)
            return res
        d.addCallback(_got)
        return d

    def test_POST_DIRURL_deepcheck_no_ophandle(self):
        d = self.shouldFail2(error.Error,
                             "test_POST_DIRURL_deepcheck_no_ophandle",
                             "400 Bad Request",
                             "slow operation requires ophandle=",
                             self.POST, self.public_url, t="start-deep-check")
        return d

    @inlineCallbacks
    def test_POST_DIRURL_deepcheck(self):
        body, headers = self.build_form(t="start-deep-check", ophandle="123")
        yield self.shouldRedirectTo(self.webish_url + self.public_url,
                                    self.webish_url + "/operations/123",
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)

        data = yield self.wait_for_operation(None, "123")
        self.failUnlessReallyEqual(data["finished"], True)
        self.failUnlessReallyEqual(data["count-objects-checked"], 11)
        self.failUnlessReallyEqual(data["count-objects-healthy"], 11)

        res = yield self.get_operation_results(None, "123", "html")
        self.failUnlessIn("Objects Checked: <span>11</span>", res)
        self.failUnlessIn("Objects Healthy: <span>11</span>", res)
        self.failUnlessIn(FAVICON_MARKUP, res)

        res = yield self.GET("/operations/123/")
        # should be the same as without the slash
        self.failUnlessIn("Objects Checked: <span>11</span>", res)
        self.failUnlessIn("Objects Healthy: <span>11</span>", res)
        self.failUnlessIn(FAVICON_MARKUP, res)

        yield self.shouldFail2(error.Error, "one", "404 Not Found",
                               "No detailed results for SI bogus",
                               self.GET, "/operations/123/bogus")

        foo_si = self._foo_node.get_storage_index()
        foo_si_s = base32.b2a(foo_si)
        res = yield self.GET("/operations/123/%s?output=JSON" % foo_si_s)
        data = json.loads(res)
        self.failUnlessEqual(data["storage-index"], foo_si_s)
        self.failUnless(data["results"]["healthy"])

    def test_POST_DIRURL_deepcheck_and_repair(self):
        url = self.webish_url + self.public_url
        body, headers = self.build_form(t="start-deep-check", repair="true",
                                        ophandle="124", output="json")
        d = do_http("post", url, data=body, headers=headers,
                    allow_redirects=True,
                    browser_like_redirects=True)
        d.addCallback(self.wait_for_operation, "124")
        def _check_json(data):
            self.failUnlessReallyEqual(data["finished"], True)
            self.failUnlessReallyEqual(data["count-objects-checked"], 11)
            self.failUnlessReallyEqual(data["count-objects-healthy-pre-repair"], 11)
            self.failUnlessReallyEqual(data["count-objects-unhealthy-pre-repair"], 0)
            self.failUnlessReallyEqual(data["count-corrupt-shares-pre-repair"], 0)
            self.failUnlessReallyEqual(data["count-repairs-attempted"], 0)
            self.failUnlessReallyEqual(data["count-repairs-successful"], 0)
            self.failUnlessReallyEqual(data["count-repairs-unsuccessful"], 0)
            self.failUnlessReallyEqual(data["count-objects-healthy-post-repair"], 11)
            self.failUnlessReallyEqual(data["count-objects-unhealthy-post-repair"], 0)
            self.failUnlessReallyEqual(data["count-corrupt-shares-post-repair"], 0)
        d.addCallback(_check_json)
        d.addCallback(self.get_operation_results, "124", "html")
        def _check_html(res):
            self.failUnlessIn("Objects Checked: <span>11</span>", res)

            self.failUnlessIn("Objects Healthy (before repair): <span>11</span>", res)
            self.failUnlessIn("Objects Unhealthy (before repair): <span>0</span>", res)
            self.failUnlessIn("Corrupt Shares (before repair): <span>0</span>", res)

            self.failUnlessIn("Repairs Attempted: <span>0</span>", res)
            self.failUnlessIn("Repairs Successful: <span>0</span>", res)
            self.failUnlessIn("Repairs Unsuccessful: <span>0</span>", res)

            self.failUnlessIn("Objects Healthy (after repair): <span>11</span>", res)
            self.failUnlessIn("Objects Unhealthy (after repair): <span>0</span>", res)
            self.failUnlessIn("Corrupt Shares (after repair): <span>0</span>", res)

            self.failUnlessIn(FAVICON_MARKUP, res)
        d.addCallback(_check_html)
        return d

    def test_POST_FILEURL_bad_t(self):
        d = self.shouldFail2(error.Error, "POST_bad_t", "400 Bad Request",
                             "POST to file: bad t=bogus",
                             self.POST, self.public_url + "/foo/bar.txt",
                             t="bogus")
        return d

    def test_POST_mkdir(self): # return value?
        d = self.POST(self.public_url + "/foo", t="mkdir", name="newdir")
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdir_mdmf(self):
        d = self.POST(self.public_url + "/foo?t=mkdir&name=newdir&format=mdmf")
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(lambda node:
            self.failUnlessEqual(node._node.get_version(), MDMF_VERSION))
        return d

    def test_POST_mkdir_sdmf(self):
        d = self.POST(self.public_url + "/foo?t=mkdir&name=newdir&format=sdmf")
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(lambda node:
            self.failUnlessEqual(node._node.get_version(), SDMF_VERSION))
        return d

    @inlineCallbacks
    def test_POST_mkdir_bad_format(self):
        url = (self.webish_url + self.public_url +
               "/foo?t=mkdir&name=newdir&format=foo")
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="post")

    def test_POST_mkdir_initial_children(self):
        (newkids, caps) = self._create_initial_children()
        d = self.POST2(self.public_url +
                       "/foo?t=mkdir-with-children&name=newdir",
                       json.dumps(newkids))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, newkids.keys())
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"child-imm", caps['filecap1'])
        return d

    def test_POST_mkdir_initial_children_mdmf(self):
        (newkids, caps) = self._create_initial_children()
        d = self.POST2(self.public_url +
                       "/foo?t=mkdir-with-children&name=newdir&format=mdmf",
                       json.dumps(newkids))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(lambda node:
            self.failUnlessEqual(node._node.get_version(), MDMF_VERSION))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"child-imm",
                       caps['filecap1'])
        return d

    # XXX: Duplication.
    def test_POST_mkdir_initial_children_sdmf(self):
        (newkids, caps) = self._create_initial_children()
        d = self.POST2(self.public_url +
                       "/foo?t=mkdir-with-children&name=newdir&format=sdmf",
                       json.dumps(newkids))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(lambda node:
            self.failUnlessEqual(node._node.get_version(), SDMF_VERSION))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"child-imm",
                       caps['filecap1'])
        return d

    @inlineCallbacks
    def test_POST_mkdir_initial_children_bad_format(self):
        (newkids, caps) = self._create_initial_children()
        url = (self.webish_url + self.public_url +
               "/foo?t=mkdir-with-children&name=newdir&format=foo")
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="post", data=json.dumps(newkids))

    def test_POST_mkdir_immutable(self):
        (newkids, caps) = self._create_immutable_children()
        d = self.POST2(self.public_url +
                       "/foo?t=mkdir-immutable&name=newdir",
                       json.dumps(newkids))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, newkids.keys())
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"child-imm", caps['filecap1'])
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"unknownchild-imm", caps['unknown_immcap'])
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"dirchild-imm", caps['immdircap'])
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"dirchild-lit", caps['litdircap'])
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessROChildURIIs, u"dirchild-empty", caps['emptydircap'])
        return d

    def test_POST_mkdir_immutable_bad(self):
        (newkids, caps) = self._create_initial_children()
        d = self.shouldFail2(error.Error, "POST_mkdir_immutable_bad",
                             "400 Bad Request",
                             "needed to be immutable but was not",
                             self.POST2,
                             self.public_url +
                             "/foo?t=mkdir-immutable&name=newdir",
                             json.dumps(newkids))
        return d

    def test_POST_mkdir_2(self):
        d = self.POST2(self.public_url + "/foo/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdirs_2(self):
        d = self.POST2(self.public_url + "/foo/bardir/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"bardir"))
        d.addCallback(lambda res: self._foo_node.get(u"bardir"))
        d.addCallback(lambda bardirnode: bardirnode.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdir_no_parentdir_noredirect(self):
        d = self.POST("/uri?t=mkdir")
        def _after_mkdir(res):
            uri.DirectoryURI.init_from_string(res)
        d.addCallback(_after_mkdir)
        return d

    def test_POST_mkdir_no_parentdir_noredirect_mdmf(self):
        d = self.POST("/uri?t=mkdir&format=mdmf")
        def _after_mkdir(res):
            u = uri.from_string(res)
            # Check that this is an MDMF writecap
            self.failUnlessIsInstance(u, uri.MDMFDirectoryURI)
        d.addCallback(_after_mkdir)
        return d

    def test_POST_mkdir_no_parentdir_noredirect_sdmf(self):
        d = self.POST("/uri?t=mkdir&format=sdmf")
        def _after_mkdir(res):
            u = uri.from_string(res)
            self.failUnlessIsInstance(u, uri.DirectoryURI)
        d.addCallback(_after_mkdir)
        return d

    @inlineCallbacks
    def test_POST_mkdir_no_parentdir_noredirect_bad_format(self):
        url = self.webish_url + self.public_url + "/uri?t=mkdir&format=foo"
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="post")

    def test_POST_mkdir_no_parentdir_noredirect2(self):
        # make sure form-based arguments (as on the welcome page) still work
        d = self.POST("/uri", t="mkdir")
        def _after_mkdir(res):
            uri.DirectoryURI.init_from_string(res)
        d.addCallback(_after_mkdir)
        d.addErrback(self.explain_web_error)
        return d

    @inlineCallbacks
    def test_POST_mkdir_no_parentdir_redirect(self):
        url = self.webish_url + "/uri?t=mkdir&redirect_to_result=true"
        target = yield self.shouldRedirectTo(url, None, method="post",
                                             code=http.SEE_OTHER)
        target = urllib.unquote(target)
        self.failUnless(target.startswith("uri/URI:DIR2:"), target)

    @inlineCallbacks
    def test_POST_mkdir_no_parentdir_redirect2(self):
        body, headers = self.build_form(t="mkdir", redirect_to_result="true")
        target = yield self.shouldRedirectTo(self.webish_url + "/uri", None,
                                             method="post",
                                             data=body, headers=headers,
                                             code=http.SEE_OTHER)
        target = urllib.unquote(target)
        self.failUnless(target.startswith("uri/URI:DIR2:"), target)

    def _make_readonly(self, u):
        ro_uri = uri.from_string(u).get_readonly()
        if ro_uri is None:
            return None
        return ro_uri.to_string()

    def _create_initial_children(self):
        contents, n, filecap1 = self.makefile(12)
        md1 = {"metakey1": "metavalue1"}
        filecap2 = make_mutable_file_uri()
        node3 = self.s.create_node_from_uri(make_mutable_file_uri())
        filecap3 = node3.get_readonly_uri()
        node4 = self.s.create_node_from_uri(make_mutable_file_uri())
        dircap = DirectoryNode(node4, None, None).get_uri()
        mdmfcap = make_mutable_file_uri(mdmf=True)
        litdircap = "URI:DIR2-LIT:ge3dumj2mewdcotyfqydulbshj5x2lbm"
        emptydircap = "URI:DIR2-LIT:"
        newkids = {u"child-imm":        ["filenode", {"rw_uri": filecap1,
                                                      "ro_uri": self._make_readonly(filecap1),
                                                      "metadata": md1, }],
                   u"child-mutable":    ["filenode", {"rw_uri": filecap2,
                                                      "ro_uri": self._make_readonly(filecap2)}],
                   u"child-mutable-ro": ["filenode", {"ro_uri": filecap3}],
                   u"unknownchild-rw":  ["unknown",  {"rw_uri": unknown_rwcap,
                                                      "ro_uri": unknown_rocap}],
                   u"unknownchild-ro":  ["unknown",  {"ro_uri": unknown_rocap}],
                   u"unknownchild-imm": ["unknown",  {"ro_uri": unknown_immcap}],
                   u"dirchild":         ["dirnode",  {"rw_uri": dircap,
                                                      "ro_uri": self._make_readonly(dircap)}],
                   u"dirchild-lit":     ["dirnode",  {"ro_uri": litdircap}],
                   u"dirchild-empty":   ["dirnode",  {"ro_uri": emptydircap}],
                   u"child-mutable-mdmf": ["filenode", {"rw_uri": mdmfcap,
                                                        "ro_uri": self._make_readonly(mdmfcap)}],
                   }
        return newkids, {'filecap1': filecap1,
                         'filecap2': filecap2,
                         'filecap3': filecap3,
                         'unknown_rwcap': unknown_rwcap,
                         'unknown_rocap': unknown_rocap,
                         'unknown_immcap': unknown_immcap,
                         'dircap': dircap,
                         'litdircap': litdircap,
                         'emptydircap': emptydircap,
                         'mdmfcap': mdmfcap}

    def _create_immutable_children(self):
        contents, n, filecap1 = self.makefile(12)
        md1 = {"metakey1": "metavalue1"}
        tnode = create_chk_filenode("immutable directory contents\n"*10,
                                    self.get_all_contents())
        dnode = DirectoryNode(tnode, None, None)
        assert not dnode.is_mutable()
        immdircap = dnode.get_uri()
        litdircap = "URI:DIR2-LIT:ge3dumj2mewdcotyfqydulbshj5x2lbm"
        emptydircap = "URI:DIR2-LIT:"
        newkids = {u"child-imm":        ["filenode", {"ro_uri": filecap1,
                                                      "metadata": md1, }],
                   u"unknownchild-imm": ["unknown",  {"ro_uri": unknown_immcap}],
                   u"dirchild-imm":     ["dirnode",  {"ro_uri": immdircap}],
                   u"dirchild-lit":     ["dirnode",  {"ro_uri": litdircap}],
                   u"dirchild-empty":   ["dirnode",  {"ro_uri": emptydircap}],
                   }
        return newkids, {'filecap1': filecap1,
                         'unknown_immcap': unknown_immcap,
                         'immdircap': immdircap,
                         'litdircap': litdircap,
                         'emptydircap': emptydircap}

    def test_POST_mkdir_no_parentdir_initial_children(self):
        (newkids, caps) = self._create_initial_children()
        d = self.POST2("/uri?t=mkdir-with-children", json.dumps(newkids))
        def _after_mkdir(res):
            self.failUnless(res.startswith("URI:DIR"), res)
            n = self.s.create_node_from_uri(res)
            d2 = self.failUnlessNodeKeysAre(n, newkids.keys())
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"child-imm",
                                                       caps['filecap1']))
            d2.addCallback(lambda ign:
                           self.failUnlessRWChildURIIs(n, u"child-mutable",
                                                       caps['filecap2']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"child-mutable-ro",
                                                       caps['filecap3']))
            d2.addCallback(lambda ign:
                           self.failUnlessRWChildURIIs(n, u"unknownchild-rw",
                                                       caps['unknown_rwcap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"unknownchild-ro",
                                                       caps['unknown_rocap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"unknownchild-imm",
                                                       caps['unknown_immcap']))
            d2.addCallback(lambda ign:
                           self.failUnlessRWChildURIIs(n, u"dirchild",
                                                       caps['dircap']))
            return d2
        d.addCallback(_after_mkdir)
        return d

    @inlineCallbacks
    def test_POST_mkdir_no_parentdir_unexpected_children(self):
        # the regular /uri?t=mkdir operation is specified to ignore its body.
        # Only t=mkdir-with-children pays attention to it.
        (newkids, caps) = self._create_initial_children()
        url = self.webish_url + "/uri?t=mkdir" # without children
        yield self.assertHTTPError(url, 400,
                                   "t=mkdir does not accept children=, "
                                   "try t=mkdir-with-children instead",
                                   method="post", data=json.dumps(newkids))

    @inlineCallbacks
    def test_POST_noparent_bad(self):
        url = self.webish_url + "/uri?t=bogus"
        yield self.assertHTTPError(url, 400,
                                   "/uri accepts only PUT, PUT?t=mkdir, "
                                   "POST?t=upload, and POST?t=mkdir",
                                   method="post")

    def test_POST_mkdir_no_parentdir_immutable(self):
        (newkids, caps) = self._create_immutable_children()
        d = self.POST2("/uri?t=mkdir-immutable", json.dumps(newkids))
        def _after_mkdir(res):
            self.failUnless(res.startswith("URI:DIR"), res)
            n = self.s.create_node_from_uri(res)
            d2 = self.failUnlessNodeKeysAre(n, newkids.keys())
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"child-imm",
                                                          caps['filecap1']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"unknownchild-imm",
                                                          caps['unknown_immcap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"dirchild-imm",
                                                          caps['immdircap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"dirchild-lit",
                                                          caps['litdircap']))
            d2.addCallback(lambda ign:
                           self.failUnlessROChildURIIs(n, u"dirchild-empty",
                                                          caps['emptydircap']))
            return d2
        d.addCallback(_after_mkdir)
        return d

    def test_POST_mkdir_no_parentdir_immutable_bad(self):
        (newkids, caps) = self._create_initial_children()
        d = self.shouldFail2(error.Error,
                             "test_POST_mkdir_no_parentdir_immutable_bad",
                             "400 Bad Request",
                             "needed to be immutable but was not",
                             self.POST2,
                             "/uri?t=mkdir-immutable",
                             json.dumps(newkids))
        return d

    @inlineCallbacks
    def test_welcome_page_mkdir_button(self):
        # Fetch the welcome page.
        res = yield self.GET("/")
        MKDIR_BUTTON_RE = re.compile(
            '<form(?: action="([^"]*)"| method="post"| enctype="multipart/form-data"){3}>.*'
            '<input (?:type="hidden" |name="t" |value="([^"]*?)" ){3}/>[ ]*'
            '<input (?:type="hidden" |name="([^"]*)" |value="([^"]*)" ){3}/>[ ]*'
            '<input (type="submit" |class="btn" |value="Create a directory[^"]*" ){3}/>')
        html = res.replace('\n', ' ')
        mo = MKDIR_BUTTON_RE.search(html)
        self.failUnless(mo, html)
        formaction = mo.group(1)
        formt = mo.group(2)
        formaname = mo.group(3)
        formavalue = mo.group(4)

        url = self.webish_url + "/%s?t=%s&%s=%s" % (formaction, formt,
                                                    formaname, formavalue)
        target = yield self.shouldRedirectTo(url, None,
                                             method="post",
                                             code=http.SEE_OTHER)
        target = urllib.unquote(target)
        self.failUnless(target.startswith("uri/URI:DIR2:"), target)

    def test_POST_mkdir_replace(self): # return value?
        d = self.POST(self.public_url + "/foo", t="mkdir", name="sub")
        d.addCallback(lambda res: self._foo_node.get(u"sub"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdir_no_replace_queryarg(self): # return value?
        d = self.POST(self.public_url + "/foo?replace=false", t="mkdir", name="sub")
        d.addBoth(self.shouldFail, error.Error,
                  "POST_mkdir_no_replace_queryarg",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self._foo_node.get(u"sub"))
        d.addCallback(self.failUnlessNodeKeysAre, [u"baz.txt"])
        return d

    def test_POST_mkdir_no_replace_field(self): # return value?
        d = self.POST(self.public_url + "/foo", t="mkdir", name="sub",
                      replace="false")
        d.addBoth(self.shouldFail, error.Error, "POST_mkdir_no_replace_field",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self._foo_node.get(u"sub"))
        d.addCallback(self.failUnlessNodeKeysAre, [u"baz.txt"])
        return d

    @inlineCallbacks
    def test_POST_mkdir_whendone_field(self):
        body, headers = self.build_form(t="mkdir", name="newdir",
                                        when_done="/THERE")
        yield self.shouldRedirectTo(self.webish_url + self.public_url + "/foo",
                                    self.webish_url + "/THERE",
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)
        res = yield self._foo_node.get(u"newdir")
        self.failUnlessNodeKeysAre(res, [])

    @inlineCallbacks
    def test_POST_mkdir_whendone_queryarg(self):
        body, headers = self.build_form(t="mkdir", name="newdir")
        url = self.webish_url + self.public_url + "/foo?when_done=/THERE"
        yield self.shouldRedirectTo(url, self.webish_url + "/THERE",
                                    method="post", data=body, headers=headers,
                                    code=http.FOUND)
        res = yield self._foo_node.get(u"newdir")
        self.failUnlessNodeKeysAre(res, [])

    def test_POST_bad_t(self):
        d = self.shouldFail2(error.Error, "POST_bad_t",
                             "400 Bad Request",
                             "POST to a directory with bad t=BOGUS",
                             self.POST, self.public_url + "/foo", t="BOGUS")
        return d

    def test_POST_set_children(self, command_name="set_children"):
        contents9, n9, newuri9 = self.makefile(9)
        contents10, n10, newuri10 = self.makefile(10)
        contents11, n11, newuri11 = self.makefile(11)

        reqbody = """{
                     "atomic_added_1": [ "filenode", { "rw_uri": "%s",
                                                "size": 0,
                                                "metadata": {
                                                  "ctime": 1002777696.7564139,
                                                  "mtime": 1002777696.7564139
                                                 }
                                               } ],
                     "atomic_added_2": [ "filenode", { "rw_uri": "%s",
                                                "size": 1,
                                                "metadata": {
                                                  "ctime": 1002777696.7564139,
                                                  "mtime": 1002777696.7564139
                                                 }
                                               } ],
                     "atomic_added_3": [ "filenode", { "rw_uri": "%s",
                                                "size": 2,
                                                "metadata": {
                                                  "ctime": 1002777696.7564139,
                                                  "mtime": 1002777696.7564139
                                                 }
                                               } ]
                    }""" % (newuri9, newuri10, newuri11)

        url = self.webish_url + self.public_url + "/foo" + "?t=" + command_name

        d = do_http("post", url, data=reqbody)
        def _then(res):
            self.failUnlessURIMatchesROChild(newuri9, self._foo_node, u"atomic_added_1")
            self.failUnlessURIMatchesROChild(newuri10, self._foo_node, u"atomic_added_2")
            self.failUnlessURIMatchesROChild(newuri11, self._foo_node, u"atomic_added_3")

        d.addCallback(_then)
        d.addErrback(self.dump_error)
        return d

    def test_POST_set_children_with_hyphen(self):
        return self.test_POST_set_children(command_name="set-children")

    def test_POST_link_uri(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo", t="uri", name="new.txt", uri=newuri)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"new.txt",
                                                      contents))
        return d

    def test_POST_link_uri_replace(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo", t="uri", name="bar.txt", uri=newuri)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node, u"bar.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"bar.txt",
                                                      contents))
        return d

    def test_POST_link_uri_unknown_bad(self):
        d = self.POST(self.public_url + "/foo", t="uri", name="future.txt", uri=unknown_rwcap)
        d.addBoth(self.shouldFail, error.Error,
                  "POST_link_uri_unknown_bad",
                  "400 Bad Request",
                  "unknown cap in a write slot")
        return d

    def test_POST_link_uri_unknown_ro_good(self):
        d = self.POST(self.public_url + "/foo", t="uri", name="future-ro.txt", uri=unknown_rocap)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node, u"future-ro.txt")
        return d

    def test_POST_link_uri_unknown_imm_good(self):
        d = self.POST(self.public_url + "/foo", t="uri", name="future-imm.txt", uri=unknown_immcap)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node, u"future-imm.txt")
        return d

    def test_POST_link_uri_no_replace_queryarg(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo?replace=false", t="uri",
                      name="bar.txt", uri=newuri)
        d.addBoth(self.shouldFail, error.Error,
                  "POST_link_uri_no_replace_queryarg",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_POST_link_uri_no_replace_field(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo", t="uri", replace="false",
                      name="bar.txt", uri=newuri)
        d.addBoth(self.shouldFail, error.Error,
                  "POST_link_uri_no_replace_field",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_POST_delete(self, command_name='delete'):
        d = self._foo_node.list()
        def _check_before(children):
            self.failUnlessIn(u"bar.txt", children)
        d.addCallback(_check_before)
        d.addCallback(lambda res: self.POST(self.public_url + "/foo", t=command_name, name="bar.txt"))
        d.addCallback(lambda res: self._foo_node.list())
        def _check_after(children):
            self.failIfIn(u"bar.txt", children)
        d.addCallback(_check_after)
        return d

    def test_POST_unlink(self):
        return self.test_POST_delete(command_name='unlink')

    def test_POST_rename_file(self):
        d = self.POST(self.public_url + "/foo", t="rename",
                      from_name="bar.txt", to_name='wibble.txt')
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"wibble.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/wibble.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/wibble.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_rename_file_redundant(self):
        d = self.POST(self.public_url + "/foo", t="rename",
                      from_name="bar.txt", to_name='bar.txt')
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_rename_file_replace(self):
        # rename a file and replace a directory with it
        d = self.POST(self.public_url + "/foo", t="rename",
                      from_name="bar.txt", to_name='empty')
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"empty"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/empty"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/empty?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_rename_file_no_replace_queryarg(self):
        # rename a file and replace a directory with it
        d = self.POST(self.public_url + "/foo?replace=false", t="rename",
                      from_name="bar.txt", to_name='empty')
        d.addBoth(self.shouldFail, error.Error,
                  "POST_rename_file_no_replace_queryarg",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/empty?t=json"))
        d.addCallback(self.failUnlessIsEmptyJSON)
        return d

    def test_POST_rename_file_no_replace_field(self):
        # rename a file and replace a directory with it
        d = self.POST(self.public_url + "/foo", t="rename", replace="false",
                      from_name="bar.txt", to_name='empty')
        d.addBoth(self.shouldFail, error.Error,
                  "POST_rename_file_no_replace_field",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/empty?t=json"))
        d.addCallback(self.failUnlessIsEmptyJSON)
        return d

    def test_POST_rename_file_no_replace_same_link(self):
        d = self.POST(self.public_url + "/foo", t="rename",
                      replace="false", from_name="bar.txt", to_name="bar.txt")
        d.addCallback(lambda res: self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_rename_file_replace_only_files(self):
        d = self.POST(self.public_url + "/foo", t="rename",
                      replace="only-files", from_name="bar.txt",
                      to_name="baz.txt")
        d.addCallback(lambda res: self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/baz.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/baz.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_rename_file_replace_only_files_conflict(self):
        d = self.shouldFail2(error.Error, "POST_relink_file_replace_only_files_conflict",
                             "409 Conflict",
                             "There was already a child by that name, and you asked me to not replace it.",
                             self.POST, self.public_url + "/foo", t="relink",
                             replace="only-files", from_name="bar.txt",
                             to_name="empty")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def failUnlessIsEmptyJSON(self, res):
        data = json.loads(res)
        self.failUnlessEqual(data[0], "dirnode", data)
        self.failUnlessReallyEqual(len(data[1]["children"]), 0)

    def test_POST_rename_file_to_slash_fail(self):
        d = self.POST(self.public_url + "/foo", t="rename",
                      from_name="bar.txt", to_name='kirk/spock.txt')
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_rename_file_to_slash_fail",
                  "400 Bad Request",
                  "to_name= may not contain a slash",
                  )
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        return d

    def test_POST_rename_file_from_slash_fail(self):
        d = self.POST(self.public_url + "/foo", t="rename",
                      from_name="sub/bar.txt", to_name='spock.txt')
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_rename_from_file_slash_fail",
                  "400 Bad Request",
                  "from_name= may not contain a slash",
                  )
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        return d

    def test_POST_rename_dir(self):
        d = self.POST(self.public_url, t="rename",
                      from_name="foo", to_name='plunk')
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self.public_root, u"foo"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self.public_root, u"plunk"))
        d.addCallback(lambda res: self.GET(self.public_url + "/plunk?t=json"))
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    def test_POST_relink_file(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      from_name="bar.txt",
                      to_dir=self.public_root.get_uri() + "/foo/sub")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._sub_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_new_name(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      from_name="bar.txt",
                      to_name="wibble.txt", to_dir=self.public_root.get_uri() + "/foo/sub")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._sub_node, u"bar.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._sub_node, u"wibble.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/wibble.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/wibble.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_replace(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      from_name="bar.txt",
                      to_name="baz.txt", to_dir=self.public_root.get_uri() + "/foo/sub")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/baz.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/baz.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_no_replace(self):
        d = self.shouldFail2(error.Error, "POST_relink_file_no_replace",
                             "409 Conflict",
                             "There was already a child by that name, and you asked me to not replace it",
                             self.POST, self.public_url + "/foo", t="relink",
                             replace="false", from_name="bar.txt",
                             to_name="baz.txt", to_dir=self.public_root.get_uri() + "/foo/sub")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/baz.txt"))
        d.addCallback(self.failUnlessIsSubBazDotTxt)
        return d

    def test_POST_relink_file_no_replace_explicitly_same_link(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      replace="false", from_name="bar.txt",
                      to_name="bar.txt", to_dir=self.public_root.get_uri() + "/foo")
        d.addCallback(lambda res: self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_replace_only_files(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      replace="only-files", from_name="bar.txt",
                      to_name="baz.txt", to_dir=self.public_root.get_uri() + "/foo/sub")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/baz.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/baz.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_replace_only_files_conflict(self):
        d = self.shouldFail2(error.Error, "POST_relink_file_replace_only_files_conflict",
                             "409 Conflict",
                             "There was already a child by that name, and you asked me to not replace it.",
                             self.POST, self.public_url + "/foo", t="relink",
                             replace="only-files", from_name="bar.txt",
                             to_name="sub", to_dir=self.public_root.get_uri() + "/foo")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_to_slash_fail(self):
        d = self.shouldFail2(error.Error, "test_POST_rename_file_slash_fail",
                             "400 Bad Request",
                             "to_name= may not contain a slash",
                             self.POST, self.public_url + "/foo", t="relink",
                             from_name="bar.txt",
                             to_name="slash/fail.txt", to_dir=self.public_root.get_uri() + "/foo/sub")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._sub_node, u"slash/fail.txt"))
        d.addCallback(lambda ign:
                      self.shouldFail2(error.Error,
                                       "test_POST_rename_file_slash_fail2",
                                       "400 Bad Request",
                                       "from_name= may not contain a slash",
                                       self.POST, self.public_url + "/foo",
                                       t="relink",
                                       from_name="nope/bar.txt",
                                       to_name="fail.txt",
                                       to_dir=self.public_root.get_uri() + "/foo/sub"))
        return d

    def test_POST_relink_file_explicitly_same_link(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      from_name="bar.txt",
                      to_name="bar.txt", to_dir=self.public_root.get_uri() + "/foo")
        d.addCallback(lambda res: self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_implicitly_same_link(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      from_name="bar.txt")
        d.addCallback(lambda res: self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_same_dir(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      from_name="bar.txt",
                      to_name="baz.txt", to_dir=self.public_root.get_uri() + "/foo")
        d.addCallback(lambda res: self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.failUnlessNodeHasChild(self._sub_node, u"baz.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/baz.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/baz.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_bad_replace(self):
        d = self.shouldFail2(error.Error, "test_POST_relink_file_bad_replace",
                             "400 Bad Request", "invalid replace= argument: 'boogabooga'",
                             self.POST,
                             self.public_url + "/foo", t="relink",
                             replace="boogabooga", from_name="bar.txt",
                             to_dir=self.public_root.get_uri() + "/foo/sub")
        return d

    def test_POST_relink_file_multi_level(self):
        d = self.POST2(self.public_url + "/foo/sub/level2?t=mkdir", "")
        d.addCallback(lambda res: self.POST(self.public_url + "/foo", t="relink",
                      from_name="bar.txt", to_dir=self.public_root.get_uri() + "/foo/sub/level2"))
        d.addCallback(lambda res: self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.failIfNodeHasChild(self._sub_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/level2/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/level2/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_to_uri(self):
        d = self.POST(self.public_url + "/foo", t="relink", target_type="uri",
                      from_name="bar.txt", to_dir=self._sub_uri)
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/sub/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_to_nonexistent_dir(self):
        d = self.shouldFail2(error.Error, "POST_relink_file_to_nonexistent_dir",
                            "404 Not Found", "No such child: nopechucktesta",
                            self.POST, self.public_url + "/foo", t="relink",
                            from_name="bar.txt",
                            to_dir=self.public_root.get_uri() + "/nopechucktesta")
        return d

    def test_POST_relink_file_into_file(self):
        d = self.shouldFail2(error.Error, "POST_relink_file_into_file",
                             "400 Bad Request", "to_dir is not a directory",
                             self.POST, self.public_url + "/foo", t="relink",
                             from_name="bar.txt",
                             to_dir=self.public_root.get_uri() + "/foo/baz.txt")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/baz.txt"))
        d.addCallback(self.failUnlessIsBazDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_file_to_bad_uri(self):
        d =  self.shouldFail2(error.Error, "POST_relink_file_to_bad_uri",
                              "400 Bad Request", "to_dir is not a directory",
                              self.POST, self.public_url + "/foo", t="relink",
                              from_name="bar.txt",
                              to_dir="URI:DIR2:mn5jlyjnrjeuydyswlzyui72i:rmneifcj6k6sycjljjhj3f6majsq2zqffydnnul5hfa4j577arma")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_relink_dir(self):
        d = self.POST(self.public_url + "/foo", t="relink",
                      from_name="bar.txt",
                      to_dir=self.public_root.get_uri() + "/foo/empty")
        d.addCallback(lambda res: self.POST(self.public_url + "/foo",
                      t="relink", from_name="empty",
                      to_dir=self.public_root.get_uri() + "/foo/sub"))
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, u"empty"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._sub_node, u"empty"))
        d.addCallback(lambda res:
                      self._sub_node.get_child_at_path(u"empty"))
        d.addCallback(lambda node:
                      self.failUnlessNodeHasChild(node, u"bar.txt"))
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/sub/empty/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    @inlineCallbacks
    def shouldRedirectTo(self, url, target_location, method="get",
                         code=None, **args):
        response = yield treq.request(method, url, persistent=False,
                                      allow_redirects=False, **args)
        codes = [http.MOVED_PERMANENTLY,
                 http.FOUND,
                 http.TEMPORARY_REDIRECT,
                 ] if code is None else [code]
        self.assertIn(response.code, codes)
        location = response.headers.getRawHeaders(b"location")[0]
        if target_location is not None:
            self.assertEquals(location, target_location)
        returnValue(location)

    @inlineCallbacks
    def test_GET_URI_form(self):
        relbase = "/uri?uri=%s" % self._bar_txt_uri
        base = self.webish_url + relbase
        # this is supposed to give us a redirect to /uri/$URI, plus arguments
        targetbase = self.webish_url + "/uri/%s" % urllib.quote(self._bar_txt_uri)
        yield self.shouldRedirectTo(base, targetbase)
        yield self.shouldRedirectTo(base+"&filename=bar.txt",
                                    targetbase+"?filename=bar.txt")
        yield self.shouldRedirectTo(base+"&t=json",
                                    targetbase+"?t=json")

        self.log(None, "about to get file by uri")
        data = yield self.GET(relbase, followRedirect=True)
        self.failUnlessIsBarDotTxt(data)
        self.log(None, "got file by uri, about to get dir by uri")
        data = yield self.GET("/uri?uri=%s&t=json" % self._foo_uri,
                              followRedirect=True)
        self.failUnlessIsFooJSON(data)
        self.log(None, "got dir by uri")

    def test_GET_URI_form_bad(self):
        d = self.shouldFail2(error.Error, "test_GET_URI_form_bad",
                             "400 Bad Request", "GET /uri requires uri=",
                             self.GET, "/uri")
        return d

    def test_GET_rename_form(self):
        d = self.GET(self.public_url + "/foo?t=rename-form&name=bar.txt",
                     followRedirect=True)
        def _check(res):
            self.failUnless(re.search('<input (name="when_done" |value="." |type="hidden" ){3}/>', res), res)
            self.failUnless(re.search(r'<input (readonly="true" |type="text" |name="from_name" |value="bar\.txt" ){4}/>', res), res)
            self.failUnlessIn(FAVICON_MARKUP, res)
        d.addCallback(_check)
        return d

    def log(self, res, msg):
        #print "MSG: %s  RES: %s" % (msg, res)
        log.msg(msg)
        return res

    def test_GET_URI_URL(self):
        base = "/uri/%s" % self._bar_txt_uri
        d = self.GET(base)
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(base+"?filename=bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(base+"?filename=bar.txt&save=true"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_URI_URL_dir(self):
        base = "/uri/%s?t=json" % self._foo_uri
        d = self.GET(base)
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    @inlineCallbacks
    def test_GET_URI_URL_missing(self):
        base = "/uri/%s" % self._bad_file_uri
        url = self.webish_url + base
        yield self.assertHTTPError(url, http.GONE, "NotEnoughSharesError")
        # TODO: how can we exercise both sides of WebDownloadTarget.fail
        # here? we must arrange for a download to fail after target.open()
        # has been called, and then inspect the response to see that it is
        # shorter than we expected.

    def test_PUT_DIRURL_uri(self):
        d = self.s.create_dirnode()
        def _made_dir(dn):
            new_uri = dn.get_uri()
            # replace /foo with a new (empty) directory
            d = self.PUT(self.public_url + "/foo?t=uri", new_uri)
            d.addCallback(lambda res:
                          self.failUnlessReallyEqual(res.strip(), new_uri))
            d.addCallback(lambda res:
                          self.failUnlessRWChildURIIs(self.public_root,
                                                      u"foo",
                                                      new_uri))
            return d
        d.addCallback(_made_dir)
        return d

    def test_PUT_DIRURL_uri_noreplace(self):
        d = self.s.create_dirnode()
        def _made_dir(dn):
            new_uri = dn.get_uri()
            # replace /foo with a new (empty) directory, but ask that
            # replace=false, so it should fail
            d = self.shouldFail2(error.Error, "test_PUT_DIRURL_uri_noreplace",
                                 "409 Conflict", "There was already a child by that name, and you asked me to not replace it",
                                 self.PUT,
                                 self.public_url + "/foo?t=uri&replace=false",
                                 new_uri)
            d.addCallback(lambda res:
                          self.failUnlessRWChildURIIs(self.public_root,
                                                      u"foo",
                                                      self._foo_uri))
            return d
        d.addCallback(_made_dir)
        return d

    def test_PUT_DIRURL_bad_t(self):
        d = self.shouldFail2(error.Error, "test_PUT_DIRURL_bad_t",
                             "400 Bad Request", "PUT to a directory",
                             self.PUT, self.public_url + "/foo?t=BOGUS", "")
        d.addCallback(lambda res:
                      self.failUnlessRWChildURIIs(self.public_root,
                                                  u"foo",
                                                  self._foo_uri))
        return d

    def test_PUT_NEWFILEURL_uri(self):
        contents, n, new_uri = self.makefile(8)
        d = self.PUT(self.public_url + "/foo/new.txt?t=uri", new_uri)
        d.addCallback(lambda res: self.failUnlessReallyEqual(res.strip(), new_uri))
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"new.txt",
                                                      contents))
        return d

    def test_PUT_NEWFILEURL_mdmf(self):
        new_contents = self.NEWFILE_CONTENTS * 300000
        d = self.PUT(self.public_url + \
                     "/foo/mdmf.txt?format=mdmf",
                     new_contents)
        d.addCallback(lambda ignored:
            self.GET(self.public_url + "/foo/mdmf.txt?t=json"))
        def _got_json(raw):
            data = json.loads(raw)
            data = data[1]
            self.failUnlessIn("format", data)
            self.failUnlessEqual(data["format"], "MDMF")
            self.failUnless(data['rw_uri'].startswith("URI:MDMF"))
            self.failUnless(data['ro_uri'].startswith("URI:MDMF"))
        d.addCallback(_got_json)
        return d

    def test_PUT_NEWFILEURL_sdmf(self):
        new_contents = self.NEWFILE_CONTENTS * 300000
        d = self.PUT(self.public_url + \
                     "/foo/sdmf.txt?format=sdmf",
                     new_contents)
        d.addCallback(lambda ignored:
            self.GET(self.public_url + "/foo/sdmf.txt?t=json"))
        def _got_json(raw):
            data = json.loads(raw)
            data = data[1]
            self.failUnlessIn("format", data)
            self.failUnlessEqual(data["format"], "SDMF")
        d.addCallback(_got_json)
        return d

    @inlineCallbacks
    def test_PUT_NEWFILEURL_bad_format(self):
        new_contents = self.NEWFILE_CONTENTS * 300000
        url = self.webish_url + self.public_url + "/foo/foo.txt?format=foo"
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="put", data=new_contents)

    def test_PUT_NEWFILEURL_uri_replace(self):
        contents, n, new_uri = self.makefile(8)
        d = self.PUT(self.public_url + "/foo/bar.txt?t=uri", new_uri)
        d.addCallback(lambda res: self.failUnlessReallyEqual(res.strip(), new_uri))
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"bar.txt",
                                                      contents))
        return d

    def test_PUT_NEWFILEURL_uri_no_replace(self):
        contents, n, new_uri = self.makefile(8)
        d = self.PUT(self.public_url + "/foo/bar.txt?t=uri&replace=false", new_uri)
        d.addBoth(self.shouldFail, error.Error,
                  "PUT_NEWFILEURL_uri_no_replace",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        return d

    def test_PUT_NEWFILEURL_uri_unknown_bad(self):
        d = self.PUT(self.public_url + "/foo/put-future.txt?t=uri", unknown_rwcap)
        d.addBoth(self.shouldFail, error.Error,
                  "POST_put_uri_unknown_bad",
                  "400 Bad Request",
                  "unknown cap in a write slot")
        return d

    def test_PUT_NEWFILEURL_uri_unknown_ro_good(self):
        d = self.PUT(self.public_url + "/foo/put-future-ro.txt?t=uri", unknown_rocap)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node,
                      u"put-future-ro.txt")
        return d

    def test_PUT_NEWFILEURL_uri_unknown_imm_good(self):
        d = self.PUT(self.public_url + "/foo/put-future-imm.txt?t=uri", unknown_immcap)
        d.addCallback(self.failUnlessURIMatchesROChild, self._foo_node,
                      u"put-future-imm.txt")
        return d

    def test_PUT_NEWFILE_URI(self):
        file_contents = "New file contents here\n"
        d = self.PUT("/uri", file_contents)
        def _check(uri):
            assert isinstance(uri, str), uri
            self.failUnlessIn(uri, self.get_all_contents())
            self.failUnlessReallyEqual(self.get_all_contents()[uri],
                                       file_contents)
            return self.GET("/uri/%s" % uri)
        d.addCallback(_check)
        def _check2(res):
            self.failUnlessReallyEqual(res, file_contents)
        d.addCallback(_check2)
        return d

    def test_PUT_NEWFILE_URI_not_mutable(self):
        file_contents = "New file contents here\n"
        d = self.PUT("/uri?mutable=false", file_contents)
        def _check(uri):
            assert isinstance(uri, str), uri
            self.failUnlessIn(uri, self.get_all_contents())
            self.failUnlessReallyEqual(self.get_all_contents()[uri],
                                       file_contents)
            return self.GET("/uri/%s" % uri)
        d.addCallback(_check)
        def _check2(res):
            self.failUnlessReallyEqual(res, file_contents)
        d.addCallback(_check2)
        return d

    def test_PUT_NEWFILE_URI_only_PUT(self):
        d = self.PUT("/uri?t=bogus", "")
        d.addBoth(self.shouldFail, error.Error,
                  "PUT_NEWFILE_URI_only_PUT",
                  "400 Bad Request",
                  "/uri accepts only PUT, PUT?t=mkdir, POST?t=upload, and POST?t=mkdir")
        return d

    def test_PUT_NEWFILE_URI_mutable(self):
        file_contents = "New file contents here\n"
        d = self.PUT("/uri?mutable=true", file_contents)
        def _check1(filecap):
            filecap = filecap.strip()
            self.failUnless(filecap.startswith("URI:SSK:"), filecap)
            self.filecap = filecap
            u = uri.WriteableSSKFileURI.init_from_string(filecap)
            self.failUnlessIn(u.get_storage_index(), self.get_all_contents())
            n = self.s.create_node_from_uri(filecap)
            return n.download_best_version()
        d.addCallback(_check1)
        def _check2(data):
            self.failUnlessReallyEqual(data, file_contents)
            return self.GET("/uri/%s" % urllib.quote(self.filecap))
        d.addCallback(_check2)
        def _check3(res):
            self.failUnlessReallyEqual(res, file_contents)
        d.addCallback(_check3)
        return d

    def test_PUT_mkdir(self):
        d = self.PUT("/uri?t=mkdir", "")
        def _check(uri):
            n = self.s.create_node_from_uri(uri.strip())
            d2 = self.failUnlessNodeKeysAre(n, [])
            d2.addCallback(lambda res:
                           self.GET("/uri/%s?t=json" % uri))
            return d2
        d.addCallback(_check)
        d.addCallback(self.failUnlessIsEmptyJSON)
        return d

    def test_PUT_mkdir_mdmf(self):
        d = self.PUT("/uri?t=mkdir&format=mdmf", "")
        def _got(res):
            u = uri.from_string(res)
            # Check that this is an MDMF writecap
            self.failUnlessIsInstance(u, uri.MDMFDirectoryURI)
        d.addCallback(_got)
        return d

    def test_PUT_mkdir_sdmf(self):
        d = self.PUT("/uri?t=mkdir&format=sdmf", "")
        def _got(res):
            u = uri.from_string(res)
            self.failUnlessIsInstance(u, uri.DirectoryURI)
        d.addCallback(_got)
        return d

    @inlineCallbacks
    def test_PUT_mkdir_bad_format(self):
        url = self.webish_url + "/uri?t=mkdir&format=foo"
        yield self.assertHTTPError(url, 400, "Unknown format: foo",
                                   method="put", data="")

    def test_POST_check(self):
        d = self.POST(self.public_url + "/foo", t="check", name="bar.txt")
        def _done(res):
            # this returns a string form of the results, which are probably
            # None since we're using fake filenodes.
            # TODO: verify that the check actually happened, by changing
            # FakeCHKFileNode to count how many times .check() has been
            # called.
            pass
        d.addCallback(_done)
        return d


    def test_PUT_update_at_offset(self):
        file_contents = "test file" * 100000 # about 900 KiB
        d = self.PUT("/uri?mutable=true", file_contents)
        def _then(filecap):
            self.filecap = filecap
            new_data = file_contents[:100]
            new = "replaced and so on"
            new_data += new
            new_data += file_contents[len(new_data):]
            assert len(new_data) == len(file_contents)
            self.new_data = new_data
        d.addCallback(_then)
        d.addCallback(lambda ignored:
            self.PUT("/uri/%s?replace=True&offset=100" % self.filecap,
                     "replaced and so on"))
        def _get_data(filecap):
            n = self.s.create_node_from_uri(filecap)
            return n.download_best_version()
        d.addCallback(_get_data)
        d.addCallback(lambda results:
            self.failUnlessEqual(results, self.new_data))
        # Now try appending things to the file
        d.addCallback(lambda ignored:
            self.PUT("/uri/%s?offset=%d" % (self.filecap, len(self.new_data)),
                     "puppies" * 100))
        d.addCallback(_get_data)
        d.addCallback(lambda results:
            self.failUnlessEqual(results, self.new_data + ("puppies" * 100)))
        # and try replacing the beginning of the file
        d.addCallback(lambda ignored:
            self.PUT("/uri/%s?offset=0" % self.filecap, "begin"))
        d.addCallback(_get_data)
        d.addCallback(lambda results:
            self.failUnlessEqual(results, "begin"+self.new_data[len("begin"):]+("puppies"*100)))
        return d

    @inlineCallbacks
    def test_PUT_update_at_invalid_offset(self):
        file_contents = "test file" * 100000 # about 900 KiB
        filecap = yield self.PUT("/uri?mutable=true", file_contents)
        # Negative offsets should cause an error.
        url = self.webish_url + "/uri/%s?offset=-1" % filecap
        yield self.assertHTTPError(url, 400, "Invalid offset",
                                   method="put", data="foo")

    @inlineCallbacks
    def test_PUT_update_at_offset_immutable(self):
        file_contents = "Test file" * 100000
        filecap = yield self.PUT("/uri", file_contents)
        url = self.webish_url + "/uri/%s?offset=50" % filecap
        yield self.assertHTTPError(url, 400, "immutable",
                                   method="put", data="foo")

    @inlineCallbacks
    def test_bad_method(self):
        url = self.webish_url + self.public_url + "/foo/bar.txt"
        yield self.assertHTTPError(url, 501,
                                   "I don't know how to treat a BOGUS request.",
                                   method="BOGUS")

    @inlineCallbacks
    def test_short_url(self):
        url = self.webish_url + "/uri"
        yield self.assertHTTPError(url, 501,
                                   "I don't know how to treat a DELETE request.",
                                   method="DELETE")

    @inlineCallbacks
    def test_ophandle_bad(self):
        url = self.webish_url + "/operations/bogus?t=status"
        yield self.assertHTTPError(url, 404,
                                   "unknown/expired handle 'bogus'")

    @inlineCallbacks
    def test_ophandle_cancel(self):
        url = self.webish_url + self.public_url + "/foo/?t=start-manifest&ophandle=128"
        yield do_http("post", url,
                      allow_redirects=True, browser_like_redirects=True)
        res = yield self.GET("/operations/128?t=status&output=JSON")
        data = json.loads(res)
        self.failUnless("finished" in data, res)
        monitor = self.ws.getServiceNamed("operations").handles["128"][0]

        res = yield self.POST("/operations/128?t=cancel&output=JSON")
        data = json.loads(res)
        self.failUnless("finished" in data, res)
        # t=cancel causes the handle to be forgotten
        self.failUnless(monitor.is_cancelled())

        url = self.webish_url + "/operations/128?t=status&output=JSON"
        yield self.assertHTTPError(url, 404, "unknown/expired handle '128'")

    @inlineCallbacks
    def test_ophandle_retainfor(self):
        url = self.webish_url + self.public_url + "/foo/?t=start-manifest&ophandle=129&retain-for=60"
        yield do_http("post", url,
                      allow_redirects=True, browser_like_redirects=True)
        res = yield self.GET("/operations/129?t=status&output=JSON&retain-for=0")
        data = json.loads(res)
        self.failUnless("finished" in data, res)

        # the retain-for=0 will cause the handle to be expired very soon
        yield self.clock.advance(2.0)
        url = self.webish_url + "/operations/129?t=status&output=JSON"
        yield self.assertHTTPError(url, 404, "unknown/expired handle '129'")

    @inlineCallbacks
    def test_ophandle_release_after_complete(self):
        url = self.webish_url + self.public_url + "/foo/?t=start-manifest&ophandle=130"
        yield do_http("post", url,
                      allow_redirects=True, browser_like_redirects=True)
        yield self.wait_for_operation(None, "130")
        yield self.GET("/operations/130?t=status&output=JSON&release-after-complete=true")
        # the release-after-complete=true will cause the handle to be expired
        op_url = self.webish_url + "/operations/130?t=status&output=JSON"
        yield self.assertHTTPError(op_url, 404, "unknown/expired handle '130'")

    @inlineCallbacks
    def test_uncollected_ophandle_expiration(self):
        # uncollected ophandles should expire after 4 days
        def _make_uncollected_ophandle(ophandle):
            url = (self.webish_url + self.public_url +
                   "/foo/?t=start-manifest&ophandle=%d" % ophandle)
            # When we start the operation, the webapi server will want to
            # redirect us to the page for the ophandle, so we get
            # confirmation that the operation has started. If the manifest
            # operation has finished by the time we get there, following that
            # redirect would have the side effect of collecting the ophandle
            # that we've just created, which means that we can't use the
            # ophandle to test the uncollected timeout anymore. So, instead,
            # catch+ignore any 302 here and don't follow it.
            d = treq.request("post", url, persistent=False)
            def _ignore_redirect(f):
                f.trap(client.ResponseFailed)
                e = f.value
                reasons = e.reasons
                r0 = reasons[0]
                r0.trap(error.PageRedirect)
            d.addErrback(_ignore_redirect)
            return d
        # Create an ophandle, don't collect it, then advance the clock by
        # 4 days - 1 second and make sure that the ophandle is still there.
        yield _make_uncollected_ophandle(131)
        yield self.clock.advance((96*60*60) - 1) # 96 hours = 4 days
        res = yield self.GET("/operations/131?t=status&output=JSON")
        data = json.loads(res)
        self.failUnless("finished" in data, res)

        # Create an ophandle, don't collect it, then try to collect it
        # after 4 days. It should be gone.
        yield _make_uncollected_ophandle(132)
        yield self.clock.advance(96*60*60)
        op_url = self.webish_url + "/operations/132?t=status&output=JSON"
        yield self.assertHTTPError(op_url, 404, "unknown/expired handle '132'")

    @inlineCallbacks
    def test_collected_ophandle_expiration(self):
        # collected ophandles should expire after 1 day
        def _make_collected_ophandle(ophandle):
            url = (self.webish_url + self.public_url +
                   "/foo/?t=start-manifest&ophandle=%d" % ophandle)
            # By following the initial redirect, we collect the ophandle
            # we've just created.
            return do_http("post", url,
                           allow_redirects=True, browser_like_redirects=True)
        # Create a collected ophandle, then collect it after 23 hours
        # and 59 seconds to make sure that it is still there.
        yield _make_collected_ophandle(133)
        yield self.clock.advance((24*60*60) - 1)
        res = yield self.GET("/operations/133?t=status&output=JSON")
        data = json.loads(res)
        self.failUnless("finished" in data, res)

        # Create another uncollected ophandle, then try to collect it
        # after 24 hours to make sure that it is gone.
        yield _make_collected_ophandle(134)
        yield self.clock.advance(24*60*60)
        op_url = self.webish_url + "/operations/134?t=status&output=JSON"
        yield self.assertHTTPError(op_url, 404, "unknown/expired handle '134'")

    def test_incident(self):
        d = self.POST("/report_incident", details="eek")
        def _done(res):
            self.failIfIn("<html>", res)
            self.failUnlessIn("An incident report has been saved", res)
        d.addCallback(_done)
        return d

    def test_static(self):
        webdir = os.path.join(self.staticdir, "subdir")
        fileutil.make_dirs(webdir)
        f = open(os.path.join(webdir, "hello.txt"), "wb")
        f.write("hello")
        f.close()

        d = self.GET("/static/subdir/hello.txt")
        def _check(res):
            self.failUnlessReallyEqual(res, "hello")
        d.addCallback(_check)
        return d

    def test_static_missing(self):
        # self.staticdir does not exist yet, because we used self.mktemp()
        d = self.assertFailure(self.GET("/static"), error.Error)
        # nevow.static throws an exception when it tries to os.stat the
        # missing directory, which gives the client a 500 Internal Server
        # Error, and the traceback reveals the parent directory name. By
        # switching to plain twisted.web.static, this gives a normal 404 that
        # doesn't reveal anything. This addresses #1720.
        d.addCallback(lambda e: self.assertEquals(str(e), "404 Not Found"))
        return d
