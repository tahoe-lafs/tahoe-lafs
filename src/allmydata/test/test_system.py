from base64 import b32encode
import os, sys, time, re, simplejson, urllib
from cStringIO import StringIO
from zope.interface import implements
from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet import threads # CLI tests use deferToThread
from twisted.internet.error import ConnectionDone, ConnectionLost
from twisted.internet.interfaces import IConsumer, IPushProducer
import allmydata
from allmydata import uri, storage, offloaded
from allmydata.immutable import download, upload, filenode
from allmydata.util import idlib, mathutil
from allmydata.util import log, base32
from allmydata.scripts import runner
from allmydata.interfaces import IDirectoryNode, IFileNode, IFileURI, \
     ICheckerResults, ICheckAndRepairResults, IDeepCheckResults, \
     IDeepCheckAndRepairResults, NoSuchChildError, NotEnoughSharesError
from allmydata.monitor import Monitor, OperationCancelledError
from allmydata.mutable.common import NotMutableError
from allmydata.mutable import layout as mutable_layout
from foolscap import DeadReferenceError
from twisted.python.failure import Failure
from twisted.web.client import getPage
from twisted.web.error import Error

from allmydata.test.common import SystemTestMixin, ErrorMixin, \
     MemoryConsumer, download_to_data

LARGE_DATA = """
This is some data to publish to the virtual drive, which needs to be large
enough to not fit inside a LIT uri.
"""

class CountingDataUploadable(upload.Data):
    bytes_read = 0
    interrupt_after = None
    interrupt_after_d = None

    def read(self, length):
        self.bytes_read += length
        if self.interrupt_after is not None:
            if self.bytes_read > self.interrupt_after:
                self.interrupt_after = None
                self.interrupt_after_d.callback(self)
        return upload.Data.read(self, length)

class GrabEverythingConsumer:
    implements(IConsumer)

    def __init__(self):
        self.contents = ""

    def registerProducer(self, producer, streaming):
        assert streaming
        assert IPushProducer.providedBy(producer)

    def write(self, data):
        self.contents += data

    def unregisterProducer(self):
        pass

