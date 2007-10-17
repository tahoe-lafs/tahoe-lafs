
from base64 import b32encode
import os, sys
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.internet import threads # CLI tests use deferToThread
from twisted.application import service
from allmydata import client, uri, download, upload
from allmydata.introducer_and_vdrive import IntroducerAndVdrive
from allmydata.util import fileutil, testutil, deferredutil
from allmydata.scripts import runner
from allmydata.interfaces import IDirectoryNode, IFileNode, IFileURI
from allmydata.dirnode import NotMutableError
from foolscap.eventual import flushEventualQueue
from twisted.python import log
from twisted.python.failure import Failure
from twisted.web.client import getPage
from twisted.web.error import Error

def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

LARGE_DATA = """
This is some data to publish to the virtual drive, which needs to be large
enough to not fit inside a LIT uri.
"""

class SystemTest(testutil.SignalMixin, unittest.TestCase):

    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()
    def tearDown(self):
        log.msg("shutting down SystemTest services")
        d = self.sparent.stopService()
        d.addBoth(flush_but_dont_ignore)
        return d

    def getdir(self, subdir):
        return os.path.join(self.basedir, subdir)

    def add_service(self, s):
        s.setServiceParent(self.sparent)
        return s

    def set_up_nodes(self, NUMCLIENTS=5):
        self.numclients = NUMCLIENTS
        iv_dir = self.getdir("introducer_and_vdrive")
        if not os.path.isdir(iv_dir):
            fileutil.make_dirs(iv_dir)
        iv = IntroducerAndVdrive(basedir=iv_dir)
        self.introducer_and_vdrive = self.add_service(iv)
        d = self.introducer_and_vdrive.when_tub_ready()
        d.addCallback(self._set_up_nodes_2)
        return d

    def _set_up_nodes_2(self, res):
        q = self.introducer_and_vdrive
        self.introducer_furl = q.urls["introducer"]
        self.vdrive_furl = q.urls["vdrive"]
        self.clients = []
        for i in range(self.numclients):
            basedir = self.getdir("client%d" % i)
            if not os.path.isdir(basedir):
                fileutil.make_dirs(basedir)
            if i == 0:
                open(os.path.join(basedir, "webport"), "w").write("tcp:0:interface=127.0.0.1")
            open(os.path.join(basedir, "introducer.furl"), "w").write(self.introducer_furl)
            open(os.path.join(basedir, "vdrive.furl"), "w").write(self.vdrive_furl)
            c = self.add_service(client.Client(basedir=basedir))
            self.clients.append(c)
        log.msg("STARTING")
        d = self.wait_for_connections()
        def _connected(res):
            log.msg("CONNECTED")
            # now find out where the web port was
            l = self.clients[0].getServiceNamed("webish").listener
            port = l._port.getHost().port
            self.webish_url = "http://localhost:%d/" % port
        d.addCallback(_connected)
        return d

    def add_extra_node(self, client_num):
        # this node is *not* parented to our self.sparent, so we can shut it
        # down separately from the rest, to exercise the connection-lost code
        basedir = self.getdir("client%d" % client_num)
        if not os.path.isdir(basedir):
            fileutil.make_dirs(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write(self.introducer_furl)
        open(os.path.join(basedir, "vdrive.furl"), "w").write(self.vdrive_furl)

        c = client.Client(basedir=basedir)
        self.clients.append(c)
        self.numclients += 1
        c.startService()
        d = self.wait_for_connections()
        d.addCallback(lambda res: c)
        return d

    def wait_for_connections(self, ignored=None):
        # TODO: replace this with something that takes a list of peerids and
        # fires when they've all been heard from, instead of using a count
        # and a threshold
        for c in self.clients:
            if (not c.introducer_client or
                len(list(c.get_all_peerids())) != self.numclients):
                d = defer.Deferred()
                d.addCallback(self.wait_for_connections)
                reactor.callLater(0.05, d.callback, None)
                return d
        return defer.succeed(None)

    def test_connections(self):
        self.basedir = "system/SystemTest/test_connections"
        d = self.set_up_nodes()
        self.extra_node = None
        d.addCallback(lambda res: self.add_extra_node(5))
        def _check(extra_node):
            self.extra_node = extra_node
            for c in self.clients:
                all_peerids = list(c.get_all_peerids())
                self.failUnlessEqual(len(all_peerids), 6)
                permuted_peers = list(c.get_permuted_peers("a", True))
                self.failUnlessEqual(len(permuted_peers), 6)
                permuted_other_peers = list(c.get_permuted_peers("a", False))
                self.failUnlessEqual(len(permuted_other_peers), 5)

        d.addCallback(_check)
        def _shutdown_extra_node(res):
            if self.extra_node:
                return self.extra_node.stopService()
            return res
        d.addBoth(_shutdown_extra_node)
        return d
    test_connections.timeout = 300
    # test_connections is subsumed by test_upload_and_download, and takes
    # quite a while to run on a slow machine (because of all the TLS
    # connections that must be established). If we ever rework the introducer
    # code to such an extent that we're not sure if it works anymore, we can
    # reinstate this test until it does.
    del test_connections

    def test_upload_and_download(self):
        self.basedir = "system/SystemTest/test_upload_and_download"
        # we use 4000 bytes of data, which will result in about 400k written
        # to disk among all our simulated nodes
        DATA = "Some data to upload\n" * 200
        d = self.set_up_nodes()
        def _check_connections(res):
            for c in self.clients:
                all_peerids = list(c.get_all_peerids())
                self.failUnlessEqual(len(all_peerids), 5)
                permuted_peers = list(c.get_permuted_peers("a", True))
                self.failUnlessEqual(len(permuted_peers), 5)
                permuted_other_peers = list(c.get_permuted_peers("a", False))
                self.failUnlessEqual(len(permuted_other_peers), 4)
        d.addCallback(_check_connections)
        def _do_upload(res):
            log.msg("UPLOADING")
            u = self.clients[0].getServiceNamed("uploader")
            self.uploader = u
            # we crank the max segsize down to 1024b for the duration of this
            # test, so we can exercise multiple segments. It is important
            # that this is not a multiple of the segment size, so that the
            # tail segment is not the same length as the others. This actualy
            # gets rounded up to 1025 to be a multiple of the number of
            # required shares (since we use 25 out of 100 FEC).
            options = {"max_segment_size": 1024}
            d1 = u.upload_data(DATA, options)
            return d1
        d.addCallback(_do_upload)
        def _upload_done(uri):
            log.msg("upload finished: uri is %s" % (uri,))
            self.uri = uri
            dl = self.clients[1].getServiceNamed("downloader")
            self.downloader = dl
        d.addCallback(_upload_done)

        def _upload_again(res):
            # upload again. This ought to be short-circuited, however with
            # the way we currently generate URIs (i.e. because they include
            # the roothash), we have to do all of the encoding work, and only
            # get to save on the upload part.
            log.msg("UPLOADING AGAIN")
            options = {"max_segment_size": 1024}
            d1 = self.uploader.upload_data(DATA, options)
        d.addCallback(_upload_again)

        def _download_to_data(res):
            log.msg("DOWNLOADING")
            return self.downloader.download_to_data(self.uri)
        d.addCallback(_download_to_data)
        def _download_to_data_done(data):
            log.msg("download finished")
            self.failUnlessEqual(data, DATA)
        d.addCallback(_download_to_data_done)

        target_filename = os.path.join(self.basedir, "download.target")
        def _download_to_filename(res):
            return self.downloader.download_to_filename(self.uri,
                                                        target_filename)
        d.addCallback(_download_to_filename)
        def _download_to_filename_done(res):
            newdata = open(target_filename, "rb").read()
            self.failUnlessEqual(newdata, DATA)
        d.addCallback(_download_to_filename_done)

        target_filename2 = os.path.join(self.basedir, "download.target2")
        def _download_to_filehandle(res):
            fh = open(target_filename2, "wb")
            return self.downloader.download_to_filehandle(self.uri, fh)
        d.addCallback(_download_to_filehandle)
        def _download_to_filehandle_done(fh):
            fh.close()
            newdata = open(target_filename2, "rb").read()
            self.failUnlessEqual(newdata, DATA)
        d.addCallback(_download_to_filehandle_done)

        def _download_nonexistent_uri(res):
            baduri = self.mangle_uri(self.uri)
            d1 = self.downloader.download_to_data(baduri)
            def _baduri_should_fail(res):
                self.failUnless(isinstance(res, Failure))
                self.failUnless(res.check(download.NotEnoughPeersError),
                                "expected NotEnoughPeersError, got %s" % res)
                # TODO: files that have zero peers should get a special kind
                # of NotEnoughPeersError, which can be used to suggest that
                # the URI might be wrong or that they've never uploaded the
                # file in the first place.
            d1.addBoth(_baduri_should_fail)
            return d1
        d.addCallback(_download_nonexistent_uri)
        return d
    test_upload_and_download.timeout = 4800

    def flip_bit(self, good):
        return good[:-1] + chr(ord(good[-1]) ^ 0x01)

    def mangle_uri(self, gooduri):
        # change the key, which changes the storage index, which means we'll
        # be asking about the wrong file, so nobody will have any shares
        u = IFileURI(gooduri)
        u2 = uri.CHKFileURI(key=self.flip_bit(u.key),
                            uri_extension_hash=u.uri_extension_hash,
                            needed_shares=u.needed_shares,
                            total_shares=u.total_shares,
                            size=u.size)
        return u2.to_string()

    # TODO: add a test which mangles the uri_extension_hash instead, and
    # should fail due to not being able to get a valid uri_extension block.
    # Also a test which sneakily mangles the uri_extension block to change
    # some of the validation data, so it will fail in the post-download phase
    # when the file's crypttext integrity check fails. Do the same thing for
    # the key, which should cause the download to fail the post-download
    # plaintext_hash check.

    def test_vdrive(self):
        self.basedir = "system/SystemTest/test_vdrive"
        self.data = LARGE_DATA
        d = self.set_up_nodes()
        d.addCallback(self.log, "starting publish")
        d.addCallback(self._do_publish1)
        d.addCallback(self._test_runner)
        d.addCallback(self._do_publish2)
        # at this point, we have the following global filesystem:
        # /
        # /subdir1
        # /subdir1/mydata567
        # /subdir1/subdir2/
        # /subdir1/subdir2/mydata992

        d.addCallback(self._bounce_client0)
        d.addCallback(self.log, "bounced client0")

        d.addCallback(self._check_publish1)
        d.addCallback(self.log, "did _check_publish1")
        d.addCallback(self._check_publish2)
        d.addCallback(self.log, "did _check_publish2")
        d.addCallback(self._do_publish_private)
        d.addCallback(self.log, "did _do_publish_private")
        # now we also have:
        #  ~client0/personal/sekrit data
        #  ~client0/s2-rw -> /subdir1/subdir2/
        #  ~client0/s2-ro -> /subdir1/subdir2/ (read-only)
        d.addCallback(self._check_publish_private)
        d.addCallback(self.log, "did _check_publish_private")
        d.addCallback(self._test_web)
        d.addCallback(self._test_web_start)
        d.addCallback(self._test_control)
        d.addCallback(self._test_cli)
        d.addCallback(self._test_checker)
        d.addCallback(self._test_verifier)
        return d
    test_vdrive.timeout = 1100

    def _do_publish1(self, res):
        ut = upload.Data(self.data)
        c0 = self.clients[0]
        d = c0.getServiceNamed("vdrive").get_public_root()
        d.addCallback(lambda root: root.create_empty_directory("subdir1"))
        def _made_subdir1(subdir1_node):
            self._subdir1_node = subdir1_node
            d1 = subdir1_node.add_file("mydata567", ut)
            d1.addCallback(self.log, "publish finished")
            def _stash_uri(filenode):
                self.uri = filenode.get_uri()
            d1.addCallback(_stash_uri)
            return d1
        d.addCallback(_made_subdir1)
        return d

    def _do_publish2(self, res):
        ut = upload.Data(self.data)
        d = self._subdir1_node.create_empty_directory("subdir2")
        d.addCallback(lambda subdir2: subdir2.add_file("mydata992", ut))
        return d

    def _bounce_client0(self, res):
        old_client0 = self.clients[0]
        d = old_client0.disownServiceParent()
        assert isinstance(d, defer.Deferred)
        d.addCallback(self.log, "STOPPED")
        # I think windows requires a moment to let the connection really stop
        # and the port number made available for re-use. TODO: examine the
        # behavior, see if this is really the problem, see if we can do
        # better than blindly waiting for a second.
        d.addCallback(self.stall, 1.0)
        def _stopped(res):
            new_client0 = client.Client(basedir=self.getdir("client0"))
            self.add_service(new_client0)
            self.clients[0] = new_client0
            return self.wait_for_connections()
        d.addCallback(_stopped)
        d.addCallback(self.log, "CONNECTED")
        def _connected(res):
            # now find out where the web port was
            l = self.clients[0].getServiceNamed("webish").listener
            port = l._port.getHost().port
            self.webish_url = "http://localhost:%d/" % port
        d.addCallback(_connected)
        d.addCallback(self.log, "GOT WEB LISTENER")
        return d

    def log(self, res, msg):
        #print "MSG: %s  RES: %s" % (msg, res)
        log.msg(msg)
        return res

    def stall(self, res, delay=1.0):
        d = defer.Deferred()
        reactor.callLater(delay, d.callback, res)
        return d

    def _do_publish_private(self, res):
        self.smalldata = "sssh, very secret stuff"
        ut = upload.Data(self.smalldata)
        vdrive0 = self.clients[0].getServiceNamed("vdrive")
        d = vdrive0.get_node_at_path("~")
        d.addCallback(self.log, "GOT ~")
        def _got_root(rootnode):
            d1 = rootnode.create_empty_directory("personal")
            d1.addCallback(self.log, "made ~/personal")
            d1.addCallback(lambda node: node.add_file("sekrit data", ut))
            d1.addCallback(self.log, "made ~/personal/sekrit data")
            d1.addCallback(lambda res:
                           vdrive0.get_node_at_path(["subdir1", "subdir2"]))
            def _got_s2(s2node):
                d2 = rootnode.set_uri("s2-rw", s2node.get_uri())
                d2.addCallback(lambda node:
                               rootnode.set_uri("s2-ro",
                                                s2node.get_immutable_uri()))
                return d2
            d1.addCallback(_got_s2)
            return d1
        d.addCallback(_got_root)
        return d

    def _check_publish1(self, res):
        # this one uses the iterative API
        c1 = self.clients[1]
        d = c1.getServiceNamed("vdrive").get_public_root()
        d.addCallback(self.log, "check_publish1 got /")
        d.addCallback(lambda root: root.get("subdir1"))
        d.addCallback(lambda subdir1: subdir1.get("mydata567"))
        d.addCallback(lambda filenode: filenode.download_to_data())
        d.addCallback(self.log, "get finished")
        def _get_done(data):
            self.failUnlessEqual(data, self.data)
        d.addCallback(_get_done)
        return d

    def _check_publish2(self, res):
        # this one uses the path-based API
        vdrive1 = self.clients[1].getServiceNamed("vdrive")
        get_path = vdrive1.get_node_at_path
        d = get_path("subdir1")
        d.addCallback(lambda dirnode:
                      self.failUnless(IDirectoryNode.providedBy(dirnode)))
        d.addCallback(lambda res: get_path("/subdir1/mydata567"))
        d.addCallback(lambda filenode: filenode.download_to_data())
        d.addCallback(lambda data: self.failUnlessEqual(data, self.data))

        d.addCallback(lambda res: get_path("subdir1/mydata567"))
        def _got_filenode(filenode):
            d1 = vdrive1.get_node(filenode.get_uri())
            d1.addCallback(self.failUnlessEqual, filenode)
            return d1
        d.addCallback(_got_filenode)
        return d

    def _check_publish_private(self, res):
        # this one uses the path-based API
        def get_path(path):
            vdrive0 = self.clients[0].getServiceNamed("vdrive")
            return vdrive0.get_node_at_path(path)
        d = get_path("~/personal")
        def _got_personal(personal):
            self._personal_node = personal
            return personal
        d.addCallback(_got_personal)
        d.addCallback(lambda dirnode:
                      self.failUnless(IDirectoryNode.providedBy(dirnode)))
        d.addCallback(lambda res: get_path("~/personal/sekrit data"))
        d.addCallback(lambda filenode: filenode.download_to_data())
        d.addCallback(lambda data: self.failUnlessEqual(data, self.smalldata))
        d.addCallback(lambda res: get_path("~/s2-rw"))
        d.addCallback(lambda dirnode: self.failUnless(dirnode.is_mutable()))
        d.addCallback(lambda res: get_path("~/s2-ro"))
        def _got_s2ro(dirnode):
            self.failIf(dirnode.is_mutable())
            d1 = defer.succeed(None)
            d1.addCallback(lambda res: dirnode.list())
            d1.addCallback(self.log, "dirnode.list")
            d1.addCallback(lambda res: dirnode.create_empty_directory("nope"))
            d1.addBoth(self.shouldFail, NotMutableError, "mkdir(nope)")
            d1.addCallback(self.log, "doing add_file(ro)")
            ut = upload.Data("I will disappear, unrecorded and unobserved. The tragedy of my demise is made more poignant by its silence, but this beauty is not for you to ever know.")
            d1.addCallback(lambda res: dirnode.add_file("hope", ut))
            d1.addBoth(self.shouldFail, NotMutableError, "add_file(nope)")

            d1.addCallback(self.log, "doing get(ro)")
            d1.addCallback(lambda res: dirnode.get("mydata992"))
            d1.addCallback(lambda filenode:
                           self.failUnless(IFileNode.providedBy(filenode)))

            d1.addCallback(self.log, "doing delete(ro)")
            d1.addCallback(lambda res: dirnode.delete("mydata992"))
            d1.addBoth(self.shouldFail, NotMutableError, "delete(nope)")

            d1.addCallback(lambda res: dirnode.set_uri("hopeless", self.uri))
            d1.addBoth(self.shouldFail, NotMutableError, "set_uri(nope)")

            d1.addCallback(lambda res: dirnode.get("missing"))
            d1.addBoth(self.shouldFail, KeyError, "get(missing)",
                       "unable to find child named 'missing'")

            d1.addCallback(self.log, "doing move_child_to(ro)")
            personal = self._personal_node
            d1.addCallback(lambda res:
                           dirnode.move_child_to("mydata992",
                                                 personal, "nope"))
            d1.addBoth(self.shouldFail, NotMutableError, "mv from readonly")

            d1.addCallback(self.log, "doing move_child_to(ro)2")
            d1.addCallback(lambda res:
                           personal.move_child_to("sekrit data",
                                                  dirnode, "nope"))
            d1.addBoth(self.shouldFail, NotMutableError, "mv to readonly")

            d1.addCallback(self.log, "finished with _got_s2ro")
            return d1
        d.addCallback(_got_s2ro)
        d.addCallback(lambda res: get_path("~"))
        def _got_home(home):
            personal = self._personal_node
            d1 = defer.succeed(None)
            d1.addCallback(self.log, "mv '~/personal/sekrit data' to ~/sekrit")
            d1.addCallback(lambda res:
                           personal.move_child_to("sekrit data",home,"sekrit"))

            d1.addCallback(self.log, "mv ~/sekrit '~/sekrit data'")
            d1.addCallback(lambda res:
                           home.move_child_to("sekrit", home, "sekrit data"))

            d1.addCallback(self.log, "mv '~/sekret data' ~/personal/")
            d1.addCallback(lambda res:
                           home.move_child_to("sekrit data", personal))

            d1.addCallback(lambda res: home.build_manifest())
            d1.addCallback(self.log, "manifest")
            #  four items:
            # ~client0/personal/
            # ~client0/personal/sekrit data
            # ~client0/s2-rw  (same as ~client/s2-ro)
            # ~client0/s2-rw/mydata992 (same as ~client/s2-rw/mydata992)
            d1.addCallback(lambda manifest:
                           self.failUnlessEqual(len(manifest), 4))
            return d1
        d.addCallback(_got_home)
        return d

    def shouldFail(self, res, expected_failure, which, substring=None):
        if isinstance(res, Failure):
            res.trap(expected_failure)
            if substring:
                self.failUnless(substring in str(res),
                                "substring '%s' not in '%s'"
                                % (substring, str(res)))
        else:
            self.fail("%s was supposed to raise %s, not get '%s'" %
                      (which, expected_failure, res))

    def PUT(self, urlpath, data):
        url = self.webish_url + urlpath
        return getPage(url, method="PUT", postdata=data)

    def GET(self, urlpath, followRedirect=False):
        url = self.webish_url + urlpath
        return getPage(url, method="GET", followRedirect=followRedirect)

    def _test_web(self, res):
        base = self.webish_url
        d = getPage(base)
        def _got_welcome(page):
            expected = "Connected Peers: <span>%d</span>" % (self.numclients)
            self.failUnless(expected in page,
                            "I didn't see the right 'connected peers' message "
                            "in: %s" % page
                            )
            expected = "My nodeid: <span>%s</span>" % (b32encode(self.clients[0].nodeid).lower(),)
            self.failUnless(expected in page,
                            "I didn't see the right 'My nodeid' message "
                            "in: %s" % page)
        d.addCallback(_got_welcome)
        d.addCallback(self.log, "done with _got_welcome")
        d.addCallback(lambda res: getPage(base + "vdrive/global"))
        d.addCallback(lambda res: getPage(base + "vdrive/global/subdir1"))
        def _got_subdir1(page):
            # there ought to be an href for our file
            self.failUnless(("<td>%d</td>" % len(self.data)) in page)
            self.failUnless(">mydata567</a>" in page)
        d.addCallback(_got_subdir1)
        d.addCallback(self.log, "done with _got_subdir1")
        d.addCallback(lambda res:
                      getPage(base + "vdrive/global/subdir1/mydata567"))
        def _got_data(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_data)

        # download from a URI embedded in a URL
        d.addCallback(self.log, "_get_from_uri")
        def _get_from_uri(res):
            return getPage(base + "uri/%s?filename=%s"
                           % (self.uri, "mydata567"))
        d.addCallback(_get_from_uri)
        def _got_from_uri(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_from_uri)

        # download from a URI embedded in a URL, second form
        d.addCallback(self.log, "_get_from_uri2")
        def _get_from_uri2(res):
            return getPage(base + "uri?uri=%s" % (self.uri,))
        d.addCallback(_get_from_uri2)
        def _got_from_uri2(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_from_uri2)

        # download from a bogus URI, make sure we get a reasonable error
        d.addCallback(self.log, "_get_from_bogus_uri")
        def _get_from_bogus_uri(res):
            d1 = getPage(base + "uri/%s?filename=%s"
                         % (self.mangle_uri(self.uri), "mydata567"))
            d1.addBoth(self.shouldFail, Error, "downloading bogus URI",
                       "410")
            return d1
        d.addCallback(_get_from_bogus_uri)

        # upload a file with PUT
        d.addCallback(self.log, "about to try PUT")
        d.addCallback(lambda res: self.PUT("vdrive/global/subdir3/new.txt",
                                           "new.txt contents"))
        d.addCallback(lambda res: self.GET("vdrive/global/subdir3/new.txt"))
        d.addCallback(self.failUnlessEqual, "new.txt contents")
        # and again with something large enough to use multiple segments,
        # and hopefully trigger pauseProducing too
        d.addCallback(lambda res: self.PUT("vdrive/global/subdir3/big.txt",
                                           "big" * 500000)) # 1.5MB
        d.addCallback(lambda res: self.GET("vdrive/global/subdir3/big.txt"))
        d.addCallback(lambda res: self.failUnlessEqual(len(res), 1500000))

        # can we replace files in place?
        d.addCallback(lambda res: self.PUT("vdrive/global/subdir3/new.txt",
                                           "NEWER contents"))
        d.addCallback(lambda res: self.GET("vdrive/global/subdir3/new.txt"))
        d.addCallback(self.failUnlessEqual, "NEWER contents")


        # TODO: mangle the second segment of a file, to test errors that
        # occur after we've already sent some good data, which uses a
        # different error path.

        # TODO: download a URI with a form
        # TODO: create a directory by using a form
        # TODO: upload by using a form on the directory page
        #    url = base + "global_vdrive/subdir1/freeform_post!!upload"
        # TODO: delete a file by using a button on the directory page

        return d

    def _test_web_start(self, res):
        basedir = self.clients[0].basedir
        startfile = os.path.join(basedir, "start.html")
        self.failUnless(os.path.exists(startfile))
        start_html = open(startfile, "r").read()
        self.failUnless(self.webish_url in start_html)
        private_uri = self.clients[0].getServiceNamed("vdrive")._private_uri
        private_url = self.webish_url + "uri/" + private_uri.replace("/","!")
        self.failUnless(private_url in start_html)

    def _test_runner(self, res):
        # exercise some of the diagnostic tools in runner.py

        # find a share
        for (dirpath, dirnames, filenames) in os.walk(self.basedir):
            if "storage" not in dirpath:
                continue
            if not filenames:
                continue
            pieces = dirpath.split(os.sep)
            if pieces[-3] == "storage" and pieces[-2] == "shares":
                # we're sitting in .../storage/shares/$SINDEX , and there are
                # sharefiles here
                filename = os.path.join(dirpath, filenames[0])
                break
        else:
            self.fail("unable to find any uri_extension files in %s"
                      % self.basedir)
        log.msg("test_system.SystemTest._test_runner using %s" % filename)

        out,err = StringIO(), StringIO()
        rc = runner.runner(["dump-share",
                            filename],
                           stdout=out, stderr=err)
        output = out.getvalue()
        self.failUnlessEqual(rc, 0)

        # we only upload a single file, so we can assert some things about
        # its size and shares.
        self.failUnless("size: %d\n" % len(self.data) in output)
        self.failUnless("num_segments: 1\n" in output)
        # segment_size is always a multiple of needed_shares
        self.failUnless("segment_size: 114\n" in output)
        self.failUnless("total_shares: 10\n" in output)
        # keys which are supposed to be present
        for key in ("size", "num_segments", "segment_size",
                    "needed_shares", "total_shares",
                    "codec_name", "codec_params", "tail_codec_params",
                    "plaintext_hash", "plaintext_root_hash",
                    "crypttext_hash", "crypttext_root_hash",
                    "share_root_hash",
                    ):
            self.failUnless("%s: " % key in output, key)

    def _test_control(self, res):
        # exercise the remote-control-the-client foolscap interfaces in
        # allmydata.control (mostly used for performance tests)
        c0 = self.clients[0]
        control_furl_file = os.path.join(c0.basedir, "control.furl")
        control_furl = open(control_furl_file, "r").read().strip()
        # it doesn't really matter which Tub we use to connect to the client,
        # so let's just use our Introducer's
        d = self.introducer_and_vdrive.tub.getReference(control_furl)
        d.addCallback(self._test_control2, control_furl_file)
        return d
    def _test_control2(self, rref, filename):
        d = rref.callRemote("upload_from_file_to_uri", filename)
        downfile = os.path.join(self.basedir, "control.downfile")
        d.addCallback(lambda uri:
                      rref.callRemote("download_from_uri_to_file",
                                      uri, downfile))
        def _check(res):
            self.failUnlessEqual(res, downfile)
            data = open(downfile, "r").read()
            expected_data = open(filename, "r").read()
            self.failUnlessEqual(data, expected_data)
        d.addCallback(_check)
        d.addCallback(lambda res: rref.callRemote("speed_test", 1, 200))
        if sys.platform == "linux2":
            d.addCallback(lambda res: rref.callRemote("get_memory_usage"))
        d.addCallback(lambda res: rref.callRemote("measure_peer_response_time"))
        return d

    def _test_cli(self, res):
        # run various CLI commands (in a thread, since they use blocking
        # network calls)

        private_uri = self.clients[0].getServiceNamed("vdrive")._private_uri
        nodeargs = [
            "--node-url", self.webish_url,
            "--root-uri", private_uri,
            ]

        argv = ["ls"] + nodeargs
        d = self._run_cli(argv)
        def _check_ls((out,err)):
            self.failUnless("personal" in out)
            self.failUnless("s2-ro" in out)
            self.failUnless("s2-rw" in out)
            self.failUnlessEqual(err, "")
        d.addCallback(_check_ls)

        def _put(res):
            tdir = self.getdir("cli_put")
            fileutil.make_dirs(tdir)
            fn = os.path.join(tdir, "upload_me")
            f = open(fn, "w")
            f.write("I will not write the same thing over and over.\n" * 100)
            f.close()
            argv = ["put"] + nodeargs + [fn, "test_put/upload.txt"]
            return self._run_cli(argv)
        d.addCallback(_put)
        def _check_put((out,err)):
            self.failUnless("200 OK" in out)
            self.failUnlessEqual(err, "")
            vdrive0 = self.clients[0].getServiceNamed("vdrive")
            d = vdrive0.get_node_at_path("~/test_put/upload.txt")
            d.addCallback(lambda filenode: filenode.download_to_data())
            def _check_put2(res):
                self.failUnless("I will not write" in res)
            d.addCallback(_check_put2)
            return d
        d.addCallback(_check_put)
        def _get(res):
            argv = ["get"] + nodeargs + ["test_put/upload.txt"]
            return self._run_cli(argv)
        d.addCallback(_get)
        def _check_get((out,err)):
            self.failUnless("I will not write" in out)
            self.failUnlessEqual(err, "")
        d.addCallback(_check_get)

        def _mv(res):
            argv = ["mv"] + nodeargs + ["test_put/upload.txt",
                                        "test_put/moved.txt"]
            return self._run_cli(argv)
        d.addCallback(_mv)
        def _check_mv((out,err)):
            self.failUnless("OK" in out)
            self.failUnlessEqual(err, "")
            vdrive0 = self.clients[0].getServiceNamed("vdrive")
            d = defer.maybeDeferred(vdrive0.get_node_at_path,
                                    "~/test_put/upload.txt")
            d.addBoth(self.shouldFail, KeyError, "test_cli._check_rm",
                      "unable to find child named 'upload.txt'")
            d.addCallback(lambda res:
                          vdrive0.get_node_at_path("~/test_put/moved.txt"))
            d.addCallback(lambda filenode: filenode.download_to_data())
            def _check_mv2(res):
                self.failUnless("I will not write" in res)
            d.addCallback(_check_mv2)
            return d
        d.addCallback(_check_mv)

        def _rm(res):
            argv = ["rm"] + nodeargs + ["test_put/moved.txt"]
            return self._run_cli(argv)
        d.addCallback(_rm)
        def _check_rm((out,err)):
            self.failUnless("200 OK" in out)
            self.failUnlessEqual(err, "")
            vdrive0 = self.clients[0].getServiceNamed("vdrive")
            d = defer.maybeDeferred(vdrive0.get_node_at_path,
                                    "~/test_put/moved.txt")
            d.addBoth(self.shouldFail, KeyError, "test_cli._check_rm",
                      "unable to find child named 'moved.txt'")
            return d
        d.addCallback(_check_rm)
        return d

    def _run_cli(self, argv):
        stdout, stderr = StringIO(), StringIO()
        d = threads.deferToThread(runner.runner, argv, run_by_human=False,
                                  stdout=stdout, stderr=stderr)
        def _done(res):
            return stdout.getvalue(), stderr.getvalue()
        d.addCallback(_done)
        return d

    def _test_checker(self, res):
        vdrive0 = self.clients[0].getServiceNamed("vdrive")
        checker1 = self.clients[1].getServiceNamed("checker")
        d = vdrive0.get_node_at_path("~")
        d.addCallback(lambda home: home.build_manifest())
        def _check_all(manifest):
            dl = []
            for si in manifest:
                dl.append(checker1.check(si))
            return deferredutil.DeferredListShouldSucceed(dl)
        d.addCallback(_check_all)
        def _done(res):
            for i in res:
                if type(i) is bool:
                    self.failUnless(i is True)
                else:
                    (needed, total, found, sharemap) = i
                    self.failUnlessEqual(needed, 3)
                    self.failUnlessEqual(total, 10)
                    self.failUnlessEqual(found, total)
                    self.failUnlessEqual(len(sharemap.keys()), 10)
                    peers = set()
                    for shpeers in sharemap.values():
                        peers.update(shpeers)
                    self.failUnlessEqual(len(peers), self.numclients-1)
        d.addCallback(_done)
        return d

    def _test_verifier(self, res):
        vdrive0 = self.clients[0].getServiceNamed("vdrive")
        checker1 = self.clients[1].getServiceNamed("checker")
        d = vdrive0.get_node_at_path("~")
        d.addCallback(lambda home: home.build_manifest())
        def _check_all(manifest):
            dl = []
            for si in manifest:
                dl.append(checker1.verify(si))
            return deferredutil.DeferredListShouldSucceed(dl)
        d.addCallback(_check_all)
        def _done(res):
            for i in res:
                self.failUnless(i is True)
        d.addCallback(_done)
        return d

