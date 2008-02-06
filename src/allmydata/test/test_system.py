
from base64 import b32encode
import os, sys, time, re
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.internet import threads # CLI tests use deferToThread
from twisted.internet.error import ConnectionDone, ConnectionLost
from twisted.application import service
from allmydata import client, uri, download, upload, storage, mutable, offloaded
from allmydata.introducer import IntroducerNode
from allmydata.util import deferredutil, fileutil, idlib, mathutil, testutil
from allmydata.util import log
from allmydata.scripts import runner
from allmydata.interfaces import IDirectoryNode, IFileNode, IFileURI
from allmydata.mutable import NotMutableError
from foolscap.eventual import flushEventualQueue
from foolscap import DeadReferenceError
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

class SystemTest(testutil.SignalMixin, testutil.PollMixin, unittest.TestCase):

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

    def set_up_nodes(self, NUMCLIENTS=5, createprivdir=False):
        self.numclients = NUMCLIENTS
        self.createprivdir = createprivdir
        iv_dir = self.getdir("introducer")
        if not os.path.isdir(iv_dir):
            fileutil.make_dirs(iv_dir)
        iv = IntroducerNode(basedir=iv_dir)
        self.introducer = self.add_service(iv)
        d = self.introducer.when_tub_ready()
        d.addCallback(self._set_up_nodes_2)
        return d

    def _set_up_nodes_2(self, res):
        q = self.introducer
        self.introducer_furl = q.introducer_url
        self.clients = []
        basedirs = []
        for i in range(self.numclients):
            basedir = self.getdir("client%d" % i)
            basedirs.append(basedir)
            fileutil.make_dirs(basedir)
            if i == 0:
                # client[0] runs a webserver and a helper
                open(os.path.join(basedir, "webport"), "w").write("tcp:0:interface=127.0.0.1")
                open(os.path.join(basedir, "run_helper"), "w").write("yes\n")
            if self.createprivdir:
                fileutil.make_dirs(os.path.join(basedir, "private"))
                open(os.path.join(basedir, "private", "root_dir.cap"), "w")
            open(os.path.join(basedir, "introducer.furl"), "w").write(self.introducer_furl)

        # start client[0], wait for it's tub to be ready (at which point it
        # will have registered the helper furl).
        c = self.add_service(client.Client(basedir=basedirs[0]))
        self.clients.append(c)
        d = c.when_tub_ready()
        def _ready(res):
            f = open(os.path.join(basedirs[0],"private","helper.furl"), "r")
            helper_furl = f.read()
            f.close()
            self.helper_furl = helper_furl
            f = open(os.path.join(basedirs[3],"helper.furl"), "w")
            f.write(helper_furl)
            f.close()

            # this starts the rest of the clients
            for i in range(1, self.numclients):
                c = self.add_service(client.Client(basedir=basedirs[i]))
                self.clients.append(c)
            log.msg("STARTING")
            return self.wait_for_connections()
        d.addCallback(_ready)
        def _connected(res):
            log.msg("CONNECTED")
            # now find out where the web port was
            l = self.clients[0].getServiceNamed("webish").listener
            port = l._port.getHost().port
            self.webish_url = "http://localhost:%d/" % port
        d.addCallback(_connected)
        return d

    def add_extra_node(self, client_num, helper_furl=None,
                       add_to_sparent=False):
        # usually this node is *not* parented to our self.sparent, so we can
        # shut it down separately from the rest, to exercise the
        # connection-lost code
        basedir = self.getdir("client%d" % client_num)
        if not os.path.isdir(basedir):
            fileutil.make_dirs(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write(self.introducer_furl)
        if helper_furl:
            f = open(os.path.join(basedir, "helper.furl") ,"w")
            f.write(helper_furl+"\n")
            f.close()

        c = client.Client(basedir=basedir)
        self.clients.append(c)
        self.numclients += 1
        if add_to_sparent:
            c.setServiceParent(self.sparent)
        else:
            c.startService()
        d = self.wait_for_connections()
        d.addCallback(lambda res: c)
        return d

    def _check_connections(self):
        for c in self.clients:
            ic = c.introducer_client
            if not ic.connected_to_introducer():
                return False
            if len(ic.get_all_peerids()) != self.numclients:
                return False
        return True

    def wait_for_connections(self, ignored=None):
        # TODO: replace this with something that takes a list of peerids and
        # fires when they've all been heard from, instead of using a count
        # and a threshold
        return self.poll(self._check_connections, timeout=200)

    def test_connections(self):
        self.basedir = "system/SystemTest/test_connections"
        d = self.set_up_nodes()
        self.extra_node = None
        d.addCallback(lambda res: self.add_extra_node(self.numclients))
        def _check(extra_node):
            self.extra_node = extra_node
            for c in self.clients:
                all_peerids = list(c.get_all_peerids())
                self.failUnlessEqual(len(all_peerids), self.numclients+1)
                permuted_peers = list(c.get_permuted_peers("storage", "a"))
                self.failUnlessEqual(len(permuted_peers), self.numclients+1)

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

    def test_upload_and_download_random_key(self):
        return self._test_upload_and_download(False)
    test_upload_and_download_random_key.timeout = 4800

    def test_upload_and_download_content_hash_key(self):
        return self._test_upload_and_download(True)
    test_upload_and_download_content_hash_key.timeout = 4800

    def _test_upload_and_download(self, contenthashkey):
        self.basedir = "system/SystemTest/test_upload_and_download"
        # we use 4000 bytes of data, which will result in about 400k written
        # to disk among all our simulated nodes
        DATA = "Some data to upload\n" * 200
        d = self.set_up_nodes()
        def _check_connections(res):
            for c in self.clients:
                all_peerids = list(c.get_all_peerids())
                self.failUnlessEqual(len(all_peerids), self.numclients)
                permuted_peers = list(c.get_permuted_peers("storage", "a"))
                self.failUnlessEqual(len(permuted_peers), self.numclients)
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
            up = upload.Data(DATA, contenthashkey=contenthashkey)
            up.max_segment_size = 1024
            d1 = u.upload(up)
            return d1
        d.addCallback(_do_upload)
        def _upload_done(uri):
            log.msg("upload finished: uri is %s" % (uri,))
            self.uri = uri
            dl = self.clients[1].getServiceNamed("downloader")
            self.downloader = dl
        d.addCallback(_upload_done)

        def _upload_again(res):
            # Upload again. If contenthashkey then this ought to be
            # short-circuited, however with the way we currently generate URIs
            # (i.e. because they include the roothash), we have to do all of the
            # encoding work, and only get to save on the upload part.
            log.msg("UPLOADING AGAIN")
            up = upload.Data(DATA, contenthashkey=contenthashkey)
            up.max_segment_size = 1024
            d1 = self.uploader.upload(up)
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
            log.msg("about to download non-existent URI", level=log.UNUSUAL,
                    facility="tahoe.tests")
            d1 = self.downloader.download_to_data(baduri)
            def _baduri_should_fail(res):
                log.msg("finished downloading non-existend URI",
                        level=log.UNUSUAL, facility="tahoe.tests")
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

        # add a new node, which doesn't accept shares, and only uses the
        # helper for upload.
        d.addCallback(lambda res: self.add_extra_node(self.numclients,
                                                      self.helper_furl,
                                                      add_to_sparent=True))
        def _added(extra_node):
            self.extra_node = extra_node
            extra_node.getServiceNamed("storageserver").sizelimit = 0
        d.addCallback(_added)

        HELPER_DATA = "Data that needs help to upload" * 1000
        def _upload_with_helper(res):
            u = upload.Data(HELPER_DATA, contenthashkey=contenthashkey)
            d = self.extra_node.upload(u)
            def _uploaded(uri):
                return self.downloader.download_to_data(uri)
            d.addCallback(_uploaded)
            def _check(newdata):
                self.failUnlessEqual(newdata, HELPER_DATA)
            d.addCallback(_check)
            return d
        d.addCallback(_upload_with_helper)

        def _upload_duplicate_with_helper(res):
            u = upload.Data(HELPER_DATA, contenthashkey=contenthashkey)
            u.debug_stash_RemoteEncryptedUploadable = True
            d = self.extra_node.upload(u)
            def _uploaded(uri):
                return self.downloader.download_to_data(uri)
            d.addCallback(_uploaded)
            def _check(newdata):
                self.failUnlessEqual(newdata, HELPER_DATA)
                self.failIf(hasattr(u, "debug_RemoteEncryptedUploadable"),
                            "uploadable started uploading, should have been avoided")
            d.addCallback(_check)
            return d
        if contenthashkey:
            d.addCallback(_upload_duplicate_with_helper)

        def _upload_resumable(res):
            DATA = "Data that needs help to upload and gets interrupted" * 1000
            u1 = upload.Data(DATA, contenthashkey=contenthashkey)
            u2 = upload.Data(DATA, contenthashkey=contenthashkey)

            # tell the upload to drop the connection after about 5kB
            u1.debug_interrupt = 5000
            u1.debug_stash_RemoteEncryptedUploadable = True
            u2.debug_stash_RemoteEncryptedUploadable = True
            # sneak into the helper and reduce its chunk size, so that our
            # debug_interrupt will sever the connection on about the fifth
            # chunk fetched. This makes sure that we've started to write the
            # new shares before we abandon them, which exercises the
            # abort/delete-partial-share code. TODO: find a cleaner way to do
            # this. I know that this will affect later uses of the helper in
            # this same test run, but I'm not currently worried about it.
            offloaded.CHKCiphertextFetcher.CHUNK_SIZE = 1000

            d = self.extra_node.upload(u1)

            def _should_not_finish(res):
                self.fail("interrupted upload should have failed, not finished"
                          " with result %s" % (res,))
            def _interrupted(f):
                f.trap(ConnectionLost, ConnectionDone, DeadReferenceError)
                reu = u1.debug_RemoteEncryptedUploadable
                # make sure we actually interrupted it before finishing the
                # file
                self.failUnless(reu._bytes_sent < len(DATA),
                                "read %d out of %d total" % (reu._bytes_sent,
                                                             len(DATA)))
                log.msg("waiting for reconnect", level=log.NOISY,
                        facility="tahoe.test.test_system")
                # now, we need to give the nodes a chance to notice that this
                # connection has gone away. When this happens, the storage
                # servers will be told to abort their uploads, removing the
                # partial shares. Unfortunately this involves TCP messages
                # going through the loopback interface, and we can't easily
                # predict how long that will take. If it were all local, we
                # could use fireEventually() to stall. Since we don't have
                # the right introduction hooks, the best we can do is use a
                # fixed delay. TODO: this is fragile.
                return self.stall(None, 2.0)
            d.addCallbacks(_should_not_finish, _interrupted)

            def _disconnected(res):
                # check to make sure the storage servers aren't still hanging
                # on to the partial share: their incoming/ directories should
                # now be empty.
                log.msg("disconnected", level=log.NOISY,
                        facility="tahoe.test.test_system")
                for i in range(self.numclients):
                    incdir = os.path.join(self.getdir("client%d" % i),
                                          "storage", "shares", "incoming")
                    self.failIf(os.path.exists(incdir) and os.listdir(incdir))
            d.addCallback(_disconnected)

            def _wait_for_reconnect(res):
                # then we need to give the reconnector a chance to
                # reestablish the connection to the helper.
                d.addCallback(lambda res:
                              log.msg("wait_for_connections", level=log.NOISY,
                                      facility="tahoe.test.test_system"))
                d.addCallback(lambda res: self.wait_for_connections())
            d.addCallback(_wait_for_reconnect)

            def _upload_again(res):
                log.msg("uploading again", level=log.NOISY,
                        facility="tahoe.test.test_system")
                return self.extra_node.upload(u2)
            d.addCallbacks(_upload_again)

            def _uploaded(uri):
                log.msg("Second upload complete", level=log.NOISY,
                        facility="tahoe.test.test_system")
                reu = u2.debug_RemoteEncryptedUploadable

                # We currently don't support resumption of upload if the data is
                # encrypted with a random key.  (Because that would require us
                # to store the key locally and re-use it on the next upload of
                # this file, which isn't a bad thing to do, but we currently
                # don't do it.)
                if contenthashkey:
                    # Make sure we did not have to read the whole file the
                    # second time around .
                    self.failUnless(reu._bytes_sent < len(DATA),
                                "resumption didn't save us any work:"
                                " read %d bytes out of %d total" %
                                (reu._bytes_sent, len(DATA)))
                else:
                    # Make sure we did have to read the whole file the second
                    # time around -- because the one that we partially uploaded
                    # earlier was encrypted with a different random key.
                    self.failIf(reu._bytes_sent < len(DATA),
                                "resumption saved us some work even though we were using random keys:"
                                " read %d bytes out of %d total" %
                                (reu._bytes_sent, len(DATA)))
                return self.downloader.download_to_data(uri)
            d.addCallback(_uploaded)

            def _check(newdata):
                self.failUnlessEqual(newdata, DATA)
                # If using a content hash key, then also check that the helper
                # has removed the temp file from its directories.
                if contenthashkey:
                    basedir = os.path.join(self.getdir("client0"), "helper")
                    files = os.listdir(os.path.join(basedir, "CHK_encoding"))
                    self.failUnlessEqual(files, [])
                    files = os.listdir(os.path.join(basedir, "CHK_incoming"))
                    self.failUnlessEqual(files, [])
            d.addCallback(_check)
            return d
        d.addCallback(_upload_resumable)

        return d

    def _find_shares(self, basedir):
        shares = []
        for (dirpath, dirnames, filenames) in os.walk(basedir):
            if "storage" not in dirpath:
                continue
            if not filenames:
                continue
            pieces = dirpath.split(os.sep)
            if pieces[-4] == "storage" and pieces[-3] == "shares":
                # we're sitting in .../storage/shares/$START/$SINDEX , and there
                # are sharefiles here
                assert pieces[-5].startswith("client")
                client_num = int(pieces[-5][-1])
                storage_index_s = pieces[-1]
                storage_index = idlib.a2b(storage_index_s)
                for sharename in filenames:
                    shnum = int(sharename)
                    filename = os.path.join(dirpath, sharename)
                    data = (client_num, storage_index, filename, shnum)
                    shares.append(data)
        if not shares:
            self.fail("unable to find any share files in %s" % basedir)
        return shares

    def _corrupt_mutable_share(self, filename, which):
        msf = storage.MutableShareFile(filename)
        datav = msf.readv([ (0, 1000000) ])
        final_share = datav[0]
        assert len(final_share) < 1000000 # ought to be truncated
        pieces = mutable.unpack_share(final_share)
        (seqnum, root_hash, IV, k, N, segsize, datalen,
         verification_key, signature, share_hash_chain, block_hash_tree,
         share_data, enc_privkey) = pieces

        if which == "seqnum":
            seqnum = seqnum + 15
        elif which == "R":
            root_hash = self.flip_bit(root_hash)
        elif which == "IV":
            IV = self.flip_bit(IV)
        elif which == "segsize":
            segsize = segsize + 15
        elif which == "pubkey":
            verification_key = self.flip_bit(verification_key)
        elif which == "signature":
            signature = self.flip_bit(signature)
        elif which == "share_hash_chain":
            nodenum = share_hash_chain.keys()[0]
            share_hash_chain[nodenum] = self.flip_bit(share_hash_chain[nodenum])
        elif which == "block_hash_tree":
            block_hash_tree[-1] = self.flip_bit(block_hash_tree[-1])
        elif which == "share_data":
            share_data = self.flip_bit(share_data)
        elif which == "encprivkey":
            enc_privkey = self.flip_bit(enc_privkey)

        prefix = mutable.pack_prefix(seqnum, root_hash, IV, k, N,
                                     segsize, datalen)
        final_share = mutable.pack_share(prefix,
                                         verification_key,
                                         signature,
                                         share_hash_chain,
                                         block_hash_tree,
                                         share_data,
                                         enc_privkey)
        msf.writev( [(0, final_share)], None)

    def test_mutable(self):
        self.basedir = "system/SystemTest/test_mutable"
        DATA = "initial contents go here."  # 25 bytes % 3 != 0
        NEWDATA = "new contents yay"
        NEWERDATA = "this is getting old"

        d = self.set_up_nodes()

        def _create_mutable(res):
            c = self.clients[0]
            log.msg("starting create_mutable_file")
            d1 = c.create_mutable_file(DATA)
            def _done(res):
                log.msg("DONE: %s" % (res,))
                self._mutable_node_1 = res
                uri = res.get_uri()
            d1.addCallback(_done)
            return d1
        d.addCallback(_create_mutable)

        def _test_debug(res):
            # find a share. It is important to run this while there is only
            # one slot in the grid.
            shares = self._find_shares(self.basedir)
            (client_num, storage_index, filename, shnum) = shares[0]
            log.msg("test_system.SystemTest.test_mutable._test_debug using %s"
                    % filename)
            log.msg(" for clients[%d]" % client_num)

            out,err = StringIO(), StringIO()
            rc = runner.runner(["dump-share",
                                filename],
                               stdout=out, stderr=err)
            output = out.getvalue()
            self.failUnlessEqual(rc, 0)
            try:
                self.failUnless("Mutable slot found:\n" in output)
                self.failUnless("share_type: SDMF\n" in output)
                peerid = idlib.nodeid_b2a(self.clients[client_num].nodeid)
                self.failUnless(" WE for nodeid: %s\n" % peerid in output)
                self.failUnless(" num_extra_leases: 0\n" in output)
                # the pubkey size can vary by a byte, so the container might
                # be a bit larger on some runs.
                m = re.search(r'^ container_size: (\d+)$', output, re.M)
                self.failUnless(m)
                container_size = int(m.group(1))
                self.failUnless(2037 <= container_size <= 2049, container_size)
                m = re.search(r'^ data_length: (\d+)$', output, re.M)
                self.failUnless(m)
                data_length = int(m.group(1))
                self.failUnless(2037 <= data_length <= 2049, data_length)
                self.failUnless("  secrets are for nodeid: %s\n" % peerid
                                in output)
                self.failUnless(" SDMF contents:\n" in output)
                self.failUnless("  seqnum: 1\n" in output)
                self.failUnless("  required_shares: 3\n" in output)
                self.failUnless("  total_shares: 10\n" in output)
                self.failUnless("  segsize: 27\n" in output, (output, filename))
                self.failUnless("  datalen: 25\n" in output)
                # the exact share_hash_chain nodes depends upon the sharenum,
                # and is more of a hassle to compute than I want to deal with
                # now
                self.failUnless("  share_hash_chain: " in output)
                self.failUnless("  block_hash_tree: 1 nodes\n" in output)
            except unittest.FailTest:
                print
                print "dump-share output was:"
                print output
                raise
        d.addCallback(_test_debug)

        # test retrieval

        # first, let's see if we can use the existing node to retrieve the
        # contents. This allows it to use the cached pubkey and maybe the
        # latest-known sharemap.

        d.addCallback(lambda res: self._mutable_node_1.download_to_data())
        def _check_download_1(res):
            self.failUnlessEqual(res, DATA)
            # now we see if we can retrieve the data from a new node,
            # constructed using the URI of the original one. We do this test
            # on the same client that uploaded the data.
            uri = self._mutable_node_1.get_uri()
            log.msg("starting retrieve1")
            newnode = self.clients[0].create_node_from_uri(uri)
            return newnode.download_to_data()
        d.addCallback(_check_download_1)

        def _check_download_2(res):
            self.failUnlessEqual(res, DATA)
            # same thing, but with a different client
            uri = self._mutable_node_1.get_uri()
            newnode = self.clients[1].create_node_from_uri(uri)
            log.msg("starting retrieve2")
            d1 = newnode.download_to_data()
            d1.addCallback(lambda res: (res, newnode))
            return d1
        d.addCallback(_check_download_2)

        def _check_download_3((res, newnode)):
            self.failUnlessEqual(res, DATA)
            # replace the data
            log.msg("starting replace1")
            d1 = newnode.replace(NEWDATA)
            d1.addCallback(lambda res: newnode.download_to_data())
            return d1
        d.addCallback(_check_download_3)

        def _check_download_4(res):
            self.failUnlessEqual(res, NEWDATA)
            # now create an even newer node and replace the data on it. This
            # new node has never been used for download before.
            uri = self._mutable_node_1.get_uri()
            newnode1 = self.clients[2].create_node_from_uri(uri)
            newnode2 = self.clients[3].create_node_from_uri(uri)
            self._newnode3 = self.clients[3].create_node_from_uri(uri)
            log.msg("starting replace2")
            d1 = newnode1.replace(NEWERDATA)
            d1.addCallback(lambda res: newnode2.download_to_data())
            return d1
        d.addCallback(_check_download_4)

        def _check_download_5(res):
            log.msg("finished replace2")
            self.failUnlessEqual(res, NEWERDATA)
        d.addCallback(_check_download_5)

        def _corrupt_shares(res):
            # run around and flip bits in all but k of the shares, to test
            # the hash checks
            shares = self._find_shares(self.basedir)
            ## sort by share number
            #shares.sort( lambda a,b: cmp(a[3], b[3]) )
            where = dict([ (shnum, filename)
                           for (client_num, storage_index, filename, shnum)
                           in shares ])
            assert len(where) == 10 # this test is designed for 3-of-10
            for shnum, filename in where.items():
                # shares 7,8,9 are left alone. read will check
                # (share_hash_chain, block_hash_tree, share_data). New
                # seqnum+R pairs will trigger a check of (seqnum, R, IV,
                # segsize, signature).
                if shnum == 0:
                    # read: this will trigger "pubkey doesn't match
                    # fingerprint".
                    self._corrupt_mutable_share(filename, "pubkey")
                    self._corrupt_mutable_share(filename, "encprivkey")
                elif shnum == 1:
                    # triggers "signature is invalid"
                    self._corrupt_mutable_share(filename, "seqnum")
                elif shnum == 2:
                    # triggers "signature is invalid"
                    self._corrupt_mutable_share(filename, "R")
                elif shnum == 3:
                    # triggers "signature is invalid"
                    self._corrupt_mutable_share(filename, "segsize")
                elif shnum == 4:
                    self._corrupt_mutable_share(filename, "share_hash_chain")
                elif shnum == 5:
                    self._corrupt_mutable_share(filename, "block_hash_tree")
                elif shnum == 6:
                    self._corrupt_mutable_share(filename, "share_data")
                # other things to correct: IV, signature
                # 7,8,9 are left alone

                # note that initial_query_count=5 means that we'll hit the
                # first 5 servers in effectively random order (based upon
                # response time), so we won't necessarily ever get a "pubkey
                # doesn't match fingerprint" error (if we hit shnum>=1 before
                # shnum=0, we pull the pubkey from there). To get repeatable
                # specific failures, we need to set initial_query_count=1,
                # but of course that will change the sequencing behavior of
                # the retrieval process. TODO: find a reasonable way to make
                # this a parameter, probably when we expand this test to test
                # for one failure mode at a time.

                # when we retrieve this, we should get three signature
                # failures (where we've mangled seqnum, R, and segsize). The
                # pubkey mangling
        d.addCallback(_corrupt_shares)

        d.addCallback(lambda res: self._newnode3.download_to_data())
        d.addCallback(_check_download_5)

        def _check_empty_file(res):
            # make sure we can create empty files, this usually screws up the
            # segsize math
            d1 = self.clients[2].create_mutable_file("")
            d1.addCallback(lambda newnode: newnode.download_to_data())
            d1.addCallback(lambda res: self.failUnlessEqual("", res))
            return d1
        d.addCallback(_check_empty_file)

        d.addCallback(lambda res: self.clients[0].create_empty_dirnode())
        def _created_dirnode(dnode):
            log.msg("_created_dirnode(%s)" % (dnode,))
            d1 = dnode.list()
            d1.addCallback(lambda children: self.failUnlessEqual(children, {}))
            d1.addCallback(lambda res: dnode.has_child("edgar"))
            d1.addCallback(lambda answer: self.failUnlessEqual(answer, False))
            d1.addCallback(lambda res: dnode.set_node("see recursive", dnode))
            d1.addCallback(lambda res: dnode.has_child("see recursive"))
            d1.addCallback(lambda answer: self.failUnlessEqual(answer, True))
            d1.addCallback(lambda res: dnode.build_manifest())
            d1.addCallback(lambda manifest:
                           self.failUnlessEqual(len(manifest), 1))
            return d1
        d.addCallback(_created_dirnode)

        return d
    # The default 120 second timeout went off when running it under valgrind
    # on my old Windows laptop, so I'm bumping up the timeout.
    test_mutable.timeout = 240

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
        d = self.set_up_nodes(createprivdir=True)
        d.addCallback(self.log, "starting publish")
        d.addCallback(self._do_publish1)
        d.addCallback(self._test_runner)
        d.addCallback(self._do_publish2)
        # at this point, we have the following filesystem (where "R" denotes
        # self._root_directory_uri):
        # R
        # R/subdir1
        # R/subdir1/mydata567
        # R/subdir1/subdir2/
        # R/subdir1/subdir2/mydata992

        d.addCallback(self._bounce_client0)
        d.addCallback(self.log, "bounced client0")

        d.addCallback(self._check_publish1)
        d.addCallback(self.log, "did _check_publish1")
        d.addCallback(self._check_publish2)
        d.addCallback(self.log, "did _check_publish2")
        d.addCallback(self._do_publish_private)
        d.addCallback(self.log, "did _do_publish_private")
        # now we also have (where "P" denotes a new dir):
        #  P/personal/sekrit data
        #  P/s2-rw -> /subdir1/subdir2/
        #  P/s2-ro -> /subdir1/subdir2/ (read-only)
        d.addCallback(self._check_publish_private)
        d.addCallback(self.log, "did _check_publish_private")
        d.addCallback(self._test_web)
        d.addCallback(self._test_control)
        d.addCallback(self._test_cli)
        # P now has four top-level children:
        # P/personal/sekrit data
        # P/s2-ro/
        # P/s2-rw/
        # P/test_put/  (empty)
        d.addCallback(self._test_checker)
        d.addCallback(self._test_verifier)
        return d
    test_vdrive.timeout = 1100

    def _do_publish1(self, res):
        ut = upload.Data(self.data)
        c0 = self.clients[0]
        d = c0.create_empty_dirnode()
        def _made_root(new_dirnode):
            self._root_directory_uri = new_dirnode.get_uri()
            return c0.create_node_from_uri(self._root_directory_uri)
        d.addCallback(_made_root)
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

    def log(self, res, msg, **kwargs):
        # print "MSG: %s  RES: %s" % (msg, res)
        log.msg(msg, **kwargs)
        return res

    def stall(self, res, delay=1.0):
        d = defer.Deferred()
        reactor.callLater(delay, d.callback, res)
        return d

    def _do_publish_private(self, res):
        self.smalldata = "sssh, very secret stuff"
        ut = upload.Data(self.smalldata)
        d = self.clients[0].create_empty_dirnode()
        d.addCallback(self.log, "GOT private directory")
        def _got_new_dir(privnode):
            rootnode = self.clients[0].create_node_from_uri(self._root_directory_uri)
            d1 = privnode.create_empty_directory("personal")
            d1.addCallback(self.log, "made P/personal")
            d1.addCallback(lambda node: node.add_file("sekrit data", ut))
            d1.addCallback(self.log, "made P/personal/sekrit data")
            d1.addCallback(lambda res: rootnode.get_child_at_path(["subdir1", "subdir2"]))
            def _got_s2(s2node):
                d2 = privnode.set_uri("s2-rw", s2node.get_uri())
                d2.addCallback(lambda node: privnode.set_uri("s2-ro", s2node.get_readonly_uri()))
                return d2
            d1.addCallback(_got_s2)
            d1.addCallback(lambda res: privnode)
            return d1
        d.addCallback(_got_new_dir)
        return d

    def _check_publish1(self, res):
        # this one uses the iterative API
        c1 = self.clients[1]
        d = defer.succeed(c1.create_node_from_uri(self._root_directory_uri))
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
        rootnode = self.clients[1].create_node_from_uri(self._root_directory_uri)
        d = rootnode.get_child_at_path("subdir1")
        d.addCallback(lambda dirnode:
                      self.failUnless(IDirectoryNode.providedBy(dirnode)))
        d.addCallback(lambda res: rootnode.get_child_at_path("subdir1/mydata567"))
        d.addCallback(lambda filenode: filenode.download_to_data())
        d.addCallback(lambda data: self.failUnlessEqual(data, self.data))

        d.addCallback(lambda res: rootnode.get_child_at_path("subdir1/mydata567"))
        def _got_filenode(filenode):
            fnode = self.clients[1].create_node_from_uri(filenode.get_uri())
            assert fnode == filenode
        d.addCallback(_got_filenode)
        return d

    def _check_publish_private(self, resnode):
        # this one uses the path-based API
        self._private_node = resnode

        d = self._private_node.get_child_at_path("personal")
        def _got_personal(personal):
            self._personal_node = personal
            return personal
        d.addCallback(_got_personal)

        d.addCallback(lambda dirnode:
                      self.failUnless(IDirectoryNode.providedBy(dirnode), dirnode))
        def get_path(path):
            return self._private_node.get_child_at_path(path)

        d.addCallback(lambda res: get_path("personal/sekrit data"))
        d.addCallback(lambda filenode: filenode.download_to_data())
        d.addCallback(lambda data: self.failUnlessEqual(data, self.smalldata))
        d.addCallback(lambda res: get_path("s2-rw"))
        d.addCallback(lambda dirnode: self.failUnless(dirnode.is_mutable()))
        d.addCallback(lambda res: get_path("s2-ro"))
        def _got_s2ro(dirnode):
            self.failUnless(dirnode.is_mutable(), dirnode)
            self.failUnless(dirnode.is_readonly(), dirnode)
            d1 = defer.succeed(None)
            d1.addCallback(lambda res: dirnode.list())
            d1.addCallback(self.log, "dirnode.list")

            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "mkdir(nope)", None, dirnode.create_empty_directory, "nope"))

            d1.addCallback(self.log, "doing add_file(ro)")
            ut = upload.Data("I will disappear, unrecorded and unobserved. The tragedy of my demise is made more poignant by its silence, but this beauty is not for you to ever know.")
            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "add_file(nope)", None, dirnode.add_file, "hope", ut))

            d1.addCallback(self.log, "doing get(ro)")
            d1.addCallback(lambda res: dirnode.get("mydata992"))
            d1.addCallback(lambda filenode:
                           self.failUnless(IFileNode.providedBy(filenode)))

            d1.addCallback(self.log, "doing delete(ro)")
            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "delete(nope)", None, dirnode.delete, "mydata992"))

            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "set_uri(nope)", None, dirnode.set_uri, "hopeless", self.uri))

            d1.addCallback(lambda res: self.shouldFail2(KeyError, "get(missing)", "'missing'", dirnode.get, "missing"))

            personal = self._personal_node
            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "mv from readonly", None, dirnode.move_child_to, "mydata992", personal, "nope"))

            d1.addCallback(self.log, "doing move_child_to(ro)2")
            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "mv to readonly", None, personal.move_child_to, "sekrit data", dirnode, "nope"))

            d1.addCallback(self.log, "finished with _got_s2ro")
            return d1
        d.addCallback(_got_s2ro)
        def _got_home(dummy):
            home = self._private_node
            personal = self._personal_node
            d1 = defer.succeed(None)
            d1.addCallback(self.log, "mv 'P/personal/sekrit data' to P/sekrit")
            d1.addCallback(lambda res:
                           personal.move_child_to("sekrit data",home,"sekrit"))

            d1.addCallback(self.log, "mv P/sekrit 'P/sekrit data'")
            d1.addCallback(lambda res:
                           home.move_child_to("sekrit", home, "sekrit data"))

            d1.addCallback(self.log, "mv 'P/sekret data' P/personal/")
            d1.addCallback(lambda res:
                           home.move_child_to("sekrit data", personal))

            d1.addCallback(lambda res: home.build_manifest())
            d1.addCallback(self.log, "manifest")
            #  four items:
            # P/personal/
            # P/personal/sekrit data
            # P/s2-rw  (same as P/s2-ro)
            # P/s2-rw/mydata992 (same as P/s2-rw/mydata992)
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

    def shouldFail2(self, expected_failure, which, substring, callable, *args, **kwargs):
        assert substring is None or isinstance(substring, str)
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnless(substring in str(res),
                                    "substring '%s' not in '%s'"
                                    % (substring, str(res)))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d

    def PUT(self, urlpath, data):
        url = self.webish_url + urlpath
        return getPage(url, method="PUT", postdata=data)

    def GET(self, urlpath, followRedirect=False):
        url = self.webish_url + urlpath
        return getPage(url, method="GET", followRedirect=followRedirect)

    def _test_web(self, res):
        base = self.webish_url
        public = "uri/" + self._root_directory_uri
        d = getPage(base)
        def _got_welcome(page):
            expected = "Connected Storage Servers: <span>%d</span>" % (self.numclients)
            self.failUnless(expected in page,
                            "I didn't see the right 'connected storage servers'"
                            " message in: %s" % page
                            )
            expected = "My nodeid: <span>%s</span>" % (b32encode(self.clients[0].nodeid).lower(),)
            self.failUnless(expected in page,
                            "I didn't see the right 'My nodeid' message "
                            "in: %s" % page)
        d.addCallback(_got_welcome)
        d.addCallback(self.log, "done with _got_welcome")
        d.addCallback(lambda res: getPage(base + public))
        d.addCallback(lambda res: getPage(base + public + "/subdir1"))
        def _got_subdir1(page):
            # there ought to be an href for our file
            self.failUnless(("<td>%d</td>" % len(self.data)) in page)
            self.failUnless(">mydata567</a>" in page)
        d.addCallback(_got_subdir1)
        d.addCallback(self.log, "done with _got_subdir1")
        d.addCallback(lambda res:
                      getPage(base + public + "/subdir1/mydata567"))
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
        d.addCallback(_got_from_uri)

        # download from a bogus URI, make sure we get a reasonable error
        d.addCallback(self.log, "_get_from_bogus_uri", level=log.UNUSUAL)
        def _get_from_bogus_uri(res):
            d1 = getPage(base + "uri/%s?filename=%s"
                         % (self.mangle_uri(self.uri), "mydata567"))
            d1.addBoth(self.shouldFail, Error, "downloading bogus URI",
                       "410")
            return d1
        d.addCallback(_get_from_bogus_uri)
        d.addCallback(self.log, "_got_from_bogus_uri", level=log.UNUSUAL)

        # upload a file with PUT
        d.addCallback(self.log, "about to try PUT")
        d.addCallback(lambda res: self.PUT(public + "/subdir3/new.txt",
                                           "new.txt contents"))
        d.addCallback(lambda res: self.GET(public + "/subdir3/new.txt"))
        d.addCallback(self.failUnlessEqual, "new.txt contents")
        # and again with something large enough to use multiple segments,
        # and hopefully trigger pauseProducing too
        d.addCallback(lambda res: self.PUT(public + "/subdir3/big.txt",
                                           "big" * 500000)) # 1.5MB
        d.addCallback(lambda res: self.GET(public + "/subdir3/big.txt"))
        d.addCallback(lambda res: self.failUnlessEqual(len(res), 1500000))

        # can we replace files in place?
        d.addCallback(lambda res: self.PUT(public + "/subdir3/new.txt",
                                           "NEWER contents"))
        d.addCallback(lambda res: self.GET(public + "/subdir3/new.txt"))
        d.addCallback(self.failUnlessEqual, "NEWER contents")


        # TODO: mangle the second segment of a file, to test errors that
        # occur after we've already sent some good data, which uses a
        # different error path.

        # TODO: download a URI with a form
        # TODO: create a directory by using a form
        # TODO: upload by using a form on the directory page
        #    url = base + "somedir/subdir1/freeform_post!!upload"
        # TODO: delete a file by using a button on the directory page

        return d

    def _test_runner(self, res):
        # exercise some of the diagnostic tools in runner.py

        # find a share
        for (dirpath, dirnames, filenames) in os.walk(self.basedir):
            if "storage" not in dirpath:
                continue
            if not filenames:
                continue
            pieces = dirpath.split(os.sep)
            if pieces[-4] == "storage" and pieces[-3] == "shares":
                # we're sitting in .../storage/shares/$START/$SINDEX , and there
                # are sharefiles here
                filename = os.path.join(dirpath, filenames[0])
                # peek at the magic to see if it is a chk share
                magic = open(filename, "rb").read(4)
                if magic == '\x00\x00\x00\x01':
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
        self.failUnless("segment_size: %d\n" % mathutil.next_multiple(len(self.data), 3) in output)
        self.failUnless("total_shares: 10\n" in output)
        # keys which are supposed to be present
        for key in ("size", "num_segments", "segment_size",
                    "needed_shares", "total_shares",
                    "codec_name", "codec_params", "tail_codec_params",
                    "plaintext_hash", "plaintext_root_hash",
                    "crypttext_hash", "crypttext_root_hash",
                    "share_root_hash",):
            self.failUnless("%s: " % key in output, key)

    def _test_control(self, res):
        # exercise the remote-control-the-client foolscap interfaces in
        # allmydata.control (mostly used for performance tests)
        c0 = self.clients[0]
        control_furl_file = os.path.join(c0.basedir, "private", "control.furl")
        control_furl = open(control_furl_file, "r").read().strip()
        # it doesn't really matter which Tub we use to connect to the client,
        # so let's just use our IntroducerNode's
        d = self.introducer.tub.getReference(control_furl)
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
        d.addCallback(lambda res: rref.callRemote("speed_test", 1, 200, False))
        if sys.platform == "linux2":
            d.addCallback(lambda res: rref.callRemote("get_memory_usage"))
        d.addCallback(lambda res: rref.callRemote("measure_peer_response_time"))
        return d

    def _test_cli(self, res):
        # run various CLI commands (in a thread, since they use blocking
        # network calls)

        private_uri = self._private_node.get_uri()
        some_uri = self._root_directory_uri
        client0_basedir = self.getdir("client0")

        nodeargs = [
            "--node-directory", client0_basedir,
            "--dir-cap", private_uri,
            ]
        public_nodeargs = [
            "--node-url", self.webish_url,
            "--dir-cap", some_uri,
            ]
        TESTDATA = "I will not write the same thing over and over.\n" * 100

        d = defer.succeed(None)

        def _ls_root(res):
            argv = ["ls"] + nodeargs
            return self._run_cli(argv)
        d.addCallback(_ls_root)
        def _check_ls_root((out,err)):
            self.failUnless("personal" in out)
            self.failUnless("s2-ro" in out)
            self.failUnless("s2-rw" in out)
            self.failUnlessEqual(err, "")
        d.addCallback(_check_ls_root)

        def _ls_subdir(res):
            argv = ["ls"] + nodeargs + ["personal"]
            return self._run_cli(argv)
        d.addCallback(_ls_subdir)
        def _check_ls_subdir((out,err)):
            self.failUnless("sekrit data" in out)
            self.failUnlessEqual(err, "")
        d.addCallback(_check_ls_subdir)

        def _ls_public_subdir(res):
            argv = ["ls"] + public_nodeargs + ["subdir1"]
            return self._run_cli(argv)
        d.addCallback(_ls_public_subdir)
        def _check_ls_public_subdir((out,err)):
            self.failUnless("subdir2" in out)
            self.failUnless("mydata567" in out)
            self.failUnlessEqual(err, "")
        d.addCallback(_check_ls_public_subdir)

        def _ls_file(res):
            argv = ["ls"] + public_nodeargs + ["subdir1/mydata567"]
            return self._run_cli(argv)
        d.addCallback(_ls_file)
        def _check_ls_file((out,err)):
            self.failUnlessEqual(out.strip(), "112 subdir1/mydata567")
            self.failUnlessEqual(err, "")
        d.addCallback(_check_ls_file)

        # tahoe_ls doesn't currently handle the error correctly: it tries to
        # JSON-parse a traceback.
