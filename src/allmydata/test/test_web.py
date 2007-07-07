
import re, os.path
from zope.interface import implements
from twisted.application import service
from twisted.trial import unittest
from twisted.internet import defer
from twisted.web import client, error
from twisted.python import failure
from allmydata import webish, interfaces, dirnode, uri
from allmydata.encode import NotEnoughPeersError
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
        print "DOWNLOADING", uri
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

class MyUploader(service.Service):
    implements(interfaces.IUploader)
    name = "uploader"
    def __init__(self, files):
        self.files = files

    def upload(self, uploadable):
        f = uploadable.get_filehandle()
        data = f.read()
        uri = str(uri_counter.next())
        self.files[uri] = data
        uploadable.close_filehandle(f)
        return defer.succeed(uri)

class MyDirectoryNode(dirnode.MutableDirectoryNode):

    def __init__(self, nodes, uri=None):
        self._nodes = nodes
        if uri is None:
            uri = str(uri_counter.next())
        self._uri = str(uri)
        self._nodes[self._uri] = self
        self.children = {}
        self._mutable = True

    def get_immutable_uri(self):
        return self.get_uri() + "RO"

    def get(self, name):
        def _try():
            uri = self.children[name]
            if uri not in self._nodes:
                raise IndexError("this isn't supposed to happen")
            return self._nodes[uri]
        return defer.maybeDeferred(_try)

    def set_uri(self, name, child_uri):
        self.children[name] = child_uri
        return defer.succeed(None)

    def create_empty_directory(self, name):
        node = MyDirectoryNode(self._nodes)
        self.children[name] = node.get_uri()
        return defer.succeed(node)

    def list(self):
        kids = dict([(name, self._nodes[uri])
                     for name,uri in self.children.iteritems()])
        return defer.succeed(kids)

class MyFileNode(dirnode.FileNode):
    pass


class MyVirtualDrive(service.Service):
    name = "vdrive"
    public_root = None
    private_root = None
    def have_public_root(self):
        return bool(self.public_root)
    def have_private_root(self):
        return bool(self.private_root)
    def get_public_root(self):
        return defer.succeed(self.public_root)
    def get_private_root(self):
        return defer.succeed(self.private_root)

