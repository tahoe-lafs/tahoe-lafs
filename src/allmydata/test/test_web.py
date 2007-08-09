
import re, os.path, urllib
from zope.interface import implements
from twisted.application import service
from twisted.trial import unittest
from twisted.internet import defer
from twisted.web import client, error, http
from twisted.python import failure, log
from allmydata import webish, interfaces, dirnode, uri
from allmydata.encode import NotEnoughPeersError
from allmydata.util import fileutil
import itertools

# create a fake uploader/downloader, and a couple of fake dirnodes, then
# create a webserver that works against them

class MyClient(service.MultiService):
    nodeid = "fake_nodeid"
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

class MyDownloader(service.Service):
    implements(interfaces.IDownloader)
    name = "downloader"
    def __init__(self, files):
        self.files = files

    def download(self, uri, target):
        if uri not in self.files:
            e = NotEnoughPeersError()
            f = failure.Failure(e)
            target.fail(f)
            return defer.fail(f)
        data = self.files[uri]
        target.open(len(data))
        target.write(data)
        target.close()
        return defer.maybeDeferred(target.finish)

uri_counter = itertools.count()

def make_newuri(data):
    n = uri_counter.next()
    assert len(str(n)) < 5
    newuri = uri.CHKFileURI(key="K%05d" % n + "k"*10,
                            uri_extension_hash="EH" + "h"*30,
                            needed_shares=25,
                            total_shares=100,
                            size=len(data))
    return newuri.to_string()

class MyUploader(service.Service):
    implements(interfaces.IUploader)
    name = "uploader"
    def __init__(self, files):
        self.files = files

    def upload(self, uploadable):
        d = uploadable.get_size()
        d.addCallback(lambda size: uploadable.read(size))
        d.addCallback(lambda data: "".join(data))
        def _got_data(data):
            newuri = make_newuri(data)
            self.files[newuri] = data
            uploadable.close()
        d.addCallback(_got_data)
        return d

class MyDirectoryNode(dirnode.MutableDirectoryNode):

    def __init__(self, nodes, files, client, myuri=None):
        self._my_nodes = nodes
        self._my_files = files
        self._my_client = client
        if myuri is None:
            u = uri.DirnodeURI("furl", "idx%s" % str(uri_counter.next()))
            myuri = u.to_string()
        self._uri = myuri
        self._my_nodes[self._uri] = self
        self.children = {}
        self._mutable = True

    def get(self, name):
        def _try():
            uri = self.children[name]
            if uri not in self._my_nodes:
                raise IndexError("this isn't supposed to happen")
            return self._my_nodes[uri]
        return defer.maybeDeferred(_try)

    def set_uri(self, name, child_uri):
        self.children[name] = child_uri
        return defer.succeed(None)

    def add_file(self, name, uploadable):
        d = uploadable.get_size()
        d.addCallback(lambda size: uploadable.read(size))
        d.addCallback(lambda data: "".join(data))
        def _got_data(data):
            newuri = make_newuri(data)
            self._my_files[newuri] = data
            self._my_nodes[newuri] = MyFileNode(newuri, self._my_client)
            self.children[name] = newuri
            uploadable.close()
            return self._my_nodes[newuri]
        d.addCallback(_got_data)
        return d

    def delete(self, name):
        def _try():
            del self.children[name]
        return defer.maybeDeferred(_try)

    def create_empty_directory(self, name):
        node = MyDirectoryNode(self._my_nodes, self._my_files, self._my_client)
        self.children[name] = node.get_uri()
        return defer.succeed(node)

    def list(self):
        kids = dict([(name, self._my_nodes[uri])
                     for name,uri in self.children.iteritems()])
        return defer.succeed(kids)

class MyFileNode(dirnode.FileNode):
    pass


class MyVirtualDrive(service.Service):
    name = "vdrive"
    public_root = None
    private_root = None
    def __init__(self, nodes):
        self._my_nodes = nodes
    def have_public_root(self):
        return bool(self.public_root)
    def have_private_root(self):
        return bool(self.private_root)
    def get_public_root(self):
        return defer.succeed(self.public_root)
    def get_private_root(self):
        return defer.succeed(self.private_root)

    def get_node(self, uri):
        def _try():
            return self._my_nodes[uri]
        return defer.maybeDeferred(_try)

