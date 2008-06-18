import re, urllib
import simplejson
from twisted.application import service
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.web import client, error, http
from twisted.python import failure, log
from allmydata import interfaces, provisioning, uri, webish, upload, download
from allmydata.web import status, common
from allmydata.util import fileutil
from allmydata.test.common import FakeDirectoryNode, FakeCHKFileNode, \
     FakeMutableFileNode, create_chk_filenode
from allmydata.interfaces import IURI, INewDirectoryURI, \
     IReadonlyNewDirectoryURI, IFileURI, IMutableFileURI, IMutableFileNode
from allmydata.mutable import servermap, publish, retrieve

# create a fake uploader/downloader, and a couple of fake dirnodes, then
# create a webserver that works against them

class FakeIntroducerClient:
    def get_all_connectors(self):
        return {}
    def get_all_connections_for(self, service_name):
        return frozenset()
    def get_all_peerids(self):
        return frozenset()

class FakeClient(service.MultiService):
    nodeid = "fake_nodeid"
    nickname = "fake_nickname"
    basedir = "fake_basedir"
    def get_versions(self):
        return {'allmydata': "fake",
                'foolscap': "fake",
                'twisted': "fake",
                'zfec': "fake",
                }
    introducer_furl = "None"
    introducer_client = FakeIntroducerClient()
    _all_upload_status = [upload.UploadStatus()]
    _all_download_status = [download.DownloadStatus()]
    _all_mapupdate_statuses = [servermap.UpdateStatus()]
    _all_publish_statuses = [publish.PublishStatus()]
    _all_retrieve_statuses = [retrieve.RetrieveStatus()]
    convergence = "some random string"

    def connected_to_introducer(self):
        return False

    def create_node_from_uri(self, auri):
        u = uri.from_string(auri)
        if (INewDirectoryURI.providedBy(u)
            or IReadonlyNewDirectoryURI.providedBy(u)):
            return FakeDirectoryNode(self).init_from_uri(u)
        if IFileURI.providedBy(u):
            return FakeCHKFileNode(u, self)
        assert IMutableFileURI.providedBy(u), u
        return FakeMutableFileNode(self).init_from_uri(u)

    def create_empty_dirnode(self):
        n = FakeDirectoryNode(self)
        d = n.create()
        d.addCallback(lambda res: n)
        return d

    MUTABLE_SIZELIMIT = FakeMutableFileNode.MUTABLE_SIZELIMIT
    def create_mutable_file(self, contents=""):
        n = FakeMutableFileNode(self)
        return n.create(contents)

    def upload(self, uploadable):
        d = uploadable.get_size()
        d.addCallback(lambda size: uploadable.read(size))
        def _got_data(datav):
            data = "".join(datav)
            n = create_chk_filenode(self, data)
            results = upload.UploadResults()
            results.uri = n.get_uri()
            return results
        d.addCallback(_got_data)
        return d

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

class HTTPClientHEADFactory(client.HTTPClientFactory):
    def __init__(self, *args, **kwargs):
        client.HTTPClientFactory.__init__(self, *args, **kwargs)
        self.deferred.addCallback(lambda res: self.response_headers)


class WebMixin(object):
    def setUp(self):
        self.s = FakeClient()
        self.s.startService()
        self.ws = s = webish.WebishServer("0")
        s.setServiceParent(self.s)
        self.webish_port = port = s.listener._port.getHost().port
        self.webish_url = "http://localhost:%d" % port

        l = [ self.s.create_empty_dirnode() for x in range(6) ]
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
            # NOTE: we ignore the deferred on all set_uri() calls, because we
            # know the fake nodes do these synchronously
            self.public_root.set_uri(u"foo", foo.get_uri())

            self.BAR_CONTENTS, n, self._bar_txt_uri = self.makefile(0)
            foo.set_uri(u"bar.txt", self._bar_txt_uri)

            foo.set_uri(u"empty", res[3][1].get_uri())
            sub_uri = res[4][1].get_uri()
            self._sub_uri = sub_uri
            foo.set_uri(u"sub", sub_uri)
            sub = self.s.create_node_from_uri(sub_uri)

            _ign, n, blocking_uri = self.makefile(1)
            foo.set_uri(u"blockingfile", blocking_uri)

            unicode_filename = u"n\u00fc.txt" # n u-umlaut . t x t
            # ok, unicode calls it LATIN SMALL LETTER U WITH DIAERESIS but I
            # still think of it as an umlaut
            foo.set_uri(unicode_filename, self._bar_txt_uri)

            _ign, n, baz_file = self.makefile(2)
            sub.set_uri(u"baz.txt", baz_file)

            _ign, n, self._bad_file_uri = self.makefile(3)
            # this uri should not be downloadable
            del FakeCHKFileNode.all_contents[self._bad_file_uri]

            rodir = res[5][1]
            self.public_root.set_uri(u"reedownlee", rodir.get_readonly_uri())
            rodir.set_uri(u"nor", baz_file)

            # public/
            # public/foo/
            # public/foo/bar.txt
            # public/foo/blockingfile
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

    def makefile(self, number):
        contents = "contents of file %s\n" % number
        n = create_chk_filenode(self.s, contents)
        return contents, n, n.get_uri()

    def tearDown(self):
        return self.s.stopService()

    def failUnlessIsBarDotTxt(self, res):
        self.failUnlessEqual(res, self.BAR_CONTENTS, res)

    def failUnlessIsBarJSON(self, res):
        data = simplejson.loads(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "filenode")
        self.failUnless(isinstance(data[1], dict))
        self.failIf(data[1]["mutable"])
        self.failIf("rw_uri" in data[1]) # immutable
        self.failUnlessEqual(data[1]["ro_uri"], self._bar_txt_uri)
        self.failUnlessEqual(data[1]["size"], len(self.BAR_CONTENTS))

    def failUnlessIsFooJSON(self, res):
        data = simplejson.loads(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "dirnode", res)
        self.failUnless(isinstance(data[1], dict))
        self.failUnless(data[1]["mutable"])
        self.failUnless("rw_uri" in data[1]) # mutable
        self.failUnlessEqual(data[1]["rw_uri"], self._foo_uri)
        self.failUnlessEqual(data[1]["ro_uri"], self._foo_readonly_uri)

        kidnames = sorted(data[1]["children"])
        self.failUnlessEqual(kidnames,
                             [u"bar.txt", u"blockingfile", u"empty",
                              u"n\u00fc.txt", u"sub"])
        kids = data[1]["children"]
        self.failUnlessEqual(kids[u"sub"][0], "dirnode")
        self.failUnless("metadata" in kids[u"sub"][1])
        self.failUnless("ctime" in kids[u"sub"][1]["metadata"])
        self.failUnless("mtime" in kids[u"sub"][1]["metadata"])
        self.failUnlessEqual(kids[u"bar.txt"][0], "filenode")
        self.failUnlessEqual(kids[u"bar.txt"][1]["size"], len(self.BAR_CONTENTS))
        self.failUnlessEqual(kids[u"bar.txt"][1]["ro_uri"], self._bar_txt_uri)
        self.failUnlessEqual(kids[u"bar.txt"][1]["metadata"]["ctime"],
                             self._bar_txt_metadata["ctime"])
        self.failUnlessEqual(kids[u"n\u00fc.txt"][1]["ro_uri"],
                             self._bar_txt_uri)

    def GET(self, urlpath, followRedirect=False):
        url = self.webish_url + urlpath
        return client.getPage(url, method="GET", followRedirect=followRedirect)

    def HEAD(self, urlpath):
        # this requires some surgery, because twisted.web.client doesn't want
        # to give us back the response headers.
        factory = HTTPClientHEADFactory(urlpath)
        reactor.connectTCP("localhost", self.webish_port, factory)
        return factory.deferred

    def PUT(self, urlpath, data):
        url = self.webish_url + urlpath
        return client.getPage(url, method="PUT", postdata=data)

    def DELETE(self, urlpath):
        url = self.webish_url + urlpath
        return client.getPage(url, method="DELETE")

    def POST(self, urlpath, followRedirect=False, **fields):
        url = self.webish_url + urlpath
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
        body = "\r\n".join(form) + "\r\n"
        headers = {"content-type": "multipart/form-data; boundary=%s" % sepbase,
                   }
        return client.getPage(url, method="POST", postdata=body,
                              headers=headers, followRedirect=followRedirect)

    def shouldFail(self, res, expected_failure, which,
                   substring=None, response_substring=None):
        if isinstance(res, failure.Failure):
            res.trap(expected_failure)
            if substring:
                self.failUnless(substring in str(res),
                                "substring '%s' not in '%s'"
                                % (substring, str(res)))
            if response_substring:
                self.failUnless(response_substring in res.value.response,
                                "response substring '%s' not in '%s'"
                                % (response_substring, res.value.response))
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
                    self.failUnless(substring in str(res),
                                    "%s: substring '%s' not in '%s'"
                                    % (which, substring, str(res)))
                if response_substring:
                    self.failUnless(response_substring in res.value.response,
                                    "%s: response substring '%s' not in '%s'"
                                    % (which,
                                       response_substring, res.value.response))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d

    def should404(self, res, which):
        if isinstance(res, failure.Failure):
            res.trap(error.Error)
            self.failUnlessEqual(res.value.status, "404")
        else:
            self.fail("%s was supposed to Error(404), not get '%s'" %
                      (which, res))

    def shouldHTTPError(self, res, which, code=None, substring=None,
                        response_substring=None):
        if isinstance(res, failure.Failure):
            res.trap(error.Error)
            if code is not None:
                self.failUnlessEqual(res.value.status, str(code))
            if substring:
                self.failUnless(substring in str(res),
                                "substring '%s' not in '%s'"
                                % (substring, str(res)))
            if response_substring:
                self.failUnless(response_substring in res.value.response,
                                "response substring '%s' not in '%s'"
                                % (response_substring, res.value.response))
        else:
            self.fail("%s was supposed to Error(%s), not get '%s'" %
                      (which, code, res))

    def shouldHTTPError2(self, which,
                         code=None, substring=None, response_substring=None,
                         callable=None, *args, **kwargs):
        assert substring is None or isinstance(substring, str)
        assert callable
        d = defer.maybeDeferred(callable, *args, **kwargs)
        d.addBoth(self.shouldHTTPError, which,
                  code, substring, response_substring)
        return d