class SystemTest(SystemTestMixin, unittest.TestCase):

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
        self.basedir = "system/SystemTest/test_upload_and_download_random_key"
        return self._test_upload_and_download(convergence=None)
    test_upload_and_download_random_key.timeout = 4800

    def test_upload_and_download_convergent(self):
        self.basedir = "system/SystemTest/test_upload_and_download_convergent"
        return self._test_upload_and_download(convergence="some convergence string")
    test_upload_and_download_convergent.timeout = 4800

    def _test_upload_and_download(self, convergence):
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
            up = upload.Data(DATA, convergence=convergence)
            up.max_segment_size = 1024
            d1 = u.upload(up)
            return d1
        d.addCallback(_do_upload)
        def _upload_done(results):
            theuri = results.uri
            log.msg("upload finished: uri is %s" % (theuri,))
            self.uri = theuri
            assert isinstance(self.uri, str), self.uri
            dl = self.clients[1].getServiceNamed("downloader")
            self.downloader = dl
        d.addCallback(_upload_done)

        def _upload_again(res):
            # Upload again. If using convergent encryption then this ought to be
            # short-circuited, however with the way we currently generate URIs
            # (i.e. because they include the roothash), we have to do all of the
            # encoding work, and only get to save on the upload part.
            log.msg("UPLOADING AGAIN")
            up = upload.Data(DATA, convergence=convergence)
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

        consumer = GrabEverythingConsumer()
        ct = download.ConsumerAdapter(consumer)
        d.addCallback(lambda res:
                      self.downloader.download(self.uri, ct))
        def _download_to_consumer_done(ign):
            self.failUnlessEqual(consumer.contents, DATA)
        d.addCallback(_download_to_consumer_done)

        def _test_read(res):
            n = self.clients[1].create_node_from_uri(self.uri)
            d = download_to_data(n)
            def _read_done(data):
                self.failUnlessEqual(data, DATA)
            d.addCallback(_read_done)
            d.addCallback(lambda ign:
                          n.read(MemoryConsumer(), offset=1, size=4))
            def _read_portion_done(mc):
                self.failUnlessEqual("".join(mc.chunks), DATA[1:1+4])
            d.addCallback(_read_portion_done)
            d.addCallback(lambda ign:
                          n.read(MemoryConsumer(), offset=2, size=None))
            def _read_tail_done(mc):
                self.failUnlessEqual("".join(mc.chunks), DATA[2:])
            d.addCallback(_read_tail_done)
            d.addCallback(lambda ign:
                          n.read(MemoryConsumer(), size=len(DATA)+1000))
            def _read_too_much(mc):
                self.failUnlessEqual("".join(mc.chunks), DATA)
            d.addCallback(_read_too_much)

            return d
        d.addCallback(_test_read)

        def _test_bad_read(res):
            bad_u = uri.from_string_filenode(self.uri)
            bad_u.key = self.flip_bit(bad_u.key)
            bad_n = self.clients[1].create_node_from_uri(bad_u.to_string())
            # this should cause an error during download

            d = self.shouldFail2(NotEnoughSharesError, "'download bad node'",
                                 None,
                                 bad_n.read, MemoryConsumer(), offset=2)
            return d
        d.addCallback(_test_bad_read)

        def _download_nonexistent_uri(res):
            baduri = self.mangle_uri(self.uri)
            log.msg("about to download non-existent URI", level=log.UNUSUAL,
                    facility="tahoe.tests")
            d1 = self.downloader.download_to_data(baduri)
            def _baduri_should_fail(res):
                log.msg("finished downloading non-existend URI",
                        level=log.UNUSUAL, facility="tahoe.tests")
                self.failUnless(isinstance(res, Failure))
                self.failUnless(res.check(NotEnoughSharesError),
                                "expected NotEnoughSharesError, got %s" % res)
                # TODO: files that have zero peers should get a special kind
                # of NotEnoughSharesError, which can be used to suggest that
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
        d.addCallback(_added)

        HELPER_DATA = "Data that needs help to upload" * 1000
        def _upload_with_helper(res):
            u = upload.Data(HELPER_DATA, convergence=convergence)
            d = self.extra_node.upload(u)
            def _uploaded(results):
                uri = results.uri
                return self.downloader.download_to_data(uri)
            d.addCallback(_uploaded)
            def _check(newdata):
                self.failUnlessEqual(newdata, HELPER_DATA)
            d.addCallback(_check)
            return d
        d.addCallback(_upload_with_helper)

        def _upload_duplicate_with_helper(res):
            u = upload.Data(HELPER_DATA, convergence=convergence)
            u.debug_stash_RemoteEncryptedUploadable = True
            d = self.extra_node.upload(u)
            def _uploaded(results):
                uri = results.uri
                return self.downloader.download_to_data(uri)
            d.addCallback(_uploaded)
            def _check(newdata):
                self.failUnlessEqual(newdata, HELPER_DATA)
                self.failIf(hasattr(u, "debug_RemoteEncryptedUploadable"),
                            "uploadable started uploading, should have been avoided")
            d.addCallback(_check)
            return d
        if convergence is not None:
            d.addCallback(_upload_duplicate_with_helper)

        def _upload_resumable(res):
            DATA = "Data that needs help to upload and gets interrupted" * 1000
            u1 = CountingDataUploadable(DATA, convergence=convergence)
            u2 = CountingDataUploadable(DATA, convergence=convergence)

            # we interrupt the connection after about 5kB by shutting down
            # the helper, then restartingit.
            u1.interrupt_after = 5000
            u1.interrupt_after_d = defer.Deferred()
            u1.interrupt_after_d.addCallback(lambda res:
                                             self.bounce_client(0))

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

                # make sure we actually interrupted it before finishing the
                # file
                self.failUnless(u1.bytes_read < len(DATA),
                                "read %d out of %d total" % (u1.bytes_read,
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
                u1.interrupt_after_d.addCallback(self.stall, 2.0)
                return u1.interrupt_after_d
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

            # then we need to give the reconnector a chance to
            # reestablish the connection to the helper.
            d.addCallback(lambda res:
                          log.msg("wait_for_connections", level=log.NOISY,
                                  facility="tahoe.test.test_system"))
            d.addCallback(lambda res: self.wait_for_connections())


            d.addCallback(lambda res:
                          log.msg("uploading again", level=log.NOISY,
                                  facility="tahoe.test.test_system"))
            d.addCallback(lambda res: self.extra_node.upload(u2))

            def _uploaded(results):
                uri = results.uri
                log.msg("Second upload complete", level=log.NOISY,
                        facility="tahoe.test.test_system")

                # this is really bytes received rather than sent, but it's
                # convenient and basically measures the same thing
                bytes_sent = results.ciphertext_fetched

                # We currently don't support resumption of upload if the data is
                # encrypted with a random key.  (Because that would require us
                # to store the key locally and re-use it on the next upload of
                # this file, which isn't a bad thing to do, but we currently
                # don't do it.)
                if convergence is not None:
                    # Make sure we did not have to read the whole file the
                    # second time around .
                    self.failUnless(bytes_sent < len(DATA),
                                "resumption didn't save us any work:"
                                " read %d bytes out of %d total" %
                                (bytes_sent, len(DATA)))
                else:
                    # Make sure we did have to read the whole file the second
                    # time around -- because the one that we partially uploaded
                    # earlier was encrypted with a different random key.
                    self.failIf(bytes_sent < len(DATA),
                                "resumption saved us some work even though we were using random keys:"
                                " read %d bytes out of %d total" %
                                (bytes_sent, len(DATA)))
                return self.downloader.download_to_data(uri)
            d.addCallback(_uploaded)

            def _check(newdata):
                self.failUnlessEqual(newdata, DATA)
                # If using convergent encryption, then also check that the
                # helper has removed the temp file from its directories.
                if convergence is not None:
                    basedir = os.path.join(self.getdir("client0"), "helper")
                    files = os.listdir(os.path.join(basedir, "CHK_encoding"))
                    self.failUnlessEqual(files, [])
                    files = os.listdir(os.path.join(basedir, "CHK_incoming"))
                    self.failUnlessEqual(files, [])
            d.addCallback(_check)
            return d
        d.addCallback(_upload_resumable)

        def _grab_stats(ignored):
            # the StatsProvider doesn't normally publish a FURL:
            # instead it passes a live reference to the StatsGatherer
            # (if and when it connects). To exercise the remote stats
            # interface, we manually publish client0's StatsProvider
            # and use client1 to query it.
            sp = self.clients[0].stats_provider
            sp_furl = self.clients[0].tub.registerReference(sp)
            d = self.clients[1].tub.getReference(sp_furl)
            d.addCallback(lambda sp_rref: sp_rref.callRemote("get_stats"))
            def _got_stats(stats):
                #print "STATS"
                #from pprint import pprint
                #pprint(stats)
                s = stats["stats"]
                self.failUnlessEqual(s["storage_server.accepting_immutable_shares"], 1)
                c = stats["counters"]
                self.failUnless("storage_server.allocate" in c)
            d.addCallback(_got_stats)
            return d
        d.addCallback(_grab_stats)

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
                storage_index = storage.si_a2b(storage_index_s)
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
        pieces = mutable_layout.unpack_share(final_share)
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

        prefix = mutable_layout.pack_prefix(seqnum, root_hash, IV, k, N,
                                            segsize, datalen)
        final_share = mutable_layout.pack_share(prefix,
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

        d = self.set_up_nodes(use_key_generator=True)

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
            rc = runner.runner(["debug", "dump-share", "--offsets",
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
                expected = ("  verify-cap: URI:SSK-Verifier:%s:" %
                            base32.b2a(storage_index))
                self.failUnless(expected in output)
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

        d.addCallback(lambda res: self._mutable_node_1.download_best_version())
        def _check_download_1(res):
            self.failUnlessEqual(res, DATA)
            # now we see if we can retrieve the data from a new node,
            # constructed using the URI of the original one. We do this test
            # on the same client that uploaded the data.
            uri = self._mutable_node_1.get_uri()
            log.msg("starting retrieve1")
            newnode = self.clients[0].create_node_from_uri(uri)
            newnode_2 = self.clients[0].create_node_from_uri(uri)
            self.failUnlessIdentical(newnode, newnode_2)
            return newnode.download_best_version()
        d.addCallback(_check_download_1)

        def _check_download_2(res):
            self.failUnlessEqual(res, DATA)
            # same thing, but with a different client
            uri = self._mutable_node_1.get_uri()
            newnode = self.clients[1].create_node_from_uri(uri)
            log.msg("starting retrieve2")
            d1 = newnode.download_best_version()
            d1.addCallback(lambda res: (res, newnode))
            return d1
        d.addCallback(_check_download_2)

        def _check_download_3((res, newnode)):
            self.failUnlessEqual(res, DATA)
            # replace the data
            log.msg("starting replace1")
            d1 = newnode.overwrite(NEWDATA)
            d1.addCallback(lambda res: newnode.download_best_version())
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
            d1 = newnode1.overwrite(NEWERDATA)
            d1.addCallback(lambda res: newnode2.download_best_version())
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

        d.addCallback(lambda res: self._newnode3.download_best_version())
        d.addCallback(_check_download_5)

        def _check_empty_file(res):
            # make sure we can create empty files, this usually screws up the
            # segsize math
            d1 = self.clients[2].create_mutable_file("")
            d1.addCallback(lambda newnode: newnode.download_best_version())
            d1.addCallback(lambda res: self.failUnlessEqual("", res))
            return d1
        d.addCallback(_check_empty_file)

        d.addCallback(lambda res: self.clients[0].create_empty_dirnode())
        def _created_dirnode(dnode):
            log.msg("_created_dirnode(%s)" % (dnode,))
            d1 = dnode.list()
            d1.addCallback(lambda children: self.failUnlessEqual(children, {}))
            d1.addCallback(lambda res: dnode.has_child(u"edgar"))
            d1.addCallback(lambda answer: self.failUnlessEqual(answer, False))
            d1.addCallback(lambda res: dnode.set_node(u"see recursive", dnode))
            d1.addCallback(lambda res: dnode.has_child(u"see recursive"))
            d1.addCallback(lambda answer: self.failUnlessEqual(answer, True))
            d1.addCallback(lambda res: dnode.build_manifest().when_done())
            d1.addCallback(lambda res:
                           self.failUnlessEqual(len(res["manifest"]), 1))
            return d1
        d.addCallback(_created_dirnode)

        def wait_for_c3_kg_conn():
            return self.clients[3]._key_generator is not None
        d.addCallback(lambda junk: self.poll(wait_for_c3_kg_conn))

        def check_kg_poolsize(junk, size_delta):
            self.failUnlessEqual(len(self.key_generator_svc.key_generator.keypool),
                                 self.key_generator_svc.key_generator.pool_size + size_delta)

        d.addCallback(check_kg_poolsize, 0)
        d.addCallback(lambda junk: self.clients[3].create_mutable_file('hello, world'))
        d.addCallback(check_kg_poolsize, -1)
        d.addCallback(lambda junk: self.clients[3].create_empty_dirnode())
        d.addCallback(check_kg_poolsize, -2)
        # use_helper induces use of clients[3], which is the using-key_gen client
        d.addCallback(lambda junk: self.POST("uri", use_helper=True, t="mkdir", name='george'))
        d.addCallback(check_kg_poolsize, -3)

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
        d = self.set_up_nodes(use_stats_gatherer=True)
        d.addCallback(self._test_introweb)
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

        d.addCallback(lambda res: self.bounce_client(0))
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
        return d
    test_vdrive.timeout = 1100

    def _test_introweb(self, res):
        d = getPage(self.introweb_url, method="GET", followRedirect=True)
        def _check(res):
            try:
                self.failUnless("allmydata: %s" % str(allmydata.__version__)
                                in res)
                self.failUnless("Announcement Summary: storage: 5, stub_client: 5" in res)
                self.failUnless("Subscription Summary: storage: 5" in res)
            except unittest.FailTest:
                print
                print "GET %s output was:" % self.introweb_url
                print res
                raise
        d.addCallback(_check)
        d.addCallback(lambda res:
                      getPage(self.introweb_url + "?t=json",
                              method="GET", followRedirect=True))
        def _check_json(res):
            data = simplejson.loads(res)
            try:
                self.failUnlessEqual(data["subscription_summary"],
                                     {"storage": 5})
                self.failUnlessEqual(data["announcement_summary"],
                                     {"storage": 5, "stub_client": 5})
                self.failUnlessEqual(data["announcement_distinct_hosts"],
                                     {"storage": 1, "stub_client": 1})
            except unittest.FailTest:
                print
                print "GET %s?t=json output was:" % self.introweb_url
                print res
                raise
        d.addCallback(_check_json)
        return d

    def _do_publish1(self, res):
        ut = upload.Data(self.data, convergence=None)
        c0 = self.clients[0]
        d = c0.create_empty_dirnode()
        def _made_root(new_dirnode):
            self._root_directory_uri = new_dirnode.get_uri()
            return c0.create_node_from_uri(self._root_directory_uri)
        d.addCallback(_made_root)
        d.addCallback(lambda root: root.create_empty_directory(u"subdir1"))
        def _made_subdir1(subdir1_node):
            self._subdir1_node = subdir1_node
            d1 = subdir1_node.add_file(u"mydata567", ut)
            d1.addCallback(self.log, "publish finished")
            def _stash_uri(filenode):
                self.uri = filenode.get_uri()
                assert isinstance(self.uri, str), (self.uri, filenode)
            d1.addCallback(_stash_uri)
            return d1
        d.addCallback(_made_subdir1)
        return d

    def _do_publish2(self, res):
        ut = upload.Data(self.data, convergence=None)
        d = self._subdir1_node.create_empty_directory(u"subdir2")
        d.addCallback(lambda subdir2: subdir2.add_file(u"mydata992", ut))
        return d

    def log(self, res, *args, **kwargs):
        # print "MSG: %s  RES: %s" % (msg, args)
        log.msg(*args, **kwargs)
        return res

    def _do_publish_private(self, res):
        self.smalldata = "sssh, very secret stuff"
        ut = upload.Data(self.smalldata, convergence=None)
        d = self.clients[0].create_empty_dirnode()
        d.addCallback(self.log, "GOT private directory")
        def _got_new_dir(privnode):
            rootnode = self.clients[0].create_node_from_uri(self._root_directory_uri)
            d1 = privnode.create_empty_directory(u"personal")
            d1.addCallback(self.log, "made P/personal")
            d1.addCallback(lambda node: node.add_file(u"sekrit data", ut))
            d1.addCallback(self.log, "made P/personal/sekrit data")
            d1.addCallback(lambda res: rootnode.get_child_at_path([u"subdir1", u"subdir2"]))
            def _got_s2(s2node):
                d2 = privnode.set_uri(u"s2-rw", s2node.get_uri())
                d2.addCallback(lambda node: privnode.set_uri(u"s2-ro", s2node.get_readonly_uri()))
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
        d.addCallback(lambda root: root.get(u"subdir1"))
        d.addCallback(lambda subdir1: subdir1.get(u"mydata567"))
        d.addCallback(lambda filenode: filenode.download_to_data())
        d.addCallback(self.log, "get finished")
        def _get_done(data):
            self.failUnlessEqual(data, self.data)
        d.addCallback(_get_done)
        return d

    def _check_publish2(self, res):
        # this one uses the path-based API
        rootnode = self.clients[1].create_node_from_uri(self._root_directory_uri)
        d = rootnode.get_child_at_path(u"subdir1")
        d.addCallback(lambda dirnode:
                      self.failUnless(IDirectoryNode.providedBy(dirnode)))
        d.addCallback(lambda res: rootnode.get_child_at_path(u"subdir1/mydata567"))
        d.addCallback(lambda filenode: filenode.download_to_data())
        d.addCallback(lambda data: self.failUnlessEqual(data, self.data))

        d.addCallback(lambda res: rootnode.get_child_at_path(u"subdir1/mydata567"))
        def _got_filenode(filenode):
            fnode = self.clients[1].create_node_from_uri(filenode.get_uri())
            assert fnode == filenode
        d.addCallback(_got_filenode)
        return d

    def _check_publish_private(self, resnode):
        # this one uses the path-based API
        self._private_node = resnode

        d = self._private_node.get_child_at_path(u"personal")
        def _got_personal(personal):
            self._personal_node = personal
            return personal
        d.addCallback(_got_personal)

        d.addCallback(lambda dirnode:
                      self.failUnless(IDirectoryNode.providedBy(dirnode), dirnode))
        def get_path(path):
            return self._private_node.get_child_at_path(path)

        d.addCallback(lambda res: get_path(u"personal/sekrit data"))
        d.addCallback(lambda filenode: filenode.download_to_data())
        d.addCallback(lambda data: self.failUnlessEqual(data, self.smalldata))
        d.addCallback(lambda res: get_path(u"s2-rw"))
        d.addCallback(lambda dirnode: self.failUnless(dirnode.is_mutable()))
        d.addCallback(lambda res: get_path(u"s2-ro"))
        def _got_s2ro(dirnode):
            self.failUnless(dirnode.is_mutable(), dirnode)
            self.failUnless(dirnode.is_readonly(), dirnode)
            d1 = defer.succeed(None)
            d1.addCallback(lambda res: dirnode.list())
            d1.addCallback(self.log, "dirnode.list")

            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "mkdir(nope)", None, dirnode.create_empty_directory, u"nope"))

            d1.addCallback(self.log, "doing add_file(ro)")
            ut = upload.Data("I will disappear, unrecorded and unobserved. The tragedy of my demise is made more poignant by its silence, but this beauty is not for you to ever know.", convergence="99i-p1x4-xd4-18yc-ywt-87uu-msu-zo -- completely and totally unguessable string (unless you read this)")
            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "add_file(nope)", None, dirnode.add_file, u"hope", ut))

            d1.addCallback(self.log, "doing get(ro)")
            d1.addCallback(lambda res: dirnode.get(u"mydata992"))
            d1.addCallback(lambda filenode:
                           self.failUnless(IFileNode.providedBy(filenode)))

            d1.addCallback(self.log, "doing delete(ro)")
            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "delete(nope)", None, dirnode.delete, u"mydata992"))

            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "set_uri(nope)", None, dirnode.set_uri, u"hopeless", self.uri))

            d1.addCallback(lambda res: self.shouldFail2(NoSuchChildError, "get(missing)", "missing", dirnode.get, u"missing"))

            personal = self._personal_node
            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "mv from readonly", None, dirnode.move_child_to, u"mydata992", personal, u"nope"))

            d1.addCallback(self.log, "doing move_child_to(ro)2")
            d1.addCallback(lambda res: self.shouldFail2(NotMutableError, "mv to readonly", None, personal.move_child_to, u"sekrit data", dirnode, u"nope"))

            d1.addCallback(self.log, "finished with _got_s2ro")
            return d1
        d.addCallback(_got_s2ro)
        def _got_home(dummy):
            home = self._private_node
            personal = self._personal_node
            d1 = defer.succeed(None)
            d1.addCallback(self.log, "mv 'P/personal/sekrit data' to P/sekrit")
            d1.addCallback(lambda res:
                           personal.move_child_to(u"sekrit data",home,u"sekrit"))

            d1.addCallback(self.log, "mv P/sekrit 'P/sekrit data'")
            d1.addCallback(lambda res:
                           home.move_child_to(u"sekrit", home, u"sekrit data"))

            d1.addCallback(self.log, "mv 'P/sekret data' P/personal/")
            d1.addCallback(lambda res:
                           home.move_child_to(u"sekrit data", personal))

            d1.addCallback(lambda res: home.build_manifest().when_done())
            d1.addCallback(self.log, "manifest")
            #  five items:
            # P/
            # P/personal/
            # P/personal/sekrit data
            # P/s2-rw  (same as P/s2-ro)
            # P/s2-rw/mydata992 (same as P/s2-rw/mydata992)
            d1.addCallback(lambda res:
                           self.failUnlessEqual(len(res["manifest"]), 5))
            d1.addCallback(lambda res: home.start_deep_stats().when_done())
            def _check_stats(stats):
                expected = {"count-immutable-files": 1,
                            "count-mutable-files": 0,
                            "count-literal-files": 1,
                            "count-files": 2,
                            "count-directories": 3,
                            "size-immutable-files": 112,
                            "size-literal-files": 23,
                            #"size-directories": 616, # varies
                            #"largest-directory": 616,
                            "largest-directory-children": 3,
                            "largest-immutable-file": 112,
                            }
                for k,v in expected.iteritems():
                    self.failUnlessEqual(stats[k], v,
                                         "stats[%s] was %s, not %s" %
                                         (k, stats[k], v))
                self.failUnless(stats["size-directories"] > 1300,
                                stats["size-directories"])
                self.failUnless(stats["largest-directory"] > 800,
                                stats["largest-directory"])
                self.failUnlessEqual(stats["size-files-histogram"],
                                     [ (11, 31, 1), (101, 316, 1) ])
            d1.addCallback(_check_stats)
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

    def POST(self, urlpath, followRedirect=False, use_helper=False, **fields):
        if use_helper:
            url = self.helper_webish_url + urlpath
        else:
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
            form.append(str(value))
            form.append(sep)
        form[-1] += "--"
        body = "\r\n".join(form) + "\r\n"
        headers = {"content-type": "multipart/form-data; boundary=%s" % sepbase,
                   }
        return getPage(url, method="POST", postdata=body,
                       headers=headers, followRedirect=followRedirect)

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
            self.failUnless("Helper: 0 active uploads" in page)
        d.addCallback(_got_welcome)
        d.addCallback(self.log, "done with _got_welcome")

        # get the welcome page from the node that uses the helper too
        d.addCallback(lambda res: getPage(self.helper_webish_url))
        def _got_welcome_helper(page):
            self.failUnless("Connected to helper?: <span>yes</span>" in page,
                            page)
            self.failUnless("Not running helper" in page)
        d.addCallback(_got_welcome_helper)

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

        # test unlinked POST
        d.addCallback(lambda res: self.POST("uri", t="upload",
                                            file=("new.txt", "data" * 10000)))
        # and again using the helper, which exercises different upload-status
        # display code
        d.addCallback(lambda res: self.POST("uri", use_helper=True, t="upload",
                                            file=("foo.txt", "data2" * 10000)))

        # check that the status page exists
        d.addCallback(lambda res: self.GET("status", followRedirect=True))
        def _got_status(res):
            # find an interesting upload and download to look at. LIT files
            # are not interesting.
            for ds in self.clients[0].list_all_download_statuses():
                if ds.get_size() > 200:
                    self._down_status = ds.get_counter()
            for us in self.clients[0].list_all_upload_statuses():
                if us.get_size() > 200:
                    self._up_status = us.get_counter()
            rs = self.clients[0].list_all_retrieve_statuses()[0]
            self._retrieve_status = rs.get_counter()
            ps = self.clients[0].list_all_publish_statuses()[0]
            self._publish_status = ps.get_counter()
            us = self.clients[0].list_all_mapupdate_statuses()[0]
            self._update_status = us.get_counter()

            # and that there are some upload- and download- status pages
            return self.GET("status/up-%d" % self._up_status)
        d.addCallback(_got_status)
        def _got_up(res):
            return self.GET("status/down-%d" % self._down_status)
        d.addCallback(_got_up)
        def _got_down(res):
            return self.GET("status/mapupdate-%d" % self._update_status)
        d.addCallback(_got_down)
        def _got_update(res):
            return self.GET("status/publish-%d" % self._publish_status)
        d.addCallback(_got_update)
        def _got_publish(res):
            return self.GET("status/retrieve-%d" % self._retrieve_status)
        d.addCallback(_got_publish)

        # check that the helper status page exists
        d.addCallback(lambda res:
                      self.GET("helper_status", followRedirect=True))
        def _got_helper_status(res):
            self.failUnless("Bytes Fetched:" in res)
            # touch a couple of files in the helper's working directory to
            # exercise more code paths
            workdir = os.path.join(self.getdir("client0"), "helper")
            incfile = os.path.join(workdir, "CHK_incoming", "spurious")
            f = open(incfile, "wb")
            f.write("small file")
            f.close()
            then = time.time() - 86400*3
            now = time.time()
            os.utime(incfile, (now, then))
            encfile = os.path.join(workdir, "CHK_encoding", "spurious")
            f = open(encfile, "wb")
            f.write("less small file")
            f.close()
            os.utime(encfile, (now, then))
        d.addCallback(_got_helper_status)
        # and that the json form exists
        d.addCallback(lambda res:
                      self.GET("helper_status?t=json", followRedirect=True))
        def _got_helper_status_json(res):
            data = simplejson.loads(res)
            self.failUnlessEqual(data["chk_upload_helper.upload_need_upload"],
                                 1)
            self.failUnlessEqual(data["chk_upload_helper.incoming_count"], 1)
            self.failUnlessEqual(data["chk_upload_helper.incoming_size"], 10)
            self.failUnlessEqual(data["chk_upload_helper.incoming_size_old"],
                                 10)
            self.failUnlessEqual(data["chk_upload_helper.encoding_count"], 1)
            self.failUnlessEqual(data["chk_upload_helper.encoding_size"], 15)
            self.failUnlessEqual(data["chk_upload_helper.encoding_size_old"],
                                 15)
        d.addCallback(_got_helper_status_json)

        # and check that client[3] (which uses a helper but does not run one
        # itself) doesn't explode when you ask for its status
        d.addCallback(lambda res: getPage(self.helper_webish_url + "status/"))
        def _got_non_helper_status(res):
            self.failUnless("Upload and Download Status" in res)
        d.addCallback(_got_non_helper_status)

        # or for helper status with t=json
        d.addCallback(lambda res:
                      getPage(self.helper_webish_url + "helper_status?t=json"))
        def _got_non_helper_status_json(res):
            data = simplejson.loads(res)
            self.failUnlessEqual(data, {})
        d.addCallback(_got_non_helper_status_json)

        # see if the statistics page exists
        d.addCallback(lambda res: self.GET("statistics"))
        def _got_stats(res):
            self.failUnless("Node Statistics" in res)
            self.failUnless("  'downloader.files_downloaded': 5," in res, res)
        d.addCallback(_got_stats)
        d.addCallback(lambda res: self.GET("statistics?t=json"))
        def _got_stats_json(res):
            data = simplejson.loads(res)
            self.failUnlessEqual(data["counters"]["uploader.files_uploaded"], 5)
            self.failUnlessEqual(data["stats"]["chk_upload_helper.upload_need_upload"], 1)
        d.addCallback(_got_stats_json)

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
        rc = runner.runner(["debug", "dump-share", "--offsets",
                            filename],
                           stdout=out, stderr=err)
        output = out.getvalue()
        self.failUnlessEqual(rc, 0)

        # we only upload a single file, so we can assert some things about
        # its size and shares.
        self.failUnless(("share filename: %s" % filename) in output)
        self.failUnless("size: %d\n" % len(self.data) in output)
        self.failUnless("num_segments: 1\n" in output)
        # segment_size is always a multiple of needed_shares
        self.failUnless("segment_size: %d\n" % mathutil.next_multiple(len(self.data), 3) in output)
        self.failUnless("total_shares: 10\n" in output)
        # keys which are supposed to be present
        for key in ("size", "num_segments", "segment_size",
                    "needed_shares", "total_shares",
                    "codec_name", "codec_params", "tail_codec_params",
                    #"plaintext_hash", "plaintext_root_hash",
                    "crypttext_hash", "crypttext_root_hash",
                    "share_root_hash", "UEB_hash"):
            self.failUnless("%s: " % key in output, key)
        self.failUnless("  verify-cap: URI:CHK-Verifier:" in output)

        # now use its storage index to find the other shares using the
        # 'find-shares' tool
        sharedir, shnum = os.path.split(filename)
        storagedir, storage_index_s = os.path.split(sharedir)
        out,err = StringIO(), StringIO()
        nodedirs = [self.getdir("client%d" % i) for i in range(self.numclients)]
        cmd = ["debug", "find-shares", storage_index_s] + nodedirs
        rc = runner.runner(cmd, stdout=out, stderr=err)
        self.failUnlessEqual(rc, 0)
        out.seek(0)
        sharefiles = [sfn.strip() for sfn in out.readlines()]
        self.failUnlessEqual(len(sharefiles), 10)

        # also exercise the 'catalog-shares' tool
        out,err = StringIO(), StringIO()
        nodedirs = [self.getdir("client%d" % i) for i in range(self.numclients)]
        cmd = ["debug", "catalog-shares"] + nodedirs
        rc = runner.runner(cmd, stdout=out, stderr=err)
        self.failUnlessEqual(rc, 0)
        out.seek(0)
        descriptions = [sfn.strip() for sfn in out.readlines()]
        self.failUnlessEqual(len(descriptions), 30)
        matching = [line
                    for line in descriptions
                    if line.startswith("CHK %s " % storage_index_s)]
        self.failUnlessEqual(len(matching), 10)

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
        d = rref.callRemote("upload_from_file_to_uri", filename, convergence=None)
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
            ]
        TESTDATA = "I will not write the same thing over and over.\n" * 100

        d = defer.succeed(None)

        # for compatibility with earlier versions, private/root_dir.cap is
        # supposed to be treated as an alias named "tahoe:". Start by making
        # sure that works, before we add other aliases.

        root_file = os.path.join(client0_basedir, "private", "root_dir.cap")
        f = open(root_file, "w")
        f.write(private_uri)
        f.close()

        def run(ignored, verb, *args, **kwargs):
            stdin = kwargs.get("stdin", "")
            newargs = [verb] + nodeargs + list(args)
            return self._run_cli(newargs, stdin=stdin)

        def _check_ls((out,err), expected_children, unexpected_children=[]):
            self.failUnlessEqual(err, "")
            for s in expected_children:
                self.failUnless(s in out, (s,out))
            for s in unexpected_children:
                self.failIf(s in out, (s,out))

        def _check_ls_root((out,err)):
            self.failUnless("personal" in out)
            self.failUnless("s2-ro" in out)
            self.failUnless("s2-rw" in out)
            self.failUnlessEqual(err, "")

        # this should reference private_uri
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["personal", "s2-ro", "s2-rw"])

        d.addCallback(run, "list-aliases")
        def _check_aliases_1((out,err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(out, "tahoe: %s\n" % private_uri)
        d.addCallback(_check_aliases_1)

        # now that that's out of the way, remove root_dir.cap and work with
        # new files
        d.addCallback(lambda res: os.unlink(root_file))
        d.addCallback(run, "list-aliases")
        def _check_aliases_2((out,err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(out, "")
        d.addCallback(_check_aliases_2)

        d.addCallback(run, "mkdir")
        def _got_dir( (out,err) ):
            self.failUnless(uri.from_string_dirnode(out.strip()))
            return out.strip()
        d.addCallback(_got_dir)
        d.addCallback(lambda newcap: run(None, "add-alias", "tahoe", newcap))

        d.addCallback(run, "list-aliases")
        def _check_aliases_3((out,err)):
            self.failUnlessEqual(err, "")
            self.failUnless("tahoe: " in out)
        d.addCallback(_check_aliases_3)

        def _check_empty_dir((out,err)):
            self.failUnlessEqual(out, "")
            self.failUnlessEqual(err, "")
        d.addCallback(run, "ls")
        d.addCallback(_check_empty_dir)

        def _check_missing_dir((out,err)):
            # TODO: check that rc==2
            self.failUnlessEqual(out, "")
            self.failUnlessEqual(err, "No such file or directory\n")
        d.addCallback(run, "ls", "bogus")
        d.addCallback(_check_missing_dir)

        files = []
        datas = []
        for i in range(10):
            fn = os.path.join(self.basedir, "file%d" % i)
            files.append(fn)
            data = "data to be uploaded: file%d\n" % i
            datas.append(data)
            open(fn,"wb").write(data)

        def _check_stdout_against((out,err), filenum=None, data=None):
            self.failUnlessEqual(err, "")
            if filenum is not None:
                self.failUnlessEqual(out, datas[filenum])
            if data is not None:
                self.failUnlessEqual(out, data)

        # test all both forms of put: from a file, and from stdin
        #  tahoe put bar FOO
        d.addCallback(run, "put", files[0], "tahoe-file0")
        def _put_out((out,err)):
            self.failUnless("URI:LIT:" in out, out)
            self.failUnless("201 Created" in err, err)
            uri0 = out.strip()
            return run(None, "get", uri0)
        d.addCallback(_put_out)
        d.addCallback(lambda (out,err): self.failUnlessEqual(out, datas[0]))

        d.addCallback(run, "put", files[1], "subdir/tahoe-file1")
        #  tahoe put bar tahoe:FOO
        d.addCallback(run, "put", files[2], "tahoe:file2")
        d.addCallback(run, "put", "--mutable", files[3], "tahoe:file3")
        def _check_put_mutable((out,err)):
            self._mutable_file3_uri = out.strip()
        d.addCallback(_check_put_mutable)
        d.addCallback(run, "get", "tahoe:file3")
        d.addCallback(_check_stdout_against, 3)

        #  tahoe put FOO
        STDIN_DATA = "This is the file to upload from stdin."
        d.addCallback(run, "put", "-", "tahoe-file-stdin", stdin=STDIN_DATA)
        #  tahoe put tahoe:FOO
        d.addCallback(run, "put", "-", "tahoe:from-stdin",
                      stdin="Other file from stdin.")

        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["tahoe-file0", "file2", "file3", "subdir",
                                  "tahoe-file-stdin", "from-stdin"])
        d.addCallback(run, "ls", "subdir")
        d.addCallback(_check_ls, ["tahoe-file1"])

        # tahoe mkdir FOO
        d.addCallback(run, "mkdir", "subdir2")
        d.addCallback(run, "ls")
        # TODO: extract the URI, set an alias with it
        d.addCallback(_check_ls, ["subdir2"])

        # tahoe get: (to stdin and to a file)
        d.addCallback(run, "get", "tahoe-file0")
        d.addCallback(_check_stdout_against, 0)
        d.addCallback(run, "get", "tahoe:subdir/tahoe-file1")
        d.addCallback(_check_stdout_against, 1)
        outfile0 = os.path.join(self.basedir, "outfile0")
        d.addCallback(run, "get", "file2", outfile0)
        def _check_outfile0((out,err)):
            data = open(outfile0,"rb").read()
            self.failUnlessEqual(data, "data to be uploaded: file2\n")
        d.addCallback(_check_outfile0)
        outfile1 = os.path.join(self.basedir, "outfile0")
        d.addCallback(run, "get", "tahoe:subdir/tahoe-file1", outfile1)
        def _check_outfile1((out,err)):
            data = open(outfile1,"rb").read()
            self.failUnlessEqual(data, "data to be uploaded: file1\n")
        d.addCallback(_check_outfile1)

        d.addCallback(run, "rm", "tahoe-file0")
        d.addCallback(run, "rm", "tahoe:file2")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, [], ["tahoe-file0", "file2"])

        d.addCallback(run, "ls", "-l")
        def _check_ls_l((out,err)):
            lines = out.split("\n")
            for l in lines:
                if "tahoe-file-stdin" in l:
                    self.failUnless(l.startswith("-r-- "), l)
                    self.failUnless(" %d " % len(STDIN_DATA) in l)
                if "file3" in l:
                    self.failUnless(l.startswith("-rw- "), l) # mutable
        d.addCallback(_check_ls_l)

        d.addCallback(run, "ls", "--uri")
        def _check_ls_uri((out,err)):
            lines = out.split("\n")
            for l in lines:
                if "file3" in l:
                    self.failUnless(self._mutable_file3_uri in l)
        d.addCallback(_check_ls_uri)

        d.addCallback(run, "ls", "--readonly-uri")
        def _check_ls_rouri((out,err)):
            lines = out.split("\n")
            for l in lines:
                if "file3" in l:
                    rw_uri = self._mutable_file3_uri
                    u = uri.from_string_mutable_filenode(rw_uri)
                    ro_uri = u.get_readonly().to_string()
                    self.failUnless(ro_uri in l)
        d.addCallback(_check_ls_rouri)


        d.addCallback(run, "mv", "tahoe-file-stdin", "tahoe-moved")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["tahoe-moved"], ["tahoe-file-stdin"])

        d.addCallback(run, "ln", "tahoe-moved", "newlink")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["tahoe-moved", "newlink"])

        d.addCallback(run, "cp", "tahoe:file3", "tahoe:file3-copy")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["file3", "file3-copy"])
        d.addCallback(run, "get", "tahoe:file3-copy")
        d.addCallback(_check_stdout_against, 3)

        # copy from disk into tahoe
        d.addCallback(run, "cp", files[4], "tahoe:file4")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["file3", "file3-copy", "file4"])
        d.addCallback(run, "get", "tahoe:file4")
        d.addCallback(_check_stdout_against, 4)

        # copy from tahoe into disk
        target_filename = os.path.join(self.basedir, "file-out")
        d.addCallback(run, "cp", "tahoe:file4", target_filename)
        def _check_cp_out((out,err)):
            self.failUnless(os.path.exists(target_filename))
            got = open(target_filename,"rb").read()
            self.failUnlessEqual(got, datas[4])
        d.addCallback(_check_cp_out)

        # copy from disk to disk (silly case)
        target2_filename = os.path.join(self.basedir, "file-out-copy")
        d.addCallback(run, "cp", target_filename, target2_filename)
        def _check_cp_out2((out,err)):
            self.failUnless(os.path.exists(target2_filename))
            got = open(target2_filename,"rb").read()
            self.failUnlessEqual(got, datas[4])
        d.addCallback(_check_cp_out2)

        # copy from tahoe into disk, overwriting an existing file
        d.addCallback(run, "cp", "tahoe:file3", target_filename)
        def _check_cp_out3((out,err)):
            self.failUnless(os.path.exists(target_filename))
            got = open(target_filename,"rb").read()
            self.failUnlessEqual(got, datas[3])
        d.addCallback(_check_cp_out3)

        # copy from disk into tahoe, overwriting an existing immutable file
        d.addCallback(run, "cp", files[5], "tahoe:file4")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["file3", "file3-copy", "file4"])
        d.addCallback(run, "get", "tahoe:file4")
        d.addCallback(_check_stdout_against, 5)

        # copy from disk into tahoe, overwriting an existing mutable file
        d.addCallback(run, "cp", files[5], "tahoe:file3")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["file3", "file3-copy", "file4"])
        d.addCallback(run, "get", "tahoe:file3")
        d.addCallback(_check_stdout_against, 5)

        # recursive copy: setup
        dn = os.path.join(self.basedir, "dir1")
        os.makedirs(dn)
        open(os.path.join(dn, "rfile1"), "wb").write("rfile1")
        open(os.path.join(dn, "rfile2"), "wb").write("rfile2")
        open(os.path.join(dn, "rfile3"), "wb").write("rfile3")
        sdn2 = os.path.join(dn, "subdir2")
        os.makedirs(sdn2)
        open(os.path.join(sdn2, "rfile4"), "wb").write("rfile4")
        open(os.path.join(sdn2, "rfile5"), "wb").write("rfile5")

        # from disk into tahoe
        d.addCallback(run, "cp", "-r", dn, "tahoe:dir1")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["dir1"])
        d.addCallback(run, "ls", "dir1")
        d.addCallback(_check_ls, ["rfile1", "rfile2", "rfile3", "subdir2"],
                      ["rfile4", "rfile5"])
        d.addCallback(run, "ls", "tahoe:dir1/subdir2")
        d.addCallback(_check_ls, ["rfile4", "rfile5"],
                      ["rfile1", "rfile2", "rfile3"])
        d.addCallback(run, "get", "dir1/subdir2/rfile4")
        d.addCallback(_check_stdout_against, data="rfile4")

        # and back out again
        dn_copy = os.path.join(self.basedir, "dir1-copy")
        d.addCallback(run, "cp", "--verbose", "-r", "tahoe:dir1", dn_copy)
        def _check_cp_r_out((out,err)):
            def _cmp(name):
                old = open(os.path.join(dn, name), "rb").read()
                newfn = os.path.join(dn_copy, name)
                self.failUnless(os.path.exists(newfn))
                new = open(newfn, "rb").read()
                self.failUnlessEqual(old, new)
            _cmp("rfile1")
            _cmp("rfile2")
            _cmp("rfile3")
            _cmp(os.path.join("subdir2", "rfile4"))
            _cmp(os.path.join("subdir2", "rfile5"))
        d.addCallback(_check_cp_r_out)

        # and copy it a second time, which ought to overwrite the same files
        d.addCallback(run, "cp", "-r", "tahoe:dir1", dn_copy)

        # and tahoe-to-tahoe
        d.addCallback(run, "cp", "-r", "tahoe:dir1", "tahoe:dir1-copy")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["dir1", "dir1-copy"])
        d.addCallback(run, "ls", "dir1-copy")
        d.addCallback(_check_ls, ["rfile1", "rfile2", "rfile3", "subdir2"],
                      ["rfile4", "rfile5"])
        d.addCallback(run, "ls", "tahoe:dir1-copy/subdir2")
        d.addCallback(_check_ls, ["rfile4", "rfile5"],
                      ["rfile1", "rfile2", "rfile3"])
        d.addCallback(run, "get", "dir1-copy/subdir2/rfile4")
        d.addCallback(_check_stdout_against, data="rfile4")

        # and copy it a second time, which ought to overwrite the same files
        d.addCallback(run, "cp", "-r", "tahoe:dir1", "tahoe:dir1-copy")

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

        return d

    def _run_cli(self, argv, stdin=""):
        #print "CLI:", argv
        stdout, stderr = StringIO(), StringIO()
        d = threads.deferToThread(runner.runner, argv, run_by_human=False,
                                  stdin=StringIO(stdin),
                                  stdout=stdout, stderr=stderr)
        def _done(res):
            return stdout.getvalue(), stderr.getvalue()
        d.addCallback(_done)
        return d

    def _test_checker(self, res):
        ut = upload.Data("too big to be literal" * 200, convergence=None)
        d = self._personal_node.add_file(u"big file", ut)

        d.addCallback(lambda res: self._personal_node.check(Monitor()))
        def _check_dirnode_results(r):
            self.failUnless(r.is_healthy())
        d.addCallback(_check_dirnode_results)
        d.addCallback(lambda res: self._personal_node.check(Monitor(), verify=True))
        d.addCallback(_check_dirnode_results)

        d.addCallback(lambda res: self._personal_node.get(u"big file"))
        def _got_chk_filenode(n):
            self.failUnless(isinstance(n, filenode.FileNode))
            d = n.check(Monitor())
            def _check_filenode_results(r):
                self.failUnless(r.is_healthy())
            d.addCallback(_check_filenode_results)
            d.addCallback(lambda res: n.check(Monitor(), verify=True))
            d.addCallback(_check_filenode_results)
            return d
        d.addCallback(_got_chk_filenode)

        d.addCallback(lambda res: self._personal_node.get(u"sekrit data"))
        def _got_lit_filenode(n):
            self.failUnless(isinstance(n, filenode.LiteralFileNode))
            d = n.check(Monitor())
            def _check_lit_filenode_results(r):
                self.failUnlessEqual(r, None)
            d.addCallback(_check_lit_filenode_results)
            d.addCallback(lambda res: n.check(Monitor(), verify=True))
            d.addCallback(_check_lit_filenode_results)
            return d
        d.addCallback(_got_lit_filenode)
        return d