class WebMixin(unittest.TestCase):
    def setUp(self):
        self.s = MyClient()
        self.s.startService()
        s = webish.WebishServer("0")
        s.setServiceParent(self.s)
        port = s.listener._port.getHost().port
        self.webish_url = "http://localhost:%d" % port

        self.nodes = {} # maps URI to node
        self.files = {} # maps file URI to contents

        v = MyVirtualDrive(self.nodes)
        v.setServiceParent(self.s)

        dl = MyDownloader(self.files)
        dl.setServiceParent(self.s)
        ul = MyUploader(self.files)
        ul.setServiceParent(self.s)

        v.public_root = self.makedir()
        self.public_root = v.public_root
        v.private_root = self.makedir()
        foo = self.makedir()
        self._foo_node = foo
        self._foo_uri = foo.get_uri()
        self._foo_readonly_uri = foo.get_immutable_uri()
        v.public_root.children["foo"] = foo.get_uri()


        self._bar_txt_uri = self.makefile(0)
        self.BAR_CONTENTS = self.files[self._bar_txt_uri]
        foo.children["bar.txt"] = self._bar_txt_uri
        foo.children["empty"] = self.makedir().get_uri()
        sub_uri = foo.children["sub"] = self.makedir().get_uri()
        sub = self.nodes[sub_uri]

        blocking_uri = self.make_smallfile(1)
        foo.children["blockingfile"] = blocking_uri

        baz_file = self.makefile(2)
        sub.children["baz.txt"] = baz_file

        self._bad_file_uri = self.makefile(3)
        del self.files[self._bad_file_uri]

        rodir = self.makedir()
        rodir._mutable = False
        v.public_root.children["readonly"] = rodir.get_uri()
        rodir.children["nor"] = baz_file

        # public/
        # public/foo/
        # public/foo/bar.txt
        # public/foo/blockingfile
        # public/foo/empty/
        # public/foo/sub/
        # public/foo/sub/baz.txt
        # public/readonly/
        # public/readonly/nor
        self.NEWFILE_CONTENTS = "newfile contents\n"

    def makefile(self, number):
        n = str(number)
        assert len(n) == 1
        newuri = uri.CHKFileURI(key="K" + n*15,
                                uri_extension_hash="EH" + n*30,
                                needed_shares=25,
                                total_shares=100,
                                size=123+number).to_string()
        assert newuri not in self.nodes
        assert newuri not in self.files
        node = MyFileNode(newuri, self.s)
        self.nodes[newuri] = node
        contents = "contents of file %s\n" % n
        self.files[newuri] = contents
        return newuri

    def make_smallfile(self, number):
        n = str(number)
        assert len(n) == 1
        contents = "small data %s\n" % n
        newuri = uri.LiteralFileURI(contents).to_string()
        assert newuri not in self.nodes
        assert newuri not in self.files
        node = MyFileNode(newuri, self.s)
        self.nodes[newuri] = node
        self.files[newuri] = contents
        return newuri

    def makedir(self):
        node = MyDirectoryNode(self.nodes, self.files, self.s)
        return node

    def tearDown(self):
        return self.s.stopService()

    def failUnlessIsBarDotTxt(self, res):
        self.failUnlessEqual(res, self.BAR_CONTENTS)

    def worlds_cheapest_json_decoder(self, json):
        # don't write tests that use 'true' or 'false' as filenames
        json = re.sub('false', 'False', json)
        json = re.sub('true', 'True', json)
        json = re.sub(r'\\/', '/', json)
        return eval(json)

    def failUnlessIsBarJSON(self, res):
        data = self.worlds_cheapest_json_decoder(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "filenode")
        self.failUnless(isinstance(data[1], dict))
        self.failUnlessEqual(data[1]["mutable"], False)
        self.failUnlessEqual(data[1]["size"], 123)
        self.failUnlessEqual(data[1]["uri"], self._bar_txt_uri)

    def failUnlessIsFooJSON(self, res):
        data = self.worlds_cheapest_json_decoder(res)
        self.failUnless(isinstance(data, list))
        self.failUnlessEqual(data[0], "dirnode")
        self.failUnless(isinstance(data[1], dict))
        self.failUnlessEqual(data[1]["mutable"], True)
        self.failUnlessEqual(data[1]["uri"], self._foo_uri)
        kidnames = sorted(data[1]["children"].keys())
        self.failUnlessEqual(kidnames,
                             ["bar.txt", "blockingfile", "empty", "sub"])
        kids = data[1]["children"]
        self.failUnlessEqual(kids["sub"][0], "dirnode")
        self.failUnlessEqual(kids["bar.txt"][0], "filenode")
        self.failUnlessEqual(kids["bar.txt"][1]["size"], 123)
        self.failUnlessEqual(kids["bar.txt"][1]["uri"], self._bar_txt_uri)

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
            form.append(value)
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

