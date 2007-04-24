
import os
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.application import service
from allmydata import client, queen, uri, download
from allmydata.util import idlib, fileutil, testutil
from foolscap.eventual import flushEventualQueue
from twisted.python import log
from twisted.python.failure import Failure
from twisted.web.client import getPage
from twisted.web.error import PageRedirect

def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

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
        queendir = self.getdir("queen")
        if not os.path.isdir(queendir):
            fileutil.make_dirs(queendir)
        self.queen = self.add_service(queen.Queen(basedir=queendir))
        d = self.queen.when_tub_ready()
        d.addCallback(self._set_up_nodes_2)
        return d

    def _set_up_nodes_2(self, res):
        q = self.queen
        self.queen_furl = q.urls["introducer"]
        self.vdrive_furl = q.urls["vdrive"]
        self.clients = []
        for i in range(self.numclients):
            basedir = self.getdir("client%d" % i)
            if not os.path.isdir(basedir):
                fileutil.make_dirs(basedir)
            if i == 0:
                open(os.path.join(basedir, "webport"), "w").write("tcp:0:interface=127.0.0.1")
            open(os.path.join(basedir, "introducer.furl"), "w").write(self.queen_furl)
            open(os.path.join(basedir, "vdrive.furl"), "w").write(self.vdrive_furl)
            c = self.add_service(client.Client(basedir=basedir))
            self.clients.append(c)
        log.msg("STARTING")
        d = self.wait_for_connections()
        def _connected(res):
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
        open(os.path.join(basedir, "introducer.furl"), "w").write(self.queen_furl)
        open(os.path.join(basedir, "vdrive.furl"), "w").write(self.vdrive_furl)

        c = client.Client(basedir=basedir)
        self.clients.append(c)
        self.numclients += 1
        c.startService()
        d = self.wait_for_connections()
        d.addCallback(lambda res: c)
        return d

    def wait_for_connections(self, ignored=None):
        for c in self.clients:
            if (not c.introducer_client or
                len(list(c.get_all_peerids())) != self.numclients):
                d = defer.Deferred()
                d.addCallback(self.wait_for_connections)
                reactor.callLater(0.05, d.callback, None)
                return d
        return defer.succeed(None)

    def test_connections(self):
        self.basedir = "test_system/SystemTest/test_connections"
        d = self.set_up_nodes()
        self.extra_node = None
        d.addCallback(lambda res: self.add_extra_node(5))
        def _check(extra_node):
            self.extra_node = extra_node
            for c in self.clients:
                self.failUnlessEqual(len(list(c.get_all_peerids())), 6)
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
        self.basedir = "test_system/SystemTest/test_upload_and_download"
        # we use 4000 bytes of data, which will result in about 400k written
        # to disk among all our simulated nodes
        DATA = "Some data to upload\n" * 200
        d = self.set_up_nodes()
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
                self.failUnless(res.check(download.NotEnoughPeersError))
                # TODO: files that have zero peers should get a special kind
                # of NotEnoughPeersError, which can be used to suggest that
                # the URI might be wrong or that they've nver uploaded the
                # file in the first place.
            d1.addBoth(_baduri_should_fail)
            return d1
        d.addCallback(_download_nonexistent_uri)
        return d
    test_upload_and_download.timeout = 600

    def flip_bit(self, good):
        return good[:-1] + chr(ord(good[-1]) ^ 0x01)

    def mangle_uri(self, gooduri):
        pieces = list(uri.unpack_uri(gooduri))
        # [4] is the verifierid
        pieces[4] = self.flip_bit(pieces[4])
        return uri.pack_uri(*pieces)

    def test_vdrive(self):
        self.basedir = "test_system/SystemTest/test_vdrive"
        self.data = DATA = "Some data to publish to the virtual drive\n"
        d = self.set_up_nodes()
        def _do_publish(res):
            log.msg("PUBLISHING")
            v0 = self.clients[0].getServiceNamed("vdrive")
            d1 = v0.make_directory("/", "subdir1")
            d1.addCallback(lambda subdir1:
                           v0.put_file_by_data(subdir1, "mydata567", DATA))
            return d1
        d.addCallback(_do_publish)
        def _publish_done(uri):
            self.uri = uri
            log.msg("publish finished")
            v1 = self.clients[1].getServiceNamed("vdrive")
            d1 = v1.get_file_to_data("/subdir1/mydata567")
            return d1
        d.addCallback(_publish_done)
        def _get_done(data):
            log.msg("get finished")
            self.failUnlessEqual(data, DATA)
        d.addCallback(_get_done)
        d.addCallback(self._test_web)
        return d
    test_vdrive.timeout = 300

    def _test_web(self, res):
        base = self.webish_url
        d = getPage(base)
        def _got_welcome(page):
            expected = "Connected Peers: <span>%d</span>" % (self.numclients)
            self.failUnless(expected in page,
                            "I didn't see the right 'connected peers' message "
                            "in: %s" % page
                            )
            expected = "My nodeid: <span>%s</span>" % idlib.b2a(self.clients[0].nodeid)
            self.failUnless(expected in page,
                            "I didn't see the right 'My nodeid' message "
                            "in: %s" % page)
        d.addCallback(_got_welcome)
        d.addCallback(lambda res: getPage(base + "vdrive"))
        d.addCallback(lambda res: getPage(base + "vdrive/subdir1"))
        def _got_subdir1(page):
            # there ought to be an href for our file
            self.failUnless(">mydata567</a>" in page)
        d.addCallback(_got_subdir1)
        d.addCallback(lambda res: getPage(base + "vdrive/subdir1/mydata567"))
        def _got_data(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_data)

        # download from a URI embedded in a URL
        def _get_from_uri(res):
            return getPage(base + "download_uri/%s?filename=%s"
                           % (self.uri, "mydata567"))
        d.addCallback(_get_from_uri)
        def _got_from_uri(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_from_uri)

        # download from a URI embedded in a URL, second form
        def _get_from_uri2(res):
            return getPage(base + "download_uri?uri=%s" % (self.uri,))
        d.addCallback(_get_from_uri2)
        def _got_from_uri2(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_from_uri2)

        # download from a URI pasted into a form. Use POST, build a
        # multipart/form-data, submit it. This actualy redirects us to a
        # /download_uri?uri=%s URL, and twisted.web.client doesn't seem to
        # handle POST redirects very well (it does a second POST instead of
        # the GET that a browser seems to do), so we just verify that we get
        # the right redirect response.
        def _get_from_form(res):
            url = base + "welcome/freeform_post!!download"
            sep = "-"*40 + "boogabooga"
            form = [sep,
                    "Content-Disposition: form-data; name=\"_charset_\"",
                    "",
                    "UTF-8",
                    sep,
                    "Content-Disposition: form-data; name=\"uri\"",
                    "",
                    self.uri,
                    sep,
                    "Content-Disposition: form-data; name=\"filename\"",
                    "",
                    "foo.txt",
                    sep,
                    "Content-Disposition: form-data; name=\"download\"",
                    "",
                    "Download",
                    sep + "--",
                    ]
            body = "\r\n".join(form)
            headers = {"content-type":
                       "multipart/form-data; boundary=%s" % sep,
                       }
            return getPage(url, None, "POST", body, headers=headers,
                           followRedirect=False)
        d.addCallback(_get_from_form)
        def _got_from_form_worked_unexpectedly(page):
            self.fail("we weren't supposed to get an actual page: %s" %
                      (page,))
        def _got_from_form_redirect(f):
            f.trap(PageRedirect)
            # the PageRedirect does not seem to capture the uri= query arg
            # properly, so we can't check for it.
            self.failUnless(f.value.location.startswith(base+"download_uri?"))
        d.addCallbacks(_got_from_form_worked_unexpectedly,
                       _got_from_form_redirect)

        # TODO: create a directory by using a form

        # TODO: upload by using a form on the directory page
        #    url = base + "vdrive/subdir1/freeform_post!!upload"

        # TODO: delete a file by using a button on the directory page

        return d