class Web(unittest.TestCase):
    def setUp(self):
        self.s = MyClient()
        self.s.startService()
        s = webish.WebishServer("0")
        s.setServiceParent(self.s)
        port = s.listener._port.getHost().port
        self.webish_url = "http://localhost:%d" % port

        v = MyVirtualDrive()
        v.setServiceParent(self.s)

        self.nodes = {} # maps URI to node
        self.files = {} # maps file URI to contents
        dl = MyDownloader(self.files)
        dl.setServiceParent(self.s)
        ul = MyUploader(self.files)
        ul.setServiceParent(self.s)

        v.public_root = MyDirectoryNode(self.nodes)
        v.private_root = MyDirectoryNode(self.nodes)
        foo = MyDirectoryNode(self.nodes)
        self._foo_node = foo
        self._foo_uri = foo.get_uri()
        self._foo_readonly_uri = foo.get_immutable_uri()
        v.public_root.children["foo"] = foo.get_uri()

        self.BAR_CONTENTS = "bar.txt contents"

        bar_uri = uri.pack_uri("SI"+"0"*30,
                               "K"+"0"*15,
                               "EH"+"0"*30,
                               25, 100, 123)
        bar_txt = MyFileNode(bar_uri, self.s)
        self._bar_txt_uri = bar_txt.get_uri()
        self.nodes[bar_uri] = bar_txt
        self.files[bar_txt.get_uri()] = self.BAR_CONTENTS
        foo.children["bar.txt"] = bar_txt.get_uri()

        foo.children["sub"] = MyDirectoryNode(self.nodes).get_uri()

        blocking_uri = uri.pack_uri("SI"+"1"*30,
                                    "K"+"1"*15,
                                    "EH"+"1"*30,
                                    25, 100, 124)
        blocking_file = MyFileNode(blocking_uri, self.s)
        self.nodes[blocking_uri] = blocking_file
        self.files[blocking_uri] = "blocking contents"
        foo.children["blockingfile"] = blocking_file.get_uri()

        # public/
        # public/foo/
        # public/foo/bar.txt
        # public/foo/sub/
        # public/foo/blockingfile
        self.NEWFILE_CONTENTS = "newfile contents\n"

    def tearDown(self):
        return self.s.stopService()

    def failUnlessIsBarDotTxt(self, res):
        self.failUnlessEqual(res, self.BAR_CONTENTS)

    def GET(self, urlpath):
        url = self.webish_url + urlpath
        return client.getPage(url, method="GET")

    def PUT(self, urlpath, data):
        url = self.webish_url + urlpath
        return client.getPage(url, method="PUT", postdata=data)

    def DELETE(self, urlpath):
        url = self.webish_url + urlpath
        return client.getPage(url, method="DELETE")

    def POST(self, urlpath, data):
        url = self.webish_url + urlpath
        return client.getPage(url, method="POST", postdata=data)

    def shouldFail(self, res, expected_failure, which, substring=None):
        print "SHOULDFAIL", res
        if isinstance(res, failure.Failure):
            res.trap(expected_failure)
            if substring:
                self.failUnless(substring in str(res),
                                "substring '%s' not in '%s'"
                                % (substring, str(res)))
        else:
            self.fail("%s was supposed to raise %s, not get '%s'" %
                      (which, expected_failure, res))

    def should404(self, res, which):
        if isinstance(res, failure.Failure):
            res.trap(error.Error)
            self.failUnlessEqual(res.value.status, "404")
        else:
            self.fail("%s was supposed to raise %s, not get '%s'" %
                      (which, expected_failure, res))

    def test_create(self): # YES
        pass

    def test_welcome(self): # YES
        d = self.GET("")
        return d

    def test_GET_FILEURL(self): # YES
        d = self.GET("/vdrive/global/foo/bar.txt")
        d.addCallback(self.failUnlessIsBarDotTxt)
        return d

    def test_GET_FILEURL_missing(self): # YES
        d = self.GET("/vdrive/global/foo/missing")
        def _oops(f):
            print f
            print dir(f)
            print f.value
            print dir(f.value)
            print f.value.args
            print f.value.response
            print f.value.status
            return f
        #d.addBoth(_oops)
        d.addBoth(self.should404, "test_GET_FILEURL_missing")
        return d

    def test_PUT_NEWFILEURL(self): # YES
        d = self.PUT("/vdrive/global/foo/new.txt", self.NEWFILE_CONTENTS)
        def _check(res):
            self.failUnless("new.txt" in self._foo_node.children)
            new_uri = self._foo_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.NEWFILE_CONTENTS)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWFILEURL_mkdirs(self): # YES
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

    def test_PUT_NEWFILEURL_blocked(self): # YES
        d = self.PUT("/vdrive/global/foo/blockingfile/new.txt",
                     self.NEWFILE_CONTENTS)
        d.addBoth(self.shouldFail, error.Error, "PUT_NEWFILEURL_blocked",
                  "403 Forbidden")
        return d

    def test_DELETE_FILEURL(self):
        d = self.DELETE("/vdrive/global/foo/bar.txt")
        return d

    def test_DELETE_FILEURL_missing(self):
        d = self.DELETE("/vdrive/global/foo/missing")
        return d

    def test_GET_FILEURL_json(self): # YES
        # twisted.web.http.parse_qs ignores any query args without an '=', so
        # I can't do "GET /path?json", I have to do "GET /path/t=json"
        # instead. This may make it tricky to emulate the S3 interface
        # completely.
        d = self.GET("/vdrive/global/foo/bar.txt?t=json")
        def _got(json):
            # TODO
            self.failUnless("JSON" in json, json)
        d.addCallback(_got)
        return d

    def test_GET_FILEURL_json_missing(self): # YES
        d = self.GET("/vdrive/global/foo/missing?json")
        d.addBoth(self.should404, "test_GET_FILEURL_json_missing")
        return d

    def test_GET_FILEURL_localfile(self): # YES
        localfile = os.path.abspath("web/GET_FILEURL_localfile")
        os.makedirs("web")
        d = self.GET("/vdrive/global/foo/bar.txt?localfile=%s" % localfile)
        def _done(res):
            self.failUnless(os.path.exists(localfile))
            data = open(localfile, "rb").read()
            self.failUnlessEqual(data, self.BAR_CONTENTS)
        d.addCallback(_done)
        return d

    def test_GET_FILEURL_localfile_nonlocal(self): # YES
        # TODO: somehow pretend that we aren't local, and verify that the
        # server refuses to write to local files, probably by changing the
        # server's idea of what counts as "local".
        old_LOCALHOST = webish.LOCALHOST
        webish.LOCALHOST = "127.0.0.2"
        localfile = os.path.abspath("web/GET_FILEURL_localfile_nonlocal")
        os.makedirs("web")
        d = self.GET("/vdrive/global/foo/bar.txt?localfile=%s" % localfile)
        d.addBoth(self.shouldFail, error.Error, "localfile non-local",
                  "403 Forbidden")
        def _check(res):
            self.failIf(os.path.exists(localfile))
        d.addCallback(_check)
        def _reset(res):
            print "RESETTING", res
            webish.LOCALHOST = old_LOCALHOST
            return res
        d.addBoth(_reset)
        return d

    def test_PUT_NEWFILEURL_localfile(self): # YES
        localfile = os.path.abspath("web/PUT_NEWFILEURL_localfile")
        os.makedirs("web")
        f = open(localfile, "wb")
        f.write(self.NEWFILE_CONTENTS)
        f.close()
        d = self.PUT("/vdrive/global/foo/new.txt?localfile=%s" % localfile, "")
        def _check(res):
            self.failUnless("new.txt" in self._foo_node.children)
            new_uri = self._foo_node.children["new.txt"]
            new_contents = self.files[new_uri]
            self.failUnlessEqual(new_contents, self.NEWFILE_CONTENTS)
            self.failUnlessEqual(res.strip(), new_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWFILEURL_localfile_mkdirs(self): # YES
        localfile = os.path.abspath("web/PUT_NEWFILEURL_localfile_mkdirs")
        os.makedirs("web")
        f = open(localfile, "wb")
        f.write(self.NEWFILE_CONTENTS)
        f.close()
        d = self.PUT("/vdrive/global/foo/newdir/new.txt?localfile=%s" %
                     localfile, "")
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

    def test_GET_FILEURL_uri(self): # YES
        d = self.GET("/vdrive/global/foo/bar.txt?t=uri")
        def _check(res):
            self.failUnlessEqual(res, self._bar_txt_uri)
        d.addCallback(_check)
        return d

    def test_GET_FILEURL_uri_missing(self): # YES
        d = self.GET("/vdrive/global/foo/missing?t=uri")
        d.addBoth(self.should404, "test_GET_FILEURL_uri_missing")
        return d

    def test_GET_DIRURL(self): # YES
        d = self.GET("/vdrive/global/foo")
        def _check(res):
            self.failUnless(re.search(r'<td><a href="bar.txt">bar.txt</a></td>'
                                      '\s+<td>FILE</td>'
                                      '\s+<td>123</td>'
                                      , res))
            self.failUnless(re.search(r'<td><a href="sub">sub</a></td>'
                                      '\s+<td>DIR</td>', res))
        d.addCallback(_check)
        return d

    def test_GET_DIRURL_json(self): # YES
        d = self.GET("/vdrive/global/foo?t=json")
        def _got(json):
            # TODO
            self.failUnless("JSON" in json, json)
        d.addCallback(_got)
        return d

    def test_GET_DIRURL_uri(self): # YES
        d = self.GET("/vdrive/global/foo?t=uri")
        def _check(res):
            self.failUnlessEqual(res, self._foo_uri)
        d.addCallback(_check)
        return d

    def test_GET_DIRURL_readonly_uri(self): # YES
        d = self.GET("/vdrive/global/foo?t=readonly-uri")
        def _check(res):
            self.failUnlessEqual(res, self._foo_readonly_uri)
        d.addCallback(_check)
        return d

    def test_PUT_NEWDIRURL(self): # YES
        d = self.PUT("/vdrive/global/foo/newdir?t=mkdir", "")
        def _check(res):
            self.failUnless("newdir" in self._foo_node.children)
            newdir_uri = self._foo_node.children["newdir"]
            newdir_node = self.nodes[newdir_uri]
            self.failIf(newdir_node.children)
        d.addCallback(_check)
        return d

    def test_PUT_NEWDIRURL_mkdirs(self): # YES
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
        return d

    def test_DELETE_DIRURL_missing(self):
        d = self.DELETE("/vdrive/global/missing")
        return d

    def test_GET_DIRURL_localdir(self):
        localdir = os.path.abspath("web/GET_DIRURL_localdir")
        os.makedirs("web")
        d = self.GET("/vdrive/global/foo?localdir=%s" % localdir)
        return d

    def test_PUT_NEWDIRURL_localdir(self):
        localdir = os.path.abspath("web/PUT_NEWDIRURL_localdir")
        os.makedirs("web")
        # create some files there
        d = self.GET("/vdrive/global/foo/newdir?localdir=%s" % localdir)
        return d

    def test_PUT_NEWDIRURL_localdir_mkdirs(self):
        localdir = os.path.abspath("web/PUT_NEWDIRURL_localdir_mkdirs")
        os.makedirs("web")
        # create some files there
        d = self.GET("/vdrive/global/foo/subdir/newdir?localdir=%s" % localdir)
        return d

    def test_POST_upload(self):
        form = "TODO"
        d = self.POST("/vdrive/global/foo", form)
        return d

    def test_POST_mkdir(self):
        form = "TODO"
        d = self.POST("/vdrive/global/foo", form)
        return d

    def test_POST_put_uri(self):
        form = "TODO"
        d = self.POST("/vdrive/global/foo", form)
        return d

    def test_POST_delete(self):
        form = "TODO, bar.txt"
        d = self.POST("/vdrive/global/foo", form)
        return d

    def test_URI_GET(self):
        d = self.GET("/uri/%s/bar.txt" % foo_uri)
        return d

    def test_PUT_NEWFILEURL_uri(self):
        d = self.PUT("/vdrive/global/foo/new.txt?uri", new_uri)
        return d

    def test_XMLRPC(self):
        pass



"""
 # GET /   (welcome)
 # GET FILEURL
# PUT NEWFILEURL
# DELETE FILEURL
 # GET FILEURL?t=json
# GET FILEURL?localfile=$FILENAME
# PUT NEWFILEURL?localfile=$FILENAME
# GET FILEURL?t=uri
# GET DIRURL
# GET DIRURL?t=json
# GET DIRURL?t=uri
# GET DIRURL?t=readonly-uri
# PUT NEWDIRURL?t=mkdir
# DELETE DIRURL
# GET DIRURL?localdir=$DIRNAME
# PUT NEWDIRURL?localdir=$DIRNAME
# POST DIRURL?t=upload-form
# POST DIRURL?t=mkdir-form
# POST DIRURL?t=put-uri-form
# POST DIRURL?t=delete-form
# GET .../url/$URI
#  and a few others
# PUT NEWFILEURL?t=uri
# /xmlrpc
"""