class Web(WebMixin):
    def test_create(self):
        pass

    def test_welcome(self):
        d = self.GET("/")
        def _check(res):
            self.failUnless('Welcome To AllMyData' in res)
            self.failUnless('Tahoe' in res)
            self.failUnless('To view the global shared filestore' in res)
            self.failUnless('To view your personal private non-shared' in res)
        d.addCallback(_check)
        return d

    def test_GET_FILEURL(self):
        d = self.GET("/vdrive/global/foo/bar.txt")
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_FILEURL_download(self):
        d = self.GET("/vdrive/global/foo/bar.txt?t=download")
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_FILEURL_missing(self):
        d = self.GET("/vdrive/global/foo/missing")
        d.addBoth(self.should404, "test_GET_FILEURL_missing")
        return d

    def test_PUT_NEWFILEURL(self):
        d = self.PUT("/vdrive/global/foo/new.txt", self.NEWFILE_CONTENTS)
        def _check(res):
            self.failUnless("new.txt" in self._foo_node.children)
            new_uri = self._foo_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.NEWFILE_CONTENTS)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWFILEURL_mkdirs(self):
        d = self.PUT("/vdrive/global/foo/newdir/new.txt", self.NEWFILE_CONTENTS)
        def _check(res):
            self.failIf("new.txt" in self._foo_node.children)
            self.failUnless("newdir" in self._foo_node.children)
            newdir_uri = self._foo_node.children["newdir"]
            newdir_node = self.nodes[newdir_uri]
            self.failUnless("new.txt" in newdir_node.children)
            new_uri = newdir_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.NEWFILE_CONTENTS)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWFILEURL_blocked(self):
        d = self.PUT("/vdrive/global/foo/blockingfile/new.txt",
                     self.NEWFILE_CONTENTS)
        d.addBoth(self.shouldFail, error.Error, "PUT_NEWFILEURL_blocked",
                  "400 Bad Request",
                  "cannot create directory because there is a file in the way")
        return d

    def test_DELETE_FILEURL(self):
        d = self.DELETE("/vdrive/global/foo/bar.txt")
        def _check(res):
            self.failIf("bar.txt" in self._foo_node.children)
        d.addCallback(_check)
        return d

    def test_DELETE_FILEURL_missing(self):
        d = self.DELETE("/vdrive/global/foo/missing")
        d.addBoth(self.should404, "test_DELETE_FILEURL_missing")
        return d

    def test_DELETE_FILEURL_missing2(self):
        d = self.DELETE("/vdrive/global/missing/missing")
        d.addBoth(self.should404, "test_DELETE_FILEURL_missing2")
        return d

    def test_GET_FILEURL_json(self):
        # twisted.web.http.parse_qs ignores any query args without an '=', so
        # I can't do "GET /path?json", I have to do "GET /path/t=json"
        # instead. This may make it tricky to emulate the S3 interface
        # completely.
        d = self.GET("/vdrive/global/foo/bar.txt?t=json")
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_GET_FILEURL_json_missing(self):
        d = self.GET("/vdrive/global/foo/missing?json")
        d.addBoth(self.should404, "test_GET_FILEURL_json_missing")
        return d

    def test_GET_FILEURL_localfile(self):
        localfile = os.path.abspath("web/GET_FILEURL_localfile")
        fileutil.make_dirs("web")
        d = self.GET("/vdrive/global/foo/bar.txt?t=download&localfile=%s"
                     % localfile)
        def _done(res):
            self.failUnless(os.path.exists(localfile))
            data = open(localfile, "rb").read()
            self.failUnlessEqual(data, self.BAR_CONTENTS)
        d.addCallback(_done)
        return d

    def test_GET_FILEURL_localfile_nonlocal(self):
        # TODO: somehow pretend that we aren't local, and verify that the
        # server refuses to write to local files, probably by changing the
        # server's idea of what counts as "local".
        old_LOCALHOST = webish.LOCALHOST
        webish.LOCALHOST = "127.0.0.2"
        localfile = os.path.abspath("web/GET_FILEURL_localfile_nonlocal")
        fileutil.make_dirs("web")
        d = self.GET("/vdrive/global/foo/bar.txt?t=download&localfile=%s"
                     % localfile)
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
        d = self.GET("/vdrive/global/foo/bar.txt?t=download&localfile=%s"
                     % localfile)
        d.addBoth(self.shouldFail, error.Error, "localfile non-absolute",
                  "403 Forbidden",
                  "localfile= or localdir= requires an absolute path")
        def _check(res):
            self.failIf(os.path.exists(localfile))
        d.addCallback(_check)
        return d

    def test_PUT_NEWFILEURL_localfile(self):
        localfile = os.path.abspath("web/PUT_NEWFILEURL_localfile")
        fileutil.make_dirs("web")
        f = open(localfile, "wb")
        f.write(self.NEWFILE_CONTENTS)
        f.close()
        d = self.PUT("/vdrive/global/foo/new.txt?t=upload&localfile=%s" %
                     localfile, "")
        def _check(res):
            self.failUnless("new.txt" in self._foo_node.children)
            new_uri = self._foo_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.NEWFILE_CONTENTS)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWFILEURL_localfile_mkdirs(self):
        localfile = os.path.abspath("web/PUT_NEWFILEURL_localfile_mkdirs")
        fileutil.make_dirs("web")
        f = open(localfile, "wb")
        f.write(self.NEWFILE_CONTENTS)
        f.close()
        d = self.PUT("/vdrive/global/foo/newdir/new.txt?t=upload&localfile=%s"
                     % localfile, "")
        def _check(res):
            self.failIf("new.txt" in self._foo_node.children)
            self.failUnless("newdir" in self._foo_node.children)
            newdir_uri = self._foo_node.children["newdir"]
            newdir_node = self.nodes[newdir_uri]
            self.failUnless("new.txt" in newdir_node.children)
            new_uri = newdir_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.NEWFILE_CONTENTS)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_GET_FILEURL_uri(self):
        d = self.GET("/vdrive/global/foo/bar.txt?t=uri")
        def _check(res):
            self.failUnlessEqual(res, self._bar_txt_uri)
        d.addCallback(_check)
        d.addCallback(lambda res:
                      self.GET("/vdrive/global/foo/bar.txt?t=readonly-uri"))
        def _check2(res):
            # for now, for files, uris and readonly-uris are the same
            self.failUnlessEqual(res, self._bar_txt_uri)
        d.addCallback(_check2)
        return d

    def test_GET_FILEURL_uri_missing(self):
        d = self.GET("/vdrive/global/foo/missing?t=uri")
        d.addBoth(self.should404, "test_GET_FILEURL_uri_missing")
        return d

    def test_GET_DIRURL(self):
        # the addSlash means we get a redirect here
        d = self.GET("/vdrive/global/foo", followRedirect=True)
        def _check(res):
            self.failUnless(re.search(r'<td><a href="bar.txt">bar.txt</a></td>'
                                      '\s+<td>FILE</td>'
                                      '\s+<td>123</td>'
                                      , res))
            self.failUnless(re.search(r'<td><a href="sub">sub</a></td>'
                                      '\s+<td>DIR</td>', res))
        d.addCallback(_check)

        # look at a directory which is readonly
        d.addCallback(lambda res:
                      self.GET("/vdrive/global/readonly", followRedirect=True))
        def _check2(res):
            self.failUnless("(readonly)" in res)
            self.failIf("Upload a file" in res)
        d.addCallback(_check2)

        # and at a directory that contains a readonly directory
        d.addCallback(lambda res:
                      self.GET("/vdrive/global", followRedirect=True))
        def _check3(res):
            self.failUnless(re.search(r'<td><a href="readonly">readonly</a>'
                                      '</td>\s+<td>DIR-RO</td>', res))
        d.addCallback(_check3)

        # and take a quick peek at the private vdrive
        d.addCallback(lambda res:
                      self.GET("/vdrive/private", followRedirect=True))
        def _check4(res):
            pass
        d.addCallback(_check4)

        return d

    def test_GET_DIRURL_json(self):
        d = self.GET("/vdrive/global/foo?t=json")
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    def test_GET_DIRURL_manifest(self):
        d = self.GET("/vdrive/global/foo?t=manifest", followRedirect=True)
        def _got(manifest):
            self.failUnless("Refresh Capabilities" in manifest)
        d.addCallback(_got)
        return d

    def test_GET_DIRURL_uri(self):
        d = self.GET("/vdrive/global/foo?t=uri")
        def _check(res):
            self.failUnlessEqual(res, self._foo_uri)
        d.addCallback(_check)
        return d

    def test_GET_DIRURL_readonly_uri(self):
        d = self.GET("/vdrive/global/foo?t=readonly-uri")
        def _check(res):
            self.failUnlessEqual(res, self._foo_readonly_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWDIRURL(self):
        d = self.PUT("/vdrive/global/foo/newdir?t=mkdir", "")
        def _check(res):
            self.failUnless("newdir" in self._foo_node.children)
            newdir_uri = self._foo_node.children["newdir"]
            newdir_node = self.nodes[newdir_uri]
            self.failIf(newdir_node.children)
        d.addCallback(_check)
        return d

    def test_PUT_NEWDIRURL_mkdirs(self):
        d = self.PUT("/vdrive/global/foo/subdir/newdir?t=mkdir", "")
        def _check(res):
            self.failIf("newdir" in self._foo_node.children)
            self.failUnless("subdir" in self._foo_node.children)
            subdir_node = self.nodes[self._foo_node.children["subdir"]]
            self.failUnless("newdir" in subdir_node.children)
            newdir_node = self.nodes[subdir_node.children["newdir"]]
            self.failIf(newdir_node.children)
        d.addCallback(_check)
        return d

    def test_DELETE_DIRURL(self):
        d = self.DELETE("/vdrive/global/foo")
        def _check(res):
            self.failIf("foo" in self.public_root.children)
        d.addCallback(_check)
        return d

    def test_DELETE_DIRURL_missing(self):
        d = self.DELETE("/vdrive/global/foo/missing")
        d.addBoth(self.should404, "test_DELETE_DIRURL_missing")
        def _check(res):
            self.failUnless("foo" in self.public_root.children)
        d.addCallback(_check)
        return d

    def test_DELETE_DIRURL_missing2(self):
        d = self.DELETE("/vdrive/global/missing")
        d.addBoth(self.should404, "test_DELETE_DIRURL_missing2")
        return d

    def test_walker(self):
        out = []
        def _visitor(path, node):
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
                                  ('readonly',),
                                  ('readonly', 'nor'),
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
        localdir = os.path.abspath("web/GET_DIRURL_localdir")
        fileutil.make_dirs("web")
        d = self.GET("/vdrive/global/foo?t=download&localdir=%s" % localdir)
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

    def test_GET_DIRURL_localdir_nonabsolute(self):
        localdir = "web/nonabsolute/dirpath"
        fileutil.make_dirs("web/nonabsolute")
        d = self.GET("/vdrive/global/foo?t=download&localdir=%s" % localdir)
        d.addBoth(self.shouldFail, error.Error, "localdir non-absolute",
                  "403 Forbidden",
                  "localfile= or localdir= requires an absolute path")
        def _check(res):
            self.failIf(os.path.exists(localdir))
        d.addCallback(_check)
        return d

    def touch(self, localdir, filename):
        path = os.path.join(localdir, filename)
        f = open(path, "w")
        f.write("contents of %s\n" % filename)
        f.close()

    def walk_mynodes(self, node, path=()):
        yield path, node
        if interfaces.IDirectoryNode.providedBy(node):
            for name in sorted(node.children.keys()):
                child_uri = node.children[name]
                childnode = self.nodes[child_uri]
                childpath = path + (name,)
                for xpath,xnode in self.walk_mynodes(childnode, childpath):
                    yield xpath, xnode

    def dump_root(self):
        print "NODEWALK"
        for path,node in self.walk_mynodes(self.public_root):
            print path

    def test_PUT_NEWDIRURL_localdir(self):
        localdir = os.path.abspath("web/PUT_NEWDIRURL_localdir")
        # create some files there
        fileutil.make_dirs(os.path.join(localdir, "one"))
        fileutil.make_dirs(os.path.join(localdir, "one/sub"))
        fileutil.make_dirs(os.path.join(localdir, "two"))
        fileutil.make_dirs(os.path.join(localdir, "three"))
        self.touch(localdir, "three/foo.txt")
        self.touch(localdir, "three/bar.txt")
        self.touch(localdir, "zap.zip")

        d = self.PUT("/vdrive/global/newdir?t=upload&localdir=%s"
                     % localdir, "")
        def _check(res):
            self.failUnless("newdir" in self.public_root.children)
            newnode = self.nodes[self.public_root.children["newdir"]]
            self.failUnlessEqual(sorted(newnode.children.keys()),
                                 sorted(["one", "two", "three", "zap.zip"]))
            onenode = self.nodes[newnode.children["one"]]
            self.failUnlessEqual(sorted(onenode.children.keys()),
                                 sorted(["sub"]))
            threenode = self.nodes[newnode.children["three"]]
            self.failUnlessEqual(sorted(threenode.children.keys()),
                                 sorted(["foo.txt", "bar.txt"]))
            barnode = self.nodes[threenode.children["bar.txt"]]
            contents = self.files[barnode.get_uri()]
            self.failUnlessEqual(contents, "contents of three/bar.txt\n")
        d.addCallback(_check)
        return d

    def test_PUT_NEWDIRURL_localdir_mkdirs(self):
        localdir = os.path.abspath("web/PUT_NEWDIRURL_localdir_mkdirs")
        # create some files there
        fileutil.make_dirs(os.path.join(localdir, "one"))
        fileutil.make_dirs(os.path.join(localdir, "one/sub"))
        fileutil.make_dirs(os.path.join(localdir, "two"))
        fileutil.make_dirs(os.path.join(localdir, "three"))
        self.touch(localdir, "three/foo.txt")
        self.touch(localdir, "three/bar.txt")
        self.touch(localdir, "zap.zip")

        d = self.PUT("/vdrive/global/foo/subdir/newdir?t=upload&localdir=%s"
                     % localdir,
                     "")
        def _check(res):
            self.failUnless("subdir" in self._foo_node.children)
            subnode = self.nodes[self._foo_node.children["subdir"]]
            self.failUnless("newdir" in subnode.children)
            newnode = self.nodes[subnode.children["newdir"]]
            self.failUnlessEqual(sorted(newnode.children.keys()),
                                 sorted(["one", "two", "three", "zap.zip"]))
            onenode = self.nodes[newnode.children["one"]]
            self.failUnlessEqual(sorted(onenode.children.keys()),
                                 sorted(["sub"]))
            threenode = self.nodes[newnode.children["three"]]
            self.failUnlessEqual(sorted(threenode.children.keys()),
                                 sorted(["foo.txt", "bar.txt"]))
            barnode = self.nodes[threenode.children["bar.txt"]]
            contents = self.files[barnode.get_uri()]
            self.failUnlessEqual(contents, "contents of three/bar.txt\n")
        d.addCallback(_check)
        return d

    def test_POST_upload(self):
        d = self.POST("/vdrive/global/foo", t="upload",
                      file=("new.txt", self.NEWFILE_CONTENTS))
        def _check(res):
            self.failUnless("new.txt" in self._foo_node.children)
            new_uri = self._foo_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.NEWFILE_CONTENTS)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_POST_upload_named(self):
        d = self.POST("/vdrive/global/foo", t="upload",
                      name="new.txt", file=self.NEWFILE_CONTENTS)
        def _check(res):
            self.failUnless("new.txt" in self._foo_node.children)
            new_uri = self._foo_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.NEWFILE_CONTENTS)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_POST_upload_named_badfilename(self):
        d = self.POST("/vdrive/global/foo", t="upload",
                      name="slashes/are/bad.txt", file=self.NEWFILE_CONTENTS)
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_upload_named_badfilename",
                  "400 Bad Request",
                  "name= may not contain a slash",
                  )
        def _check(res):
            # make sure that nothing was added
            kids = sorted(self._foo_node.children.keys())
            self.failUnlessEqual(sorted(["bar.txt", "blockingfile",
                                         "empty", "sub"]),
                                 kids)
        d.addCallback(_check)
        return d

    def test_POST_mkdir(self): # return value?
        d = self.POST("/vdrive/global/foo", t="mkdir", name="newdir")
        def _check(res):
            self.failUnless("newdir" in self._foo_node.children)
            newdir_uri = self._foo_node.children["newdir"]
            newdir_node = self.nodes[newdir_uri]
            self.failIf(newdir_node.children)
        d.addCallback(_check)
        return d

    def test_POST_mkdir_whendone(self):
        d = self.POST("/vdrive/global/foo?when_done=/THERE",
                      t="mkdir", name="newdir")
        d.addBoth(self.shouldRedirect, "/THERE")
        def _check(res):
            self.failUnless("newdir" in self._foo_node.children)
            newdir_uri = self._foo_node.children["newdir"]
            newdir_node = self.nodes[newdir_uri]
            self.failIf(newdir_node.children)
        d.addCallback(_check)
        return d

    def test_POST_put_uri(self):
        newuri = self.makefile(8)
        contents = self.files[newuri]
        d = self.POST("/vdrive/global/foo", t="uri", name="new.txt", uri=newuri)
        def _check(res):
            self.failUnless("new.txt" in self._foo_node.children)
            new_uri = self._foo_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, contents)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_POST_delete(self):
        d = self.POST("/vdrive/global/foo", t="delete", name="bar.txt")
        def _check(res):
            self.failIf("bar.txt" in self._foo_node.children)
        d.addCallback(_check)
        return d

    def test_POST_rename_file(self):
        d = self.POST("/vdrive/global/foo", t="rename",
                      from_name="bar.txt", to_name='wibble.txt')
        def _check(res):
            self.failIf("bar.txt" in self._foo_node.children)
            self.failUnless("wibble.txt" in self._foo_node.children)
        d.addCallback(_check)
        d.addCallback(lambda res: self.GET("/vdrive/global/foo/wibble.txt"))
        d.addCallback(self.failUnlessIsBarDotTxt)
        d.addCallback(lambda res: self.GET("/vdrive/global/foo/wibble.txt?t=json"))
        d.addCallback(self.failUnlessIsBarJSON)
        return d

    def test_POST_rename_file_slash_fail(self):
        d = self.POST("/vdrive/global/foo", t="rename",
                      from_name="bar.txt", to_name='kirk/spock.txt')
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_rename_file_slash_fail",
                  "400 Bad Request",
                  "to_name= may not contain a slash",
                  )
        def _check1(res):
            self.failUnless("bar.txt" in self._foo_node.children)
        d.addCallback(_check1)
        d.addCallback(lambda res: self.POST("/vdrive/global", t="rename",
                      from_name="foo/bar.txt", to_name='george.txt'))
        d.addBoth(self.shouldFail, error.Error,
                  "test_POST_rename_file_slash_fail",
                  "400 Bad Request",
                  "from_name= may not contain a slash",
                  )
        def _check2(res):
            self.failUnless("foo" in self.public_root.children)
            self.failIf("george.txt" in self.public_root.children)
            self.failUnless("bar.txt" in self._foo_node.children)
        d.addCallback(_check2)
        d.addCallback(lambda res: self.GET("/vdrive/global/foo?t=json"))
        d.addCallback(self.failUnlessIsFooJSON)
        return d

    def test_POST_rename_dir(self):
        d = self.POST("/vdrive/global", t="rename",
                      from_name="foo", to_name='plunk')
        def _check(res):
            self.failIf("foo" in self.public_root.children)
            self.failUnless("plunk" in self.public_root.children)
        d.addCallback(_check)
        d.addCallback(lambda res: self.GET("/vdrive/global/plunk?t=json"))
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
        d = self.GET("/vdrive/global/foo?t=rename-form&name=bar.txt",
                     followRedirect=True) # XXX [ ] todo: figure out why '.../foo' doesn't work
        def _check(res):
            self.failUnless(re.search(r'name="when_done" value=".*vdrive/global/foo/', res))
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
        new_uri = self.makefile(8)
        d = self.PUT("/vdrive/global/foo/new.txt?t=uri", new_uri)
        def _check(res):
            self.failUnless("new.txt" in self._foo_node.children)
            new_uri = self._foo_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.files[new_uri])
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_XMLRPC(self):
        raise unittest.SkipTest("not yet")
        pass