class MutableChecker(SystemTestMixin, unittest.TestCase, ErrorMixin):

    def _run_cli(self, argv):
        stdout, stderr = StringIO(), StringIO()
        # this can only do synchronous operations
        assert argv[0] == "debug"
        runner.runner(argv, run_by_human=False, stdout=stdout, stderr=stderr)
        return stdout.getvalue()

    def test_good(self):
        self.basedir = self.mktemp()
        d = self.set_up_nodes()
        CONTENTS = "a little bit of data"
        d.addCallback(lambda res: self.clients[0].create_mutable_file(CONTENTS))
        def _created(node):
            self.node = node
            si = self.node.get_storage_index()
        d.addCallback(_created)
        # now make sure the webapi verifier sees no problems
        def _do_check(res):
            url = (self.webish_url +
                   "uri/%s" % urllib.quote(self.node.get_uri()) +
                   "?t=check&verify=true")
            return getPage(url, method="POST")
        d.addCallback(_do_check)
        def _got_results(out):
            self.failUnless("<span>Healthy : Healthy</span>" in out, out)
            self.failUnless("Recoverable Versions: 10*seq1-" in out, out)
            self.failIf("Not Healthy!" in out, out)
            self.failIf("Unhealthy" in out, out)
            self.failIf("Corrupt Shares" in out, out)
        d.addCallback(_got_results)
        d.addErrback(self.explain_web_error)
        return d

    def test_corrupt(self):
        self.basedir = self.mktemp()
        d = self.set_up_nodes()
        CONTENTS = "a little bit of data"
        d.addCallback(lambda res: self.clients[0].create_mutable_file(CONTENTS))
        def _created(node):
            self.node = node
            si = self.node.get_storage_index()
            out = self._run_cli(["debug", "find-shares", base32.b2a(si),
                                self.clients[1].basedir])
            files = out.split("\n")
            # corrupt one of them, using the CLI debug command
            f = files[0]
            shnum = os.path.basename(f)
            nodeid = self.clients[1].nodeid
            nodeid_prefix = idlib.shortnodeid_b2a(nodeid)
            self.corrupt_shareid = "%s-sh%s" % (nodeid_prefix, shnum)
            out = self._run_cli(["debug", "corrupt-share", files[0]])
        d.addCallback(_created)
        # now make sure the webapi verifier notices it
        def _do_check(res):
            url = (self.webish_url +
                   "uri/%s" % urllib.quote(self.node.get_uri()) +
                   "?t=check&verify=true")
            return getPage(url, method="POST")
        d.addCallback(_do_check)
        def _got_results(out):
            self.failUnless("Not Healthy!" in out, out)
            self.failUnless("Unhealthy: best version has only 9 shares (encoding is 3-of-10)" in out, out)
            self.failUnless("Corrupt Shares:" in out, out)
        d.addCallback(_got_results)

        # now make sure the webapi repairer can fix it
        def _do_repair(res):
            url = (self.webish_url +
                   "uri/%s" % urllib.quote(self.node.get_uri()) +
                   "?t=check&verify=true&repair=true")
            return getPage(url, method="POST")
        d.addCallback(_do_repair)
        def _got_repair_results(out):
            self.failUnless("<div>Repair successful</div>" in out, out)
        d.addCallback(_got_repair_results)
        d.addCallback(_do_check)
        def _got_postrepair_results(out):
            self.failIf("Not Healthy!" in out, out)
            self.failUnless("Recoverable Versions: 10*seq" in out, out)
        d.addCallback(_got_postrepair_results)
        d.addErrback(self.explain_web_error)

        return d

    def test_delete_share(self):
        self.basedir = self.mktemp()
        d = self.set_up_nodes()
        CONTENTS = "a little bit of data"
        d.addCallback(lambda res: self.clients[0].create_mutable_file(CONTENTS))
        def _created(node):
            self.node = node
            si = self.node.get_storage_index()
            out = self._run_cli(["debug", "find-shares", base32.b2a(si),
                                self.clients[1].basedir])
            files = out.split("\n")
            # corrupt one of them, using the CLI debug command
            f = files[0]
            shnum = os.path.basename(f)
            nodeid = self.clients[1].nodeid
            nodeid_prefix = idlib.shortnodeid_b2a(nodeid)
            self.corrupt_shareid = "%s-sh%s" % (nodeid_prefix, shnum)
            os.unlink(files[0])
        d.addCallback(_created)
        # now make sure the webapi checker notices it
        def _do_check(res):
            url = (self.webish_url +
                   "uri/%s" % urllib.quote(self.node.get_uri()) +
                   "?t=check&verify=false")
            return getPage(url, method="POST")
        d.addCallback(_do_check)
        def _got_results(out):
            self.failUnless("Not Healthy!" in out, out)
            self.failUnless("Unhealthy: best version has only 9 shares (encoding is 3-of-10)" in out, out)
            self.failIf("Corrupt Shares" in out, out)
        d.addCallback(_got_results)

        # now make sure the webapi repairer can fix it
        def _do_repair(res):
            url = (self.webish_url +
                   "uri/%s" % urllib.quote(self.node.get_uri()) +
                   "?t=check&verify=false&repair=true")
            return getPage(url, method="POST")
        d.addCallback(_do_repair)
        def _got_repair_results(out):
            self.failUnless("Repair successful" in out)
        d.addCallback(_got_repair_results)
        d.addCallback(_do_check)
        def _got_postrepair_results(out):
            self.failIf("Not Healthy!" in out, out)
            self.failUnless("Recoverable Versions: 10*seq" in out)
        d.addCallback(_got_postrepair_results)
        d.addErrback(self.explain_web_error)

        return d


