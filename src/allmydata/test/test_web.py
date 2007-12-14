import re, os.path, urllib
import simplejson
from twisted.application import service
from twisted.trial import unittest
from twisted.internet import defer
from twisted.web import client, error, http
from twisted.python import failure, log
from allmydata import webish, interfaces, provisioning
from allmydata.util import fileutil
from allmydata.test.common import NonGridDirectoryNode, FakeCHKFileNode, FakeMutableFileNode, create_chk_filenode
from allmydata.interfaces import IURI, INewDirectoryURI, IReadonlyNewDirectoryURI, IFileURI, IMutableFileURI, IMutableFileNode

# create a fake uploader/downloader, and a couple of fake dirnodes, then
# create a webserver that works against them

class FakeClient(service.MultiService):
    nodeid = "fake_nodeid"
    basedir = "fake_basedir"
    def get_versions(self):
        return {'allmydata': "fake",
                'foolscap': "fake",
                'twisted': "fake",
                'zfec': "fake",
                }
    introducer_furl = "None"
    def connected_to_introducer(self):
        return False
    def get_all_peerids(self):
        return []

    def create_node_from_uri(self, uri):
        u = IURI(uri)
        if (INewDirectoryURI.providedBy(u)
            or IReadonlyNewDirectoryURI.providedBy(u)):
            return NonGridDirectoryNode(self).init_from_uri(u)
        if IFileURI.providedBy(u):
            return FakeCHKFileNode(u, self)
        assert IMutableFileURI.providedBy(u), u
        return FakeMutableFileNode(self).init_from_uri(u)

    def create_empty_dirnode(self, wait_for_numpeers=None):
        n = NonGridDirectoryNode(self)
        d = n.create(wait_for_numpeers)
        d.addCallback(lambda res: n)
        return d

    def create_mutable_file(self, contents="", wait_for_numpeers=None):
        n = FakeMutableFileNode(self)
        return n.create(contents)

    def upload(self, uploadable, wait_for_numpeers=None):
        d = uploadable.get_size()
        d.addCallback(lambda size: uploadable.read(size))
        def _got_data(datav):
            data = "".join(datav)
            n = create_chk_filenode(self, data)
            return n.get_uri()
        d.addCallback(_got_data)
        return d


class WebMixin(object):
    def setUp(self):
        self.s = FakeClient()
        self.s.startService()
        self.ws = s = webish.WebishServer("0")
        s.allow_local_access(True)
        s.setServiceParent(self.s)
        port = s.listener._port.getHost().port
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
            self.public_root.set_uri("foo", foo.get_uri())

            self.BAR_CONTENTS, n, self._bar_txt_uri = self.makefile(0)
            foo.set_uri("bar.txt", self._bar_txt_uri)
            foo.set_uri("empty", res[3][1].get_uri())
            sub_uri = res[4][1].get_uri()
            foo.set_uri("sub", sub_uri)
            sub = self.s.create_node_from_uri(sub_uri)

            _ign, n, blocking_uri = self.makefile(1)
            foo.set_uri("blockingfile", blocking_uri)

            _ign, n, baz_file = self.makefile(2)
            sub.set_uri("baz.txt", baz_file)

            _ign, n, self._bad_file_uri = self.makefile(3)
            # this uri should not be downloadable
            del FakeCHKFileNode.all_contents[self._bad_file_uri]

            rodir = res[5][1]
            self.public_root.set_uri("reedownlee", rodir.get_readonly_uri())
            rodir.set_uri("nor", baz_file)

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
        d.addCallback(_then)
        return d

    def makefile(self, number):
        contents = "contents of file %s\n" % number
        n = create_chk_filenode(self.s, contents)
        return contents, n, n.get_uri()

    def tearDown(self):
        return self.s.stopService()

    def failUnlessIsBarDotTxt(self, res):
        self.failUnlessEqual(res, self.BAR_CONTENTS)

    def failUnlessIsBarJSON(self, res):
        data = simplejson.loads(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "filenode")
        self.failUnless(isinstance(data[1], dict))
        self.failIf("rw_uri" in data[1]) # immutable
        self.failUnlessEqual(data[1]["ro_uri"], self._bar_txt_uri)
        self.failUnlessEqual(data[1]["size"], len(self.BAR_CONTENTS))

    def failUnlessIsFooJSON(self, res):
        data = simplejson.loads(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "dirnode", res)
        self.failUnless(isinstance(data[1], dict))
        self.failUnless("rw_uri" in data[1]) # mutable
        self.failUnlessEqual(data[1]["rw_uri"], self._foo_uri)
        self.failUnlessEqual(data[1]["ro_uri"], self._foo_readonly_uri)

        kidnames = sorted(data[1]["children"])
        self.failUnlessEqual(kidnames,
                             ["bar.txt", "blockingfile", "empty", "sub"])
        kids = data[1]["children"]
        self.failUnlessEqual(kids["sub"][0], "dirnode")
        self.failUnlessEqual(kids["bar.txt"][0], "filenode")
        self.failUnlessEqual(kids["bar.txt"][1]["size"], len(self.BAR_CONTENTS))
        self.failUnlessEqual(kids["bar.txt"][1]["ro_uri"], self._bar_txt_uri)

    def GET(self, urlpath, followRedirect=False):
        url = self.webish_url + urlpath
        return client.getPage(url, method="GET", followRedirect=followRedirect)

    def PUT(self, urlpath, data):
        url = self.webish_url + urlpath
        return client.getPage(url, method="PUT", postdata=data)

    def DELETE(self, urlpath):
        url = self.webish_url + urlpath
        return client.getPage(url, method="DELETE")

    def POST(self, urlpath, **fields):
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
                            'filename="%s"' % (name, filename))
            else:
                form.append('Content-Disposition: form-data; name="%s"' % name)
            form.append('')
            form.append(str(value))
            form.append(sep)
        form[-1] += "--"
        body = "\r\n".join(form) + "\r\n"
        headers = {"content-type": "multipart/form-data; boundary=%s" % sepbase,
                   }
        return client.getPage(url, method="POST", postdata=body,
                              headers=headers, followRedirect=False)

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
                                "respose substring '%s' not in '%s'"
                                % (response_substring, res.value.response))
        else:
            self.fail("%s was supposed to raise %s, not get '%s'" %
                      (which, expected_failure, res))

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
                                "respose substring '%s' not in '%s'"
                                % (response_substring, res.value.response))
        else:
            self.fail("%s was supposed to Error(%s), not get '%s'" %
                      (which, code, res))