##         def _ls_missing(res):
##             argv = ["ls"] + nodeargs + ["bogus"]
##             return self._run_cli(argv)
##         d.addCallback(_ls_missing)
##         def _check_ls_missing((out,err)):
##             print "OUT", out
##             print "ERR", err
##             self.failUnlessEqual(err, "")
##         d.addCallback(_check_ls_missing)

        def _put(res):
            tdir = self.getdir("cli_put")
            fileutil.make_dirs(tdir)
            fn = os.path.join(tdir, "upload_me")
            f = open(fn, "wb")
            f.write(TESTDATA)
            f.close()
            argv = ["put"] + nodeargs + [fn, "test_put/upload.txt"]
            return self._run_cli(argv)
        d.addCallback(_put)
        def _check_put((out,err)):
            self.failUnless("200 OK" in out)
            self.failUnlessEqual(err, "")
            d = self._private_node.get_child_at_path("test_put/upload.txt")
            d.addCallback(lambda filenode: filenode.download_to_data())
            def _check_put2(res):
                self.failUnlessEqual(res, TESTDATA)
            d.addCallback(_check_put2)
            return d
        d.addCallback(_check_put)

        def _get_to_stdout(res):
            argv = ["get"] + nodeargs + ["test_put/upload.txt"]
            return self._run_cli(argv)
        d.addCallback(_get_to_stdout)
        def _check_get_to_stdout((out,err)):
            self.failUnlessEqual(out, TESTDATA)
            self.failUnlessEqual(err, "")
        d.addCallback(_check_get_to_stdout)

        get_to_file_target = self.basedir + "/get.downfile"
        def _get_to_file(res):
            argv = ["get"] + nodeargs + ["test_put/upload.txt",
                                         get_to_file_target]
            return self._run_cli(argv)
        d.addCallback(_get_to_file)
        def _check_get_to_file((out,err)):
            data = open(get_to_file_target, "rb").read()
            self.failUnlessEqual(data, TESTDATA)
            self.failUnlessEqual(out, "")
            self.failUnlessEqual(err, "test_put/upload.txt retrieved and written to system/SystemTest/test_vdrive/get.downfile\n")
        d.addCallback(_check_get_to_file)


        def _mv(res):
            argv = ["mv"] + nodeargs + ["test_put/upload.txt",
                                        "test_put/moved.txt"]
            return self._run_cli(argv)
        d.addCallback(_mv)
        def _check_mv((out,err)):
            self.failUnless("OK" in out)
            self.failUnlessEqual(err, "")
            d = self.shouldFail2(KeyError, "test_cli._check_rm", "'upload.txt'", self._private_node.get_child_at_path, "test_put/upload.txt")

            d.addCallback(lambda res:
                          self._private_node.get_child_at_path("test_put/moved.txt"))
            d.addCallback(lambda filenode: filenode.download_to_data())
            def _check_mv2(res):
                self.failUnlessEqual(res, TESTDATA)
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
            d = self.shouldFail2(KeyError, "test_cli._check_rm", "'moved.txt'", self._private_node.get_child_at_path, "test_put/moved.txt")
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
        d = self._private_node.build_manifest()
        d.addCallback(self._test_checker_2)
        return d

    def _test_checker_2(self, manifest):
        checker1 = self.clients[1].getServiceNamed("checker")
        self.failUnlessEqual(checker1.checker_results_for(None), [])
        self.failUnlessEqual(checker1.checker_results_for(list(manifest)[0]),
                             [])
        dl = []
        starting_time = time.time()
        for si in manifest:
            dl.append(checker1.check(si))
        d = deferredutil.DeferredListShouldSucceed(dl)

        def _check_checker_results(res):
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
                    self.failUnlessEqual(len(peers), self.numclients)
        d.addCallback(_check_checker_results)

        def _check_stored_results(res):
            finish_time = time.time()
            all_results = []
            for si in manifest:
                results = checker1.checker_results_for(si)
                if not results:
                    # TODO: implement checker for mutable files and implement tests of that checker
                    continue
                self.failUnlessEqual(len(results), 1)
                when, those_results = results[0]
                self.failUnless(isinstance(when, (int, float)))
                self.failUnless(starting_time <= when <= finish_time)
                all_results.append(those_results)
            _check_checker_results(all_results)
        d.addCallback(_check_stored_results)

        d.addCallback(self._test_checker_3)
        return d

    def _test_checker_3(self, res):
        # check one file, through FileNode.check()
        d = self._private_node.get_child_at_path("personal/sekrit data")
        d.addCallback(lambda n: n.check())
        def _checked(results):
            # 'sekrit data' is small, and fits in a LiteralFileNode, so
            # checking it is trivial and always returns True
            self.failUnlessEqual(results, True)
        d.addCallback(_checked)

        c0 = self.clients[1]
        n = c0.create_node_from_uri(self._root_directory_uri)
        d.addCallback(lambda res: n.get_child_at_path("subdir1/mydata567"))
        d.addCallback(lambda n: n.check())
        def _checked2(results):
            # mydata567 is large and lives in a CHK
            (needed, total, found, sharemap) = results
            self.failUnlessEqual(needed, 3)
            self.failUnlessEqual(total, 10)
            self.failUnlessEqual(found, 10)
            self.failUnlessEqual(len(sharemap), 10)
            for shnum in range(10):
                self.failUnlessEqual(len(sharemap[shnum]), 1)
        d.addCallback(_checked2)
        return d


    def _test_verifier(self, res):
        checker1 = self.clients[1].getServiceNamed("checker")
        d = self._private_node.build_manifest()
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
        d.addCallback(lambda res: checker1.verify(None))
        d.addCallback(self.failUnlessEqual, True)
        return d