class DeepCheckBase(SystemTestMixin, ErrorMixin):

    def web_json(self, n, **kwargs):
        kwargs["output"] = "json"
        d = self.web(n, "POST", **kwargs)
        d.addCallback(self.decode_json)
        return d

    def decode_json(self, (s,url)):
        try:
            data = simplejson.loads(s)
        except ValueError:
            self.fail("%s: not JSON: '%s'" % (url, s))
        return data

    def web(self, n, method="GET", **kwargs):
        # returns (data, url)
        url = (self.webish_url + "uri/%s" % urllib.quote(n.get_uri())
               + "?" + "&".join(["%s=%s" % (k,v) for (k,v) in kwargs.items()]))
        d = getPage(url, method=method)
        d.addCallback(lambda data: (data,url))
        return d

    def wait_for_operation(self, ignored, ophandle):
        url = self.webish_url + "operations/" + ophandle
        url += "?t=status&output=JSON"
        d = getPage(url)
        def _got(res):
            try:
                data = simplejson.loads(res)
            except ValueError:
                self.fail("%s: not JSON: '%s'" % (url, res))
            if not data["finished"]:
                d = self.stall(delay=1.0)
                d.addCallback(self.wait_for_operation, ophandle)
                return d
            return data
        d.addCallback(_got)
        return d

    def get_operation_results(self, ignored, ophandle, output=None):
        url = self.webish_url + "operations/" + ophandle
        url += "?t=status"
        if output:
            url += "&output=" + output
        d = getPage(url)
        def _got(res):
            if output and output.lower() == "json":
                try:
                    return simplejson.loads(res)
                except ValueError:
                    self.fail("%s: not JSON: '%s'" % (url, res))
            return res
        d.addCallback(_got)
        return d

    def slow_web(self, n, output=None, **kwargs):
        # use ophandle=
        handle = base32.b2a(os.urandom(4))
        d = self.web(n, "POST", ophandle=handle, **kwargs)
        d.addCallback(self.wait_for_operation, handle)
        d.addCallback(self.get_operation_results, handle, output=output)
        return d