class Web(WebMixin, unittest.TestCase):
    def test_create(self):
        pass

    def test_welcome(self):
        d = self.GET("/")
        def _check(res):
            self.failUnless('Welcome To AllMyData' in res)
            self.failUnless('Tahoe' in res)
            self.failUnless('personal vdrive not available.' in res)

            self.s.basedir = 'web/test_welcome'
            fileutil.make_dirs("web/test_welcome")
            self.ws.create_start_html("private_uri",
                                      "web/test_welcome/start.html",
                                      "web/test_welcome/node.url")
            return self.GET("/")
        d.addCallback(_check)
        def _check2(res):
            self.failUnless('To view your personal private non-shared' in res)
            self.failUnless('from your local filesystem:' in res)
            self.failUnless(os.path.abspath('web/test_welcome/start.html')
                            in res)
        d.addCallback(_check2)
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

    def test_start_html(self):
        fileutil.make_dirs("web")
        startfile = "web/start.html"
        nodeurlfile = "web/node.url"
        self.ws.create_start_html("private_uri", startfile, nodeurlfile)

        self.failUnless(os.path.exists(startfile))
        start_html = open(startfile, "r").read()
        self.failUnless(self.webish_url in start_html)
        private_url = self.webish_url + "/uri/private_uri"
        self.failUnless(private_url in start_html)

        self.failUnless(os.path.exists(nodeurlfile))
        nodeurl = open(nodeurlfile, "r").read().strip()
        self.failUnless(nodeurl.startswith("http://localhost"))

    def test_GET_FILEURL(self):
        d = self.GET(self.public_url + "/foo/bar.txt")
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_FILEURL_save(self):
        d = self.GET(self.public_url + "/foo/bar.txt?save=bar.txt")
        # TODO: look at the headers, expect a Content-Disposition: attachment
        # header.
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_FILEURL_download(self):
        d = self.GET(self.public_url + "/foo/bar.txt?t=download")
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
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, "new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, "new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_replace(self):
        d = self.PUT(self.public_url + "/foo/bar.txt", self.NEWFILE_CONTENTS)
        # TODO: we lose the response code, so we can't check this
        #self.failUnlessEqual(responsecode, 200)
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, "bar.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, "bar.txt",
                                                      self.NEWFILE_CONTENTS))
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
        d.addCallback(self.failUnlessURIMatchesChild, fn, "newdir/new.txt")
        d.addCallback(lambda res: self.failIfNodeHasChild(fn, "new.txt"))
        d.addCallback(lambda res: self.failUnlessNodeHasChild(fn, "newdir"))
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, "newdir/new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_blocked(self):
        d = self.PUT(self.public_url + "/foo/blockingfile/new.txt",
                     self.NEWFILE_CONTENTS)
        d.addBoth(self.shouldFail, error.Error, "PUT_NEWFILEURL_blocked",
                  "400 Bad Request",
                  "cannot create directory because there is a file in the way")
        return d

    def test_DELETE_FILEURL(self):
        d = self.DELETE(self.public_url + "/foo/bar.txt")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, "bar.txt"))
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

    def disable_local_access(self, res=None):
        self.ws.allow_local_access(False)
        return res

    def test_GET_FILEURL_localfile(self):
        localfile = os.path.abspath("web/GET_FILEURL_local file")
        url = (self.public_url + "/foo/bar.txt?t=download&localfile=%s" %
               urllib.quote(localfile))
        fileutil.make_dirs("web")
        d = self.GET(url)
        def _done(res):
            self.failUnless(os.path.exists(localfile))
            data = open(localfile, "rb").read()
            self.failUnlessEqual(data, self.BAR_CONTENTS)
        d.addCallback(_done)
        return d

    def test_GET_FILEURL_localfile_disabled(self):
        localfile = os.path.abspath("web/GET_FILEURL_local file_disabled")
        url = (self.public_url + "/foo/bar.txt?t=download&localfile=%s" %
               urllib.quote(localfile))
        fileutil.make_dirs("web")
        self.disable_local_access()
        d = self.GET(url)
        d.addBoth(self.shouldFail, error.Error, "localfile disabled",
                  "403 Forbidden",
                  "local file access is disabled")
        return d

    def test_GET_FILEURL_localfile_nonlocal(self):
        # TODO: somehow pretend that we aren't local, and verify that the
        # server refuses to write to local files, probably by changing the
        # server's idea of what counts as "local".
        old_LOCALHOST = webish.LOCALHOST
        webish.LOCALHOST = "127.0.0.2"
        localfile = os.path.abspath("web/GET_FILEURL_local file_nonlocal")
        fileutil.make_dirs("web")
        d = self.GET(self.public_url + "/foo/bar.txt?t=download&localfile=%s"
                     % urllib.quote(localfile))
        d.addBoth(self.shouldFail, error.Error, "localfile non-local",
                  "403 Forbidden",
                  "localfile= or localdir= requires a local connection")
        def _check(res):
            self.failIf(os.path.exists(localfile))
        d.addCallback(_check)
        def _reset(res):
            webish.LOCALHOST = old_LOCALHOST
            return res
        d.addBoth(_reset)
        return d

    def test_GET_FILEURL_localfile_nonabsolute(self):
        localfile = "web/nonabsolute/path"
        fileutil.make_dirs("web/nonabsolute")
        d = self.GET(self.public_url + "/foo/bar.txt?t=download&localfile=%s"
                     % urllib.quote(localfile))
        d.addBoth(self.shouldFail, error.Error, "localfile non-absolute",
                  "403 Forbidden",
                  "localfile= or localdir= requires an absolute path")
        def _check(res):
            self.failIf(os.path.exists(localfile))
        d.addCallback(_check)
        return d

    def test_PUT_NEWFILEURL_localfile(self):
        localfile = os.path.abspath("web/PUT_NEWFILEURL_local file")
        url = (self.public_url + "/foo/new.txt?t=upload&localfile=%s" %
               urllib.quote(localfile))
        fileutil.make_dirs("web")
        f = open(localfile, "wb")
        f.write(self.NEWFILE_CONTENTS)
        f.close()
        d = self.PUT(url, "")
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, "new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, "new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_PUT_NEWFILEURL_localfile_disabled(self):
        localfile = os.path.abspath("web/PUT_NEWFILEURL_local file_disabled")
        url = (self.public_url + "/foo/new.txt?t=upload&localfile=%s" %
               urllib.quote(localfile))
        fileutil.make_dirs("web")
        f = open(localfile, "wb")
        f.write(self.NEWFILE_CONTENTS)
        f.close()
        self.disable_local_access()
        d = self.PUT(url, "")
        d.addBoth(self.shouldFail, error.Error, "put localfile disabled",
                  "403 Forbidden",
                  "local file access is disabled")
        return d

    def test_PUT_NEWFILEURL_localfile_mkdirs(self):
        localfile = os.path.abspath("web/PUT_NEWFILEURL_local file_mkdirs")
        fileutil.make_dirs("web")
        f = open(localfile, "wb")
        f.write(self.NEWFILE_CONTENTS)
        f.close()
        d = self.PUT(self.public_url + "/foo/newdir/new.txt?t=upload&localfile=%s"
                     % urllib.quote(localfile), "")
        d.addCallback(self.failUnlessURIMatchesChild,
                      self._foo_node, "newdir/new.txt")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, "new.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, "newdir"))
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node,
                                                      "newdir/new.txt",
                                                      self.NEWFILE_CONTENTS))
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

    def test_GET_FILEURL_uri_missing(self):
        d = self.GET(self.public_url + "/foo/missing?t=uri")
        d.addBoth(self.should404, "test_GET_FILEURL_uri_missing")
        return d

    def test_GET_DIRURL(self):
        # the addSlash means we get a redirect here
        d = self.GET(self.public_url + "/foo", followRedirect=True)
        def _check(res):
            # the FILE reference points to a URI, but it should end in bar.txt
            self.failUnless(re.search(r'<td>'
                                      '<a href="[^"]+bar.txt">bar.txt</a>'
                                      '</td>'
                                      '\s+<td>FILE</td>'
                                      '\s+<td>%d</td>' % len(self.BAR_CONTENTS)
                                      , res))
            self.failUnless(re.search(r'<td><a href="sub">sub</a></td>'
                                      '\s+<td>DIR</td>', res))
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
            self.failUnless(re.search(r'<td><a href="reedownlee">reedownlee</a>'
                                      '</td>\s+<td>DIR-RO</td>', res))
        d.addCallback(_check3)

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
                      self.failUnlessNodeHasChild(self._foo_node, "newdir"))
        d.addCallback(lambda res: self._foo_node.get("newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_PUT_NEWDIRURL_replace(self):
        d = self.PUT(self.public_url + "/foo/sub?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, "sub"))
        d.addCallback(lambda res: self._foo_node.get("sub"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_PUT_NEWDIRURL_no_replace(self):
        d = self.PUT(self.public_url + "/foo/sub?t=mkdir&replace=false", "")
        d.addBoth(self.shouldFail, error.Error, "PUT_NEWDIRURL_no_replace",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, "sub"))
        d.addCallback(lambda res: self._foo_node.get("sub"))
        d.addCallback(self.failUnlessNodeKeysAre, ["baz.txt"])
        return d

    def test_PUT_NEWDIRURL_mkdirs(self):
        d = self.PUT(self.public_url + "/foo/subdir/newdir?t=mkdir", "")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, "newdir"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, "subdir"))
        d.addCallback(lambda res:
                      self._foo_node.get_child_at_path("subdir/newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_DELETE_DIRURL(self):
        d = self.DELETE(self.public_url + "/foo")
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self.public_root, "foo"))
        return d

    def test_DELETE_DIRURL_missing(self):
        d = self.DELETE(self.public_url + "/foo/missing")
        d.addBoth(self.should404, "test_DELETE_DIRURL_missing")
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self.public_root, "foo"))
        return d

    def test_DELETE_DIRURL_missing2(self):
        d = self.DELETE(self.public_url + "/missing")
        d.addBoth(self.should404, "test_DELETE_DIRURL_missing2")
        return d

    def test_walker(self):
        out = []
        def _visitor(path, node, metadata):
            out.append((path, node))
            return defer.succeed(None)
        w = webish.DirnodeWalkerMixin()
        d = w.walk(self.public_root, _visitor)
        def _check(res):
            names = [path for (path,node) in out]
            self.failUnlessEqual(sorted(names),
                                 [('foo',),
                                  ('foo','bar.txt'),
                                  ('foo','blockingfile'),
                                  ('foo', 'empty'),
                                  ('foo', 'sub'),
                                  ('foo','sub','baz.txt'),
                                  ('reedownlee',),
                                  ('reedownlee', 'nor'),
                                  ])
            subindex = names.index( ('foo', 'sub') )
            bazindex = names.index( ('foo', 'sub', 'baz.txt') )
            self.failUnless(subindex < bazindex)
            for path,node in out:
                if path[-1] in ('bar.txt', 'blockingfile', 'baz.txt', 'nor'):
                    self.failUnless(interfaces.IFileNode.providedBy(node))
                else:
                    self.failUnless(interfaces.IDirectoryNode.providedBy(node))
        d.addCallback(_check)
        return d

    def test_GET_DIRURL_localdir(self):
        localdir = os.path.abspath("web/GET_DIRURL_local dir")
        fileutil.make_dirs("web")
        d = self.GET(self.public_url + "/foo?t=download&localdir=%s" %
                     urllib.quote(localdir))
        def _check(res):
            barfile = os.path.join(localdir, "bar.txt")
            self.failUnless(os.path.exists(barfile))
            data = open(barfile, "rb").read()
            self.failUnlessEqual(data, self.BAR_CONTENTS)
            blockingfile = os.path.join(localdir, "blockingfile")
            self.failUnless(os.path.exists(blockingfile))
            subdir = os.path.join(localdir, "sub")
            self.failUnless(os.path.isdir(subdir))
        d.addCallback(_check)
        return d

    def test_GET_DIRURL_localdir_disabled(self):
        localdir = os.path.abspath("web/GET_DIRURL_local dir_disabled")
        fileutil.make_dirs("web")
        self.disable_local_access()
        d = self.GET(self.public_url + "/foo?t=download&localdir=%s" %
                     urllib.quote(localdir))
        d.addBoth(self.shouldFail, error.Error, "localfile disabled",
                  "403 Forbidden",
                  "local file access is disabled")
        return d

    def test_GET_DIRURL_localdir_nonabsolute(self):
        localdir = "web/nonabsolute/dir path"
        fileutil.make_dirs("web/nonabsolute")
        d = self.GET(self.public_url + "/foo?t=download&localdir=%s" %
                     urllib.quote(localdir))
        d.addBoth(self.shouldFail, error.Error, "localdir non-absolute",
                  "403 Forbidden",
                  "localfile= or localdir= requires an absolute path")
        def _check(res):
            self.failIf(os.path.exists(localdir))
        d.addCallback(_check)
        return d

    def touch(self, localdir, filename):
        path = os.path.join(localdir, filename)
        f = open(path, "wb")
        f.write("contents of %s\n" % filename)
        f.close()

    def dump_root(self):
        print "NODEWALK"
        w = webish.DirnodeWalkerMixin()
        def visitor(childpath, childnode, metadata):
            print childpath
        d = w.walk(self.public_root, visitor)
        return d

    def failUnlessNodeKeysAre(self, node, expected_keys):
        d = node.list()
        def _check(children):
            self.failUnlessEqual(sorted(children.keys()), sorted(expected_keys))
        d.addCallback(_check)
        return d
    def failUnlessNodeHasChild(self, node, name):
        d = node.list()
        def _check(children):
            self.failUnless(name in children)
        d.addCallback(_check)
        return d
    def failIfNodeHasChild(self, node, name):
        d = node.list()
        def _check(children):
            self.failIf(name in children)
        d.addCallback(_check)
        return d

    def failUnlessChildContentsAre(self, node, name, expected_contents):
        d = node.get_child_at_path(name)
        d.addCallback(lambda node: node.download_to_data())
        def _check(contents):
            self.failUnlessEqual(contents, expected_contents)
        d.addCallback(_check)
        return d

    def failUnlessChildURIIs(self, node, name, expected_uri):
        d = node.get_child_at_path(name)
        def _check(child):
            self.failUnlessEqual(child.get_uri(), expected_uri.strip())
        d.addCallback(_check)
        return d

    def failUnlessURIMatchesChild(self, got_uri, node, name):
        d = node.get_child_at_path(name)
        def _check(child):
            self.failUnlessEqual(got_uri.strip(), child.get_uri())
        d.addCallback(_check)
        return d

    def failUnlessCHKURIHasContents(self, got_uri, contents):
        self.failUnless(FakeCHKFileNode.all_contents[got_uri] == contents)

    def test_PUT_NEWDIRURL_localdir(self):
        localdir = os.path.abspath("web/PUT_NEWDIRURL_local dir")
        # create some files there
        fileutil.make_dirs(os.path.join(localdir, "one"))
        fileutil.make_dirs(os.path.join(localdir, "one/sub"))
        fileutil.make_dirs(os.path.join(localdir, "two"))
        fileutil.make_dirs(os.path.join(localdir, "three"))
        self.touch(localdir, "three/foo.txt")
        self.touch(localdir, "three/bar.txt")
        self.touch(localdir, "zap.zip")

        d = self.PUT(self.public_url + "/newdir?t=upload&localdir=%s"
                     % urllib.quote(localdir), "")
        pr = self.public_root
        d.addCallback(lambda res: self.failUnlessNodeHasChild(pr, "newdir"))
        d.addCallback(lambda res: pr.get("newdir"))
        d.addCallback(self.failUnlessNodeKeysAre,
                      ["one", "two", "three", "zap.zip"])
        d.addCallback(lambda res: pr.get_child_at_path("newdir/one"))
        d.addCallback(self.failUnlessNodeKeysAre, ["sub"])
        d.addCallback(lambda res: pr.get_child_at_path("newdir/three"))
        d.addCallback(self.failUnlessNodeKeysAre, ["foo.txt", "bar.txt"])
        d.addCallback(lambda res: pr.get_child_at_path("newdir/three/bar.txt"))
        d.addCallback(lambda barnode: barnode.download_to_data())
        d.addCallback(lambda contents:
                      self.failUnlessEqual(contents,
                                           "contents of three/bar.txt\n"))
        return d

    def test_PUT_NEWDIRURL_localdir_disabled(self):
        localdir = os.path.abspath("web/PUT_NEWDIRURL_local dir_disabled")
        # create some files there
        fileutil.make_dirs(os.path.join(localdir, "one"))
        fileutil.make_dirs(os.path.join(localdir, "one/sub"))
        fileutil.make_dirs(os.path.join(localdir, "two"))
        fileutil.make_dirs(os.path.join(localdir, "three"))
        self.touch(localdir, "three/foo.txt")
        self.touch(localdir, "three/bar.txt")
        self.touch(localdir, "zap.zip")

        self.disable_local_access()
        d = self.PUT(self.public_url + "/newdir?t=upload&localdir=%s"
                     % urllib.quote(localdir), "")
        d.addBoth(self.shouldFail, error.Error, "localfile disabled",
                  "403 Forbidden",
                  "local file access is disabled")
        return d

    def test_PUT_NEWDIRURL_localdir_mkdirs(self):
        localdir = os.path.abspath("web/PUT_NEWDIRURL_local dir_mkdirs")
        # create some files there
        fileutil.make_dirs(os.path.join(localdir, "one"))
        fileutil.make_dirs(os.path.join(localdir, "one/sub"))
        fileutil.make_dirs(os.path.join(localdir, "two"))
        fileutil.make_dirs(os.path.join(localdir, "three"))
        self.touch(localdir, "three/foo.txt")
        self.touch(localdir, "three/bar.txt")
        self.touch(localdir, "zap.zip")

        d = self.PUT(self.public_url + "/foo/subdir/newdir?t=upload&localdir=%s"
                     % urllib.quote(localdir),
                     "")
        fn = self._foo_node
        d.addCallback(lambda res: self.failUnlessNodeHasChild(fn, "subdir"))
        d.addCallback(lambda res: fn.get_child_at_path("subdir/newdir"))
        d.addCallback(self.failUnlessNodeKeysAre,
                      ["one", "two", "three", "zap.zip"])
        d.addCallback(lambda res: fn.get_child_at_path("subdir/newdir/one"))
        d.addCallback(self.failUnlessNodeKeysAre, ["sub"])
        d.addCallback(lambda res: fn.get_child_at_path("subdir/newdir/three"))
        d.addCallback(self.failUnlessNodeKeysAre, ["foo.txt", "bar.txt"])
        d.addCallback(lambda res:
                      fn.get_child_at_path("subdir/newdir/three/bar.txt"))
        d.addCallback(lambda barnode: barnode.download_to_data())
        d.addCallback(lambda contents:
                      self.failUnlessEqual(contents,
                                           "contents of three/bar.txt\n"))
        return d

    def test_POST_upload(self):
        d = self.POST(self.public_url + "/foo", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesChild, fn, "new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, "new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_no_link(self):
        d = self.POST("/uri/", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        d.addCallback(self.failUnlessCHKURIHasContents, self.NEWFILE_CONTENTS)
        return d

    def test_POST_upload_no_link_whendone(self):
        d = self.POST("/uri/", t="upload", when_done="/",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        d.addBoth(self.shouldRedirect, "/")
        # XXX Test that resulting welcome page has a "most recent
        # upload", the URI of which points to the file contents that
        # you just uploaded.
        return d
    test_POST_upload_no_link_whendone.todo = "Not yet implemented."

    def test_POST_upload_mutable(self):
        # this creates a mutable file
        d = self.POST(self.public_url + "/foo", t="upload", mutable="true",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesChild, fn, "new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, "new.txt",
                                                      self.NEWFILE_CONTENTS))
        d.addCallback(lambda res: self._foo_node.get("new.txt"))
        def _got(newnode):
            self.failUnless(IMutableFileNode.providedBy(newnode))
            self.failUnless(newnode.is_mutable())
            self.failIf(newnode.is_readonly())
            self._mutable_uri = newnode.get_uri()
        d.addCallback(_got)

        # now upload it again and make sure that the URI doesn't change
        NEWER_CONTENTS = self.NEWFILE_CONTENTS + "newer\n"
        d.addCallback(lambda res:
                      self.POST(self.public_url + "/foo", t="upload",
                                mutable="true",
                                file=("new.txt", NEWER_CONTENTS)))
        d.addCallback(self.failUnlessURIMatchesChild, fn, "new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, "new.txt",
                                                      NEWER_CONTENTS))
        d.addCallback(lambda res: self._foo_node.get("new.txt"))
        def _got2(newnode):
            self.failUnless(IMutableFileNode.providedBy(newnode))
            self.failUnless(newnode.is_mutable())
            self.failIf(newnode.is_readonly())
            self.failUnlessEqual(self._mutable_uri, newnode.get_uri())
        d.addCallback(_got2)

        # also test t=overwrite while we're here
        EVEN_NEWER_CONTENTS = NEWER_CONTENTS + "even newer\n"
        d.addCallback(lambda res:
                      self.POST(self.public_url + "/foo/new.txt",
                                t="overwrite",
                                file=("new.txt", EVEN_NEWER_CONTENTS)))
        d.addCallback(self.failUnlessURIMatchesChild, fn, "new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, "new.txt",
                                                      EVEN_NEWER_CONTENTS))
        d.addCallback(lambda res: self._foo_node.get("new.txt"))
        def _got3(newnode):
            self.failUnless(IMutableFileNode.providedBy(newnode))
            self.failUnless(newnode.is_mutable())
            self.failIf(newnode.is_readonly())
            self.failUnlessEqual(self._mutable_uri, newnode.get_uri())
        d.addCallback(_got3)

        # finally list the directory, since mutable files are displayed
        # differently

        d.addCallback(lambda res:
                      self.GET(self.public_url + "/foo",
                               followRedirect=True))
        def _check_page(res):
            # TODO: assert more about the contents
            self.failUnless("Overwrite" in res)
            self.failUnless("Choose new file:" in res)
        d.addCallback(_check_page)

        return d

    def test_POST_upload_replace(self):
        d = self.POST(self.public_url + "/foo", t="upload",
                      file=("bar.txt", self.NEWFILE_CONTENTS))
        fn = self._foo_node
        d.addCallback(self.failUnlessURIMatchesChild, fn, "bar.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, "bar.txt",
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
                      self.failUnlessChildContentsAre(fn, "new.txt",
                                                      self.NEWFILE_CONTENTS))
        return d

    def test_POST_upload_named(self):
        fn = self._foo_node
        d = self.POST(self.public_url + "/foo", t="upload",
                      name="new.txt", file=self.NEWFILE_CONTENTS)
        d.addCallback(self.failUnlessURIMatchesChild, fn, "new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(fn, "new.txt",
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
                                                 ["bar.txt", "blockingfile",
                                                  "empty", "sub"]))
        return d

    def test_POST_mkdir(self): # return value?
        d = self.POST(self.public_url + "/foo", t="mkdir", name="newdir")
        d.addCallback(lambda res: self._foo_node.get("newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdir_replace(self): # return value?
        d = self.POST(self.public_url + "/foo", t="mkdir", name="sub")
        d.addCallback(lambda res: self._foo_node.get("sub"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdir_no_replace_queryarg(self): # return value?
        d = self.POST(self.public_url + "/foo?replace=false", t="mkdir", name="sub")
        d.addBoth(self.shouldFail, error.Error,
                  "POST_mkdir_no_replace_queryarg",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self._foo_node.get("sub"))
        d.addCallback(self.failUnlessNodeKeysAre, ["baz.txt"])
        return d

    def test_POST_mkdir_no_replace_field(self): # return value?
        d = self.POST(self.public_url + "/foo", t="mkdir", name="sub",
                      replace="false")
        d.addBoth(self.shouldFail, error.Error, "POST_mkdir_no_replace_field",
                  "409 Conflict",
                  "There was already a child by that name, and you asked me "
                  "to not replace it")
        d.addCallback(lambda res: self._foo_node.get("sub"))
        d.addCallback(self.failUnlessNodeKeysAre, ["baz.txt"])
        return d

    def test_POST_mkdir_whendone_field(self):
        d = self.POST(self.public_url + "/foo",
                      t="mkdir", name="newdir", when_done="/THERE")
        d.addBoth(self.shouldRedirect, "/THERE")
        d.addCallback(lambda res: self._foo_node.get("newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_mkdir_whendone_queryarg(self):
        d = self.POST(self.public_url + "/foo?when_done=/THERE",
                      t="mkdir", name="newdir")
        d.addBoth(self.shouldRedirect, "/THERE")
        d.addCallback(lambda res: self._foo_node.get("newdir"))
        d.addCallback(self.failUnlessNodeKeysAre, [])
        return d

    def test_POST_put_uri(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo", t="uri", name="new.txt", uri=newuri)
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, "new.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, "new.txt",
                                                      contents))
        return d

    def test_POST_put_uri_replace(self):
        contents, n, newuri = self.makefile(8)
        d = self.POST(self.public_url + "/foo", t="uri", name="bar.txt", uri=newuri)
        d.addCallback(self.failUnlessURIMatchesChild, self._foo_node, "bar.txt")
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, "bar.txt",
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
            self.failIf("bar.txt" in children)
        d.addCallback(_check)
        return d

    def test_POST_rename_file(self):
        d = self.POST(self.public_url + "/foo", t="rename",
                      from_name="bar.txt", to_name='wibble.txt')
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self._foo_node, "bar.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, "wibble.txt"))
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
                      self.failIfNodeHasChild(self._foo_node, "bar.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, "empty"))
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
                      self.failUnlessNodeHasChild(self._foo_node, "bar.txt"))
        d.addCallback(lambda res: self.POST(self.public_url, t="rename",
                      from_name="foo/bar.txt", to_name='george.txt'))
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_rename_file_slash_fail",
                  "400 Bad Request",
                  "from_name= may not contain a slash",
                  )
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self.public_root, "foo"))
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self.public_root, "george.txt"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self._foo_node, "bar.txt"))
        d.addCallback(lambda res: self.GET(self.public_url + "/foo?t=json"))
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    def test_POST_rename_dir(self):
        d = self.POST(self.public_url, t="rename",
                      from_name="foo", to_name='plunk')
        d.addCallback(lambda res:
                      self.failIfNodeHasChild(self.public_root, "foo"))
        d.addCallback(lambda res:
                      self.failUnlessNodeHasChild(self.public_root, "plunk"))
        d.addCallback(lambda res: self.GET(self.public_url + "/plunk?t=json"))
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    def shouldRedirect(self, res, target):
        if not isinstance(res, failure.Failure):
            self.fail("we were expecting to get redirected to %s, not get an"
                      " actual page: %s" % (target, res))
        res.trap(error.PageRedirect)
        # the PageRedirect does not seem to capture the uri= query arg
        # properly, so we can't check for it.
        realtarget = self.webish_url + target
        self.failUnlessEqual(res.value.location, realtarget)

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

    def test_GET_rename_form(self):
        d = self.GET(self.public_url + "/foo?t=rename-form&name=bar.txt",
                     followRedirect=True) # XXX [ ] todo: figure out why '.../foo' doesn't work
        def _check(res):
            self.failUnless(re.search(r'name="when_done" value=".*%s/foo/' % (urllib.quote(self.public_url),), res), (r'name="when_done" value=".*%s/foo/' % (urllib.quote(self.public_url),), res,))
            self.failUnless(re.search(r'name="from_name" value="bar\.txt"', res))
        d.addCallback(_check)
        return d

    def log(self, res, msg):
        #print "MSG: %s  RES: %s" % (msg, res)
        log.msg(msg)
        return res

    def test_GET_URI_URL(self):
        base = "/uri/%s" % self._bar_txt_uri.replace("/","!")
        d = self.GET(base)
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(base+"?filename=bar.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET(base+"?filename=bar.txt&save=true"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_URI_URL_dir(self):
        base = "/uri/%s?t=json" % self._foo_uri.replace("/","!")
        d = self.GET(base)
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    def test_GET_URI_URL_missing(self):
        base = "/uri/%s" % self._bad_file_uri.replace("/","!")
        d = self.GET(base)
        d.addBoth(self.shouldHTTPError, "test_GET_URI_URL_missing",
                  http.GONE, response_substring="NotEnoughPeersError")
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
                      self.failUnlessChildContentsAre(self._foo_node, "new.txt",
                                                      contents))
        return d

    def test_PUT_NEWFILEURL_uri_replace(self):
        contents, n, new_uri = self.makefile(8)
        d = self.PUT(self.public_url + "/foo/bar.txt?t=uri", new_uri)
        d.addCallback(lambda res: self.failUnlessEqual(res.strip(), new_uri))
        d.addCallback(lambda res:
                      self.failUnlessChildContentsAre(self._foo_node, "bar.txt",
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
            return self.GET("/uri/%s" % uri.replace("/","!"))
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
                  "/uri only accepts PUT and PUT?t=mkdir")
        return d

    def test_PUT_NEWDIR_URI(self):
        d = self.PUT("/uri?t=mkdir", "")
        def _check(uri):
            n = self.s.create_node_from_uri(uri.strip())
            d2 = self.failUnlessNodeKeysAre(n, [])
            d2.addCallback(lambda res:
                           self.GET("/uri/%s?t=json" % uri.replace("/","!")))
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