class Web(WebMixin, unittest.TestCase):
    def test_create(self):
        pass

    def test_welcome(self):
        d = self.GET("/")
        def _check(res):
            self.failUnless('Welcome To AllMyData' in res)
            self.failUnless('Tahoe' in res)

            self.s.basedir = 'web/test_welcome'
            fileutil.make_dirs("web/test_welcome")
            fileutil.make_dirs("web/test_welcome/private")
            return self.GET("/")
        d.addCallback(_check)
        return d

    def test_provisioning_math(self):
        self.failUnlessEqual(provisioning.binomial(10, 0), 1)
        self.failUnlessEqual(provisioning.binomial(10, 1), 10)
        self.failUnlessEqual(provisioning.binomial(10, 2), 45)
        self.failUnlessEqual(provisioning.binomial(10, 9), 10)
        self.failUnlessEqual(provisioning.binomial(10, 10), 1)

    def test_provisioning(self):
        d = self.GET("/provisioning/")
        def _check(res):
            self.failUnless('Tahoe Provisioning Tool' in res)
            fields = {'filled': True,
                      "num_users": int(50e3),
                      "files_per_user": 1000,
                      "space_per_user": int(1e9),
                      "sharing_ratio": 1.0,
                      "encoding_parameters": "3-of-10-5",
                      "num_servers": 30,
                      "ownership_mode": "A",
                      "download_rate": 100,
                      "upload_rate": 10,
                      "delete_rate": 10,
                      "lease_timer": 7,
                      }
            return self.POST("/provisioning/", **fields)

        d.addCallback(_check)
        def _check2(res):
            self.failUnless('Tahoe Provisioning Tool' in res)
            self.failUnless("Share space consumed: 167.01TB" in res)

            fields = {'filled': True,
                      "num_users": int(50e6),
                      "files_per_user": 1000,
                      "space_per_user": int(5e9),
                      "sharing_ratio": 1.0,
                      "encoding_parameters": "25-of-100-50",
                      "num_servers": 30000,
                      "ownership_mode": "E",
                      "drive_failure_model": "U",
                      "drive_size": 1000,
                      "download_rate": 1000,
                      "upload_rate": 100,
                      "delete_rate": 100,
                      "lease_timer": 7,
                      }
            return self.POST("/provisioning/", **fields)
        d.addCallback(_check2)
        def _check3(res):
            self.failUnless("Share space consumed: huge!" in res)
            fields = {'filled': True}
            return self.POST("/provisioning/", **fields)
        d.addCallback(_check3)
        def _check4(res):
            self.failUnless("Share space consumed:" in res)
        d.addCallback(_check4)
        return d

    def test_status(self):
        dl_num = self.s.list_all_download_statuses()[0].get_counter()
        ul_num = self.s.list_all_upload_statuses()[0].get_counter()
        mu_num = self.s.list_all_mapupdate_statuses()[0].get_counter()
        pub_num = self.s.list_all_publish_statuses()[0].get_counter()
        ret_num = self.s.list_all_retrieve_statuses()[0].get_counter()
        d = self.GET("/status", followRedirect=True)
        def _check(res):
            self.failUnless('Upload and Download Status' in res, res)
            self.failUnless('"down-%d"' % dl_num in res, res)
            self.failUnless('"up-%d"' % ul_num in res, res)
            self.failUnless('"mapupdate-%d"' % mu_num in res, res)
            self.failUnless('"publish-%d"' % pub_num in res, res)
            self.failUnless('"retrieve-%d"' % ret_num in res, res)
        d.addCallback(_check)
        d.addCallback(lambda res: self.GET("/status/down-%d" % dl_num))
        def _check_dl(res):
            self.failUnless("File Download Status" in res, res)
        d.addCallback(_check_dl)
        d.addCallback(lambda res: self.GET("/status/up-%d" % ul_num))
        def _check_ul(res):
            self.failUnless("File Upload Status" in res, res)
        d.addCallback(_check_ul)
        d.addCallback(lambda res: self.GET("/status/mapupdate-%d" % mu_num))
        def _check_mapupdate(res):
            self.failUnless("Mutable File Servermap Update Status" in res, res)
        d.addCallback(_check_mapupdate)
        d.addCallback(lambda res: self.GET("/status/publish-%d" % pub_num))
        def _check_publish(res):
            self.failUnless("Mutable File Publish Status" in res, res)
        d.addCallback(_check_publish)
        d.addCallback(lambda res: self.GET("/status/retrieve-%d" % ret_num))
        def _check_retrieve(res):
            self.failUnless("Mutable File Retrieve Status" in res, res)
        d.addCallback(_check_retrieve)

        return d

    def test_status_numbers(self):
        drrm = status.DownloadResultsRendererMixin()
        self.failUnlessEqual(drrm.render_time(None, None), "")
        self.failUnlessEqual(drrm.render_time(None, 2.5), "2.50s")
        self.failUnlessEqual(drrm.render_time(None, 0.25), "250ms")
        self.failUnlessEqual(drrm.render_time(None, 0.0021), "2.1ms")
        self.failUnlessEqual(drrm.render_time(None, 0.000123), "123us")
        self.failUnlessEqual(drrm.render_rate(None, None), "")
        self.failUnlessEqual(drrm.render_rate(None, 2500000), "2.50MBps")
        self.failUnlessEqual(drrm.render_rate(None, 30100), "30.1kBps")
        self.failUnlessEqual(drrm.render_rate(None, 123), "123Bps")

        urrm = status.UploadResultsRendererMixin()
        self.failUnlessEqual(urrm.render_time(None, None), "")
        self.failUnlessEqual(urrm.render_time(None, 2.5), "2.50s")
        self.failUnlessEqual(urrm.render_time(None, 0.25), "250ms")
        self.failUnlessEqual(urrm.render_time(None, 0.0021), "2.1ms")
        self.failUnlessEqual(urrm.render_time(None, 0.000123), "123us")
        self.failUnlessEqual(urrm.render_rate(None, None), "")
        self.failUnlessEqual(urrm.render_rate(None, 2500000), "2.50MBps")
        self.failUnlessEqual(urrm.render_rate(None, 30100), "30.1kBps")
        self.failUnlessEqual(urrm.render_rate(None, 123), "123Bps")

    def test_GET_FILEURL(self):
        d = self.GET(self.public_url + "/foo/bar.txt")
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_HEAD_FILEURL(self):
        d = self.HEAD(self.public_url + "/foo/bar.txt")
        def _got(headers):
            self.failUnlessEqual(headers["content-length"][0],
                                 str(len(self.BAR_CONTENTS)))
            self.failUnlessEqual(headers["content-type"], ["text/plain"])
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
        verifier_cap = n.get_verifier().to_string()
        base = "/file/%s" % urllib.quote(verifier_cap)
        # client.create_node_from_uri() can't handle verify-caps
        d = self.shouldFail2(error.Error, "GET_unhandled_URI_named",
                             "400 Bad Request",
                             "is not a valid file- or directory- cap",
                             self.GET, base)
        return d

    def test_GET_unhandled_URI(self):
        contents, n, newuri = self.makefile(12)
        verifier_cap = n.get_verifier().to_string()
        base = "/uri/%s" % urllib.quote(verifier_cap)
        # client.create_node_from_uri() can't handle verify-caps
        d = self.shouldFail2(error.Error, "test_GET_unhandled_URI",
                             "400 Bad Request",
                             "is not a valid file- or directory- cap",
                             self.GET, base)
        return d

    def test_GET_FILE_URI(self):
        base = "/uri/%s" % urllib.quote(self._bar_txt_uri)
        d = self.GET(base)
        d.addCallback(self.failUnlessIsBarDotTxt)
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

    def test_GET_FILEURL_save(self):
        d = self.GET(self.public_url + "/foo/bar.txt?filename=bar.txt&save=true")
        # TODO: look at the headers, expect a Content-Disposition: attachment
        # header.
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_FILEURL_missing(self):
        d = self.GET(self.public_url + "/foo/missing")
        d.addBoth(self.should404, "test_GET_FILEURL_missing")
        return d

    def test_PUT_NEWFILEURL(self):
        d = self.PUT(self.public_url + "/foo/new.txt", self.NEWFILE_CONTENTS)
        # TODO: we lose the response code, so we can't check this
        #self.failUnlessEqual(responsecode, 201)
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_mutable(self):
        d = self.PUT(self.public_url + "/foo/new.txt?mutable=true",
                     self.NEWFILE_CONTENTS)
        # TODO: we lose the response code, so we can't check this
        #self.failUnlessEqual(responsecode, 201)
        def _check_uri(res):
            u = uri.from_string_mutable_filenode(res)
            self.failUnless(u.is_mutable())
            self.failIf(u.is_readonly())
            return res
        d.addCallback(_check_uri)
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessMutableChildContentsAre(self._foo_node,
                                                             u"new.txt",
                                                             self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_mutable_toobig(self):
        d = self.shouldFail2(error.Error, "test_PUT_NEWFILEURL_mutable_toobig",
                             "413 Request Entity Too Large",
                             "SDMF is limited to one segment, and 10001 > 10000",
                             self.PUT,
                             self.public_url + "/foo/new.txt?mutable=true",
                             "b" * (self.s.MUTABLE_SIZELIMIT+1))
        return d

    def test_PUT_NEWFILEURL_replace(self):
        d = self.PUT(self.public_url + "/foo/bar.txt", self.NEWFILE_CONTENTS)
        # TODO: we lose the response code, so we can't check this
        #self.failUnlessEqual(responsecode, 200)
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, u"bar.txt")
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
        d.addCallback(self.failUnlessURIMatchesChild, fn, u"newdir/new.txt")
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

    def test_GET_FILEURL_json(self):
        # twisted.web.http.parse_qs ignores any query args without an '=', so
        # I can't do "GET /path?json", I have to do "GET /path/t=json"
        # instead. This may make it tricky to emulate the S3 interface
        # completely.
        d = self.GET(self.public_url + "/foo/bar.txt?t=json")
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_GET_FILEURL_json_missing(self):
        d = self.GET(self.public_url + "/foo/missing?json")
        d.addBoth(self.should404, "test_GET_FILEURL_json_missing")
        return d

    def test_GET_FILEURL_uri(self):
        d = self.GET(self.public_url + "/foo/bar.txt?t=uri")
        def _check(res):
            self.failUnlessEqual(res, self._bar_txt_uri)
        d.addCallback(_check)
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/bar.txt?t=readonly-uri"))
        def _check2(res):
            # for now, for files, uris and readonly-uris are the same
            self.failUnlessEqual(res, self._bar_txt_uri)
        d.addCallback(_check2)
        return d

    def test_GET_FILEURL_badtype(self):
        d = self.shouldHTTPError2("GET t=bogus", 400, "Bad Request",
                                  "bad t=bogus",
                                  self.GET,
                                  self.public_url + "/foo/bar.txt?t=bogus")
        return d

    def test_GET_FILEURL_uri_missing(self):
        d = self.GET(self.public_url + "/foo/missing?t=uri")
        d.addBoth(self.should404, "test_GET_FILEURL_uri_missing")
        return d

    def test_GET_DIRURL(self):
        # the addSlash means we get a redirect here
        # from /uri/$URI/foo/ , we need ../../../ to get back to the root
        ROOT = "../../.."
        d = self.GET(self.public_url + "/foo", followRedirect=True)
        def _check(res):
            self.failUnless(('<a href="%s">Return to Welcome page' % ROOT)
                            in res, res)
            # the FILE reference points to a URI, but it should end in bar.txt
            bar_url = ("%s/file/%s/@@named=/bar.txt" %
                       (ROOT, urllib.quote(self._bar_txt_uri)))
            get_bar = "".join([r'<td>',
                               r'<a href="%s">bar.txt</a>' % bar_url,
                               r'</td>',
                               r'\s+<td>FILE</td>',
                               r'\s+<td>%d</td>' % len(self.BAR_CONTENTS),
                               ])
            self.failUnless(re.search(get_bar, res), res)
            for line in res.split("\n"):
                # find the line that contains the delete button for bar.txt
                if ("form action" in line and
                    'value="delete"' in line and
                    'value="bar.txt"' in line):
                    # the form target should use a relative URL
                    foo_url = urllib.quote("%s/uri/%s/" % (ROOT, self._foo_uri))
                    self.failUnless(('action="%s"' % foo_url) in line, line)
                    # and the when_done= should too
                    #done_url = urllib.quote(???)
                    #self.failUnless(('name="when_done" value="%s"' % done_url)
                    #                in line, line)
                    break
            else:
                self.fail("unable to find delete-bar.txt line", res)

            # the DIR reference just points to a URI
            sub_url = ("%s/uri/%s/" % (ROOT, urllib.quote(self._sub_uri)))
            get_sub = ((r'<td><a href="%s">sub</a></td>' % sub_url)
                       + r'\s+<td>DIR</td>')
            self.failUnless(re.search(get_sub, res), res)
        d.addCallback(_check)

        # look at a directory which is readonly
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/reedownlee", followRedirect=True))
        def _check2(res):
            self.failUnless("(readonly)" in res, res)
            self.failIf("Upload a file" in res, res)
        d.addCallback(_check2)

        # and at a directory that contains a readonly directory
        d.addCallback(lambda res:
                      self.GET(self.public_url, followRedirect=True))
        def _check3(res):
            self.failUnless(re.search(r'<td><a href="[\.\/]+/uri/URI%3ADIR2-RO%3A[^"]+">reedownlee</a>'
                                      '</td>\s+<td>DIR-RO</td>', res))
        d.addCallback(_check3)

        return d

    def test_GET_DIRURL_badtype(self):
        d = self.shouldHTTPError2("test_GET_DIRURL_badtype",
                                  400, "Bad Request",
                                  "bad t=bogus",
                                  self.GET,
                                  self.public_url + "/foo?t=bogus")
        return d

    def test_GET_DIRURL_json(self):
        d = self.GET(self.public_url + "/foo?t=json")
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    def test_GET_DIRURL_manifest(self):
        d = self.GET(self.public_url + "/foo?t=manifest", followRedirect=True)
        def _got(manifest):
            self.failUnless("Refresh Capabilities" in manifest)
        d.addCallback(_got)
        return d

    def test_GET_DIRURL_deepsize(self):
        d = self.GET(self.public_url + "/foo?t=deep-size", followRedirect=True)
        def _got(manifest):
            self.failUnless(re.search(r'^\d+$', manifest), manifest)
            self.failUnlessEqual("57", manifest)
        d.addCallback(_got)
        return d

    def test_GET_DIRURL_deepstats(self):
        d = self.GET(self.public_url + "/foo?t=deep-stats", followRedirect=True)
        def _got(stats_json):
            stats = simplejson.loads(stats_json)
            expected = {"count-immutable-files": 3,
                        "count-mutable-files": 0,
                        "count-literal-files": 0,
                        "count-files": 3,
                        "count-directories": 3,
                        "size-immutable-files": 57,
                        "size-literal-files": 0,
                        #"size-directories": 1912, # varies
                        #"largest-directory": 1590,
                        "largest-directory-children": 5,
                        "largest-immutable-file": 19,
                        }
            for k,v in expected.iteritems():
                self.failUnlessEqual(stats[k], v,
                                     "stats[%s] was %s, not %s" %
                                     (k, stats[k], v))
            self.failUnlessEqual(stats["size-files-histogram"],
                                 [ [11, 31, 3] ])
        d.addCallback(_got)
        return d

    def test_GET_DIRURL_uri(self):
        d = self.GET(self.public_url + "/foo?t=uri")
        def _check(res):
            self.failUnlessEqual(res, self._foo_uri)
        d.addCallback(_check)
        return d

    def test_GET_DIRURL_readonly_uri(self):
        d = self.GET(self.public_url + "/foo?t=readonly-uri")
        def _check(res):
            self.failUnlessEqual(res, self._foo_readonly_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWDIRURL(self):
        d = self.PUT(self.public_url + "/foo/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
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

    def test_PUT_NEWDIRURL_mkdir_p(self):
        d = defer.succeed(None)
        d.addCallback(lambda res: self.POST(self.public_url + "/foo", t='mkdir', name='mkp'))
        d.addCallback(lambda res: self.failUnlessNodeHasChild(self._foo_node, u"mkp"))
        d.addCallback(lambda res: self._foo_node.get(u"mkp"))
        def mkdir_p(mkpnode):
            url = '/uri/%s?t=mkdir-p&path=/sub1/sub2' % urllib.quote(mkpnode.get_uri())
            d = self.POST(url)
            def made_subsub(ssuri):
                d = self._foo_node.get_child_at_path(u"mkp/sub1/sub2")
                d.addCallback(lambda ssnode: self.failUnlessEqual(ssnode.get_uri(), ssuri))
                d = self.POST(url)
                d.addCallback(lambda uri2: self.failUnlessEqual(uri2, ssuri))
                return d
            d.addCallback(made_subsub)
            return d
        d.addCallback(mkdir_p)
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
        print "NODEWALK"
        w = webish.DirnodeWalkerMixin()
        def visitor(childpath, childnode, metadata):
            print childpath
        d = w.walk(self.public_root, visitor)
        return d

    def failUnlessNodeKeysAre(self, node, expected_keys):
        for k in expected_keys:
            assert isinstance(k, unicode)
        d = node.list()
        def _check(children):
            self.failUnlessEqual(sorted(children.keys()), sorted(expected_keys))
        d.addCallback(_check)
        return d
    def failUnlessNodeHasChild(self, node, name):
        assert isinstance(name, unicode)
        d = node.list()
        def _check(children):
            self.failUnless(name in children)
        d.addCallback(_check)
        return d
    def failIfNodeHasChild(self, node, name):
        assert isinstance(name, unicode)
        d = node.list()
        def _check(children):
            self.failIf(name in children)
        d.addCallback(_check)
        return d

    def failUnlessChildContentsAre(self, node, name, expected_contents):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        d.addCallback(lambda node: node.download_to_data())
        def _check(contents):
            self.failUnlessEqual(contents, expected_contents)
        d.addCallback(_check)
        return d

    def failUnlessMutableChildContentsAre(self, node, name, expected_contents):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        d.addCallback(lambda node: node.download_best_version())
        def _check(contents):
            self.failUnlessEqual(contents, expected_contents)
        d.addCallback(_check)
        return d

    def failUnlessChildURIIs(self, node, name, expected_uri):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        def _check(child):
            self.failUnlessEqual(child.get_uri(), expected_uri.strip())
        d.addCallback(_check)
        return d

    def failUnlessURIMatchesChild(self, got_uri, node, name):
        assert isinstance(name, unicode)
        d = node.get_child_at_path(name)
        def _check(child):
            self.failUnlessEqual(got_uri.strip(), child.get_uri())
        d.addCallback(_check)
        return d

    def failUnlessCHKURIHasContents(self, got_uri, contents):
        self.failUnless(FakeCHKFileNode.all_contents[got_uri] == contents)

    def test_POST_upload(self):
        d = self.POST(self.public_url + "/foo", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesChild, fn, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, u"new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_unicode(self):
        filename = u"n\u00e9wer.txt" # n e-acute w e r . t x t
        d = self.POST(self.public_url + "/foo", t="upload",
                      file=(filename, self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesChild, fn, filename)
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, filename,
                                                      self.NEWFILE_CONTENTS))
        target_url = self.public_url + "/foo/" + filename.encode("utf-8")
        d.addCallback(lambda res: self.GET(target_url))
        d.addCallback(lambda contents: self.failUnlessEqual(contents,
                                                            self.NEWFILE_CONTENTS,
                                                            contents))
        return d

    def test_POST_upload_unicode_named(self):
        filename = u"n\u00e9wer.txt" # n e-acute w e r . t x t
        d = self.POST(self.public_url + "/foo", t="upload",
                      name=filename,
                      file=("overridden", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesChild, fn, filename)
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, filename,
                                                      self.NEWFILE_CONTENTS))
        target_url = self.public_url + "/foo/" + filename.encode("utf-8")
        d.addCallback(lambda res: self.GET(target_url))
        d.addCallback(lambda contents: self.failUnlessEqual(contents,
                                                            self.NEWFILE_CONTENTS,
                                                            contents))
        return d

    def test_POST_upload_no_link(self):
        d = self.POST("/uri", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        def _check_upload_results(page):
            # this should be a page which describes the results of the upload
            # that just finished.
            self.failUnless("Upload Results:" in page)
            self.failUnless("URI:" in page)
            uri_re = re.compile("URI: <tt><span>(.*)</span>")
            mo = uri_re.search(page)
            self.failUnless(mo, page)
            new_uri = mo.group(1)
            return new_uri
        d.addCallback(_check_upload_results)
        d.addCallback(self.failUnlessCHKURIHasContents, self.NEWFILE_CONTENTS)
        return d

    def test_POST_upload_no_link_whendone(self):
        d = self.POST("/uri", t="upload", when_done="/",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        d.addBoth(self.shouldRedirect, "/")
        return d

    def shouldRedirect2(self, which, checker, callable, *args, **kwargs):
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(error.PageRedirect)
                statuscode = res.value.status
                target = res.value.location
                return checker(statuscode, target)
            self.fail("%s: callable was supposed to redirect, not return '%s'"
                      % (which, res))
        d.addBoth(done)
        return d

    def test_POST_upload_no_link_whendone_results(self):
        def check(statuscode, target):
            self.failUnlessEqual(statuscode, str(http.FOUND))
            self.failUnless(target.startswith(self.webish_url), target)
            return client.getPage(target, method="GET")
        d = self.shouldRedirect2("test_POST_upload_no_link_whendone_results",
                                 check,
                                 self.POST, "/uri", t="upload",
                                 when_done="/uri/%(uri)s",
                                 file=("new.txt", self.NEWFILE_CONTENTS))
        d.addCallback(lambda res:
                      self.failUnlessEqual(res, self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_no_link_mutable(self):
        d = self.POST("/uri", t="upload", mutable="true",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        def _check(new_uri):
            new_uri = new_uri.strip()
            self.new_uri = new_uri
            u = IURI(new_uri)
            self.failUnless(IMutableFileURI.providedBy(u))
            self.failUnless(u.storage_index in FakeMutableFileNode.all_contents)
            n = self.s.create_node_from_uri(new_uri)
            return n.download_best_version()
        d.addCallback(_check)
        def _check2(data):
            self.failUnlessEqual(data, self.NEWFILE_CONTENTS)
            return self.GET("/uri/%s" % urllib.quote(self.new_uri))
        d.addCallback(_check2)
        def _check3(data):
            self.failUnlessEqual(data, self.NEWFILE_CONTENTS)
            return self.GET("/file/%s" % urllib.quote(self.new_uri))
        d.addCallback(_check3)
        def _check4(data):
            self.failUnlessEqual(data, self.NEWFILE_CONTENTS)
        d.addCallback(_check4)
        return d

    def test_POST_upload_no_link_mutable_toobig(self):
        d = self.shouldFail2(error.Error,
                             "test_POST_upload_no_link_mutable_toobig",
                             "413 Request Entity Too Large",
                             "SDMF is limited to one segment, and 10001 > 10000",
                             self.POST,
                             "/uri", t="upload", mutable="true",
                             file=("new.txt",
                                   "b" * (self.s.MUTABLE_SIZELIMIT+1)) )
        return d

    def test_POST_upload_mutable(self):
        # this creates a mutable file
        d = self.POST(self.public_url + "/foo", t="upload", mutable="true",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesChild, fn, u"new.txt")
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
        d.addCallback(self.failUnlessURIMatchesChild, fn, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessMutableChildContentsAre(fn, u"new.txt",
                                                             NEWER_CONTENTS))
        d.addCallback(lambda res: self._foo_node.get(u"new.txt"))
        def _got2(newnode):
            self.failUnless(IMutableFileNode.providedBy(newnode))
            self.failUnless(newnode.is_mutable())
            self.failIf(newnode.is_readonly())
            self.failUnlessEqual(self._mutable_uri, newnode.get_uri())
        d.addCallback(_got2)

        # upload a second time, using PUT instead of POST
        NEW2_CONTENTS = NEWER_CONTENTS + "overwrite with PUT\n"
        d.addCallback(lambda res:
                      self.PUT(self.public_url + "/foo/new.txt", NEW2_CONTENTS))
        d.addCallback(self.failUnlessURIMatchesChild, fn, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessMutableChildContentsAre(fn, u"new.txt",
                                                             NEW2_CONTENTS))

        # finally list the directory, since mutable files are displayed
        # differently

        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/",
                               followRedirect=True))
        def _check_page(res):
            # TODO: assert more about the contents
            self.failUnless("Overwrite" in res)
            self.failUnless("Choose new file:" in res)
            return res
        d.addCallback(_check_page)

        # test that clicking on the "overwrite" button works
        EVEN_NEWER_CONTENTS = NEWER_CONTENTS + "even newer\n"
        def _parse_overwrite_form_and_submit(res):

            OVERWRITE_FORM_RE=re.compile('<form action="([^"]*)" method="post" .*<input type="hidden" name="t" value="upload" /><input type="hidden" name="when_done" value="([^"]*)" />', re.I)
            mo = OVERWRITE_FORM_RE.search(res)
            self.failUnless(mo, "overwrite form not found in '" + res +
                            "', in which the overwrite form was not found")
            formaction=mo.group(1)
            formwhendone=mo.group(2)

            fileurl = "../../../uri/" + urllib.quote(self._mutable_uri)
            self.failUnlessEqual(formaction, fileurl)
            # to POST, we need to absoluteify the URL
            new_formaction = "/uri/%s" % urllib.quote(self._mutable_uri)
            self.failUnlessEqual(formwhendone,
                                 "../uri/%s/" % urllib.quote(self._foo_uri))
            return self.POST(new_formaction,
                             t="upload",
                             file=("new.txt", EVEN_NEWER_CONTENTS),
                             when_done=formwhendone,
                             followRedirect=False)
        d.addCallback(_parse_overwrite_form_and_submit)
        # This will redirect us to ../uri/$FOOURI, rather than
        # ../uri/$PARENT/foo, but apparently twisted.web.client absolutifies
        # the redirect for us, and remember that shouldRedirect prepends
        # self.webish_url for us.
        d.addBoth(self.shouldRedirect,
                  "/uri/%s/" % urllib.quote(self._foo_uri),
                  which="test_POST_upload_mutable.overwrite")
        d.addCallback(lambda res:
                      self.failUnlessMutableChildContentsAre(fn, u"new.txt",
                                                             EVEN_NEWER_CONTENTS))
        d.addCallback(lambda res: self._foo_node.get(u"new.txt"))
        def _got3(newnode):
            self.failUnless(IMutableFileNode.providedBy(newnode))
            self.failUnless(newnode.is_mutable())
            self.failIf(newnode.is_readonly())
            self.failUnlessEqual(self._mutable_uri, newnode.get_uri())
        d.addCallback(_got3)

        # look at the JSON form of the enclosing directory
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/?t=json",
                               followRedirect=True))
        def _check_page_json(res):
            parsed = simplejson.loads(res)
            self.failUnlessEqual(parsed[0], "dirnode")
            children = parsed[1]["children"]
            self.failUnless("new.txt" in children)
            new_json = children["new.txt"]
            self.failUnlessEqual(new_json[0], "filenode")
            self.failUnless(new_json[1]["mutable"])
            self.failUnlessEqual(new_json[1]["rw_uri"], self._mutable_uri)
            ro_uri = unicode(self._mutable_node.get_readonly().to_string())
            self.failUnlessEqual(new_json[1]["ro_uri"], ro_uri)
        d.addCallback(_check_page_json)

        # and the JSON form of the file
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/new.txt?t=json"))
        def _check_file_json(res):
            parsed = simplejson.loads(res)
            self.failUnlessEqual(parsed[0], "filenode")
            self.failUnless(parsed[1]["mutable"])
            self.failUnlessEqual(parsed[1]["rw_uri"], self._mutable_uri)
            ro_uri = unicode(self._mutable_node.get_readonly().to_string())
            self.failUnlessEqual(parsed[1]["ro_uri"], ro_uri)
        d.addCallback(_check_file_json)

        # and look at t=uri and t=readonly-uri
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/new.txt?t=uri"))
        d.addCallback(lambda res: self.failUnlessEqual(res, self._mutable_uri))
        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo/new.txt?t=readonly-uri"))
        def _check_ro_uri(res):
            ro_uri = unicode(self._mutable_node.get_readonly().to_string())
            self.failUnlessEqual(res, ro_uri)
        d.addCallback(_check_ro_uri)

        # make sure we can get to it from /uri/URI
        d.addCallback(lambda res:
                      self.GET("/uri/%s" % urllib.quote(self._mutable_uri)))
        d.addCallback(lambda res:
                      self.failUnlessEqual(res, EVEN_NEWER_CONTENTS))

        # and that HEAD computes the size correctly
        d.addCallback(lambda res:
                      self.HEAD(self.public_url + "/foo/new.txt"))
        def _got_headers(headers):
            self.failUnlessEqual(headers["content-length"][0],
                                 str(len(EVEN_NEWER_CONTENTS)))
            self.failUnlessEqual(headers["content-type"], ["text/plain"])
        d.addCallback(_got_headers)

        # make sure that size errors are displayed correctly for overwrite
        d.addCallback(lambda res:
                      self.shouldFail2(error.Error,
                                       "test_POST_upload_mutable-toobig",
                                       "413 Request Entity Too Large",
                                       "SDMF is limited to one segment, and 10001 > 10000",
                                       self.POST,
                                       self.public_url + "/foo", t="upload",
                                       mutable="true",
                                       file=("new.txt",
                                             "b" * (self.s.MUTABLE_SIZELIMIT+1)),
                                       ))

        d.addErrback(self.dump_error)
        return d

    def test_POST_upload_mutable_toobig(self):
        d = self.shouldFail2(error.Error,
                             "test_POST_upload_no_link_mutable_toobig",
                             "413 Request Entity Too Large",
                             "SDMF is limited to one segment, and 10001 > 10000",
                             self.POST,
                             self.public_url + "/foo",
                             t="upload", mutable="true",
                             file=("new.txt",
                                   "b" * (self.s.MUTABLE_SIZELIMIT+1)) )
        return d

    def dump_error(self, f):
        # if the web server returns an error code (like 400 Bad Request),
        # web.client.getPage puts the HTTP response body into the .response
        # attribute of the exception object that it gives back. It does not
        # appear in the Failure's repr(), so the ERROR that trial displays
        # will be rather terse and unhelpful. addErrback this method to the
        # end of your chain to get more information out of these errors.
        if f.check(error.Error):
            print "web.error.Error:"
            print f
            print f.value.response
        return f

    def test_POST_upload_replace(self):
        d = self.POST(self.public_url + "/foo", t="upload",
                      file=("bar.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesChild, fn, u"bar.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, u"bar.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_no_replace_ok(self):
        d = self.POST(self.public_url + "/foo?replace=false", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/new.txt"))
        d.addCallback(lambda res: self.failUnlessEqual(res,
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

    def test_POST_upload_whendone(self):
        d = self.POST(self.public_url + "/foo", t="upload", when_done="/THERE",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        d.addBoth(self.shouldRedirect, "/THERE")
        fn = self._foo_node
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, u"new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_named(self):
        fn = self._foo_node
        d = self.POST(self.public_url + "/foo", t="upload",
                      name="new.txt", file=self.NEWFILE_CONTENTS)
        d.addCallback(self.failUnlessURIMatchesChild, fn, u"new.txt")
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
                                                 [u"bar.txt", u"blockingfile",
                                                  u"empty", u"n\u00fc.txt",
                                                  u"sub"]))
        return d

    def test_POST_FILEURL_check(self):
        d = self.POST(self.public_url + "/foo/bar.txt", t="check")
        def _check(res):
            # this currently just returns "None". You'd only really use it
            # with a when_done= redirect.
            self.failUnlessEqual(res, "None")
        d.addCallback(_check)
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

    def test_POST_mkdir_2(self):
        d = self.POST(self.public_url + "/foo/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"newdir"))
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdirs_2(self):
        d = self.POST(self.public_url + "/foo/bardir/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"bardir"))
        d.addCallback(lambda res: self._foo_node.get(u"bardir"))
        d.addCallback(lambda bardirnode: bardirnode.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdir_no_parentdir_noredirect(self):
        d = self.POST("/uri?t=mkdir")
        def _after_mkdir(res):
            uri.NewDirectoryURI.init_from_string(res)
        d.addCallback(_after_mkdir)
        return d

    def test_POST_mkdir_no_parentdir_redirect(self):
        d = self.POST("/uri?t=mkdir&redirect_to_result=true")
        d.addBoth(self.shouldRedirect, None, statuscode='303')
        def _check_target(target):
            target = urllib.unquote(target)
            self.failUnless(target.startswith("uri/URI:DIR2:"), target)
        d.addCallback(_check_target)
        return d

    def test_POST_noparent_bad(self):
        d = self.shouldHTTPError2("POST /uri?t=bogus", 400, "Bad Request",
                                  "/uri accepts only PUT, PUT?t=mkdir, "
                                  "POST?t=upload, and POST?t=mkdir",
                                  self.POST, "/uri?t=bogus")
        return d

    def test_welcome_page_mkdir_button(self):
        # Fetch the welcome page.
        d = self.GET("/")
        def _after_get_welcome_page(res):
            MKDIR_BUTTON_RE=re.compile('<form action="([^"]*)" method="post".*<input type="hidden" name="t" value="([^"]*)" /><input type="hidden" name="([^"]*)" value="([^"]*)" /><input type="submit" value="Create Directory!" />', re.I)
            mo = MKDIR_BUTTON_RE.search(res)
            formaction = mo.group(1)
            formt = mo.group(2)
            formaname = mo.group(3)
            formavalue = mo.group(4)
            return (formaction, formt, formaname, formavalue)
        d.addCallback(_after_get_welcome_page)
        def _after_parse_form(res):
            (formaction, formt, formaname, formavalue) = res
            return self.POST("/%s?t=%s&%s=%s" % (formaction, formt, formaname, formavalue))
        d.addCallback(_after_parse_form)
        d.addBoth(self.shouldRedirect, None, statuscode='303')
        return d

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

    def test_POST_mkdir_whendone_field(self):
        d = self.POST(self.public_url + "/foo",
                      t="mkdir", name="newdir", when_done="/THERE")
        d.addBoth(self.shouldRedirect, "/THERE")
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdir_whendone_queryarg(self):
        d = self.POST(self.public_url + "/foo?when_done=/THERE",
                      t="mkdir", name="newdir")
        d.addBoth(self.shouldRedirect, "/THERE")
        d.addCallback(lambda res: self._foo_node.get(u"newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_bad_t(self):
        d = self.shouldFail2(error.Error, "POST_bad_t", "400 Bad Request",
                             "POST to a directory with bad t=BOGUS",
                             self.POST, self.public_url + "/foo", t="BOGUS")
        return d

    def test_POST_set_children(self):
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

        url = self.webish_url + self.public_url + "/foo" + "?t=set_children"

        d = client.getPage(url, method="POST", postdata=reqbody)
        def _then(res):
            self.failUnlessURIMatchesChild(newuri9, self._foo_node, u"atomic_added_1")
            self.failUnlessURIMatchesChild(newuri10, self._foo_node, u"atomic_added_2")
            self.failUnlessURIMatchesChild(newuri11, self._foo_node, u"atomic_added_3")

        d.addCallback(_then)
        d.addErrback(self.dump_error)
        return d

    def test_POST_put_uri(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo", t="uri", name="new.txt", uri=newuri)
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, u"new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"new.txt",
                                                      contents))
        return d

    def test_POST_put_uri_replace(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo", t="uri", name="bar.txt", uri=newuri)
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, u"bar.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"bar.txt",
                                                      contents))
        return d

    def test_POST_put_uri_no_replace_queryarg(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo?replace=false", t="uri",
                      name="bar.txt", uri=newuri)
        d.addBoth(self.shouldFail, error.Error,
                  "POST_put_uri_no_replace_queryarg",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_POST_put_uri_no_replace_field(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo", t="uri", replace="false",
                      name="bar.txt", uri=newuri)
        d.addBoth(self.shouldFail, error.Error,
                  "POST_put_uri_no_replace_field",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self.GET(self.public_url + "/foo/bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_POST_delete(self):
        d = self.POST(self.public_url + "/foo", t="delete", name="bar.txt")
        d.addCallback(lambda res: self._foo_node.list())
        def _check(children):
            self.failIf(u"bar.txt" in children)
        d.addCallback(_check)
        return d

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

    def failUnlessIsEmptyJSON(self, res):
        data = simplejson.loads(res)
        self.failUnlessEqual(data[0], "dirnode", data)
        self.failUnlessEqual(len(data[1]["children"]), 0)

    def test_POST_rename_file_slash_fail(self):
        d = self.POST(self.public_url + "/foo", t="rename",
                      from_name="bar.txt", to_name='kirk/spock.txt')
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_rename_file_slash_fail",
                  "400 Bad Request",
                  "to_name= may not contain a slash",
                  )
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.POST(self.public_url, t="rename",
                      from_name="foo/bar.txt", to_name='george.txt'))
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_rename_file_slash_fail",
                  "400 Bad Request",
                  "from_name= may not contain a slash",
                  )
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self.public_root, u"foo"))
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self.public_root, u"george.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, u"bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo?t=json"))
        d.addCallback(self.failUnlessIsFooJSON)
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

    def shouldRedirect(self, res, target=None, statuscode=None, which=""):
        """ If target is not None then the redirection has to go to target.  If
        statuscode is not None then the redirection has to be accomplished with
        that HTTP status code."""
        if not isinstance(res, failure.Failure):
            to_where = (target is None) and "somewhere" or ("to " + target)
            self.fail("%s: we were expecting to get redirected %s, not get an"
                      " actual page: %s" % (which, to_where, res))
        res.trap(error.PageRedirect)
        if statuscode is not None:
            self.failUnlessEqual(res.value.status, statuscode,
                                 "%s: not a redirect" % which)
        if target is not None:
            # the PageRedirect does not seem to capture the uri= query arg
            # properly, so we can't check for it.
            realtarget = self.webish_url + target
            self.failUnlessEqual(res.value.location, realtarget,
                                 "%s: wrong target" % which)
        return res.value.location

    def test_GET_URI_form(self):
        base = "/uri?uri=%s" % self._bar_txt_uri
        # this is supposed to give us a redirect to /uri/$URI, plus arguments
        targetbase = "/uri/%s" % urllib.quote(self._bar_txt_uri)
        d = self.GET(base)
        d.addBoth(self.shouldRedirect, targetbase)
        d.addCallback(lambda res: self.GET(base+"&filename=bar.txt"))
        d.addBoth(self.shouldRedirect, targetbase+"?filename=bar.txt")
        d.addCallback(lambda res: self.GET(base+"&t=json"))
        d.addBoth(self.shouldRedirect, targetbase+"?t=json")
        d.addCallback(self.log, "about to get file by uri")
        d.addCallback(lambda res: self.GET(base, followRedirect=True))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(self.log, "got file by uri, about to get dir by uri")
        d.addCallback(lambda res: self.GET("/uri?uri=%s&t=json" % self._foo_uri,
                                           followRedirect=True))
        d.addCallback(self.failUnlessIsFooJSON)
        d.addCallback(self.log, "got dir by uri")

        return d

    def test_GET_URI_form_bad(self):
        d = self.shouldFail2(error.Error, "test_GET_URI_form_bad",
                             "400 Bad Request", "GET /uri requires uri=",
                             self.GET, "/uri")
        return d

    def test_GET_rename_form(self):
        d = self.GET(self.public_url + "/foo?t=rename-form&name=bar.txt",
                     followRedirect=True)
        def _check(res):
            self.failUnless('name="when_done" value="."' in res, res)
            self.failUnless(re.search(r'name="from_name" value="bar\.txt"', res))
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

    def test_GET_URI_URL_missing(self):
        base = "/uri/%s" % self._bad_file_uri
        d = self.GET(base)
        d.addBoth(self.shouldHTTPError, "test_GET_URI_URL_missing",
                  http.GONE, response_substring="NotEnoughSharesError")
        # TODO: how can we exercise both sides of WebDownloadTarget.fail
        # here? we must arrange for a download to fail after target.open()
        # has been called, and then inspect the response to see that it is
        # shorter than we expected.
        return d

    def test_PUT_NEWFILEURL_uri(self):
        contents, n, new_uri = self.makefile(8)
        d = self.PUT(self.public_url + "/foo/new.txt?t=uri", new_uri)
        d.addCallback(lambda res: self.failUnlessEqual(res.strip(), new_uri))
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"new.txt",
                                                      contents))
        return d

    def test_PUT_NEWFILEURL_uri_replace(self):
        contents, n, new_uri = self.makefile(8)
        d = self.PUT(self.public_url + "/foo/bar.txt?t=uri", new_uri)
        d.addCallback(lambda res: self.failUnlessEqual(res.strip(), new_uri))
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, u"bar.txt",
                                                      contents))
        return d

    def test_PUT_NEWFILEURL_uri_no_replace(self):
        contents, n, new_uri = self.makefile(8)
        d = self.PUT(self.public_url + "/foo/bar.txt?t=uri&replace=false", new_uri)
        d.addBoth(self.shouldFail, error.Error, "PUT_NEWFILEURL_uri_no_replace",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        return d

    def test_PUT_NEWFILE_URI(self):
        file_contents = "New file contents here\n"
        d = self.PUT("/uri", file_contents)
        def _check(uri):
            self.failUnless(uri in FakeCHKFileNode.all_contents)
            self.failUnlessEqual(FakeCHKFileNode.all_contents[uri],
                                 file_contents)
            return self.GET("/uri/%s" % uri)
        d.addCallback(_check)
        def _check2(res):
            self.failUnlessEqual(res, file_contents)
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
        def _check_mutable(uri):
            uri = uri.strip()
            u = IURI(uri)
            self.failUnless(IMutableFileURI.providedBy(u))
            self.failUnless(u.storage_index in FakeMutableFileNode.all_contents)
            n = self.s.create_node_from_uri(uri)
            return n.download_best_version()
        d.addCallback(_check_mutable)
        def _check2_mutable(data):
            self.failUnlessEqual(data, file_contents)
        d.addCallback(_check2_mutable)
        return d

        def _check(uri):
            self.failUnless(uri in FakeCHKFileNode.all_contents)
            self.failUnlessEqual(FakeCHKFileNode.all_contents[uri],
                                 file_contents)
            return self.GET("/uri/%s" % uri)
        d.addCallback(_check)
        def _check2(res):
            self.failUnlessEqual(res, file_contents)
        d.addCallback(_check2)
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

    def test_bad_method(self):
        url = self.webish_url + self.public_url + "/foo/bar.txt"
        d = self.shouldHTTPError2("test_bad_method",
                                  501, "Not Implemented",
                                  "I don't know how to treat a BOGUS request.",
                                  client.getPage, url, method="BOGUS")
        return d

    def test_short_url(self):
        url = self.webish_url + "/uri"
        d = self.shouldHTTPError2("test_short_url", 501, "Not Implemented",
                                  "I don't know how to treat a DELETE request.",
                                  client.getPage, url, method="DELETE")
        return d

class Util(unittest.TestCase):
    def test_abbreviate_time(self):
        self.failUnlessEqual(common.abbreviate_time(None), "")
        self.failUnlessEqual(common.abbreviate_time(1.234), "1.23s")
        self.failUnlessEqual(common.abbreviate_time(0.123), "123ms")
        self.failUnlessEqual(common.abbreviate_time(0.00123), "1.2ms")
        self.failUnlessEqual(common.abbreviate_time(0.000123), "123us")

    def test_abbreviate_rate(self):
        self.failUnlessEqual(common.abbreviate_rate(None), "")
        self.failUnlessEqual(common.abbreviate_rate(1234000), "1.23MBps")
        self.failUnlessEqual(common.abbreviate_rate(12340), "12.3kBps")
        self.failUnlessEqual(common.abbreviate_rate(123), "123Bps")

    def test_abbreviate_size(self):
        self.failUnlessEqual(common.abbreviate_size(None), "")
        self.failUnlessEqual(common.abbreviate_size(1.23*1000*1000*1000), "1.23GB")
        self.failUnlessEqual(common.abbreviate_size(1.23*1000*1000), "1.23MB")
        self.failUnlessEqual(common.abbreviate_size(1230), "1.2kB")
        self.failUnlessEqual(common.abbreviate_size(123), "123B")

