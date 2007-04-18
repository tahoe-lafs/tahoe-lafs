
import os
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.application import service
from allmydata import client, queen
from allmydata.util import idlib, fileutil
from foolscap.eventual import flushEventualQueue
from twisted.python import log
from twisted.web.client import getPage

def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

class SystemTest(unittest.TestCase):

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
            d1 = dl.download_to_data(uri)
            return d1
        d.addCallback(_upload_done)
        def _download_done(data):
            log.msg("download finished")
            self.failUnlessEqual(data, DATA)
        d.addCallback(_download_done)

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

        return d
    test_upload_and_download.timeout = 600

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
        def _publish_done(res):
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
        d.addCallback(lambda res: getPage(base + "vdrive/subdir1"))
        def _got_subdir1(page):
            # there ought to be an href for our file
            self.failUnless(">mydata567</a>" in page)
        d.addCallback(_got_subdir1)
        d.addCallback(lambda res: getPage(base + "vdrive/subdir1/mydata567"))
        def _got_data(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_data)
        return d