class DeepCheckWebGood(DeepCheckBase, unittest.TestCase):
    # construct a small directory tree (with one dir, one immutable file, one
    # mutable file, one LIT file, and a loop), and then check/examine it in
    # various ways.

    def set_up_tree(self, ignored):
        # 2.9s

        # root
        #   mutable
        #   large
        #   small
        #   small2
        #   loop -> root
        c0 = self.clients[0]
        d = c0.create_empty_dirnode()
        def _created_root(n):
            self.root = n
            self.root_uri = n.get_uri()
        d.addCallback(_created_root)
        d.addCallback(lambda ign: c0.create_mutable_file("mutable file contents"))
        d.addCallback(lambda n: self.root.set_node(u"mutable", n))
        def _created_mutable(n):
            self.mutable = n
            self.mutable_uri = n.get_uri()
        d.addCallback(_created_mutable)

        large = upload.Data("Lots of data\n" * 1000, None)
        d.addCallback(lambda ign: self.root.add_file(u"large", large))
        def _created_large(n):
            self.large = n
            self.large_uri = n.get_uri()
        d.addCallback(_created_large)

        small = upload.Data("Small enough for a LIT", None)
        d.addCallback(lambda ign: self.root.add_file(u"small", small))
        def _created_small(n):
            self.small = n
            self.small_uri = n.get_uri()
        d.addCallback(_created_small)

        small2 = upload.Data("Small enough for a LIT too", None)
        d.addCallback(lambda ign: self.root.add_file(u"small2", small2))
        def _created_small2(n):
            self.small2 = n
            self.small2_uri = n.get_uri()
        d.addCallback(_created_small2)

        d.addCallback(lambda ign: self.root.set_node(u"loop", self.root))
        return d

    def check_is_healthy(self, cr, n, where, incomplete=False):
        self.failUnless(ICheckerResults.providedBy(cr), where)
        self.failUnless(cr.is_healthy(), where)
        self.failUnlessEqual(cr.get_storage_index(), n.get_storage_index(),
                             where)
        self.failUnlessEqual(cr.get_storage_index_string(),
                             base32.b2a(n.get_storage_index()), where)
        needs_rebalancing = bool( len(self.clients) < 10 )
        if not incomplete:
            self.failUnlessEqual(cr.needs_rebalancing(), needs_rebalancing, str((where, cr, cr.get_data())))
        d = cr.get_data()
        self.failUnlessEqual(d["count-shares-good"], 10, where)
        self.failUnlessEqual(d["count-shares-needed"], 3, where)
        self.failUnlessEqual(d["count-shares-expected"], 10, where)
        if not incomplete:
            self.failUnlessEqual(d["count-good-share-hosts"], len(self.clients), where)
        self.failUnlessEqual(d["count-corrupt-shares"], 0, where)
        self.failUnlessEqual(d["list-corrupt-shares"], [], where)
        if not incomplete:
            self.failUnlessEqual(sorted(d["servers-responding"]),
                                 sorted([c.nodeid for c in self.clients]),
                                 where)
            self.failUnless("sharemap" in d, str((where, d)))
            all_serverids = set()
            for (shareid, serverids) in d["sharemap"].items():
                all_serverids.update(serverids)
            self.failUnlessEqual(sorted(all_serverids),
                                 sorted([c.nodeid for c in self.clients]),
                                 where)

        self.failUnlessEqual(d["count-wrong-shares"], 0, where)
        self.failUnlessEqual(d["count-recoverable-versions"], 1, where)
        self.failUnlessEqual(d["count-unrecoverable-versions"], 0, where)


    def check_and_repair_is_healthy(self, cr, n, where, incomplete=False):
        self.failUnless(ICheckAndRepairResults.providedBy(cr), where)
        self.failUnless(cr.get_pre_repair_results().is_healthy(), where)
        self.check_is_healthy(cr.get_pre_repair_results(), n, where, incomplete)
        self.failUnless(cr.get_post_repair_results().is_healthy(), where)
        self.check_is_healthy(cr.get_post_repair_results(), n, where, incomplete)
        self.failIf(cr.get_repair_attempted(), where)

    def deep_check_is_healthy(self, cr, num_healthy, where):
        self.failUnless(IDeepCheckResults.providedBy(cr))
        self.failUnlessEqual(cr.get_counters()["count-objects-healthy"],
                             num_healthy, where)

    def deep_check_and_repair_is_healthy(self, cr, num_healthy, where):
        self.failUnless(IDeepCheckAndRepairResults.providedBy(cr), where)
        c = cr.get_counters()
        self.failUnlessEqual(c["count-objects-healthy-pre-repair"],
                             num_healthy, where)
        self.failUnlessEqual(c["count-objects-healthy-post-repair"],
                             num_healthy, where)
        self.failUnlessEqual(c["count-repairs-attempted"], 0, where)

    def test_good(self):
        self.basedir = self.mktemp()
        d = self.set_up_nodes()
        d.addCallback(self.set_up_tree)
        d.addCallback(self.do_stats)
        d.addCallback(self.do_test_check_good)
        d.addCallback(self.do_test_web_good)
        d.addCallback(self.do_test_cli_good)
        d.addErrback(self.explain_web_error)
        d.addErrback(self.explain_error)
        return d

    def do_stats(self, ignored):
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.root.start_deep_stats().when_done())
        d.addCallback(self.check_stats_good)
        return d

    def check_stats_good(self, s):
        self.failUnlessEqual(s["count-directories"], 1)
        self.failUnlessEqual(s["count-files"], 4)
        self.failUnlessEqual(s["count-immutable-files"], 1)
        self.failUnlessEqual(s["count-literal-files"], 2)
        self.failUnlessEqual(s["count-mutable-files"], 1)
        # don't check directories: their size will vary
        # s["largest-directory"]
        # s["size-directories"]
        self.failUnlessEqual(s["largest-directory-children"], 5)
        self.failUnlessEqual(s["largest-immutable-file"], 13000)
        # to re-use this function for both the local
        # dirnode.start_deep_stats() and the webapi t=start-deep-stats, we
        # coerce the result into a list of tuples. dirnode.start_deep_stats()
        # returns a list of tuples, but JSON only knows about lists., so
        # t=start-deep-stats returns a list of lists.
        histogram = [tuple(stuff) for stuff in s["size-files-histogram"]]
        self.failUnlessEqual(histogram, [(11, 31, 2),
                                         (10001, 31622, 1),
                                         ])
        self.failUnlessEqual(s["size-immutable-files"], 13000)
        self.failUnlessEqual(s["size-literal-files"], 48)

    def do_test_check_good(self, ignored):
        d = defer.succeed(None)
        # check the individual items
        d.addCallback(lambda ign: self.root.check(Monitor()))
        d.addCallback(self.check_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.mutable.check(Monitor()))
        d.addCallback(self.check_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.large.check(Monitor()))
        d.addCallback(self.check_is_healthy, self.large, "large")
        d.addCallback(lambda ign: self.small.check(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "small")
        d.addCallback(lambda ign: self.small2.check(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "small2")

        # and again with verify=True
        d.addCallback(lambda ign: self.root.check(Monitor(), verify=True))
        d.addCallback(self.check_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.mutable.check(Monitor(), verify=True))
        d.addCallback(self.check_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.large.check(Monitor(), verify=True))
        d.addCallback(self.check_is_healthy, self.large, "large",
                      incomplete=True)
        d.addCallback(lambda ign: self.small.check(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "small")
        d.addCallback(lambda ign: self.small2.check(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "small2")

        # and check_and_repair(), which should be a nop
        d.addCallback(lambda ign: self.root.check_and_repair(Monitor()))
        d.addCallback(self.check_and_repair_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.mutable.check_and_repair(Monitor()))
        d.addCallback(self.check_and_repair_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.large.check_and_repair(Monitor()))
        d.addCallback(self.check_and_repair_is_healthy, self.large, "large")
        d.addCallback(lambda ign: self.small.check_and_repair(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "small")
        d.addCallback(lambda ign: self.small2.check_and_repair(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "small2")

        # check_and_repair(verify=True)
        d.addCallback(lambda ign: self.root.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.check_and_repair_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.mutable.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.check_and_repair_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.large.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.check_and_repair_is_healthy, self.large, "large",
                      incomplete=True)
        d.addCallback(lambda ign: self.small.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "small")
        d.addCallback(lambda ign: self.small2.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "small2")


        # now deep-check the root, with various verify= and repair= options
        d.addCallback(lambda ign:
                      self.root.start_deep_check().when_done())
        d.addCallback(self.deep_check_is_healthy, 3, "root")
        d.addCallback(lambda ign:
                      self.root.start_deep_check(verify=True).when_done())
        d.addCallback(self.deep_check_is_healthy, 3, "root")
        d.addCallback(lambda ign:
                      self.root.start_deep_check_and_repair().when_done())
        d.addCallback(self.deep_check_and_repair_is_healthy, 3, "root")
        d.addCallback(lambda ign:
                      self.root.start_deep_check_and_repair(verify=True).when_done())
        d.addCallback(self.deep_check_and_repair_is_healthy, 3, "root")

        # and finally, start a deep-check, but then cancel it.
        d.addCallback(lambda ign: self.root.start_deep_check())
        def _checking(monitor):
            monitor.cancel()
            d = monitor.when_done()
            # this should fire as soon as the next dirnode.list finishes.
            # TODO: add a counter to measure how many list() calls are made,
            # assert that no more than one gets to run before the cancel()
            # takes effect.
            def _finished_normally(res):
                self.fail("this was supposed to fail, not finish normally")
            def _cancelled(f):
                f.trap(OperationCancelledError)
            d.addCallbacks(_finished_normally, _cancelled)
            return d
        d.addCallback(_checking)

        return d

    def json_check_is_healthy(self, data, n, where, incomplete=False):

        self.failUnlessEqual(data["storage-index"],
                             base32.b2a(n.get_storage_index()), where)
        self.failUnless("summary" in data, (where, data))
        self.failUnlessEqual(data["summary"].lower(), "healthy",
                             "%s: '%s'" % (where, data["summary"]))
        r = data["results"]
        self.failUnlessEqual(r["healthy"], True, where)
        needs_rebalancing = bool( len(self.clients) < 10 )
        if not incomplete:
            self.failUnlessEqual(r["needs-rebalancing"], needs_rebalancing, where)
        self.failUnlessEqual(r["count-shares-good"], 10, where)
        self.failUnlessEqual(r["count-shares-needed"], 3, where)
        self.failUnlessEqual(r["count-shares-expected"], 10, where)
        if not incomplete:
            self.failUnlessEqual(r["count-good-share-hosts"], len(self.clients), where)
        self.failUnlessEqual(r["count-corrupt-shares"], 0, where)
        self.failUnlessEqual(r["list-corrupt-shares"], [], where)
        if not incomplete:
            self.failUnlessEqual(sorted(r["servers-responding"]),
                                 sorted([idlib.nodeid_b2a(c.nodeid)
                                         for c in self.clients]), where)
            self.failUnless("sharemap" in r, where)
            all_serverids = set()
            for (shareid, serverids_s) in r["sharemap"].items():
                all_serverids.update(serverids_s)
            self.failUnlessEqual(sorted(all_serverids),
                                 sorted([idlib.nodeid_b2a(c.nodeid)
                                         for c in self.clients]), where)
        self.failUnlessEqual(r["count-wrong-shares"], 0, where)
        self.failUnlessEqual(r["count-recoverable-versions"], 1, where)
        self.failUnlessEqual(r["count-unrecoverable-versions"], 0, where)

    def json_check_and_repair_is_healthy(self, data, n, where, incomplete=False):
        self.failUnlessEqual(data["storage-index"],
                             base32.b2a(n.get_storage_index()), where)
        self.failUnlessEqual(data["repair-attempted"], False, where)
        self.json_check_is_healthy(data["pre-repair-results"],
                                   n, where, incomplete)
        self.json_check_is_healthy(data["post-repair-results"],
                                   n, where, incomplete)

    def json_full_deepcheck_is_healthy(self, data, n, where):
        self.failUnlessEqual(data["root-storage-index"],
                             base32.b2a(n.get_storage_index()), where)
        self.failUnlessEqual(data["count-objects-checked"], 3, where)
        self.failUnlessEqual(data["count-objects-healthy"], 3, where)
        self.failUnlessEqual(data["count-objects-unhealthy"], 0, where)
        self.failUnlessEqual(data["count-corrupt-shares"], 0, where)
        self.failUnlessEqual(data["list-corrupt-shares"], [], where)
        self.failUnlessEqual(data["list-unhealthy-files"], [], where)
        self.json_check_stats_good(data["stats"], where)

    def json_full_deepcheck_and_repair_is_healthy(self, data, n, where):
        self.failUnlessEqual(data["root-storage-index"],
                             base32.b2a(n.get_storage_index()), where)
        self.failUnlessEqual(data["count-objects-checked"], 3, where)

        self.failUnlessEqual(data["count-objects-healthy-pre-repair"], 3, where)
        self.failUnlessEqual(data["count-objects-unhealthy-pre-repair"], 0, where)
        self.failUnlessEqual(data["count-corrupt-shares-pre-repair"], 0, where)

        self.failUnlessEqual(data["count-objects-healthy-post-repair"], 3, where)
        self.failUnlessEqual(data["count-objects-unhealthy-post-repair"], 0, where)
        self.failUnlessEqual(data["count-corrupt-shares-post-repair"], 0, where)

        self.failUnlessEqual(data["list-corrupt-shares"], [], where)
        self.failUnlessEqual(data["list-remaining-corrupt-shares"], [], where)
        self.failUnlessEqual(data["list-unhealthy-files"], [], where)

        self.failUnlessEqual(data["count-repairs-attempted"], 0, where)
        self.failUnlessEqual(data["count-repairs-successful"], 0, where)
        self.failUnlessEqual(data["count-repairs-unsuccessful"], 0, where)


    def json_check_lit(self, data, n, where):
        self.failUnlessEqual(data["storage-index"], "", where)
        self.failUnlessEqual(data["results"]["healthy"], True, where)

    def json_check_stats_good(self, data, where):
        self.check_stats_good(data)

    def do_test_web_good(self, ignored):
        d = defer.succeed(None)

        # stats
        d.addCallback(lambda ign:
                      self.slow_web(self.root,
                                    t="start-deep-stats", output="json"))
        d.addCallback(self.json_check_stats_good, "deep-stats")

        # check, no verify
        d.addCallback(lambda ign: self.web_json(self.root, t="check"))
        d.addCallback(self.json_check_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.web_json(self.mutable, t="check"))
        d.addCallback(self.json_check_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.web_json(self.large, t="check"))
        d.addCallback(self.json_check_is_healthy, self.large, "large")
        d.addCallback(lambda ign: self.web_json(self.small, t="check"))
        d.addCallback(self.json_check_lit, self.small, "small")
        d.addCallback(lambda ign: self.web_json(self.small2, t="check"))
        d.addCallback(self.json_check_lit, self.small2, "small2")

        # check and verify
        d.addCallback(lambda ign:
                      self.web_json(self.root, t="check", verify="true"))
        d.addCallback(self.json_check_is_healthy, self.root, "root+v")
        d.addCallback(lambda ign:
                      self.web_json(self.mutable, t="check", verify="true"))
        d.addCallback(self.json_check_is_healthy, self.mutable, "mutable+v")
        d.addCallback(lambda ign:
                      self.web_json(self.large, t="check", verify="true"))
        d.addCallback(self.json_check_is_healthy, self.large, "large+v",
                      incomplete=True)
        d.addCallback(lambda ign:
                      self.web_json(self.small, t="check", verify="true"))
        d.addCallback(self.json_check_lit, self.small, "small+v")
        d.addCallback(lambda ign:
                      self.web_json(self.small2, t="check", verify="true"))
        d.addCallback(self.json_check_lit, self.small2, "small2+v")

        # check and repair, no verify
        d.addCallback(lambda ign:
                      self.web_json(self.root, t="check", repair="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.root, "root+r")
        d.addCallback(lambda ign:
                      self.web_json(self.mutable, t="check", repair="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.mutable, "mutable+r")
        d.addCallback(lambda ign:
                      self.web_json(self.large, t="check", repair="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.large, "large+r")
        d.addCallback(lambda ign:
                      self.web_json(self.small, t="check", repair="true"))
        d.addCallback(self.json_check_lit, self.small, "small+r")
        d.addCallback(lambda ign:
                      self.web_json(self.small2, t="check", repair="true"))
        d.addCallback(self.json_check_lit, self.small2, "small2+r")

        # check+verify+repair
        d.addCallback(lambda ign:
                      self.web_json(self.root, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.root, "root+vr")
        d.addCallback(lambda ign:
                      self.web_json(self.mutable, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.mutable, "mutable+vr")
        d.addCallback(lambda ign:
                      self.web_json(self.large, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.large, "large+vr", incomplete=True)
        d.addCallback(lambda ign:
                      self.web_json(self.small, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_lit, self.small, "small+vr")
        d.addCallback(lambda ign:
                      self.web_json(self.small2, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_lit, self.small2, "small2+vr")

        # now run a deep-check, with various verify= and repair= flags
        d.addCallback(lambda ign:
                      self.slow_web(self.root, t="start-deep-check", output="json"))
        d.addCallback(self.json_full_deepcheck_is_healthy, self.root, "root+d")
        d.addCallback(lambda ign:
                      self.slow_web(self.root, t="start-deep-check", verify="true",
                                    output="json"))
        d.addCallback(self.json_full_deepcheck_is_healthy, self.root, "root+dv")
        d.addCallback(lambda ign:
                      self.slow_web(self.root, t="start-deep-check", repair="true",
                                    output="json"))
        d.addCallback(self.json_full_deepcheck_and_repair_is_healthy, self.root, "root+dr")
        d.addCallback(lambda ign:
                      self.slow_web(self.root, t="start-deep-check", verify="true", repair="true", output="json"))
        d.addCallback(self.json_full_deepcheck_and_repair_is_healthy, self.root, "root+dvr")

        # now look at t=info
        d.addCallback(lambda ign: self.web(self.root, t="info"))
        # TODO: examine the output
        d.addCallback(lambda ign: self.web(self.mutable, t="info"))
        d.addCallback(lambda ign: self.web(self.large, t="info"))
        d.addCallback(lambda ign: self.web(self.small, t="info"))
        d.addCallback(lambda ign: self.web(self.small2, t="info"))

        return d

    def _run_cli(self, argv, stdin=""):
        #print "CLI:", argv
        stdout, stderr = StringIO(), StringIO()
        d = threads.deferToThread(runner.runner, argv, run_by_human=False,
                                  stdin=StringIO(stdin),
                                  stdout=stdout, stderr=stderr)
        def _done(res):
            return stdout.getvalue(), stderr.getvalue()
        d.addCallback(_done)
        return d

    def do_test_cli_good(self, ignored):
        basedir = self.getdir("client0")
        d = self._run_cli(["manifest",
                           "--node-directory", basedir,
                           self.root_uri])
        def _check((out,err)):
            self.failUnlessEqual(err, "")
            lines = [l for l in out.split("\n") if l]
            self.failUnlessEqual(len(lines), 5)
            caps = {}
            for l in lines:
                try:
                    cap, path = l.split(None, 1)
                except ValueError:
                    cap = l.strip()
                    path = ""
                caps[cap] = path
            self.failUnless(self.root.get_uri() in caps)
            self.failUnlessEqual(caps[self.root.get_uri()], "")
            self.failUnlessEqual(caps[self.mutable.get_uri()], "mutable")
            self.failUnlessEqual(caps[self.large.get_uri()], "large")
            self.failUnlessEqual(caps[self.small.get_uri()], "small")
            self.failUnlessEqual(caps[self.small2.get_uri()], "small2")
        d.addCallback(_check)

        d.addCallback(lambda res:
                      self._run_cli(["manifest",
                                     "--node-directory", basedir,
                                     "--storage-index", self.root_uri]))
        def _check2((out,err)):
            self.failUnlessEqual(err, "")
            lines = [l for l in out.split("\n") if l]
            self.failUnlessEqual(len(lines), 3)
            self.failUnless(base32.b2a(self.root.get_storage_index()) in lines)
            self.failUnless(base32.b2a(self.mutable.get_storage_index()) in lines)
            self.failUnless(base32.b2a(self.large.get_storage_index()) in lines)
        d.addCallback(_check2)

        d.addCallback(lambda res:
                      self._run_cli(["manifest",
                                     "--node-directory", basedir,
                                     "--raw", self.root_uri]))
        def _check2r((out,err)):
            self.failUnlessEqual(err, "")
            data = simplejson.loads(out)
            sis = data["storage-index"]
            self.failUnlessEqual(len(sis), 3)
            self.failUnless(base32.b2a(self.root.get_storage_index()) in sis)
            self.failUnless(base32.b2a(self.mutable.get_storage_index()) in sis)
            self.failUnless(base32.b2a(self.large.get_storage_index()) in sis)
            self.failUnlessEqual(data["stats"]["count-files"], 4)
            self.failUnlessEqual(data["origin"],
                                 base32.b2a(self.root.get_storage_index()))
            verifycaps = data["verifycaps"]
            self.failUnlessEqual(len(verifycaps), 3)
            self.failUnless(self.root.get_verify_cap().to_string() in verifycaps)
            self.failUnless(self.mutable.get_verify_cap().to_string() in verifycaps)
            self.failUnless(self.large.get_verify_cap().to_string() in verifycaps)
        d.addCallback(_check2r)

        d.addCallback(lambda res:
                      self._run_cli(["stats",
                                     "--node-directory", basedir,
                                     self.root_uri]))
        def _check3((out,err)):
            lines = [l.strip() for l in out.split("\n") if l]
            self.failUnless("count-immutable-files: 1" in lines)
            self.failUnless("count-mutable-files: 1" in lines)
            self.failUnless("count-literal-files: 2" in lines)
            self.failUnless("count-files: 4" in lines)
            self.failUnless("count-directories: 1" in lines)
            self.failUnless("size-immutable-files: 13000    (13.00 kB, 12.70 kiB)" in lines, lines)
            self.failUnless("size-literal-files: 48" in lines)
            self.failUnless("   11-31    : 2    (31 B, 31 B)".strip() in lines)
            self.failUnless("10001-31622 : 1    (31.62 kB, 30.88 kiB)".strip() in lines)
        d.addCallback(_check3)

        d.addCallback(lambda res:
                      self._run_cli(["stats",
                                     "--node-directory", basedir,
                                     "--raw",
                                     self.root_uri]))
        def _check4((out,err)):
            data = simplejson.loads(out)
            self.failUnlessEqual(data["count-immutable-files"], 1)
            self.failUnlessEqual(data["count-immutable-files"], 1)
            self.failUnlessEqual(data["count-mutable-files"], 1)
            self.failUnlessEqual(data["count-literal-files"], 2)
            self.failUnlessEqual(data["count-files"], 4)
            self.failUnlessEqual(data["count-directories"], 1)
            self.failUnlessEqual(data["size-immutable-files"], 13000)
            self.failUnlessEqual(data["size-literal-files"], 48)
            self.failUnless([11,31,2] in data["size-files-histogram"])
            self.failUnless([10001,31622,1] in data["size-files-histogram"])
        d.addCallback(_check4)

        return d


class DeepCheckWebBad(DeepCheckBase, unittest.TestCase):

    def test_bad(self):
        self.basedir = self.mktemp()
        d = self.set_up_nodes()
        d.addCallback(self.set_up_damaged_tree)
        d.addCallback(self.do_check)
        d.addCallback(self.do_deepcheck)
        d.addCallback(self.do_test_web_bad)
        d.addErrback(self.explain_web_error)
        d.addErrback(self.explain_error)
        return d



    def set_up_damaged_tree(self, ignored):
        # 6.4s

        # root
        #   mutable-good
        #   mutable-missing-shares
        #   mutable-corrupt-shares
        #   mutable-unrecoverable
        #   large-good
        #   large-missing-shares
        #   large-corrupt-shares
        #   large-unrecoverable

        self.nodes = {}

        c0 = self.clients[0]
        d = c0.create_empty_dirnode()
        def _created_root(n):
            self.root = n
            self.root_uri = n.get_uri()
        d.addCallback(_created_root)
        d.addCallback(self.create_mangled, "mutable-good")
        d.addCallback(self.create_mangled, "mutable-missing-shares")
        d.addCallback(self.create_mangled, "mutable-corrupt-shares")
        d.addCallback(self.create_mangled, "mutable-unrecoverable")
        d.addCallback(self.create_mangled, "large-good")
        d.addCallback(self.create_mangled, "large-missing-shares")
        d.addCallback(self.create_mangled, "large-corrupt-shares")
        d.addCallback(self.create_mangled, "large-unrecoverable")

        return d


    def create_mangled(self, ignored, name):
        nodetype, mangletype = name.split("-", 1)
        if nodetype == "mutable":
            d = self.clients[0].create_mutable_file("mutable file contents")
            d.addCallback(lambda n: self.root.set_node(unicode(name), n))
        elif nodetype == "large":
            large = upload.Data("Lots of data\n" * 1000 + name + "\n", None)
            d = self.root.add_file(unicode(name), large)
        elif nodetype == "small":
            small = upload.Data("Small enough for a LIT", None)
            d = self.root.add_file(unicode(name), small)

        def _stash_node(node):
            self.nodes[name] = node
            return node
        d.addCallback(_stash_node)

        if mangletype == "good":
            pass
        elif mangletype == "missing-shares":
            d.addCallback(self._delete_some_shares)
        elif mangletype == "corrupt-shares":
            d.addCallback(self._corrupt_some_shares)
        else:
            assert mangletype == "unrecoverable"
            d.addCallback(self._delete_most_shares)

        return d

    def _run_cli(self, argv):
        stdout, stderr = StringIO(), StringIO()
        # this can only do synchronous operations
        assert argv[0] == "debug"
        runner.runner(argv, run_by_human=False, stdout=stdout, stderr=stderr)
        return stdout.getvalue()

    def _find_shares(self, node):
        si = node.get_storage_index()
        out = self._run_cli(["debug", "find-shares", base32.b2a(si)] +
                            [c.basedir for c in self.clients])
        files = out.split("\n")
        return [f for f in files if f]

    def _delete_some_shares(self, node):
        shares = self._find_shares(node)
        os.unlink(shares[0])
        os.unlink(shares[1])

    def _corrupt_some_shares(self, node):
        shares = self._find_shares(node)
        self._run_cli(["debug", "corrupt-share", shares[0]])
        self._run_cli(["debug", "corrupt-share", shares[1]])

    def _delete_most_shares(self, node):
        shares = self._find_shares(node)
        for share in shares[1:]:
            os.unlink(share)


    def check_is_healthy(self, cr, where):
        try:
            self.failUnless(ICheckerResults.providedBy(cr), (cr, type(cr), where))
            self.failUnless(cr.is_healthy(), (cr.get_report(), cr.is_healthy(), cr.get_summary(), where))
            self.failUnless(cr.is_recoverable(), where)
            d = cr.get_data()
            self.failUnlessEqual(d["count-recoverable-versions"], 1, where)
            self.failUnlessEqual(d["count-unrecoverable-versions"], 0, where)
            return cr
        except Exception, le:
            le.args = tuple(le.args + (where,))
            raise

    def check_is_missing_shares(self, cr, where):
        self.failUnless(ICheckerResults.providedBy(cr), where)
        self.failIf(cr.is_healthy(), where)
        self.failUnless(cr.is_recoverable(), where)
        d = cr.get_data()
        self.failUnlessEqual(d["count-recoverable-versions"], 1, where)
        self.failUnlessEqual(d["count-unrecoverable-versions"], 0, where)
        return cr

    def check_has_corrupt_shares(self, cr, where):
        # by "corrupt-shares" we mean the file is still recoverable
        self.failUnless(ICheckerResults.providedBy(cr), where)
        d = cr.get_data()
        self.failIf(cr.is_healthy(), (where, cr))
        self.failUnless(cr.is_recoverable(), where)
        d = cr.get_data()
        self.failUnless(d["count-shares-good"] < 10, where)
        self.failUnless(d["count-corrupt-shares"], where)
        self.failUnless(d["list-corrupt-shares"], where)
        return cr

    def check_is_unrecoverable(self, cr, where):
        self.failUnless(ICheckerResults.providedBy(cr), where)
        d = cr.get_data()
        self.failIf(cr.is_healthy(), where)
        self.failIf(cr.is_recoverable(), where)
        self.failUnless(d["count-shares-good"] < d["count-shares-needed"],
                        where)
        self.failUnlessEqual(d["count-recoverable-versions"], 0, where)
        self.failUnlessEqual(d["count-unrecoverable-versions"], 1, where)
        return cr

    def do_check(self, ignored):
        d = defer.succeed(None)

        # check the individual items, without verification. This will not
        # detect corrupt shares.
        def _check(which, checker):
            d = self.nodes[which].check(Monitor())
            d.addCallback(checker, which + "--check")
            return d

        d.addCallback(lambda ign: _check("mutable-good", self.check_is_healthy))
        d.addCallback(lambda ign: _check("mutable-missing-shares",
                                         self.check_is_missing_shares))
        d.addCallback(lambda ign: _check("mutable-corrupt-shares",
                                         self.check_is_healthy))
        d.addCallback(lambda ign: _check("mutable-unrecoverable",
                                         self.check_is_unrecoverable))
        d.addCallback(lambda ign: _check("large-good", self.check_is_healthy))
        d.addCallback(lambda ign: _check("large-missing-shares",
                                         self.check_is_missing_shares))
        d.addCallback(lambda ign: _check("large-corrupt-shares",
                                         self.check_is_healthy))
        d.addCallback(lambda ign: _check("large-unrecoverable",
                                         self.check_is_unrecoverable))

        # and again with verify=True, which *does* detect corrupt shares.
        def _checkv(which, checker):
            d = self.nodes[which].check(Monitor(), verify=True)
            d.addCallback(checker, which + "--check-and-verify")
            return d

        d.addCallback(lambda ign: _checkv("mutable-good", self.check_is_healthy))
        d.addCallback(lambda ign: _checkv("mutable-missing-shares",
                                         self.check_is_missing_shares))
        d.addCallback(lambda ign: _checkv("mutable-corrupt-shares",
                                         self.check_has_corrupt_shares))
        d.addCallback(lambda ign: _checkv("mutable-unrecoverable",
                                         self.check_is_unrecoverable))
        d.addCallback(lambda ign: _checkv("large-good", self.check_is_healthy))
        # disabled pending immutable verifier
        #d.addCallback(lambda ign: _checkv("large-missing-shares",
        #                                 self.check_is_missing_shares))
        #d.addCallback(lambda ign: _checkv("large-corrupt-shares",
        #                                 self.check_has_corrupt_shares))
        d.addCallback(lambda ign: _checkv("large-unrecoverable",
                                         self.check_is_unrecoverable))

        return d

    def do_deepcheck(self, ignored):
        d = defer.succeed(None)

        # now deep-check the root, with various verify= and repair= options
        d.addCallback(lambda ign:
                      self.root.start_deep_check().when_done())
        def _check1(cr):
            self.failUnless(IDeepCheckResults.providedBy(cr))
            c = cr.get_counters()
            self.failUnlessEqual(c["count-objects-checked"], 9)
            self.failUnlessEqual(c["count-objects-healthy"], 5)
            self.failUnlessEqual(c["count-objects-unhealthy"], 4)
            self.failUnlessEqual(c["count-objects-unrecoverable"], 2)
        d.addCallback(_check1)

        d.addCallback(lambda ign:
                      self.root.start_deep_check(verify=True).when_done())
        def _check2(cr):
            self.failUnless(IDeepCheckResults.providedBy(cr))
            c = cr.get_counters()
            self.failUnlessEqual(c["count-objects-checked"], 9)
            # until we have a real immutable verifier, these counts will be
            # off
            #self.failUnlessEqual(c["count-objects-healthy"], 3)
            #self.failUnlessEqual(c["count-objects-unhealthy"], 6)
            self.failUnlessEqual(c["count-objects-healthy"], 5) # todo
            self.failUnlessEqual(c["count-objects-unhealthy"], 4)
            self.failUnlessEqual(c["count-objects-unrecoverable"], 2)
        d.addCallback(_check2)

        return d

    def json_is_healthy(self, data, where):
        r = data["results"]
        self.failUnless(r["healthy"], where)
        self.failUnless(r["recoverable"], where)
        self.failUnlessEqual(r["count-recoverable-versions"], 1, where)
        self.failUnlessEqual(r["count-unrecoverable-versions"], 0, where)

    def json_is_missing_shares(self, data, where):
        r = data["results"]
        self.failIf(r["healthy"], where)
        self.failUnless(r["recoverable"], where)
        self.failUnlessEqual(r["count-recoverable-versions"], 1, where)
        self.failUnlessEqual(r["count-unrecoverable-versions"], 0, where)

    def json_has_corrupt_shares(self, data, where):
        # by "corrupt-shares" we mean the file is still recoverable
        r = data["results"]
        self.failIf(r["healthy"], where)
        self.failUnless(r["recoverable"], where)
        self.failUnless(r["count-shares-good"] < 10, where)
        self.failUnless(r["count-corrupt-shares"], where)
        self.failUnless(r["list-corrupt-shares"], where)

    def json_is_unrecoverable(self, data, where):
        r = data["results"]
        self.failIf(r["healthy"], where)
        self.failIf(r["recoverable"], where)
        self.failUnless(r["count-shares-good"] < r["count-shares-needed"],
                        where)
        self.failUnlessEqual(r["count-recoverable-versions"], 0, where)
        self.failUnlessEqual(r["count-unrecoverable-versions"], 1, where)

    def do_test_web_bad(self, ignored):
        d = defer.succeed(None)

        # check, no verify
        def _check(which, checker):
            d = self.web_json(self.nodes[which], t="check")
            d.addCallback(checker, which + "--webcheck")
            return d

        d.addCallback(lambda ign: _check("mutable-good",
                                         self.json_is_healthy))
        d.addCallback(lambda ign: _check("mutable-missing-shares",
                                         self.json_is_missing_shares))
        d.addCallback(lambda ign: _check("mutable-corrupt-shares",
                                         self.json_is_healthy))
        d.addCallback(lambda ign: _check("mutable-unrecoverable",
                                         self.json_is_unrecoverable))
        d.addCallback(lambda ign: _check("large-good",
                                         self.json_is_healthy))
        d.addCallback(lambda ign: _check("large-missing-shares",
                                         self.json_is_missing_shares))
        d.addCallback(lambda ign: _check("large-corrupt-shares",
                                         self.json_is_healthy))
        d.addCallback(lambda ign: _check("large-unrecoverable",
                                         self.json_is_unrecoverable))

        # check and verify
        def _checkv(which, checker):
            d = self.web_json(self.nodes[which], t="check", verify="true")
            d.addCallback(checker, which + "--webcheck-and-verify")
            return d

        d.addCallback(lambda ign: _checkv("mutable-good",
                                          self.json_is_healthy))
        d.addCallback(lambda ign: _checkv("mutable-missing-shares",
                                         self.json_is_missing_shares))
        d.addCallback(lambda ign: _checkv("mutable-corrupt-shares",
                                         self.json_has_corrupt_shares))
        d.addCallback(lambda ign: _checkv("mutable-unrecoverable",
                                         self.json_is_unrecoverable))
        d.addCallback(lambda ign: _checkv("large-good",
                                          self.json_is_healthy))
        # disabled pending immutable verifier
        #d.addCallback(lambda ign: _checkv("large-missing-shares",
        #                                 self.json_is_missing_shares))
        #d.addCallback(lambda ign: _checkv("large-corrupt-shares",
        #                                 self.json_has_corrupt_shares))
        d.addCallback(lambda ign: _checkv("large-unrecoverable",
                                         self.json_is_unrecoverable))

        return d
