import os, re, base64
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.internet import defer, reactor
from allmydata import uri, client
from allmydata.nodemaker import NodeMaker
from allmydata.util import base32, consumer, fileutil, mathutil
from allmydata.util.hashutil import tagged_hash, ssk_writekey_hash, \
     ssk_pubkey_fingerprint_hash
from allmydata.util.consumer import MemoryConsumer
from allmydata.util.deferredutil import gatherResults
from allmydata.interfaces import IRepairResults, ICheckAndRepairResults, \
     NotEnoughSharesError, SDMF_VERSION, MDMF_VERSION, DownloadStopped
from allmydata.monitor import Monitor
from allmydata.test.common import ShouldFailMixin
from allmydata.test.no_network import GridTestMixin
from foolscap.api import eventually, fireEventually
from foolscap.logging import log
from allmydata.storage_client import StorageFarmBroker
from allmydata.storage.common import storage_index_to_dir
from allmydata.scripts import debug

from allmydata.mutable.filenode import MutableFileNode, BackoffAgent
from allmydata.mutable.common import ResponseCache, \
     MODE_CHECK, MODE_ANYTHING, MODE_WRITE, MODE_READ, \
     NeedMoreDataError, UnrecoverableFileError, UncoordinatedWriteError, \
     NotEnoughServersError, CorruptShareError
from allmydata.mutable.retrieve import Retrieve
from allmydata.mutable.publish import Publish, MutableFileHandle, \
                                      MutableData, \
                                      DEFAULT_MAX_SEGMENT_SIZE
from allmydata.mutable.servermap import ServerMap, ServermapUpdater
from allmydata.mutable.layout import unpack_header, MDMFSlotReadProxy
from allmydata.mutable.repairer import MustForceRepairError

import allmydata.test.common_util as testutil
from allmydata.test.common import TEST_RSA_KEY_SIZE
from allmydata.test.test_download import PausingConsumer, \
     PausingAndStoppingConsumer, StoppingConsumer, \
     ImmediatelyStoppingConsumer


# this "FakeStorage" exists to put the share data in RAM and avoid using real
# network connections, both to speed up the tests and to reduce the amount of
# non-mutable.py code being exercised.

class FakeStorage:
    # this class replaces the collection of storage servers, allowing the
    # tests to examine and manipulate the published shares. It also lets us
    # control the order in which read queries are answered, to exercise more
    # of the error-handling code in Retrieve .
    #
    # Note that we ignore the storage index: this FakeStorage instance can
    # only be used for a single storage index.


    def __init__(self):
        self._peers = {}
        # _sequence is used to cause the responses to occur in a specific
        # order. If it is in use, then we will defer queries instead of
        # answering them right away, accumulating the Deferreds in a dict. We
        # don't know exactly how many queries we'll get, so exactly one
        # second after the first query arrives, we will release them all (in
        # order).
        self._sequence = None
        self._pending = {}
        self._pending_timer = None

    def read(self, peerid, storage_index):
        shares = self._peers.get(peerid, {})
        if self._sequence is None:
            return defer.succeed(shares)
        d = defer.Deferred()
        if not self._pending:
            self._pending_timer = reactor.callLater(1.0, self._fire_readers)
        if peerid not in self._pending:
            self._pending[peerid] = []
        self._pending[peerid].append( (d, shares) )
        return d

    def _fire_readers(self):
        self._pending_timer = None
        pending = self._pending
        self._pending = {}
        for peerid in self._sequence:
            if peerid in pending:
                for (d, shares) in pending.pop(peerid):
                    eventually(d.callback, shares)
        for peerid in pending:
            for (d, shares) in pending[peerid]:
                eventually(d.callback, shares)

    def write(self, peerid, storage_index, shnum, offset, data):
        if peerid not in self._peers:
            self._peers[peerid] = {}
        shares = self._peers[peerid]
        f = StringIO()
        f.write(shares.get(shnum, ""))
        f.seek(offset)
        f.write(data)
        shares[shnum] = f.getvalue()


class FakeStorageServer:
    def __init__(self, peerid, storage):
        self.peerid = peerid
        self.storage = storage
        self.queries = 0
    def callRemote(self, methname, *args, **kwargs):
        self.queries += 1
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        d = fireEventually()
        d.addCallback(lambda res: _call())
        return d

    def callRemoteOnly(self, methname, *args, **kwargs):
        self.queries += 1
        d = self.callRemote(methname, *args, **kwargs)
        d.addBoth(lambda ignore: None)
        pass

    def advise_corrupt_share(self, share_type, storage_index, shnum, reason):
        pass

    def slot_readv(self, storage_index, shnums, readv):
        d = self.storage.read(self.peerid, storage_index)
        def _read(shares):
            response = {}
            for shnum in shares:
                if shnums and shnum not in shnums:
                    continue
                vector = response[shnum] = []
                for (offset, length) in readv:
                    assert isinstance(offset, (int, long)), offset
                    assert isinstance(length, (int, long)), length
                    vector.append(shares[shnum][offset:offset+length])
            return response
        d.addCallback(_read)
        return d

    def slot_testv_and_readv_and_writev(self, storage_index, secrets,
                                        tw_vectors, read_vector):
        # always-pass: parrot the test vectors back to them.
        readv = {}
        for shnum, (testv, writev, new_length) in tw_vectors.items():
            for (offset, length, op, specimen) in testv:
                assert op in ("le", "eq", "ge")
            # TODO: this isn't right, the read is controlled by read_vector,
            # not by testv
            readv[shnum] = [ specimen
                             for (offset, length, op, specimen)
                             in testv ]
            for (offset, data) in writev:
                self.storage.write(self.peerid, storage_index, shnum,
                                   offset, data)
        answer = (True, readv)
        return fireEventually(answer)


def flip_bit(original, byte_offset):
    return (original[:byte_offset] +
            chr(ord(original[byte_offset]) ^ 0x01) +
            original[byte_offset+1:])

def add_two(original, byte_offset):
    # It isn't enough to simply flip the bit for the version number,
    # because 1 is a valid version number. So we add two instead.
    return (original[:byte_offset] +
            chr(ord(original[byte_offset]) ^ 0x02) +
            original[byte_offset+1:])

def corrupt(res, s, offset, shnums_to_corrupt=None, offset_offset=0):
    # if shnums_to_corrupt is None, corrupt all shares. Otherwise it is a
    # list of shnums to corrupt.
    ds = []
    for peerid in s._peers:
        shares = s._peers[peerid]
        for shnum in shares:
            if (shnums_to_corrupt is not None
                and shnum not in shnums_to_corrupt):
                continue
            data = shares[shnum]
            # We're feeding the reader all of the share data, so it
            # won't need to use the rref that we didn't provide, nor the
            # storage index that we didn't provide. We do this because
            # the reader will work for both MDMF and SDMF.
            reader = MDMFSlotReadProxy(None, None, shnum, data)
            # We need to get the offsets for the next part.
            d = reader.get_verinfo()
            def _do_corruption(verinfo, data, shnum, shares):
                (seqnum,
                 root_hash,
                 IV,
                 segsize,
                 datalen,
                 k, n, prefix, o) = verinfo
                if isinstance(offset, tuple):
                    offset1, offset2 = offset
                else:
                    offset1 = offset
                    offset2 = 0
                if offset1 == "pubkey" and IV:
                    real_offset = 107
                elif offset1 in o:
                    real_offset = o[offset1]
                else:
                    real_offset = offset1
                real_offset = int(real_offset) + offset2 + offset_offset
                assert isinstance(real_offset, int), offset
                if offset1 == 0: # verbyte
                    f = add_two
                else:
                    f = flip_bit
                shares[shnum] = f(data, real_offset)
            d.addCallback(_do_corruption, data, shnum, shares)
            ds.append(d)
    dl = defer.DeferredList(ds)
    dl.addCallback(lambda ignored: res)
    return dl

def make_storagebroker(s=None, num_peers=10):
    if not s:
        s = FakeStorage()
    peerids = [tagged_hash("peerid", "%d" % i)[:20]
               for i in range(num_peers)]
    storage_broker = StorageFarmBroker(None, True)
    for peerid in peerids:
        fss = FakeStorageServer(peerid, s)
        storage_broker.test_add_rref(peerid, fss)
    return storage_broker

def make_nodemaker(s=None, num_peers=10):
    storage_broker = make_storagebroker(s, num_peers)
    sh = client.SecretHolder("lease secret", "convergence secret")
    keygen = client.KeyGenerator()
    keygen.set_default_keysize(TEST_RSA_KEY_SIZE)
    nodemaker = NodeMaker(storage_broker, sh, None,
                          None, None,
                          {"k": 3, "n": 10}, SDMF_VERSION, keygen)
    return nodemaker

class Filenode(unittest.TestCase, testutil.ShouldFailMixin):
    # this used to be in Publish, but we removed the limit. Some of
    # these tests test whether the new code correctly allows files
    # larger than the limit.
    OLD_MAX_SEGMENT_SIZE = 3500000
    def setUp(self):
        self._storage = s = FakeStorage()
        self.nodemaker = make_nodemaker(s)

    def test_create(self):
        d = self.nodemaker.create_mutable_file()
        def _created(n):
            self.failUnless(isinstance(n, MutableFileNode))
            self.failUnlessEqual(n.get_storage_index(), n._storage_index)
            sb = self.nodemaker.storage_broker
            peer0 = sorted(sb.get_all_serverids())[0]
            shnums = self._storage._peers[peer0].keys()
            self.failUnlessEqual(len(shnums), 1)
        d.addCallback(_created)
        return d


    def test_create_mdmf(self):
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.failUnless(isinstance(n, MutableFileNode))
            self.failUnlessEqual(n.get_storage_index(), n._storage_index)
            sb = self.nodemaker.storage_broker
            peer0 = sorted(sb.get_all_serverids())[0]
            shnums = self._storage._peers[peer0].keys()
            self.failUnlessEqual(len(shnums), 1)
        d.addCallback(_created)
        return d

    def test_single_share(self):
        # Make sure that we tolerate publishing a single share.
        self.nodemaker.default_encoding_parameters['k'] = 1
        self.nodemaker.default_encoding_parameters['happy'] = 1
        self.nodemaker.default_encoding_parameters['n'] = 1
        d = defer.succeed(None)
        for v in (SDMF_VERSION, MDMF_VERSION):
            d.addCallback(lambda ignored, v=v:
                self.nodemaker.create_mutable_file(version=v))
            def _created(n):
                self.failUnless(isinstance(n, MutableFileNode))
                self._node = n
                return n
            d.addCallback(_created)
            d.addCallback(lambda n:
                n.overwrite(MutableData("Contents" * 50000)))
            d.addCallback(lambda ignored:
                self._node.download_best_version())
            d.addCallback(lambda contents:
                self.failUnlessEqual(contents, "Contents" * 50000))
        return d

    def test_max_shares(self):
        self.nodemaker.default_encoding_parameters['n'] = 255
        d = self.nodemaker.create_mutable_file(version=SDMF_VERSION)
        def _created(n):
            self.failUnless(isinstance(n, MutableFileNode))
            self.failUnlessEqual(n.get_storage_index(), n._storage_index)
            sb = self.nodemaker.storage_broker
            num_shares = sum([len(self._storage._peers[x].keys()) for x \
                              in sb.get_all_serverids()])
            self.failUnlessEqual(num_shares, 255)
            self._node = n
            return n
        d.addCallback(_created)
        # Now we upload some contents
        d.addCallback(lambda n:
            n.overwrite(MutableData("contents" * 50000)))
        # ...then download contents
        d.addCallback(lambda ignored:
            self._node.download_best_version())
        # ...and check to make sure everything went okay.
        d.addCallback(lambda contents:
            self.failUnlessEqual("contents" * 50000, contents))
        return d

    def test_max_shares_mdmf(self):
        # Test how files behave when there are 255 shares.
        self.nodemaker.default_encoding_parameters['n'] = 255
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.failUnless(isinstance(n, MutableFileNode))
            self.failUnlessEqual(n.get_storage_index(), n._storage_index)
            sb = self.nodemaker.storage_broker
            num_shares = sum([len(self._storage._peers[x].keys()) for x \
                              in sb.get_all_serverids()])
            self.failUnlessEqual(num_shares, 255)
            self._node = n
            return n
        d.addCallback(_created)
        d.addCallback(lambda n:
            n.overwrite(MutableData("contents" * 50000)))
        d.addCallback(lambda ignored:
            self._node.download_best_version())
        d.addCallback(lambda contents:
            self.failUnlessEqual(contents, "contents" * 50000))
        return d

    def test_mdmf_filenode_cap(self):
        # Test that an MDMF filenode, once created, returns an MDMF URI.
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.failUnless(isinstance(n, MutableFileNode))
            cap = n.get_cap()
            self.failUnless(isinstance(cap, uri.WriteableMDMFFileURI))
            rcap = n.get_readcap()
            self.failUnless(isinstance(rcap, uri.ReadonlyMDMFFileURI))
            vcap = n.get_verify_cap()
            self.failUnless(isinstance(vcap, uri.MDMFVerifierURI))
        d.addCallback(_created)
        return d


    def test_create_from_mdmf_writecap(self):
        # Test that the nodemaker is capable of creating an MDMF
        # filenode given an MDMF cap.
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.failUnless(isinstance(n, MutableFileNode))
            s = n.get_uri()
            self.failUnless(s.startswith("URI:MDMF"))
            n2 = self.nodemaker.create_from_cap(s)
            self.failUnless(isinstance(n2, MutableFileNode))
            self.failUnlessEqual(n.get_storage_index(), n2.get_storage_index())
            self.failUnlessEqual(n.get_uri(), n2.get_uri())
        d.addCallback(_created)
        return d


    def test_create_from_mdmf_readcap(self):
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.failUnless(isinstance(n, MutableFileNode))
            s = n.get_readonly_uri()
            n2 = self.nodemaker.create_from_cap(s)
            self.failUnless(isinstance(n2, MutableFileNode))

            # Check that it's a readonly node
            self.failUnless(n2.is_readonly())
        d.addCallback(_created)
        return d


    def test_internal_version_from_cap(self):
        # MutableFileNodes and MutableFileVersions have an internal
        # switch that tells them whether they're dealing with an SDMF or
        # MDMF mutable file when they start doing stuff. We want to make
        # sure that this is set appropriately given an MDMF cap.
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.uri = n.get_uri()
            self.failUnlessEqual(n._protocol_version, MDMF_VERSION)

            n2 = self.nodemaker.create_from_cap(self.uri)
            self.failUnlessEqual(n2._protocol_version, MDMF_VERSION)
        d.addCallback(_created)
        return d


    def test_serialize(self):
        n = MutableFileNode(None, None, {"k": 3, "n": 10}, None)
        calls = []
        def _callback(*args, **kwargs):
            self.failUnlessEqual(args, (4,) )
            self.failUnlessEqual(kwargs, {"foo": 5})
            calls.append(1)
            return 6
        d = n._do_serialized(_callback, 4, foo=5)
        def _check_callback(res):
            self.failUnlessEqual(res, 6)
            self.failUnlessEqual(calls, [1])
        d.addCallback(_check_callback)

        def _errback():
            raise ValueError("heya")
        d.addCallback(lambda res:
                      self.shouldFail(ValueError, "_check_errback", "heya",
                                      n._do_serialized, _errback))
        return d

    def test_upload_and_download(self):
        d = self.nodemaker.create_mutable_file()
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            d.addCallback(lambda smap: smap.dump(StringIO()))
            d.addCallback(lambda sio:
                          self.failUnless("3-of-10" in sio.getvalue()))
            d.addCallback(lambda res: n.overwrite(MutableData("contents 1")))
            d.addCallback(lambda res: self.failUnlessIdentical(res, None))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.get_size_of_best_version())
            d.addCallback(lambda size:
                          self.failUnlessEqual(size, len("contents 1")))
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            d.addCallback(lambda smap: n.upload(MutableData("contents 3"), smap))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            d.addCallback(lambda res: n.get_servermap(MODE_ANYTHING))
            d.addCallback(lambda smap:
                          n.download_version(smap,
                                             smap.best_recoverable_version()))
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            # test a file that is large enough to overcome the
            # mapupdate-to-retrieve data caching (i.e. make the shares larger
            # than the default readsize, which is 2000 bytes). A 15kB file
            # will have 5kB shares.
            d.addCallback(lambda res: n.overwrite(MutableData("large size file" * 1000)))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res:
                          self.failUnlessEqual(res, "large size file" * 1000))
            return d
        d.addCallback(_created)
        return d


    def test_upload_and_download_mdmf(self):
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda ignored:
                n.get_servermap(MODE_READ))
            def _then(servermap):
                dumped = servermap.dump(StringIO())
                self.failUnlessIn("3-of-10", dumped.getvalue())
            d.addCallback(_then)
            # Now overwrite the contents with some new contents. We want 
            # to make them big enough to force the file to be uploaded
            # in more than one segment.
            big_contents = "contents1" * 100000 # about 900 KiB
            big_contents_uploadable = MutableData(big_contents)
            d.addCallback(lambda ignored:
                n.overwrite(big_contents_uploadable))
            d.addCallback(lambda ignored:
                n.download_best_version())
            d.addCallback(lambda data:
                self.failUnlessEqual(data, big_contents))
            # Overwrite the contents again with some new contents. As
            # before, they need to be big enough to force multiple
            # segments, so that we make the downloader deal with
            # multiple segments.
            bigger_contents = "contents2" * 1000000 # about 9MiB 
            bigger_contents_uploadable = MutableData(bigger_contents)
            d.addCallback(lambda ignored:
                n.overwrite(bigger_contents_uploadable))
            d.addCallback(lambda ignored:
                n.download_best_version())
            d.addCallback(lambda data:
                self.failUnlessEqual(data, bigger_contents))
            return d
        d.addCallback(_created)
        return d


    def test_retrieve_producer_mdmf(self):
        # We should make sure that the retriever is able to pause and stop
        # correctly.
        data = "contents1" * 100000
        d = self.nodemaker.create_mutable_file(MutableData(data),
                                               version=MDMF_VERSION)
        d.addCallback(lambda node: node.get_best_mutable_version())
        d.addCallback(self._test_retrieve_producer, "MDMF", data)
        return d

    # note: SDMF has only one big segment, so we can't use the usual
    # after-the-first-write() trick to pause or stop the download.
    # Disabled until we find a better approach.
    def OFF_test_retrieve_producer_sdmf(self):
        data = "contents1" * 100000
        d = self.nodemaker.create_mutable_file(MutableData(data),
                                               version=SDMF_VERSION)
        d.addCallback(lambda node: node.get_best_mutable_version())
        d.addCallback(self._test_retrieve_producer, "SDMF", data)
        return d

    def _test_retrieve_producer(self, version, kind, data):
        # Now we'll retrieve it into a pausing consumer.
        c = PausingConsumer()
        d = version.read(c)
        d.addCallback(lambda ign: self.failUnlessEqual(c.size, len(data)))

        c2 = PausingAndStoppingConsumer()
        d.addCallback(lambda ign:
                      self.shouldFail(DownloadStopped, kind+"_pause_stop",
                                      "our Consumer called stopProducing()",
                                      version.read, c2))

        c3 = StoppingConsumer()
        d.addCallback(lambda ign:
                      self.shouldFail(DownloadStopped, kind+"_stop",
                                      "our Consumer called stopProducing()",
                                      version.read, c3))

        c4 = ImmediatelyStoppingConsumer()
        d.addCallback(lambda ign:
                      self.shouldFail(DownloadStopped, kind+"_stop_imm",
                                      "our Consumer called stopProducing()",
                                      version.read, c4))

        def _then(ign):
            c5 = MemoryConsumer()
            d1 = version.read(c5)
            c5.producer.stopProducing()
            return self.shouldFail(DownloadStopped, kind+"_stop_imm2",
                                   "our Consumer called stopProducing()",
                                   lambda: d1)
        d.addCallback(_then)
        return d

    def test_download_from_mdmf_cap(self):
        # We should be able to download an MDMF file given its cap
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(node):
            self.uri = node.get_uri()
            # also confirm that the cap has no extension fields
            pieces = self.uri.split(":")
            self.failUnlessEqual(len(pieces), 4)

            return node.overwrite(MutableData("contents1" * 100000))
        def _then(ignored):
            node = self.nodemaker.create_from_cap(self.uri)
            return node.download_best_version()
        def _downloaded(data):
            self.failUnlessEqual(data, "contents1" * 100000)
        d.addCallback(_created)
        d.addCallback(_then)
        d.addCallback(_downloaded)
        return d


    def test_mdmf_write_count(self):
        # Publishing an MDMF file should only cause one write for each
        # share that is to be published. Otherwise, we introduce
        # undesirable semantics that are a regression from SDMF
        upload = MutableData("MDMF" * 100000) # about 400 KiB
        d = self.nodemaker.create_mutable_file(upload,
                                               version=MDMF_VERSION)
        def _check_server_write_counts(ignored):
            sb = self.nodemaker.storage_broker
            for server in sb.servers.itervalues():
                self.failUnlessEqual(server.get_rref().queries, 1)
        d.addCallback(_check_server_write_counts)
        return d


    def test_create_with_initial_contents(self):
        upload1 = MutableData("contents 1")
        d = self.nodemaker.create_mutable_file(upload1)
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            upload2 = MutableData("contents 2")
            d.addCallback(lambda res: n.overwrite(upload2))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d


    def test_create_mdmf_with_initial_contents(self):
        initial_contents = "foobarbaz" * 131072 # 900KiB
        initial_contents_uploadable = MutableData(initial_contents)
        d = self.nodemaker.create_mutable_file(initial_contents_uploadable,
                                               version=MDMF_VERSION)
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda data:
                self.failUnlessEqual(data, initial_contents))
            uploadable2 = MutableData(initial_contents + "foobarbaz")
            d.addCallback(lambda ignored:
                n.overwrite(uploadable2))
            d.addCallback(lambda ignored:
                n.download_best_version())
            d.addCallback(lambda data:
                self.failUnlessEqual(data, initial_contents +
                                           "foobarbaz"))
            return d
        d.addCallback(_created)
        return d


    def test_response_cache_memory_leak(self):
        d = self.nodemaker.create_mutable_file("contents")
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents"))
            d.addCallback(lambda ign: self.failUnless(isinstance(n._cache, ResponseCache)))

            def _check_cache(expected):
                # The total size of cache entries should not increase on the second download;
                # in fact the cache contents should be identical.
                d2 = n.download_best_version()
                d2.addCallback(lambda rep: self.failUnlessEqual(repr(n._cache.cache), expected))
                return d2
            d.addCallback(lambda ign: _check_cache(repr(n._cache.cache)))
            return d
        d.addCallback(_created)
        return d

    def test_create_with_initial_contents_function(self):
        data = "initial contents"
        def _make_contents(n):
            self.failUnless(isinstance(n, MutableFileNode))
            key = n.get_writekey()
            self.failUnless(isinstance(key, str), key)
            self.failUnlessEqual(len(key), 16) # AES key size
            return MutableData(data)
        d = self.nodemaker.create_mutable_file(_make_contents)
        def _created(n):
            return n.download_best_version()
        d.addCallback(_created)
        d.addCallback(lambda data2: self.failUnlessEqual(data2, data))
        return d


    def test_create_mdmf_with_initial_contents_function(self):
        data = "initial contents" * 100000
        def _make_contents(n):
            self.failUnless(isinstance(n, MutableFileNode))
            key = n.get_writekey()
            self.failUnless(isinstance(key, str), key)
            self.failUnlessEqual(len(key), 16)
            return MutableData(data)
        d = self.nodemaker.create_mutable_file(_make_contents,
                                               version=MDMF_VERSION)
        d.addCallback(lambda n:
            n.download_best_version())
        d.addCallback(lambda data2:
            self.failUnlessEqual(data2, data))
        return d


    def test_create_with_too_large_contents(self):
        BIG = "a" * (self.OLD_MAX_SEGMENT_SIZE + 1)
        BIG_uploadable = MutableData(BIG)
        d = self.nodemaker.create_mutable_file(BIG_uploadable)
        def _created(n):
            other_BIG_uploadable = MutableData(BIG)
            d = n.overwrite(other_BIG_uploadable)
            return d
        d.addCallback(_created)
        return d

    def failUnlessCurrentSeqnumIs(self, n, expected_seqnum, which):
        d = n.get_servermap(MODE_READ)
        d.addCallback(lambda servermap: servermap.best_recoverable_version())
        d.addCallback(lambda verinfo:
                      self.failUnlessEqual(verinfo[0], expected_seqnum, which))
        return d

    def test_modify(self):
        def _modifier(old_contents, servermap, first_time):
            new_contents = old_contents + "line2"
            return new_contents
        def _non_modifier(old_contents, servermap, first_time):
            return old_contents
        def _none_modifier(old_contents, servermap, first_time):
            return None
        def _error_modifier(old_contents, servermap, first_time):
            raise ValueError("oops")
        def _toobig_modifier(old_contents, servermap, first_time):
            new_content = "b" * (self.OLD_MAX_SEGMENT_SIZE + 1)
            return new_content
        calls = []
        def _ucw_error_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            new_contents = old_contents + "line3"
            return new_contents
        def _ucw_error_non_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once, and don't actually
            # modify the contents on subsequent invocations
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents

        initial_contents = "line1"
        d = self.nodemaker.create_mutable_file(MutableData(initial_contents))
        def _created(n):
            d = n.modify(_modifier)
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "m"))

            d.addCallback(lambda res: n.modify(_non_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "non"))

            d.addCallback(lambda res: n.modify(_none_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "none"))

            d.addCallback(lambda res:
                          self.shouldFail(ValueError, "error_modifier", None,
                                          n.modify, _error_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "err"))


            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "big"))

            d.addCallback(lambda res: n.modify(_ucw_error_modifier))
            d.addCallback(lambda res: self.failUnlessEqual(len(calls), 2))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "ucw"))

            def _reset_ucw_error_modifier(res):
                calls[:] = []
                return res
            d.addCallback(_reset_ucw_error_modifier)

            # in practice, this n.modify call should publish twice: the first
            # one gets a UCWE, the second does not. But our test jig (in
            # which the modifier raises the UCWE) skips over the first one,
            # so in this test there will be only one publish, and the seqnum
            # will only be one larger than the previous test, not two (i.e. 4
            # instead of 5).
            d.addCallback(lambda res: n.modify(_ucw_error_non_modifier))
            d.addCallback(lambda res: self.failUnlessEqual(len(calls), 2))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 4, "ucw"))
            d.addCallback(lambda res: n.modify(_toobig_modifier))
            return d
        d.addCallback(_created)
        return d


    def test_modify_backoffer(self):
        def _modifier(old_contents, servermap, first_time):
            return old_contents + "line2"
        calls = []
        def _ucw_error_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents + "line3"
        def _always_ucw_error_modifier(old_contents, servermap, first_time):
            raise UncoordinatedWriteError("simulated")
        def _backoff_stopper(node, f):
            return f
        def _backoff_pauser(node, f):
            d = defer.Deferred()
            reactor.callLater(0.5, d.callback, None)
            return d

        # the give-up-er will hit its maximum retry count quickly
        giveuper = BackoffAgent()
        giveuper._delay = 0.1
        giveuper.factor = 1

        d = self.nodemaker.create_mutable_file(MutableData("line1"))
        def _created(n):
            d = n.modify(_modifier)
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "m"))

            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "_backoff_stopper", None,
                                          n.modify, _ucw_error_modifier,
                                          _backoff_stopper))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "stop"))

            def _reset_ucw_error_modifier(res):
                calls[:] = []
                return res
            d.addCallback(_reset_ucw_error_modifier)
            d.addCallback(lambda res: n.modify(_ucw_error_modifier,
                                               _backoff_pauser))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "pause"))

            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "giveuper", None,
                                          n.modify, _always_ucw_error_modifier,
                                          giveuper.delay))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "giveup"))

            return d
        d.addCallback(_created)
        return d

    def test_upload_and_download_full_size_keys(self):
        self.nodemaker.key_generator = client.KeyGenerator()
        d = self.nodemaker.create_mutable_file()
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            d.addCallback(lambda smap: smap.dump(StringIO()))
            d.addCallback(lambda sio:
                          self.failUnless("3-of-10" in sio.getvalue()))
            d.addCallback(lambda res: n.overwrite(MutableData("contents 1")))
            d.addCallback(lambda res: self.failUnlessIdentical(res, None))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            d.addCallback(lambda smap: n.upload(MutableData("contents 3"), smap))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            d.addCallback(lambda res: n.get_servermap(MODE_ANYTHING))
            d.addCallback(lambda smap:
                          n.download_version(smap,
                                             smap.best_recoverable_version()))
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            return d
        d.addCallback(_created)
        return d


    def test_size_after_servermap_update(self):
        # a mutable file node should have something to say about how big
        # it is after a servermap update is performed, since this tells
        # us how large the best version of that mutable file is.
        d = self.nodemaker.create_mutable_file()
        def _created(n):
            self.n = n
            return n.get_servermap(MODE_READ)
        d.addCallback(_created)
        d.addCallback(lambda ignored:
            self.failUnlessEqual(self.n.get_size(), 0))
        d.addCallback(lambda ignored:
            self.n.overwrite(MutableData("foobarbaz")))
        d.addCallback(lambda ignored:
            self.failUnlessEqual(self.n.get_size(), 9))
        d.addCallback(lambda ignored:
            self.nodemaker.create_mutable_file(MutableData("foobarbaz")))
        d.addCallback(_created)
        d.addCallback(lambda ignored:
            self.failUnlessEqual(self.n.get_size(), 9))
        return d


class PublishMixin:
    def publish_one(self):
        # publish a file and create shares, which can then be manipulated
        # later.
        self.CONTENTS = "New contents go here" * 1000
        self.uploadable = MutableData(self.CONTENTS)
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage)
        self._storage_broker = self._nodemaker.storage_broker
        d = self._nodemaker.create_mutable_file(self.uploadable)
        def _created(node):
            self._fn = node
            self._fn2 = self._nodemaker.create_from_cap(node.get_uri())
        d.addCallback(_created)
        return d

    def publish_mdmf(self):
        # like publish_one, except that the result is guaranteed to be
        # an MDMF file.
        # self.CONTENTS should have more than one segment.
        self.CONTENTS = "This is an MDMF file" * 100000
        self.uploadable = MutableData(self.CONTENTS)
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage)
        self._storage_broker = self._nodemaker.storage_broker
        d = self._nodemaker.create_mutable_file(self.uploadable, version=MDMF_VERSION)
        def _created(node):
            self._fn = node
            self._fn2 = self._nodemaker.create_from_cap(node.get_uri())
        d.addCallback(_created)
        return d


    def publish_sdmf(self):
        # like publish_one, except that the result is guaranteed to be
        # an SDMF file
        self.CONTENTS = "This is an SDMF file" * 1000
        self.uploadable = MutableData(self.CONTENTS)
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage)
        self._storage_broker = self._nodemaker.storage_broker
        d = self._nodemaker.create_mutable_file(self.uploadable, version=SDMF_VERSION)
        def _created(node):
            self._fn = node
            self._fn2 = self._nodemaker.create_from_cap(node.get_uri())
        d.addCallback(_created)
        return d


    def publish_multiple(self, version=0):
        self.CONTENTS = ["Contents 0",
                         "Contents 1",
                         "Contents 2",
                         "Contents 3a",
                         "Contents 3b"]
        self.uploadables = [MutableData(d) for d in self.CONTENTS]
        self._copied_shares = {}
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage)
        d = self._nodemaker.create_mutable_file(self.uploadables[0], version=version) # seqnum=1
        def _created(node):
            self._fn = node
            # now create multiple versions of the same file, and accumulate
            # their shares, so we can mix and match them later.
            d = defer.succeed(None)
            d.addCallback(self._copy_shares, 0)
            d.addCallback(lambda res: node.overwrite(self.uploadables[1])) #s2
            d.addCallback(self._copy_shares, 1)
            d.addCallback(lambda res: node.overwrite(self.uploadables[2])) #s3
            d.addCallback(self._copy_shares, 2)
            d.addCallback(lambda res: node.overwrite(self.uploadables[3])) #s4a
            d.addCallback(self._copy_shares, 3)
            # now we replace all the shares with version s3, and upload a new
            # version to get s4b.
            rollback = dict([(i,2) for i in range(10)])
            d.addCallback(lambda res: self._set_versions(rollback))
            d.addCallback(lambda res: node.overwrite(self.uploadables[4])) #s4b
            d.addCallback(self._copy_shares, 4)
            # we leave the storage in state 4
            return d
        d.addCallback(_created)
        return d


    def _copy_shares(self, ignored, index):
        shares = self._storage._peers
        # we need a deep copy
        new_shares = {}
        for peerid in shares:
            new_shares[peerid] = {}
            for shnum in shares[peerid]:
                new_shares[peerid][shnum] = shares[peerid][shnum]
        self._copied_shares[index] = new_shares

    def _set_versions(self, versionmap):
        # versionmap maps shnums to which version (0,1,2,3,4) we want the
        # share to be at. Any shnum which is left out of the map will stay at
        # its current version.
        shares = self._storage._peers
        oldshares = self._copied_shares
        for peerid in shares:
            for shnum in shares[peerid]:
                if shnum in versionmap:
                    index = versionmap[shnum]
                    shares[peerid][shnum] = oldshares[index][peerid][shnum]

class Servermap(unittest.TestCase, PublishMixin):
    def setUp(self):
        return self.publish_one()

    def make_servermap(self, mode=MODE_CHECK, fn=None, sb=None,
                       update_range=None):
        if fn is None:
            fn = self._fn
        if sb is None:
            sb = self._storage_broker
        smu = ServermapUpdater(fn, sb, Monitor(),
                               ServerMap(), mode, update_range=update_range)
        d = smu.update()
        return d

    def update_servermap(self, oldmap, mode=MODE_CHECK):
        smu = ServermapUpdater(self._fn, self._storage_broker, Monitor(),
                               oldmap, mode)
        d = smu.update()
        return d

    def failUnlessOneRecoverable(self, sm, num_shares):
        self.failUnlessEqual(len(sm.recoverable_versions()), 1)
        self.failUnlessEqual(len(sm.unrecoverable_versions()), 0)
        best = sm.best_recoverable_version()
        self.failIfEqual(best, None)
        self.failUnlessEqual(sm.recoverable_versions(), set([best]))
        self.failUnlessEqual(len(sm.shares_available()), 1)
        self.failUnlessEqual(sm.shares_available()[best], (num_shares, 3, 10))
        shnum, peerids = sm.make_sharemap().items()[0]
        peerid = list(peerids)[0]
        self.failUnlessEqual(sm.version_on_peer(peerid, shnum), best)
        self.failUnlessEqual(sm.version_on_peer(peerid, 666), None)
        return sm

    def test_basic(self):
        d = defer.succeed(None)
        ms = self.make_servermap
        us = self.update_servermap

        d.addCallback(lambda res: ms(mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda res: ms(mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda res: ms(mode=MODE_READ))
        # this mode stops at k+epsilon, and epsilon=k, so 6 shares
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 6))
        d.addCallback(lambda res: ms(mode=MODE_ANYTHING))
        # this mode stops at 'k' shares
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 3))

        # and can we re-use the same servermap? Note that these are sorted in
        # increasing order of number of servers queried, since once a server
        # gets into the servermap, we'll always ask it for an update.
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 3))
        d.addCallback(lambda sm: us(sm, mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 6))
        d.addCallback(lambda sm: us(sm, mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda sm: us(sm, mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda sm: us(sm, mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))

        return d

    def test_fetch_privkey(self):
        d = defer.succeed(None)
        # use the sibling filenode (which hasn't been used yet), and make
        # sure it can fetch the privkey. The file is small, so the privkey
        # will be fetched on the first (query) pass.
        d.addCallback(lambda res: self.make_servermap(MODE_WRITE, self._fn2))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))

        # create a new file, which is large enough to knock the privkey out
        # of the early part of the file
        LARGE = "These are Larger contents" * 200 # about 5KB
        LARGE_uploadable = MutableData(LARGE)
        d.addCallback(lambda res: self._nodemaker.create_mutable_file(LARGE_uploadable))
        def _created(large_fn):
            large_fn2 = self._nodemaker.create_from_cap(large_fn.get_uri())
            return self.make_servermap(MODE_WRITE, large_fn2)
        d.addCallback(_created)
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        return d


    def test_mark_bad(self):
        d = defer.succeed(None)
        ms = self.make_servermap

        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 6))
        def _made_map(sm):
            v = sm.best_recoverable_version()
            vm = sm.make_versionmap()
            shares = list(vm[v])
            self.failUnlessEqual(len(shares), 6)
            self._corrupted = set()
            # mark the first 5 shares as corrupt, then update the servermap.
            # The map should not have the marked shares it in any more, and
            # new shares should be found to replace the missing ones.
            for (shnum, peerid, timestamp) in shares:
                if shnum < 5:
                    self._corrupted.add( (peerid, shnum) )
                    sm.mark_bad_share(peerid, shnum, "")
            return self.update_servermap(sm, MODE_WRITE)
        d.addCallback(_made_map)
        def _check_map(sm):
            # this should find all 5 shares that weren't marked bad
            v = sm.best_recoverable_version()
            vm = sm.make_versionmap()
            shares = list(vm[v])
            for (peerid, shnum) in self._corrupted:
                peer_shares = sm.shares_on_peer(peerid)
                self.failIf(shnum in peer_shares,
                            "%d was in %s" % (shnum, peer_shares))
            self.failUnlessEqual(len(shares), 5)
        d.addCallback(_check_map)
        return d

    def failUnlessNoneRecoverable(self, sm):
        self.failUnlessEqual(len(sm.recoverable_versions()), 0)
        self.failUnlessEqual(len(sm.unrecoverable_versions()), 0)
        best = sm.best_recoverable_version()
        self.failUnlessEqual(best, None)
        self.failUnlessEqual(len(sm.shares_available()), 0)

    def test_no_shares(self):
        self._storage._peers = {} # delete all shares
        ms = self.make_servermap
        d = defer.succeed(None)
#
        d.addCallback(lambda res: ms(mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessNoneRecoverable(sm))

        d.addCallback(lambda res: ms(mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessNoneRecoverable(sm))

        d.addCallback(lambda res: ms(mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessNoneRecoverable(sm))

        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessNoneRecoverable(sm))

        return d

    def failUnlessNotQuiteEnough(self, sm):
        self.failUnlessEqual(len(sm.recoverable_versions()), 0)
        self.failUnlessEqual(len(sm.unrecoverable_versions()), 1)
        best = sm.best_recoverable_version()
        self.failUnlessEqual(best, None)
        self.failUnlessEqual(len(sm.shares_available()), 1)
        self.failUnlessEqual(sm.shares_available().values()[0], (2,3,10) )
        return sm

    def test_not_quite_enough_shares(self):
        s = self._storage
        ms = self.make_servermap
        num_shares = len(s._peers)
        for peerid in s._peers:
            s._peers[peerid] = {}
            num_shares -= 1
            if num_shares == 2:
                break
        # now there ought to be only two shares left
        assert len([peerid for peerid in s._peers if s._peers[peerid]]) == 2

        d = defer.succeed(None)

        d.addCallback(lambda res: ms(mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda sm:
                      self.failUnlessEqual(len(sm.make_sharemap()), 2))
        d.addCallback(lambda res: ms(mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda res: ms(mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))

        return d


    def test_servermapupdater_finds_mdmf_files(self):
        # setUp already published an MDMF file for us. We just need to
        # make sure that when we run the ServermapUpdater, the file is
        # reported to have one recoverable version.
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.publish_mdmf())
        d.addCallback(lambda ignored:
            self.make_servermap(mode=MODE_CHECK))
        # Calling make_servermap also updates the servermap in the mode
        # that we specify, so we just need to see what it says.
        def _check_servermap(sm):
            self.failUnlessEqual(len(sm.recoverable_versions()), 1)
        d.addCallback(_check_servermap)
        return d


    def test_fetch_update(self):
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.publish_mdmf())
        d.addCallback(lambda ignored:
            self.make_servermap(mode=MODE_WRITE, update_range=(1, 2)))
        def _check_servermap(sm):
            # 10 shares
            self.failUnlessEqual(len(sm.update_data), 10)
            # one version
            for data in sm.update_data.itervalues():
                self.failUnlessEqual(len(data), 1)
        d.addCallback(_check_servermap)
        return d


    def test_servermapupdater_finds_sdmf_files(self):
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.publish_sdmf())
        d.addCallback(lambda ignored:
            self.make_servermap(mode=MODE_CHECK))
        d.addCallback(lambda servermap:
            self.failUnlessEqual(len(servermap.recoverable_versions()), 1))
        return d


class Roundtrip(unittest.TestCase, testutil.ShouldFailMixin, PublishMixin):
    def setUp(self):
        return self.publish_one()

    def make_servermap(self, mode=MODE_READ, oldmap=None, sb=None):
        if oldmap is None:
            oldmap = ServerMap()
        if sb is None:
            sb = self._storage_broker
        smu = ServermapUpdater(self._fn, sb, Monitor(), oldmap, mode)
        d = smu.update()
        return d

    def abbrev_verinfo(self, verinfo):
        if verinfo is None:
            return None
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = verinfo
        return "%d-%s" % (seqnum, base32.b2a(root_hash)[:4])

    def abbrev_verinfo_dict(self, verinfo_d):
        output = {}
        for verinfo,value in verinfo_d.items():
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = verinfo
            output["%d-%s" % (seqnum, base32.b2a(root_hash)[:4])] = value
        return output

    def dump_servermap(self, servermap):
        print "SERVERMAP", servermap
        print "RECOVERABLE", [self.abbrev_verinfo(v)
                              for v in servermap.recoverable_versions()]
        print "BEST", self.abbrev_verinfo(servermap.best_recoverable_version())
        print "available", self.abbrev_verinfo_dict(servermap.shares_available())

    def do_download(self, servermap, version=None):
        if version is None:
            version = servermap.best_recoverable_version()
        r = Retrieve(self._fn, servermap, version)
        c = consumer.MemoryConsumer()
        d = r.download(consumer=c)
        d.addCallback(lambda mc: "".join(mc.chunks))
        return d


    def test_basic(self):
        d = self.make_servermap()
        def _do_retrieve(servermap):
            self._smap = servermap
            #self.dump_servermap(servermap)
            self.failUnlessEqual(len(servermap.recoverable_versions()), 1)
            return self.do_download(servermap)
        d.addCallback(_do_retrieve)
        def _retrieved(new_contents):
            self.failUnlessEqual(new_contents, self.CONTENTS)
        d.addCallback(_retrieved)
        # we should be able to re-use the same servermap, both with and
        # without updating it.
        d.addCallback(lambda res: self.do_download(self._smap))
        d.addCallback(_retrieved)
        d.addCallback(lambda res: self.make_servermap(oldmap=self._smap))
        d.addCallback(lambda res: self.do_download(self._smap))
        d.addCallback(_retrieved)
        # clobbering the pubkey should make the servermap updater re-fetch it
        def _clobber_pubkey(res):
            self._fn._pubkey = None
        d.addCallback(_clobber_pubkey)
        d.addCallback(lambda res: self.make_servermap(oldmap=self._smap))
        d.addCallback(lambda res: self.do_download(self._smap))
        d.addCallback(_retrieved)
        return d

    def test_all_shares_vanished(self):
        d = self.make_servermap()
        def _remove_shares(servermap):
            for shares in self._storage._peers.values():
                shares.clear()
            d1 = self.shouldFail(NotEnoughSharesError,
                                 "test_all_shares_vanished",
                                 "ran out of peers",
                                 self.do_download, servermap)
            return d1
        d.addCallback(_remove_shares)
        return d

    def test_no_servers(self):
        sb2 = make_storagebroker(num_peers=0)
        # if there are no servers, then a MODE_READ servermap should come
        # back empty
        d = self.make_servermap(sb=sb2)
        def _check_servermap(servermap):
            self.failUnlessEqual(servermap.best_recoverable_version(), None)
            self.failIf(servermap.recoverable_versions())
            self.failIf(servermap.unrecoverable_versions())
            self.failIf(servermap.all_peers())
        d.addCallback(_check_servermap)
        return d

    def test_no_servers_download(self):
        sb2 = make_storagebroker(num_peers=0)
        self._fn._storage_broker = sb2
        d = self.shouldFail(UnrecoverableFileError,
                            "test_no_servers_download",
                            "no recoverable versions",
                            self._fn.download_best_version)
        def _restore(res):
            # a failed download that occurs while we aren't connected to
            # anybody should not prevent a subsequent download from working.
            # This isn't quite the webapi-driven test that #463 wants, but it
            # should be close enough.
            self._fn._storage_broker = self._storage_broker
            return self._fn.download_best_version()
        def _retrieved(new_contents):
            self.failUnlessEqual(new_contents, self.CONTENTS)
        d.addCallback(_restore)
        d.addCallback(_retrieved)
        return d


    def _test_corrupt_all(self, offset, substring,
                          should_succeed=False,
                          corrupt_early=True,
                          failure_checker=None,
                          fetch_privkey=False):
        d = defer.succeed(None)
        if corrupt_early:
            d.addCallback(corrupt, self._storage, offset)
        d.addCallback(lambda res: self.make_servermap())
        if not corrupt_early:
            d.addCallback(corrupt, self._storage, offset)
        def _do_retrieve(servermap):
            ver = servermap.best_recoverable_version()
            if ver is None and not should_succeed:
                # no recoverable versions == not succeeding. The problem
                # should be noted in the servermap's list of problems.
                if substring:
                    allproblems = [str(f) for f in servermap.problems]
                    self.failUnlessIn(substring, "".join(allproblems))
                return servermap
            if should_succeed:
                d1 = self._fn.download_version(servermap, ver,
                                               fetch_privkey)
                d1.addCallback(lambda new_contents:
                               self.failUnlessEqual(new_contents, self.CONTENTS))
            else:
                d1 = self.shouldFail(NotEnoughSharesError,
                                     "_corrupt_all(offset=%s)" % (offset,),
                                     substring,
                                     self._fn.download_version, servermap,
                                                                ver,
                                                                fetch_privkey)
            if failure_checker:
                d1.addCallback(failure_checker)
            d1.addCallback(lambda res: servermap)
            return d1
        d.addCallback(_do_retrieve)
        return d

    def test_corrupt_all_verbyte(self):
        # when the version byte is not 0 or 1, we hit an UnknownVersionError
        # error in unpack_share().
        d = self._test_corrupt_all(0, "UnknownVersionError")
        def _check_servermap(servermap):
            # and the dump should mention the problems
            s = StringIO()
            dump = servermap.dump(s).getvalue()
            self.failUnless("30 PROBLEMS" in dump, dump)
        d.addCallback(_check_servermap)
        return d

    def test_corrupt_all_seqnum(self):
        # a corrupt sequence number will trigger a bad signature
        return self._test_corrupt_all(1, "signature is invalid")

    def test_corrupt_all_R(self):
        # a corrupt root hash will trigger a bad signature
        return self._test_corrupt_all(9, "signature is invalid")

    def test_corrupt_all_IV(self):
        # a corrupt salt/IV will trigger a bad signature
        return self._test_corrupt_all(41, "signature is invalid")

    def test_corrupt_all_k(self):
        # a corrupt 'k' will trigger a bad signature
        return self._test_corrupt_all(57, "signature is invalid")

    def test_corrupt_all_N(self):
        # a corrupt 'N' will trigger a bad signature
        return self._test_corrupt_all(58, "signature is invalid")

    def test_corrupt_all_segsize(self):
        # a corrupt segsize will trigger a bad signature
        return self._test_corrupt_all(59, "signature is invalid")

    def test_corrupt_all_datalen(self):
        # a corrupt data length will trigger a bad signature
        return self._test_corrupt_all(67, "signature is invalid")

    def test_corrupt_all_pubkey(self):
        # a corrupt pubkey won't match the URI's fingerprint. We need to
        # remove the pubkey from the filenode, or else it won't bother trying
        # to update it.
        self._fn._pubkey = None
        return self._test_corrupt_all("pubkey",
                                      "pubkey doesn't match fingerprint")

    def test_corrupt_all_sig(self):
        # a corrupt signature is a bad one
        # the signature runs from about [543:799], depending upon the length
        # of the pubkey
        return self._test_corrupt_all("signature", "signature is invalid")

    def test_corrupt_all_share_hash_chain_number(self):
        # a corrupt share hash chain entry will show up as a bad hash. If we
        # mangle the first byte, that will look like a bad hash number,
        # causing an IndexError
        return self._test_corrupt_all("share_hash_chain", "corrupt hashes")

    def test_corrupt_all_share_hash_chain_hash(self):
        # a corrupt share hash chain entry will show up as a bad hash. If we
        # mangle a few bytes in, that will look like a bad hash.
        return self._test_corrupt_all(("share_hash_chain",4), "corrupt hashes")

    def test_corrupt_all_block_hash_tree(self):
        return self._test_corrupt_all("block_hash_tree",
                                      "block hash tree failure")

    def test_corrupt_all_block(self):
        return self._test_corrupt_all("share_data", "block hash tree failure")

    def test_corrupt_all_encprivkey(self):
        # a corrupted privkey won't even be noticed by the reader, only by a
        # writer.
        return self._test_corrupt_all("enc_privkey", None, should_succeed=True)


    def test_corrupt_all_encprivkey_late(self):
        # this should work for the same reason as above, but we corrupt 
        # after the servermap update to exercise the error handling
        # code.
        # We need to remove the privkey from the node, or the retrieve
        # process won't know to update it.
        self._fn._privkey = None
        return self._test_corrupt_all("enc_privkey",
                                      None, # this shouldn't fail
                                      should_succeed=True,
                                      corrupt_early=False,
                                      fetch_privkey=True)


    # disabled until retrieve tests checkstring on each blockfetch. I didn't
    # just use a .todo because the failing-but-ignored test emits about 30kB
    # of noise.
    def OFF_test_corrupt_all_seqnum_late(self):
        # corrupting the seqnum between mapupdate and retrieve should result
        # in NotEnoughSharesError, since each share will look invalid
        def _check(res):
            f = res[0]
            self.failUnless(f.check(NotEnoughSharesError))
            self.failUnless("uncoordinated write" in str(f))
        return self._test_corrupt_all(1, "ran out of peers",
                                      corrupt_early=False,
                                      failure_checker=_check)

    def test_corrupt_all_block_hash_tree_late(self):
        def _check(res):
            f = res[0]
            self.failUnless(f.check(NotEnoughSharesError))
        return self._test_corrupt_all("block_hash_tree",
                                      "block hash tree failure",
                                      corrupt_early=False,
                                      failure_checker=_check)


    def test_corrupt_all_block_late(self):
        def _check(res):
            f = res[0]
            self.failUnless(f.check(NotEnoughSharesError))
        return self._test_corrupt_all("share_data", "block hash tree failure",
                                      corrupt_early=False,
                                      failure_checker=_check)


    def test_basic_pubkey_at_end(self):
        # we corrupt the pubkey in all but the last 'k' shares, allowing the
        # download to succeed but forcing a bunch of retries first. Note that
        # this is rather pessimistic: our Retrieve process will throw away
        # the whole share if the pubkey is bad, even though the rest of the
        # share might be good.

        self._fn._pubkey = None
        k = self._fn.get_required_shares()
        N = self._fn.get_total_shares()
        d = defer.succeed(None)
        d.addCallback(corrupt, self._storage, "pubkey",
                      shnums_to_corrupt=range(0, N-k))
        d.addCallback(lambda res: self.make_servermap())
        def _do_retrieve(servermap):
            self.failUnless(servermap.problems)
            self.failUnless("pubkey doesn't match fingerprint"
                            in str(servermap.problems[0]))
            ver = servermap.best_recoverable_version()
            r = Retrieve(self._fn, servermap, ver)
            c = consumer.MemoryConsumer()
            return r.download(c)
        d.addCallback(_do_retrieve)
        d.addCallback(lambda mc: "".join(mc.chunks))
        d.addCallback(lambda new_contents:
                      self.failUnlessEqual(new_contents, self.CONTENTS))
        return d


    def _test_corrupt_some(self, offset, mdmf=False):
        if mdmf:
            d = self.publish_mdmf()
        else:
            d = defer.succeed(None)
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, offset, range(5)))
        d.addCallback(lambda ignored:
            self.make_servermap())
        def _do_retrieve(servermap):
            ver = servermap.best_recoverable_version()
            self.failUnless(ver)
            return self._fn.download_best_version()
        d.addCallback(_do_retrieve)
        d.addCallback(lambda new_contents:
            self.failUnlessEqual(new_contents, self.CONTENTS))
        return d


    def test_corrupt_some(self):
        # corrupt the data of first five shares (so the servermap thinks
        # they're good but retrieve marks them as bad), so that the
        # MODE_READ set of 6 will be insufficient, forcing node.download to
        # retry with more servers.
        return self._test_corrupt_some("share_data")


    def test_download_fails(self):
        d = corrupt(None, self._storage, "signature")
        d.addCallback(lambda ignored:
            self.shouldFail(UnrecoverableFileError, "test_download_anyway",
                            "no recoverable versions",
                            self._fn.download_best_version))
        return d



    def test_corrupt_mdmf_block_hash_tree(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            self._test_corrupt_all(("block_hash_tree", 12 * 32),
                                   "block hash tree failure",
                                   corrupt_early=False,
                                   should_succeed=False))
        return d


    def test_corrupt_mdmf_block_hash_tree_late(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            self._test_corrupt_all(("block_hash_tree", 12 * 32),
                                   "block hash tree failure",
                                   corrupt_early=True,
                                   should_succeed=False))
        return d


    def test_corrupt_mdmf_share_data(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            # TODO: Find out what the block size is and corrupt a
            # specific block, rather than just guessing.
            self._test_corrupt_all(("share_data", 12 * 40),
                                    "block hash tree failure",
                                    corrupt_early=True,
                                    should_succeed=False))
        return d


    def test_corrupt_some_mdmf(self):
        return self._test_corrupt_some(("share_data", 12 * 40),
                                       mdmf=True)


class CheckerMixin:
    def check_good(self, r, where):
        self.failUnless(r.is_healthy(), where)
        return r

    def check_bad(self, r, where):
        self.failIf(r.is_healthy(), where)
        return r

    def check_expected_failure(self, r, expected_exception, substring, where):
        for (peerid, storage_index, shnum, f) in r.problems:
            if f.check(expected_exception):
                self.failUnless(substring in str(f),
                                "%s: substring '%s' not in '%s'" %
                                (where, substring, str(f)))
                return
        self.fail("%s: didn't see expected exception %s in problems %s" %
                  (where, expected_exception, r.problems))


class Checker(unittest.TestCase, CheckerMixin, PublishMixin):
    def setUp(self):
        return self.publish_one()


    def test_check_good(self):
        d = self._fn.check(Monitor())
        d.addCallback(self.check_good, "test_check_good")
        return d

    def test_check_mdmf_good(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_good, "test_check_mdmf_good")
        return d

    def test_check_no_shares(self):
        for shares in self._storage._peers.values():
            shares.clear()
        d = self._fn.check(Monitor())
        d.addCallback(self.check_bad, "test_check_no_shares")
        return d

    def test_check_mdmf_no_shares(self):
        d = self.publish_mdmf()
        def _then(ignored):
            for share in self._storage._peers.values():
                share.clear()
        d.addCallback(_then)
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_check_mdmf_no_shares")
        return d

    def test_check_not_enough_shares(self):
        for shares in self._storage._peers.values():
            for shnum in shares.keys():
                if shnum > 0:
                    del shares[shnum]
        d = self._fn.check(Monitor())
        d.addCallback(self.check_bad, "test_check_not_enough_shares")
        return d

    def test_check_mdmf_not_enough_shares(self):
        d = self.publish_mdmf()
        def _then(ignored):
            for shares in self._storage._peers.values():
                for shnum in shares.keys():
                    if shnum > 0:
                        del shares[shnum]
        d.addCallback(_then)
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_check_mdmf_not_enougH_shares")
        return d


    def test_check_all_bad_sig(self):
        d = corrupt(None, self._storage, 1) # bad sig
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_check_all_bad_sig")
        return d

    def test_check_mdmf_all_bad_sig(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, 1))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_check_mdmf_all_bad_sig")
        return d

    def test_check_all_bad_blocks(self):
        d = corrupt(None, self._storage, "share_data", [9]) # bad blocks
        # the Checker won't notice this.. it doesn't look at actual data
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_good, "test_check_all_bad_blocks")
        return d


    def test_check_mdmf_all_bad_blocks(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "share_data"))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_good, "test_check_mdmf_all_bad_blocks")
        return d

    def test_verify_good(self):
        d = self._fn.check(Monitor(), verify=True)
        d.addCallback(self.check_good, "test_verify_good")
        return d

    def test_verify_all_bad_sig(self):
        d = corrupt(None, self._storage, 1) # bad sig
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_all_bad_sig")
        return d

    def test_verify_one_bad_sig(self):
        d = corrupt(None, self._storage, 1, [9]) # bad sig
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_one_bad_sig")
        return d

    def test_verify_one_bad_block(self):
        d = corrupt(None, self._storage, "share_data", [9]) # bad blocks
        # the Verifier *will* notice this, since it examines every byte
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_one_bad_block")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "block hash tree failure",
                      "test_verify_one_bad_block")
        return d

    def test_verify_one_bad_sharehash(self):
        d = corrupt(None, self._storage, "share_hash_chain", [9], 5)
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_one_bad_sharehash")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "corrupt hashes",
                      "test_verify_one_bad_sharehash")
        return d

    def test_verify_one_bad_encprivkey(self):
        d = corrupt(None, self._storage, "enc_privkey", [9]) # bad privkey
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_one_bad_encprivkey")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "invalid privkey",
                      "test_verify_one_bad_encprivkey")
        return d

    def test_verify_one_bad_encprivkey_uncheckable(self):
        d = corrupt(None, self._storage, "enc_privkey", [9]) # bad privkey
        readonly_fn = self._fn.get_readonly()
        # a read-only node has no way to validate the privkey
        d.addCallback(lambda ignored:
            readonly_fn.check(Monitor(), verify=True))
        d.addCallback(self.check_good,
                      "test_verify_one_bad_encprivkey_uncheckable")
        return d


    def test_verify_mdmf_good(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_good, "test_verify_mdmf_good")
        return d


    def test_verify_mdmf_one_bad_block(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "share_data", [1]))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        # We should find one bad block here
        d.addCallback(self.check_bad, "test_verify_mdmf_one_bad_block")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "block hash tree failure",
                      "test_verify_mdmf_one_bad_block")
        return d


    def test_verify_mdmf_bad_encprivkey(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "enc_privkey", [0]))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_mdmf_bad_encprivkey")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "privkey",
                      "test_verify_mdmf_bad_encprivkey")
        return d


    def test_verify_mdmf_bad_sig(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, 1, [1]))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_mdmf_bad_sig")
        return d


    def test_verify_mdmf_bad_encprivkey_uncheckable(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "enc_privkey", [1]))
        d.addCallback(lambda ignored:
            self._fn.get_readonly())
        d.addCallback(lambda fn:
            fn.check(Monitor(), verify=True))
        d.addCallback(self.check_good,
                      "test_verify_mdmf_bad_encprivkey_uncheckable")
        return d


class Repair(unittest.TestCase, PublishMixin, ShouldFailMixin):

    def get_shares(self, s):
        all_shares = {} # maps (peerid, shnum) to share data
        for peerid in s._peers:
            shares = s._peers[peerid]
            for shnum in shares:
                data = shares[shnum]
                all_shares[ (peerid, shnum) ] = data
        return all_shares

    def copy_shares(self, ignored=None):
        self.old_shares.append(self.get_shares(self._storage))

    def test_repair_nop(self):
        self.old_shares = []
        d = self.publish_one()
        d.addCallback(self.copy_shares)
        d.addCallback(lambda res: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        def _check_results(rres):
            self.failUnless(IRepairResults.providedBy(rres))
            self.failUnless(rres.get_successful())
            # TODO: examine results

            self.copy_shares()

            initial_shares = self.old_shares[0]
            new_shares = self.old_shares[1]
            # TODO: this really shouldn't change anything. When we implement
            # a "minimal-bandwidth" repairer", change this test to assert:
            #self.failUnlessEqual(new_shares, initial_shares)

            # all shares should be in the same place as before
            self.failUnlessEqual(set(initial_shares.keys()),
                                 set(new_shares.keys()))
            # but they should all be at a newer seqnum. The IV will be
            # different, so the roothash will be too.
            for key in initial_shares:
                (version0,
                 seqnum0,
                 root_hash0,
                 IV0,
                 k0, N0, segsize0, datalen0,
                 o0) = unpack_header(initial_shares[key])
                (version1,
                 seqnum1,
                 root_hash1,
                 IV1,
                 k1, N1, segsize1, datalen1,
                 o1) = unpack_header(new_shares[key])
                self.failUnlessEqual(version0, version1)
                self.failUnlessEqual(seqnum0+1, seqnum1)
                self.failUnlessEqual(k0, k1)
                self.failUnlessEqual(N0, N1)
                self.failUnlessEqual(segsize0, segsize1)
                self.failUnlessEqual(datalen0, datalen1)
        d.addCallback(_check_results)
        return d

    def failIfSharesChanged(self, ignored=None):
        old_shares = self.old_shares[-2]
        current_shares = self.old_shares[-1]
        self.failUnlessEqual(old_shares, current_shares)


    def test_unrepairable_0shares(self):
        d = self.publish_one()
        def _delete_all_shares(ign):
            shares = self._storage._peers
            for peerid in shares:
                shares[peerid] = {}
        d.addCallback(_delete_all_shares)
        d.addCallback(lambda ign: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        def _check(crr):
            self.failUnlessEqual(crr.get_successful(), False)
        d.addCallback(_check)
        return d

    def test_mdmf_unrepairable_0shares(self):
        d = self.publish_mdmf()
        def _delete_all_shares(ign):
            shares = self._storage._peers
            for peerid in shares:
                shares[peerid] = {}
        d.addCallback(_delete_all_shares)
        d.addCallback(lambda ign: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        d.addCallback(lambda crr: self.failIf(crr.get_successful()))
        return d


    def test_unrepairable_1share(self):
        d = self.publish_one()
        def _delete_all_shares(ign):
            shares = self._storage._peers
            for peerid in shares:
                for shnum in list(shares[peerid]):
                    if shnum > 0:
                        del shares[peerid][shnum]
        d.addCallback(_delete_all_shares)
        d.addCallback(lambda ign: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        def _check(crr):
            self.failUnlessEqual(crr.get_successful(), False)
        d.addCallback(_check)
        return d

    def test_mdmf_unrepairable_1share(self):
        d = self.publish_mdmf()
        def _delete_all_shares(ign):
            shares = self._storage._peers
            for peerid in shares:
                for shnum in list(shares[peerid]):
                    if shnum > 0:
                        del shares[peerid][shnum]
        d.addCallback(_delete_all_shares)
        d.addCallback(lambda ign: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        def _check(crr):
            self.failUnlessEqual(crr.get_successful(), False)
        d.addCallback(_check)
        return d

    def test_repairable_5shares(self):
        d = self.publish_mdmf()
        def _delete_all_shares(ign):
            shares = self._storage._peers
            for peerid in shares:
                for shnum in list(shares[peerid]):
                    if shnum > 4:
                        del shares[peerid][shnum]
        d.addCallback(_delete_all_shares)
        d.addCallback(lambda ign: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        def _check(crr):
            self.failUnlessEqual(crr.get_successful(), True)
        d.addCallback(_check)
        return d

    def test_mdmf_repairable_5shares(self):
        d = self.publish_mdmf()
        def _delete_some_shares(ign):
            shares = self._storage._peers
            for peerid in shares:
                for shnum in list(shares[peerid]):
                    if shnum > 5:
                        del shares[peerid][shnum]
        d.addCallback(_delete_some_shares)
        d.addCallback(lambda ign: self._fn.check(Monitor()))
        def _check(cr):
            self.failIf(cr.is_healthy())
            self.failUnless(cr.is_recoverable())
            return cr
        d.addCallback(_check)
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        def _check1(crr):
            self.failUnlessEqual(crr.get_successful(), True)
        d.addCallback(_check1)
        return d


    def test_merge(self):
        self.old_shares = []
        d = self.publish_multiple()
        # repair will refuse to merge multiple highest seqnums unless you
        # pass force=True
        d.addCallback(lambda res:
                      self._set_versions({0:3,2:3,4:3,6:3,8:3,
                                          1:4,3:4,5:4,7:4,9:4}))
        d.addCallback(self.copy_shares)
        d.addCallback(lambda res: self._fn.check(Monitor()))
        def _try_repair(check_results):
            ex = "There were multiple recoverable versions with identical seqnums, so force=True must be passed to the repair() operation"
            d2 = self.shouldFail(MustForceRepairError, "test_merge", ex,
                                 self._fn.repair, check_results)
            d2.addCallback(self.copy_shares)
            d2.addCallback(self.failIfSharesChanged)
            d2.addCallback(lambda res: check_results)
            return d2
        d.addCallback(_try_repair)
        d.addCallback(lambda check_results:
                      self._fn.repair(check_results, force=True))
        # this should give us 10 shares of the highest roothash
        def _check_repair_results(rres):
            self.failUnless(rres.get_successful())
            pass # TODO
        d.addCallback(_check_repair_results)
        d.addCallback(lambda res: self._fn.get_servermap(MODE_CHECK))
        def _check_smap(smap):
            self.failUnlessEqual(len(smap.recoverable_versions()), 1)
            self.failIf(smap.unrecoverable_versions())
            # now, which should have won?
            roothash_s4a = self.get_roothash_for(3)
            roothash_s4b = self.get_roothash_for(4)
            if roothash_s4b > roothash_s4a:
                expected_contents = self.CONTENTS[4]
            else:
                expected_contents = self.CONTENTS[3]
            new_versionid = smap.best_recoverable_version()
            self.failUnlessEqual(new_versionid[0], 5) # seqnum 5
            d2 = self._fn.download_version(smap, new_versionid)
            d2.addCallback(self.failUnlessEqual, expected_contents)
            return d2
        d.addCallback(_check_smap)
        return d

    def test_non_merge(self):
        self.old_shares = []
        d = self.publish_multiple()
        # repair should not refuse a repair that doesn't need to merge. In
        # this case, we combine v2 with v3. The repair should ignore v2 and
        # copy v3 into a new v5.
        d.addCallback(lambda res:
                      self._set_versions({0:2,2:2,4:2,6:2,8:2,
                                          1:3,3:3,5:3,7:3,9:3}))
        d.addCallback(lambda res: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        # this should give us 10 shares of v3
        def _check_repair_results(rres):
            self.failUnless(rres.get_successful())
            pass # TODO
        d.addCallback(_check_repair_results)
        d.addCallback(lambda res: self._fn.get_servermap(MODE_CHECK))
        def _check_smap(smap):
            self.failUnlessEqual(len(smap.recoverable_versions()), 1)
            self.failIf(smap.unrecoverable_versions())
            # now, which should have won?
            expected_contents = self.CONTENTS[3]
            new_versionid = smap.best_recoverable_version()
            self.failUnlessEqual(new_versionid[0], 5) # seqnum 5
            d2 = self._fn.download_version(smap, new_versionid)
            d2.addCallback(self.failUnlessEqual, expected_contents)
            return d2
        d.addCallback(_check_smap)
        return d

    def get_roothash_for(self, index):
        # return the roothash for the first share we see in the saved set
        shares = self._copied_shares[index]
        for peerid in shares:
            for shnum in shares[peerid]:
                share = shares[peerid][shnum]
                (version, seqnum, root_hash, IV, k, N, segsize, datalen, o) = \
                          unpack_header(share)
                return root_hash

    def test_check_and_repair_readcap(self):
        # we can't currently repair from a mutable readcap: #625
        self.old_shares = []
        d = self.publish_one()
        d.addCallback(self.copy_shares)
        def _get_readcap(res):
            self._fn3 = self._fn.get_readonly()
            # also delete some shares
            for peerid,shares in self._storage._peers.items():
                shares.pop(0, None)
        d.addCallback(_get_readcap)
        d.addCallback(lambda res: self._fn3.check_and_repair(Monitor()))
        def _check_results(crr):
            self.failUnless(ICheckAndRepairResults.providedBy(crr))
            # we should detect the unhealthy, but skip over mutable-readcap
            # repairs until #625 is fixed
            self.failIf(crr.get_pre_repair_results().is_healthy())
            self.failIf(crr.get_repair_attempted())
            self.failIf(crr.get_post_repair_results().is_healthy())
        d.addCallback(_check_results)
        return d

class DevNullDictionary(dict):
    def __setitem__(self, key, value):
        return

class MultipleEncodings(unittest.TestCase):
    def setUp(self):
        self.CONTENTS = "New contents go here"
        self.uploadable = MutableData(self.CONTENTS)
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage, num_peers=20)
        self._storage_broker = self._nodemaker.storage_broker
        d = self._nodemaker.create_mutable_file(self.uploadable)
        def _created(node):
            self._fn = node
        d.addCallback(_created)
        return d

    def _encode(self, k, n, data, version=SDMF_VERSION):
        # encode 'data' into a peerid->shares dict.

        fn = self._fn
        # disable the nodecache, since for these tests we explicitly need
        # multiple nodes pointing at the same file
        self._nodemaker._node_cache = DevNullDictionary()
        fn2 = self._nodemaker.create_from_cap(fn.get_uri())
        # then we copy over other fields that are normally fetched from the
        # existing shares
        fn2._pubkey = fn._pubkey
        fn2._privkey = fn._privkey
        fn2._encprivkey = fn._encprivkey
        # and set the encoding parameters to something completely different
        fn2._required_shares = k
        fn2._total_shares = n

        s = self._storage
        s._peers = {} # clear existing storage
        p2 = Publish(fn2, self._storage_broker, None)
        uploadable = MutableData(data)
        d = p2.publish(uploadable)
        def _published(res):
            shares = s._peers
            s._peers = {}
            return shares
        d.addCallback(_published)
        return d

    def make_servermap(self, mode=MODE_READ, oldmap=None):
        if oldmap is None:
            oldmap = ServerMap()
        smu = ServermapUpdater(self._fn, self._storage_broker, Monitor(),
                               oldmap, mode)
        d = smu.update()
        return d

    def test_multiple_encodings(self):
        # we encode the same file in two different ways (3-of-10 and 4-of-9),
        # then mix up the shares, to make sure that download survives seeing
        # a variety of encodings. This is actually kind of tricky to set up.

        contents1 = "Contents for encoding 1 (3-of-10) go here"
        contents2 = "Contents for encoding 2 (4-of-9) go here"
        contents3 = "Contents for encoding 3 (4-of-7) go here"

        # we make a retrieval object that doesn't know what encoding
        # parameters to use
        fn3 = self._nodemaker.create_from_cap(self._fn.get_uri())

        # now we upload a file through fn1, and grab its shares
        d = self._encode(3, 10, contents1)
        def _encoded_1(shares):
            self._shares1 = shares
        d.addCallback(_encoded_1)
        d.addCallback(lambda res: self._encode(4, 9, contents2))
        def _encoded_2(shares):
            self._shares2 = shares
        d.addCallback(_encoded_2)
        d.addCallback(lambda res: self._encode(4, 7, contents3))
        def _encoded_3(shares):
            self._shares3 = shares
        d.addCallback(_encoded_3)

        def _merge(res):
            log.msg("merging sharelists")
            # we merge the shares from the two sets, leaving each shnum in
            # its original location, but using a share from set1 or set2
            # according to the following sequence:
            #
            #  4-of-9  a  s2
            #  4-of-9  b  s2
            #  4-of-7  c   s3
            #  4-of-9  d  s2
            #  3-of-9  e s1
            #  3-of-9  f s1
            #  3-of-9  g s1
            #  4-of-9  h  s2
            #
            # so that neither form can be recovered until fetch [f], at which
            # point version-s1 (the 3-of-10 form) should be recoverable. If
            # the implementation latches on to the first version it sees,
            # then s2 will be recoverable at fetch [g].

            # Later, when we implement code that handles multiple versions,
            # we can use this framework to assert that all recoverable
            # versions are retrieved, and test that 'epsilon' does its job

            places = [2, 2, 3, 2, 1, 1, 1, 2]

            sharemap = {}
            sb = self._storage_broker

            for peerid in sorted(sb.get_all_serverids()):
                for shnum in self._shares1.get(peerid, {}):
                    if shnum < len(places):
                        which = places[shnum]
                    else:
                        which = "x"
                    self._storage._peers[peerid] = peers = {}
                    in_1 = shnum in self._shares1[peerid]
                    in_2 = shnum in self._shares2.get(peerid, {})
                    in_3 = shnum in self._shares3.get(peerid, {})
                    if which == 1:
                        if in_1:
                            peers[shnum] = self._shares1[peerid][shnum]
                            sharemap[shnum] = peerid
                    elif which == 2:
                        if in_2:
                            peers[shnum] = self._shares2[peerid][shnum]
                            sharemap[shnum] = peerid
                    elif which == 3:
                        if in_3:
                            peers[shnum] = self._shares3[peerid][shnum]
                            sharemap[shnum] = peerid

            # we don't bother placing any other shares
            # now sort the sequence so that share 0 is returned first
            new_sequence = [sharemap[shnum]
                            for shnum in sorted(sharemap.keys())]
            self._storage._sequence = new_sequence
            log.msg("merge done")
        d.addCallback(_merge)
        d.addCallback(lambda res: fn3.download_best_version())
        def _retrieved(new_contents):
            # the current specified behavior is "first version recoverable"
            self.failUnlessEqual(new_contents, contents1)
        d.addCallback(_retrieved)
        return d


class MultipleVersions(unittest.TestCase, PublishMixin, CheckerMixin):

    def setUp(self):
        return self.publish_multiple()

    def test_multiple_versions(self):
        # if we see a mix of versions in the grid, download_best_version
        # should get the latest one
        self._set_versions(dict([(i,2) for i in (0,2,4,6,8)]))
        d = self._fn.download_best_version()
        d.addCallback(lambda res: self.failUnlessEqual(res, self.CONTENTS[4]))
        # and the checker should report problems
        d.addCallback(lambda res: self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_multiple_versions")

        # but if everything is at version 2, that's what we should download
        d.addCallback(lambda res:
                      self._set_versions(dict([(i,2) for i in range(10)])))
        d.addCallback(lambda res: self._fn.download_best_version())
        d.addCallback(lambda res: self.failUnlessEqual(res, self.CONTENTS[2]))
        # if exactly one share is at version 3, we should still get v2
        d.addCallback(lambda res:
                      self._set_versions({0:3}))
        d.addCallback(lambda res: self._fn.download_best_version())
        d.addCallback(lambda res: self.failUnlessEqual(res, self.CONTENTS[2]))
        # but the servermap should see the unrecoverable version. This
        # depends upon the single newer share being queried early.
        d.addCallback(lambda res: self._fn.get_servermap(MODE_READ))
        def _check_smap(smap):
            self.failUnlessEqual(len(smap.unrecoverable_versions()), 1)
            newer = smap.unrecoverable_newer_versions()
            self.failUnlessEqual(len(newer), 1)
            verinfo, health = newer.items()[0]
            self.failUnlessEqual(verinfo[0], 4)
            self.failUnlessEqual(health, (1,3))
            self.failIf(smap.needs_merge())
        d.addCallback(_check_smap)
        # if we have a mix of two parallel versions (s4a and s4b), we could
        # recover either
        d.addCallback(lambda res:
                      self._set_versions({0:3,2:3,4:3,6:3,8:3,
                                          1:4,3:4,5:4,7:4,9:4}))
        d.addCallback(lambda res: self._fn.get_servermap(MODE_READ))
        def _check_smap_mixed(smap):
            self.failUnlessEqual(len(smap.unrecoverable_versions()), 0)
            newer = smap.unrecoverable_newer_versions()
            self.failUnlessEqual(len(newer), 0)
            self.failUnless(smap.needs_merge())
        d.addCallback(_check_smap_mixed)
        d.addCallback(lambda res: self._fn.download_best_version())
        d.addCallback(lambda res: self.failUnless(res == self.CONTENTS[3] or
                                                  res == self.CONTENTS[4]))
        return d

    def test_replace(self):
        # if we see a mix of versions in the grid, we should be able to
        # replace them all with a newer version

        # if exactly one share is at version 3, we should download (and
        # replace) v2, and the result should be v4. Note that the index we
        # give to _set_versions is different than the sequence number.
        target = dict([(i,2) for i in range(10)]) # seqnum3
        target[0] = 3 # seqnum4
        self._set_versions(target)

        def _modify(oldversion, servermap, first_time):
            return oldversion + " modified"
        d = self._fn.modify(_modify)
        d.addCallback(lambda res: self._fn.download_best_version())
        expected = self.CONTENTS[2] + " modified"
        d.addCallback(lambda res: self.failUnlessEqual(res, expected))
        # and the servermap should indicate that the outlier was replaced too
        d.addCallback(lambda res: self._fn.get_servermap(MODE_CHECK))
        def _check_smap(smap):
            self.failUnlessEqual(smap.highest_seqnum(), 5)
            self.failUnlessEqual(len(smap.unrecoverable_versions()), 0)
            self.failUnlessEqual(len(smap.recoverable_versions()), 1)
        d.addCallback(_check_smap)
        return d


class Utils(unittest.TestCase):
    def test_cache(self):
        c = ResponseCache()
        # xdata = base62.b2a(os.urandom(100))[:100]
        xdata = "1Ex4mdMaDyOl9YnGBM3I4xaBF97j8OQAg1K3RBR01F2PwTP4HohB3XpACuku8Xj4aTQjqJIR1f36mEj3BCNjXaJmPBEZnnHL0U9l"
        ydata = "4DCUQXvkEPnnr9Lufikq5t21JsnzZKhzxKBhLhrBB6iIcBOWRuT4UweDhjuKJUre8A4wOObJnl3Kiqmlj4vjSLSqUGAkUD87Y3vs"
        c.add("v1", 1, 0, xdata)
        c.add("v1", 1, 2000, ydata)
        self.failUnlessEqual(c.read("v2", 1, 10, 11), None)
        self.failUnlessEqual(c.read("v1", 2, 10, 11), None)
        self.failUnlessEqual(c.read("v1", 1, 0, 10), xdata[:10])
        self.failUnlessEqual(c.read("v1", 1, 90, 10), xdata[90:])
        self.failUnlessEqual(c.read("v1", 1, 300, 10), None)
        self.failUnlessEqual(c.read("v1", 1, 2050, 5), ydata[50:55])
        self.failUnlessEqual(c.read("v1", 1, 0, 101), None)
        self.failUnlessEqual(c.read("v1", 1, 99, 1), xdata[99:100])
        self.failUnlessEqual(c.read("v1", 1, 100, 1), None)
        self.failUnlessEqual(c.read("v1", 1, 1990, 9), None)
        self.failUnlessEqual(c.read("v1", 1, 1990, 10), None)
        self.failUnlessEqual(c.read("v1", 1, 1990, 11), None)
        self.failUnlessEqual(c.read("v1", 1, 1990, 15), None)
        self.failUnlessEqual(c.read("v1", 1, 1990, 19), None)
        self.failUnlessEqual(c.read("v1", 1, 1990, 20), None)
        self.failUnlessEqual(c.read("v1", 1, 1990, 21), None)
        self.failUnlessEqual(c.read("v1", 1, 1990, 25), None)
        self.failUnlessEqual(c.read("v1", 1, 1999, 25), None)

        # test joining fragments
        c = ResponseCache()
        c.add("v1", 1, 0, xdata[:10])
        c.add("v1", 1, 10, xdata[10:20])
        self.failUnlessEqual(c.read("v1", 1, 0, 20), xdata[:20])

class Exceptions(unittest.TestCase):
    def test_repr(self):
        nmde = NeedMoreDataError(100, 50, 100)
        self.failUnless("NeedMoreDataError" in repr(nmde), repr(nmde))
        ucwe = UncoordinatedWriteError()
        self.failUnless("UncoordinatedWriteError" in repr(ucwe), repr(ucwe))

class SameKeyGenerator:
    def __init__(self, pubkey, privkey):
        self.pubkey = pubkey
        self.privkey = privkey
    def generate(self, keysize=None):
        return defer.succeed( (self.pubkey, self.privkey) )

class FirstServerGetsKilled:
    done = False
    def notify(self, retval, wrapper, methname):
        if not self.done:
            wrapper.broken = True
            self.done = True
        return retval

class FirstServerGetsDeleted:
    def __init__(self):
        self.done = False
        self.silenced = None
    def notify(self, retval, wrapper, methname):
        if not self.done:
            # this query will work, but later queries should think the share
            # has been deleted
            self.done = True
            self.silenced = wrapper
            return retval
        if wrapper == self.silenced:
            assert methname == "slot_testv_and_readv_and_writev"
            return (True, {})
        return retval

class Problems(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin):
    def do_publish_surprise(self, version):
        self.basedir = "mutable/Problems/test_publish_surprise_%s" % version
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        d = nm.create_mutable_file(MutableData("contents 1"),
                                    version=version)
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
            d.addCallback(_got_smap1)
            # then modify the file, leaving the old map untouched
            d.addCallback(lambda res: log.msg("starting winning write"))
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            # now attempt to modify the file with the old servermap. This
            # will look just like an uncoordinated write, in which every
            # single share got updated between our mapupdate and our publish
            d.addCallback(lambda res: log.msg("starting doomed write"))
            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "test_publish_surprise", None,
                                          n.upload,
                                          MutableData("contents 2a"), self.old_map))
            return d
        d.addCallback(_created)
        return d

    def test_publish_surprise_sdmf(self):
        return self.do_publish_surprise(SDMF_VERSION)

    def test_publish_surprise_mdmf(self):
        return self.do_publish_surprise(MDMF_VERSION)

    def test_retrieve_surprise(self):
        self.basedir = "mutable/Problems/test_retrieve_surprise"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        d = nm.create_mutable_file(MutableData("contents 1"))
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
            d.addCallback(_got_smap1)
            # then modify the file, leaving the old map untouched
            d.addCallback(lambda res: log.msg("starting winning write"))
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            # now attempt to retrieve the old version with the old servermap.
            # This will look like someone has changed the file since we
            # updated the servermap.
            d.addCallback(lambda res: n._cache._clear())
            d.addCallback(lambda res: log.msg("starting doomed read"))
            d.addCallback(lambda res:
                          self.shouldFail(NotEnoughSharesError,
                                          "test_retrieve_surprise",
                                          "ran out of peers: have 0 of 1",
                                          n.download_version,
                                          self.old_map,
                                          self.old_map.best_recoverable_version(),
                                          ))
            return d
        d.addCallback(_created)
        return d


    def test_unexpected_shares(self):
        # upload the file, take a servermap, shut down one of the servers,
        # upload it again (causing shares to appear on a new server), then
        # upload using the old servermap. The last upload should fail with an
        # UncoordinatedWriteError, because of the shares that didn't appear
        # in the servermap.
        self.basedir = "mutable/Problems/test_unexpected_shares"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        d = nm.create_mutable_file(MutableData("contents 1"))
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
                # now shut down one of the servers
                peer0 = list(smap.make_sharemap()[0])[0]
                self.g.remove_server(peer0)
                # then modify the file, leaving the old map untouched
                log.msg("starting winning write")
                return n.overwrite(MutableData("contents 2"))
            d.addCallback(_got_smap1)
            # now attempt to modify the file with the old servermap. This
            # will look just like an uncoordinated write, in which every
            # single share got updated between our mapupdate and our publish
            d.addCallback(lambda res: log.msg("starting doomed write"))
            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "test_surprise", None,
                                          n.upload,
                                          MutableData("contents 2a"), self.old_map))
            return d
        d.addCallback(_created)
        return d

    def test_bad_server(self):
        # Break one server, then create the file: the initial publish should
        # complete with an alternate server. Breaking a second server should
        # not prevent an update from succeeding either.
        self.basedir = "mutable/Problems/test_bad_server"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker

        # to make sure that one of the initial peers is broken, we have to
        # get creative. We create an RSA key and compute its storage-index.
        # Then we make a KeyGenerator that always returns that one key, and
        # use it to create the mutable file. This will get easier when we can
        # use #467 static-server-selection to disable permutation and force
        # the choice of server for share[0].

        d = nm.key_generator.generate(TEST_RSA_KEY_SIZE)
        def _got_key( (pubkey, privkey) ):
            nm.key_generator = SameKeyGenerator(pubkey, privkey)
            pubkey_s = pubkey.serialize()
            privkey_s = privkey.serialize()
            u = uri.WriteableSSKFileURI(ssk_writekey_hash(privkey_s),
                                        ssk_pubkey_fingerprint_hash(pubkey_s))
            self._storage_index = u.get_storage_index()
        d.addCallback(_got_key)
        def _break_peer0(res):
            si = self._storage_index
            servers = nm.storage_broker.get_servers_for_psi(si)
            self.g.break_server(servers[0].get_serverid())
            self.server1 = servers[1]
        d.addCallback(_break_peer0)
        # now "create" the file, using the pre-established key, and let the
        # initial publish finally happen
        d.addCallback(lambda res: nm.create_mutable_file(MutableData("contents 1")))
        # that ought to work
        def _got_node(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            # now break the second peer
            def _break_peer1(res):
                self.g.break_server(self.server1.get_serverid())
            d.addCallback(_break_peer1)
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            # that ought to work too
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            def _explain_error(f):
                print f
                if f.check(NotEnoughServersError):
                    print "first_error:", f.value.first_error
                return f
            d.addErrback(_explain_error)
            return d
        d.addCallback(_got_node)
        return d

    def test_bad_server_overlap(self):
        # like test_bad_server, but with no extra unused servers to fall back
        # upon. This means that we must re-use a server which we've already
        # used. If we don't remember the fact that we sent them one share
        # already, we'll mistakenly think we're experiencing an
        # UncoordinatedWriteError.

        # Break one server, then create the file: the initial publish should
        # complete with an alternate server. Breaking a second server should
        # not prevent an update from succeeding either.
        self.basedir = "mutable/Problems/test_bad_server_overlap"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        sb = nm.storage_broker

        peerids = [s.get_serverid() for s in sb.get_connected_servers()]
        self.g.break_server(peerids[0])

        d = nm.create_mutable_file(MutableData("contents 1"))
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            # now break one of the remaining servers
            def _break_second_server(res):
                self.g.break_server(peerids[1])
            d.addCallback(_break_second_server)
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            # that ought to work too
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d

    def test_publish_all_servers_bad(self):
        # Break all servers: the publish should fail
        self.basedir = "mutable/Problems/test_publish_all_servers_bad"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        for s in nm.storage_broker.get_connected_servers():
            s.get_rref().broken = True

        d = self.shouldFail(NotEnoughServersError,
                            "test_publish_all_servers_bad",
                            "ran out of good servers",
                            nm.create_mutable_file, MutableData("contents"))
        return d

    def test_publish_no_servers(self):
        # no servers at all: the publish should fail
        self.basedir = "mutable/Problems/test_publish_no_servers"
        self.set_up_grid(num_servers=0)
        nm = self.g.clients[0].nodemaker

        d = self.shouldFail(NotEnoughServersError,
                            "test_publish_no_servers",
                            "Ran out of non-bad servers",
                            nm.create_mutable_file, MutableData("contents"))
        return d


    def test_privkey_query_error(self):
        # when a servermap is updated with MODE_WRITE, it tries to get the
        # privkey. Something might go wrong during this query attempt.
        # Exercise the code in _privkey_query_failed which tries to handle
        # such an error.
        self.basedir = "mutable/Problems/test_privkey_query_error"
        self.set_up_grid(num_servers=20)
        nm = self.g.clients[0].nodemaker
        nm._node_cache = DevNullDictionary() # disable the nodecache

        # we need some contents that are large enough to push the privkey out
        # of the early part of the file
        LARGE = "These are Larger contents" * 2000 # about 50KB
        LARGE_uploadable = MutableData(LARGE)
        d = nm.create_mutable_file(LARGE_uploadable)
        def _created(n):
            self.uri = n.get_uri()
            self.n2 = nm.create_from_cap(self.uri)

            # When a mapupdate is performed on a node that doesn't yet know
            # the privkey, a short read is sent to a batch of servers, to get
            # the verinfo and (hopefully, if the file is short enough) the
            # encprivkey. Our file is too large to let this first read
            # contain the encprivkey. Each non-encprivkey-bearing response
            # that arrives (until the node gets the encprivkey) will trigger
            # a second read to specifically read the encprivkey.
            #
            # So, to exercise this case:
            #  1. notice which server gets a read() call first
            #  2. tell that server to start throwing errors
            killer = FirstServerGetsKilled()
            for s in nm.storage_broker.get_connected_servers():
                s.get_rref().post_call_notifier = killer.notify
        d.addCallback(_created)

        # now we update a servermap from a new node (which doesn't have the
        # privkey yet, forcing it to use a separate privkey query). Note that
        # the map-update will succeed, since we'll just get a copy from one
        # of the other shares.
        d.addCallback(lambda res: self.n2.get_servermap(MODE_WRITE))

        return d

    def test_privkey_query_missing(self):
        # like test_privkey_query_error, but the shares are deleted by the
        # second query, instead of raising an exception.
        self.basedir = "mutable/Problems/test_privkey_query_missing"
        self.set_up_grid(num_servers=20)
        nm = self.g.clients[0].nodemaker
        LARGE = "These are Larger contents" * 2000 # about 50KiB
        LARGE_uploadable = MutableData(LARGE)
        nm._node_cache = DevNullDictionary() # disable the nodecache

        d = nm.create_mutable_file(LARGE_uploadable)
        def _created(n):
            self.uri = n.get_uri()
            self.n2 = nm.create_from_cap(self.uri)
            deleter = FirstServerGetsDeleted()
            for s in nm.storage_broker.get_connected_servers():
                s.get_rref().post_call_notifier = deleter.notify
        d.addCallback(_created)
        d.addCallback(lambda res: self.n2.get_servermap(MODE_WRITE))
        return d


    def test_block_and_hash_query_error(self):
        # This tests for what happens when a query to a remote server
        # fails in either the hash validation step or the block getting
        # step (because of batching, this is the same actual query).
        # We need to have the storage server persist up until the point
        # that its prefix is validated, then suddenly die. This
        # exercises some exception handling code in Retrieve.
        self.basedir = "mutable/Problems/test_block_and_hash_query_error"
        self.set_up_grid(num_servers=20)
        nm = self.g.clients[0].nodemaker
        CONTENTS = "contents" * 2000
        CONTENTS_uploadable = MutableData(CONTENTS)
        d = nm.create_mutable_file(CONTENTS_uploadable)
        def _created(node):
            self._node = node
        d.addCallback(_created)
        d.addCallback(lambda ignored:
            self._node.get_servermap(MODE_READ))
        def _then(servermap):
            # we have our servermap. Now we set up the servers like the
            # tests above -- the first one that gets a read call should
            # start throwing errors, but only after returning its prefix
            # for validation. Since we'll download without fetching the
            # private key, the next query to the remote server will be
            # for either a block and salt or for hashes, either of which
            # will exercise the error handling code.
            killer = FirstServerGetsKilled()
            for s in nm.storage_broker.get_connected_servers():
                s.get_rref().post_call_notifier = killer.notify
            ver = servermap.best_recoverable_version()
            assert ver
            return self._node.download_version(servermap, ver)
        d.addCallback(_then)
        d.addCallback(lambda data:
            self.failUnlessEqual(data, CONTENTS))
        return d

    def test_1654(self):
        # test that the Retrieve object unconditionally verifies the block
        # hash tree root for mutable shares. The failure mode is that
        # carefully crafted shares can cause undetected corruption (the
        # retrieve appears to finish successfully, but the result is
        # corrupted). When fixed, these shares always cause a
        # CorruptShareError, which results in NotEnoughSharesError in this
        # 2-of-2 file.
        self.basedir = "mutable/Problems/test_1654"
        self.set_up_grid(num_servers=2)
        cap = uri.from_string(TEST_1654_CAP)
        si = cap.get_storage_index()

        for share, shnum in [(TEST_1654_SH0, 0), (TEST_1654_SH1, 1)]:
            sharedata = base64.b64decode(share)
            storedir = self.get_serverdir(shnum)
            storage_path = os.path.join(storedir, "shares",
                                        storage_index_to_dir(si))
            fileutil.make_dirs(storage_path)
            fileutil.write(os.path.join(storage_path, "%d" % shnum),
                           sharedata)

        nm = self.g.clients[0].nodemaker
        n = nm.create_from_cap(TEST_1654_CAP)
        # to exercise the problem correctly, we must ensure that sh0 is
        # processed first, and sh1 second. NoNetworkGrid has facilities to
        # stall the first request from a single server, but it's not
        # currently easy to extend that to stall the second request (mutable
        # retrievals will see two: first the mapupdate, then the fetch).
        # However, repeated executions of this run without the #1654 fix
        # suggests that we're failing reliably even without explicit stalls,
        # probably because the servers are queried in a fixed order. So I'm
        # ok with relying upon that.
        d = self.shouldFail(NotEnoughSharesError, "test #1654 share corruption",
                            "ran out of peers",
                            n.download_best_version)
        return d


TEST_1654_CAP = "URI:SSK:6jthysgozssjnagqlcxjq7recm:yxawei54fmf2ijkrvs2shs6iey4kpdp6joi7brj2vrva6sp5nf3a"

TEST_1654_SH0 = """\
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
72mXGlqyLyWYuAAAAAA="""

TEST_1654_SH1 = """\
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
72mXGlqyLyWYuAAAAAA="""


class FileHandle(unittest.TestCase):
    def setUp(self):
        self.test_data = "Test Data" * 50000
        self.sio = StringIO(self.test_data)
        self.uploadable = MutableFileHandle(self.sio)


    def test_filehandle_read(self):
        self.basedir = "mutable/FileHandle/test_filehandle_read"
        chunk_size = 10
        for i in xrange(0, len(self.test_data), chunk_size):
            data = self.uploadable.read(chunk_size)
            data = "".join(data)
            start = i
            end = i + chunk_size
            self.failUnlessEqual(data, self.test_data[start:end])


    def test_filehandle_get_size(self):
        self.basedir = "mutable/FileHandle/test_filehandle_get_size"
        actual_size = len(self.test_data)
        size = self.uploadable.get_size()
        self.failUnlessEqual(size, actual_size)


    def test_filehandle_get_size_out_of_order(self):
        # We should be able to call get_size whenever we want without
        # disturbing the location of the seek pointer.
        chunk_size = 100
        data = self.uploadable.read(chunk_size)
        self.failUnlessEqual("".join(data), self.test_data[:chunk_size])

        # Now get the size.
        size = self.uploadable.get_size()
        self.failUnlessEqual(size, len(self.test_data))

        # Now get more data. We should be right where we left off.
        more_data = self.uploadable.read(chunk_size)
        start = chunk_size
        end = chunk_size * 2
        self.failUnlessEqual("".join(more_data), self.test_data[start:end])


    def test_filehandle_file(self):
        # Make sure that the MutableFileHandle works on a file as well
        # as a StringIO object, since in some cases it will be asked to
        # deal with files.
        self.basedir = self.mktemp()
        # necessary? What am I doing wrong here?
        os.mkdir(self.basedir)
        f_path = os.path.join(self.basedir, "test_file")
        f = open(f_path, "w")
        f.write(self.test_data)
        f.close()
        f = open(f_path, "r")

        uploadable = MutableFileHandle(f)

        data = uploadable.read(len(self.test_data))
        self.failUnlessEqual("".join(data), self.test_data)
        size = uploadable.get_size()
        self.failUnlessEqual(size, len(self.test_data))


    def test_close(self):
        # Make sure that the MutableFileHandle closes its handle when
        # told to do so.
        self.uploadable.close()
        self.failUnless(self.sio.closed)


class DataHandle(unittest.TestCase):
    def setUp(self):
        self.test_data = "Test Data" * 50000
        self.uploadable = MutableData(self.test_data)


    def test_datahandle_read(self):
        chunk_size = 10
        for i in xrange(0, len(self.test_data), chunk_size):
            data = self.uploadable.read(chunk_size)
            data = "".join(data)
            start = i
            end = i + chunk_size
            self.failUnlessEqual(data, self.test_data[start:end])


    def test_datahandle_get_size(self):
        actual_size = len(self.test_data)
        size = self.uploadable.get_size()
        self.failUnlessEqual(size, actual_size)


    def test_datahandle_get_size_out_of_order(self):
        # We should be able to call get_size whenever we want without
        # disturbing the location of the seek pointer.
        chunk_size = 100
        data = self.uploadable.read(chunk_size)
        self.failUnlessEqual("".join(data), self.test_data[:chunk_size])

        # Now get the size.
        size = self.uploadable.get_size()
        self.failUnlessEqual(size, len(self.test_data))

        # Now get more data. We should be right where we left off.
        more_data = self.uploadable.read(chunk_size)
        start = chunk_size
        end = chunk_size * 2
        self.failUnlessEqual("".join(more_data), self.test_data[start:end])


class Version(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin, \
              PublishMixin):
    def setUp(self):
        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker
        self.data = "test data" * 100000 # about 900 KiB; MDMF
        self.small_data = "test data" * 10 # about 90 B; SDMF


    def do_upload_mdmf(self):
        d = self.nm.create_mutable_file(MutableData(self.data),
                                        version=MDMF_VERSION)
        def _then(n):
            assert isinstance(n, MutableFileNode)
            assert n._protocol_version == MDMF_VERSION
            self.mdmf_node = n
            return n
        d.addCallback(_then)
        return d

    def do_upload_sdmf(self):
        d = self.nm.create_mutable_file(MutableData(self.small_data))
        def _then(n):
            assert isinstance(n, MutableFileNode)
            assert n._protocol_version == SDMF_VERSION
            self.sdmf_node = n
            return n
        d.addCallback(_then)
        return d

    def do_upload_empty_sdmf(self):
        d = self.nm.create_mutable_file(MutableData(""))
        def _then(n):
            assert isinstance(n, MutableFileNode)
            self.sdmf_zero_length_node = n
            assert n._protocol_version == SDMF_VERSION
            return n
        d.addCallback(_then)
        return d

    def do_upload(self):
        d = self.do_upload_mdmf()
        d.addCallback(lambda ign: self.do_upload_sdmf())
        return d

    def test_debug(self):
        d = self.do_upload_mdmf()
        def _debug(n):
            fso = debug.FindSharesOptions()
            storage_index = base32.b2a(n.get_storage_index())
            fso.si_s = storage_index
            fso.nodedirs = [unicode(os.path.dirname(os.path.abspath(storedir)))
                            for (i,ss,storedir)
                            in self.iterate_servers()]
            fso.stdout = StringIO()
            fso.stderr = StringIO()
            debug.find_shares(fso)
            sharefiles = fso.stdout.getvalue().splitlines()
            expected = self.nm.default_encoding_parameters["n"]
            self.failUnlessEqual(len(sharefiles), expected)

            do = debug.DumpOptions()
            do["filename"] = sharefiles[0]
            do.stdout = StringIO()
            debug.dump_share(do)
            output = do.stdout.getvalue()
            lines = set(output.splitlines())
            self.failUnless("Mutable slot found:" in lines, output)
            self.failUnless(" share_type: MDMF" in lines, output)
            self.failUnless(" num_extra_leases: 0" in lines, output)
            self.failUnless(" MDMF contents:" in lines, output)
            self.failUnless("  seqnum: 1" in lines, output)
            self.failUnless("  required_shares: 3" in lines, output)
            self.failUnless("  total_shares: 10" in lines, output)
            self.failUnless("  segsize: 131073" in lines, output)
            self.failUnless("  datalen: %d" % len(self.data) in lines, output)
            vcap = n.get_verify_cap().to_string()
            self.failUnless("  verify-cap: %s" % vcap in lines, output)

            cso = debug.CatalogSharesOptions()
            cso.nodedirs = fso.nodedirs
            cso.stdout = StringIO()
            cso.stderr = StringIO()
            debug.catalog_shares(cso)
            shares = cso.stdout.getvalue().splitlines()
            oneshare = shares[0] # all shares should be MDMF
            self.failIf(oneshare.startswith("UNKNOWN"), oneshare)
            self.failUnless(oneshare.startswith("MDMF"), oneshare)
            fields = oneshare.split()
            self.failUnlessEqual(fields[0], "MDMF")
            self.failUnlessEqual(fields[1], storage_index)
            self.failUnlessEqual(fields[2], "3/10")
            self.failUnlessEqual(fields[3], "%d" % len(self.data))
            self.failUnless(fields[4].startswith("#1:"), fields[3])
            # the rest of fields[4] is the roothash, which depends upon
            # encryption salts and is not constant. fields[5] is the
            # remaining time on the longest lease, which is timing dependent.
            # The rest of the line is the quoted pathname to the share.
        d.addCallback(_debug)
        return d

    def test_get_sequence_number(self):
        d = self.do_upload()
        d.addCallback(lambda ign: self.mdmf_node.get_best_readable_version())
        d.addCallback(lambda bv:
            self.failUnlessEqual(bv.get_sequence_number(), 1))
        d.addCallback(lambda ignored:
            self.sdmf_node.get_best_readable_version())
        d.addCallback(lambda bv:
            self.failUnlessEqual(bv.get_sequence_number(), 1))
        # Now update. The sequence number in both cases should be 1 in
        # both cases.
        def _do_update(ignored):
            new_data = MutableData("foo bar baz" * 100000)
            new_small_data = MutableData("foo bar baz" * 10)
            d1 = self.mdmf_node.overwrite(new_data)
            d2 = self.sdmf_node.overwrite(new_small_data)
            dl = gatherResults([d1, d2])
            return dl
        d.addCallback(_do_update)
        d.addCallback(lambda ignored:
            self.mdmf_node.get_best_readable_version())
        d.addCallback(lambda bv:
            self.failUnlessEqual(bv.get_sequence_number(), 2))
        d.addCallback(lambda ignored:
            self.sdmf_node.get_best_readable_version())
        d.addCallback(lambda bv:
            self.failUnlessEqual(bv.get_sequence_number(), 2))
        return d


    def test_cap_after_upload(self):
        # If we create a new mutable file and upload things to it, and
        # it's an MDMF file, we should get an MDMF cap back from that
        # file and should be able to use that.
        # That's essentially what MDMF node is, so just check that.
        d = self.do_upload_mdmf()
        def _then(ign):
            mdmf_uri = self.mdmf_node.get_uri()
            cap = uri.from_string(mdmf_uri)
            self.failUnless(isinstance(cap, uri.WriteableMDMFFileURI))
            readonly_mdmf_uri = self.mdmf_node.get_readonly_uri()
            cap = uri.from_string(readonly_mdmf_uri)
            self.failUnless(isinstance(cap, uri.ReadonlyMDMFFileURI))
        d.addCallback(_then)
        return d

    def test_mutable_version(self):
        # assert that getting parameters from the IMutableVersion object
        # gives us the same data as getting them from the filenode itself
        d = self.do_upload()
        d.addCallback(lambda ign: self.mdmf_node.get_best_mutable_version())
        def _check_mdmf(bv):
            n = self.mdmf_node
            self.failUnlessEqual(bv.get_writekey(), n.get_writekey())
            self.failUnlessEqual(bv.get_storage_index(), n.get_storage_index())
            self.failIf(bv.is_readonly())
        d.addCallback(_check_mdmf)
        d.addCallback(lambda ign: self.sdmf_node.get_best_mutable_version())
        def _check_sdmf(bv):
            n = self.sdmf_node
            self.failUnlessEqual(bv.get_writekey(), n.get_writekey())
            self.failUnlessEqual(bv.get_storage_index(), n.get_storage_index())
            self.failIf(bv.is_readonly())
        d.addCallback(_check_sdmf)
        return d


    def test_get_readonly_version(self):
        d = self.do_upload()
        d.addCallback(lambda ign: self.mdmf_node.get_best_readable_version())
        d.addCallback(lambda bv: self.failUnless(bv.is_readonly()))

        # Attempting to get a mutable version of a mutable file from a
        # filenode initialized with a readcap should return a readonly
        # version of that same node.
        d.addCallback(lambda ign: self.mdmf_node.get_readonly())
        d.addCallback(lambda ro: ro.get_best_mutable_version())
        d.addCallback(lambda v: self.failUnless(v.is_readonly()))

        d.addCallback(lambda ign: self.sdmf_node.get_best_readable_version())
        d.addCallback(lambda bv: self.failUnless(bv.is_readonly()))

        d.addCallback(lambda ign: self.sdmf_node.get_readonly())
        d.addCallback(lambda ro: ro.get_best_mutable_version())
        d.addCallback(lambda v: self.failUnless(v.is_readonly()))
        return d


    def test_toplevel_overwrite(self):
        new_data = MutableData("foo bar baz" * 100000)
        new_small_data = MutableData("foo bar baz" * 10)
        d = self.do_upload()
        d.addCallback(lambda ign: self.mdmf_node.overwrite(new_data))
        d.addCallback(lambda ignored:
            self.mdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.failUnlessEqual(data, "foo bar baz" * 100000))
        d.addCallback(lambda ignored:
            self.sdmf_node.overwrite(new_small_data))
        d.addCallback(lambda ignored:
            self.sdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.failUnlessEqual(data, "foo bar baz" * 10))
        return d


    def test_toplevel_modify(self):
        d = self.do_upload()
        def modifier(old_contents, servermap, first_time):
            return old_contents + "modified"
        d.addCallback(lambda ign: self.mdmf_node.modify(modifier))
        d.addCallback(lambda ignored:
            self.mdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.failUnlessIn("modified", data))
        d.addCallback(lambda ignored:
            self.sdmf_node.modify(modifier))
        d.addCallback(lambda ignored:
            self.sdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.failUnlessIn("modified", data))
        return d


    def test_version_modify(self):
        # TODO: When we can publish multiple versions, alter this test
        # to modify a version other than the best usable version, then
        # test to see that the best recoverable version is that.
        d = self.do_upload()
        def modifier(old_contents, servermap, first_time):
            return old_contents + "modified"
        d.addCallback(lambda ign: self.mdmf_node.modify(modifier))
        d.addCallback(lambda ignored:
            self.mdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.failUnlessIn("modified", data))
        d.addCallback(lambda ignored:
            self.sdmf_node.modify(modifier))
        d.addCallback(lambda ignored:
            self.sdmf_node.download_best_version())
        d.addCallback(lambda data:
            self.failUnlessIn("modified", data))
        return d


    def test_download_version(self):
        d = self.publish_multiple()
        # We want to have two recoverable versions on the grid.
        d.addCallback(lambda res:
                      self._set_versions({0:0,2:0,4:0,6:0,8:0,
                                          1:1,3:1,5:1,7:1,9:1}))
        # Now try to download each version. We should get the plaintext
        # associated with that version.
        d.addCallback(lambda ignored:
            self._fn.get_servermap(mode=MODE_READ))
        def _got_servermap(smap):
            versions = smap.recoverable_versions()
            assert len(versions) == 2

            self.servermap = smap
            self.version1, self.version2 = versions
            assert self.version1 != self.version2

            self.version1_seqnum = self.version1[0]
            self.version2_seqnum = self.version2[0]
            self.version1_index = self.version1_seqnum - 1
            self.version2_index = self.version2_seqnum - 1

        d.addCallback(_got_servermap)
        d.addCallback(lambda ignored:
            self._fn.download_version(self.servermap, self.version1))
        d.addCallback(lambda results:
            self.failUnlessEqual(self.CONTENTS[self.version1_index],
                                 results))
        d.addCallback(lambda ignored:
            self._fn.download_version(self.servermap, self.version2))
        d.addCallback(lambda results:
            self.failUnlessEqual(self.CONTENTS[self.version2_index],
                                 results))
        return d


    def test_download_nonexistent_version(self):
        d = self.do_upload_mdmf()
        d.addCallback(lambda ign: self.mdmf_node.get_servermap(mode=MODE_WRITE))
        def _set_servermap(servermap):
            self.servermap = servermap
        d.addCallback(_set_servermap)
        d.addCallback(lambda ignored:
           self.shouldFail(UnrecoverableFileError, "nonexistent version",
                           None,
                           self.mdmf_node.download_version, self.servermap,
                           "not a version"))
        return d


    def test_partial_read(self):
        d = self.do_upload_mdmf()
        d.addCallback(lambda ign: self.mdmf_node.get_best_readable_version())
        modes = [("start_on_segment_boundary",
                  mathutil.next_multiple(128 * 1024, 3), 50),
                 ("ending_one_byte_after_segment_boundary",
                  mathutil.next_multiple(128 * 1024, 3)-50, 51),
                 ("zero_length_at_start", 0, 0),
                 ("zero_length_in_middle", 50, 0),
                 ("zero_length_at_segment_boundary",
                  mathutil.next_multiple(128 * 1024, 3), 0),
                 ]
        for (name, offset, length) in modes:
            d.addCallback(self._do_partial_read, name, offset, length)
        # then read only a few bytes at a time, and see that the results are
        # what we expect.
        def _read_data(version):
            c = consumer.MemoryConsumer()
            d2 = defer.succeed(None)
            for i in xrange(0, len(self.data), 10000):
                d2.addCallback(lambda ignored, i=i: version.read(c, i, 10000))
            d2.addCallback(lambda ignored:
                self.failUnlessEqual(self.data, "".join(c.chunks)))
            return d2
        d.addCallback(_read_data)
        return d
    def _do_partial_read(self, version, name, offset, length):
        c = consumer.MemoryConsumer()
        d = version.read(c, offset, length)
        expected = self.data[offset:offset+length]
        d.addCallback(lambda ignored: "".join(c.chunks))
        def _check(results):
            if results != expected:
                print
                print "got: %s ... %s" % (results[:20], results[-20:])
                print "exp: %s ... %s" % (expected[:20], expected[-20:])
                self.fail("results[%s] != expected" % name)
            return version # daisy-chained to next call
        d.addCallback(_check)
        return d


    def _test_read_and_download(self, node, expected):
        d = node.get_best_readable_version()
        def _read_data(version):
            c = consumer.MemoryConsumer()
            d2 = defer.succeed(None)
            d2.addCallback(lambda ignored: version.read(c))
            d2.addCallback(lambda ignored:
                self.failUnlessEqual(expected, "".join(c.chunks)))
            return d2
        d.addCallback(_read_data)
        d.addCallback(lambda ignored: node.download_best_version())
        d.addCallback(lambda data: self.failUnlessEqual(expected, data))
        return d

    def test_read_and_download_mdmf(self):
        d = self.do_upload_mdmf()
        d.addCallback(self._test_read_and_download, self.data)
        return d

    def test_read_and_download_sdmf(self):
        d = self.do_upload_sdmf()
        d.addCallback(self._test_read_and_download, self.small_data)
        return d

    def test_read_and_download_sdmf_zero_length(self):
        d = self.do_upload_empty_sdmf()
        d.addCallback(self._test_read_and_download, "")
        return d


class Update(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin):
    timeout = 400 # these tests are too big, 120s is not enough on slow
                  # platforms
    def setUp(self):
        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker
        self.data = "testdata " * 100000 # about 900 KiB; MDMF
        self.small_data = "test data" * 10 # about 90 B; SDMF


    def do_upload_sdmf(self):
        d = self.nm.create_mutable_file(MutableData(self.small_data))
        def _then(n):
            assert isinstance(n, MutableFileNode)
            self.sdmf_node = n
            # Make SDMF node that has 255 shares.
            self.nm.default_encoding_parameters['n'] = 255
            self.nm.default_encoding_parameters['k'] = 127
            return self.nm.create_mutable_file(MutableData(self.small_data))
        d.addCallback(_then)
        def _then2(n):
            assert isinstance(n, MutableFileNode)
            self.sdmf_max_shares_node = n
        d.addCallback(_then2)
        return d

    def do_upload_mdmf(self):
        d = self.nm.create_mutable_file(MutableData(self.data),
                                        version=MDMF_VERSION)
        def _then(n):
            assert isinstance(n, MutableFileNode)
            self.mdmf_node = n
            # Make MDMF node that has 255 shares.
            self.nm.default_encoding_parameters['n'] = 255
            self.nm.default_encoding_parameters['k'] = 127
            return self.nm.create_mutable_file(MutableData(self.data),
                                               version=MDMF_VERSION)
        d.addCallback(_then)
        def _then2(n):
            assert isinstance(n, MutableFileNode)
            self.mdmf_max_shares_node = n
        d.addCallback(_then2)
        return d

    def _test_replace(self, offset, new_data):
        expected = self.data[:offset]+new_data+self.data[offset+len(new_data):]
        d0 = self.do_upload_mdmf()
        def _run(ign):
            d = defer.succeed(None)
            for node in (self.mdmf_node, self.mdmf_max_shares_node):
                # close over 'node'.
                d.addCallback(lambda ign, node=node:
                              node.get_best_mutable_version())
                d.addCallback(lambda mv:
                              mv.update(MutableData(new_data), offset))
                d.addCallback(lambda ign, node=node:
                              node.download_best_version())
                def _check(results):
                    if results != expected:
                        print
                        print "got: %s ... %s" % (results[:20], results[-20:])
                        print "exp: %s ... %s" % (expected[:20], expected[-20:])
                        self.fail("results != expected")
                d.addCallback(_check)
            return d
        d0.addCallback(_run)
        return d0

    def test_append(self):
        # We should be able to append data to a mutable file and get
        # what we expect.
        return self._test_replace(len(self.data), "appended")

    def test_replace_middle(self):
        # We should be able to replace data in the middle of a mutable
        # file and get what we expect back.
        return self._test_replace(100, "replaced")

    def test_replace_beginning(self):
        # We should be able to replace data at the beginning of the file
        # without truncating the file
        return self._test_replace(0, "beginning")

    def test_replace_segstart1(self):
        return self._test_replace(128*1024+1, "NNNN")

    def test_replace_zero_length_beginning(self):
        return self._test_replace(0, "")

    def test_replace_zero_length_middle(self):
        return self._test_replace(50, "")

    def test_replace_zero_length_segstart1(self):
        return self._test_replace(128*1024+1, "")

    def test_replace_and_extend(self):
        # We should be able to replace data in the middle of a mutable
        # file and extend that mutable file and get what we expect.
        return self._test_replace(100, "modified " * 100000)


    def _check_differences(self, got, expected):
        # displaying arbitrary file corruption is tricky for a
        # 1MB file of repeating data,, so look for likely places
        # with problems and display them separately
        gotmods = [mo.span() for mo in re.finditer('([A-Z]+)', got)]
        expmods = [mo.span() for mo in re.finditer('([A-Z]+)', expected)]
        gotspans = ["%d:%d=%s" % (start,end,got[start:end])
                    for (start,end) in gotmods]
        expspans = ["%d:%d=%s" % (start,end,expected[start:end])
                    for (start,end) in expmods]
        #print "expecting: %s" % expspans

        SEGSIZE = 128*1024
        if got != expected:
            print "differences:"
            for segnum in range(len(expected)//SEGSIZE):
                start = segnum * SEGSIZE
                end = (segnum+1) * SEGSIZE
                got_ends = "%s .. %s" % (got[start:start+20], got[end-20:end])
                exp_ends = "%s .. %s" % (expected[start:start+20], expected[end-20:end])
                if got_ends != exp_ends:
                    print "expected[%d]: %s" % (start, exp_ends)
                    print "got     [%d]: %s" % (start, got_ends)
            if expspans != gotspans:
                print "expected: %s" % expspans
                print "got     : %s" % gotspans
            open("EXPECTED","wb").write(expected)
            open("GOT","wb").write(got)
            print "wrote data to EXPECTED and GOT"
            self.fail("didn't get expected data")


    def test_replace_locations(self):
        # exercise fencepost conditions
        SEGSIZE = 128*1024
        suspects = range(SEGSIZE-3, SEGSIZE+1)+range(2*SEGSIZE-3, 2*SEGSIZE+1)
        letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        d0 = self.do_upload_mdmf()
        def _run(ign):
            expected = self.data
            d = defer.succeed(None)
            for offset in suspects:
                new_data = letters.next()*2 # "AA", then "BB", etc
                expected = expected[:offset]+new_data+expected[offset+2:]
                d.addCallback(lambda ign:
                              self.mdmf_node.get_best_mutable_version())
                def _modify(mv, offset=offset, new_data=new_data):
                    # close over 'offset','new_data'
                    md = MutableData(new_data)
                    return mv.update(md, offset)
                d.addCallback(_modify)
                d.addCallback(lambda ignored:
                              self.mdmf_node.download_best_version())
                d.addCallback(self._check_differences, expected)
            return d
        d0.addCallback(_run)
        return d0

    def test_replace_locations_max_shares(self):
        # exercise fencepost conditions
        SEGSIZE = 128*1024
        suspects = range(SEGSIZE-3, SEGSIZE+1)+range(2*SEGSIZE-3, 2*SEGSIZE+1)
        letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        d0 = self.do_upload_mdmf()
        def _run(ign):
            expected = self.data
            d = defer.succeed(None)
            for offset in suspects:
                new_data = letters.next()*2 # "AA", then "BB", etc
                expected = expected[:offset]+new_data+expected[offset+2:]
                d.addCallback(lambda ign:
                              self.mdmf_max_shares_node.get_best_mutable_version())
                def _modify(mv, offset=offset, new_data=new_data):
                    # close over 'offset','new_data'
                    md = MutableData(new_data)
                    return mv.update(md, offset)
                d.addCallback(_modify)
                d.addCallback(lambda ignored:
                              self.mdmf_max_shares_node.download_best_version())
                d.addCallback(self._check_differences, expected)
            return d
        d0.addCallback(_run)
        return d0


    def test_append_power_of_two(self):
        # If we attempt to extend a mutable file so that its segment
        # count crosses a power-of-two boundary, the update operation
        # should know how to reencode the file.

        # Note that the data populating self.mdmf_node is about 900 KiB
        # long -- this is 7 segments in the default segment size. So we
        # need to add 2 segments worth of data to push it over a
        # power-of-two boundary.
        segment = "a" * DEFAULT_MAX_SEGMENT_SIZE
        new_data = self.data + (segment * 2)
        d0 = self.do_upload_mdmf()
        def _run(ign):
            d = defer.succeed(None)
            for node in (self.mdmf_node, self.mdmf_max_shares_node):
                # close over 'node'.
                d.addCallback(lambda ign, node=node:
                              node.get_best_mutable_version())
                d.addCallback(lambda mv:
                              mv.update(MutableData(segment * 2), len(self.data)))
                d.addCallback(lambda ign, node=node:
                              node.download_best_version())
                d.addCallback(lambda results:
                              self.failUnlessEqual(results, new_data))
            return d
        d0.addCallback(_run)
        return d0

    def test_update_sdmf(self):
        # Running update on a single-segment file should still work.
        new_data = self.small_data + "appended"
        d0 = self.do_upload_sdmf()
        def _run(ign):
            d = defer.succeed(None)
            for node in (self.sdmf_node, self.sdmf_max_shares_node):
                # close over 'node'.
                d.addCallback(lambda ign, node=node:
                              node.get_best_mutable_version())
                d.addCallback(lambda mv:
                              mv.update(MutableData("appended"), len(self.small_data)))
                d.addCallback(lambda ign, node=node:
                              node.download_best_version())
                d.addCallback(lambda results:
                              self.failUnlessEqual(results, new_data))
            return d
        d0.addCallback(_run)
        return d0

    def test_replace_in_last_segment(self):
        # The wrapper should know how to handle the tail segment
        # appropriately.
        replace_offset = len(self.data) - 100
        new_data = self.data[:replace_offset] + "replaced"
        rest_offset = replace_offset + len("replaced")
        new_data += self.data[rest_offset:]
        d0 = self.do_upload_mdmf()
        def _run(ign):
            d = defer.succeed(None)
            for node in (self.mdmf_node, self.mdmf_max_shares_node):
                # close over 'node'.
                d.addCallback(lambda ign, node=node:
                              node.get_best_mutable_version())
                d.addCallback(lambda mv:
                              mv.update(MutableData("replaced"), replace_offset))
                d.addCallback(lambda ign, node=node:
                              node.download_best_version())
                d.addCallback(lambda results:
                              self.failUnlessEqual(results, new_data))
            return d
        d0.addCallback(_run)
        return d0

    def test_multiple_segment_replace(self):
        replace_offset = 2 * DEFAULT_MAX_SEGMENT_SIZE
        new_data = self.data[:replace_offset]
        new_segment = "a" * DEFAULT_MAX_SEGMENT_SIZE
        new_data += 2 * new_segment
        new_data += "replaced"
        rest_offset = len(new_data)
        new_data += self.data[rest_offset:]
        d0 = self.do_upload_mdmf()
        def _run(ign):
            d = defer.succeed(None)
            for node in (self.mdmf_node, self.mdmf_max_shares_node):
                # close over 'node'.
                d.addCallback(lambda ign, node=node:
                              node.get_best_mutable_version())
                d.addCallback(lambda mv:
                              mv.update(MutableData((2 * new_segment) + "replaced"),
                                        replace_offset))
                d.addCallback(lambda ignored, node=node:
                              node.download_best_version())
                d.addCallback(lambda results:
                              self.failUnlessEqual(results, new_data))
            return d
        d0.addCallback(_run)
        return d0

class Interoperability(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin):
    sdmf_old_shares = {}
    sdmf_old_shares[0] = "VGFob2UgbXV0YWJsZSBjb250YWluZXIgdjEKdQlEA47ESLbTdKdpLJXCpBxd5OH239tl5hvAiz1dvGdE5rIOpf8cbfxbPcwNF+Y5dM92uBVbmV6KAAAAAAAAB/wAAAAAAAAJ0AAAAAFOWSw7jSx7WXzaMpdleJYXwYsRCV82jNA5oex9m2YhXSnb2POh+vvC1LE1NAfRc9GOb2zQG84Xdsx1Jub2brEeKkyt0sRIttN0p2kslcKkHF3k4fbf22XmAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABamJprL6ecrsOoFKdrXUmWveLq8nzEGDOjFnyK9detI3noX3uyK2MwSnFdAfyN0tuAwoAAAAAAAAAFQAAAAAAAAAVAAABjwAAAo8AAAMXAAADNwAAAAAAAAM+AAAAAAAAB/wwggEgMA0GCSqGSIb3DQEBAQUAA4IBDQAwggEIAoIBAQC1IkainlJF12IBXBQdpRK1zXB7a26vuEYqRmQM09YjC6sQjCs0F2ICk8n9m/2Kw4l16eIEboB2Au9pODCE+u/dEAakEFh4qidTMn61rbGUbsLK8xzuWNW22ezzz9/nPia0HDrulXt51/FYtfnnAuD1RJGXJv/8tDllE9FL/18TzlH4WuB6Fp8FTgv7QdbZAfWJHDGFIpVCJr1XxOCsSZNFJIqGwZnD2lsChiWw5OJDbKd8otqN1hIbfHyMyfMOJ/BzRzvZXaUt4Dv5nf93EmQDWClxShRwpuX/NkZ5B2K9OFonFTbOCexm/MjMAdCBqebKKaiHFkiknUCn9eJQpZ5bAgERgV50VKj+AVTDfgTpqfO2vfo4wrufi6ZBb8QV7hllhUFBjYogQ9C96dnS7skv0s+cqFuUjwMILr5/rsbEmEMGvl0T0ytyAbtlXuowEFVj/YORNknM4yjY72YUtEPTlMpk0Cis7aIgTvu5qWMPER26PMApZuRqiwRsGIkaJIvOVOTHHjFYe3/YzdMkc7OZtqRMfQLtwVl2/zKQQV8b/a9vaT6q3mRLRd4P3esaAFe/+7sR/t+9tmB+a8kxtKM6kmaVQJMbXJZ4aoHGfeLX0m35Rcvu2Bmph7QfSDjk/eaE3q55zYSoGWShmlhlw4Kwg84sMuhmcVhLvo0LovR8bKmbdgACtTh7+7gs/l5w1lOkgbF6w7rkXLNslK7L2KYF4SPFLUcABOOLy8EETxh7h7/z9d62EiPu9CNpRrCOLxUhn+JUS+DuAAhgcAb/adrQFrhlrRNoRpvjDuxmFebA4F0qCyqWssm61AAQ/EX4eC/1+hGOQ/h4EiKUkqxdsfzdcPlDvd11SGWZ0VHsUclZChTzuBAU2zLTXm+cG8IFhO50ly6Ey/DB44NtMKVaVzO0nU8DE0Wua7Lx6Bnad5n91qmHAnwSEJE5YIhQM634omd6cq9Wk4seJCUIn+ucoknrpxp0IR9QMxpKSMRHRUg2K8ZegnY3YqFunRZKCfsq9ufQEKgjZN12AFqi551KPBdn4/3V5HK6xTv0P4robSsE/BvuIfByvRf/W7ZrDx+CFC4EEcsBOACOZCrkhhqd5TkYKbe9RA+vs56+9N5qZGurkxcoKviiyEncxvTuShD65DK/6x6kMDMgQv/EdZDI3x9GtHTnRBYXwDGnPJ19w+q2zC3e2XarbxTGYQIPEC5mYx0gAA0sbjf018NGfwBhl6SB54iGsa8uLvR3jHv6OSRJgwxL6j7P0Ts4Hv2EtO12P0Lv21pwi3JC1O/WviSrKCvrQD5lMHL9Uym3hwFi2zu0mqwZvxOAbGy7kfOPXkLYKOHTZLthzKj3PsdjeceWBfYIvPGKYcd6wDr36d1aXSYS4IWeApTS2AQ2lu0DUcgSefAvsA8NkgOklvJY1cjTMSg6j6cxQo48Bvl8RAWGLbr4h2S/8KwDGxwLsSv0Gop/gnFc3GzCsmL0EkEyHHWkCA8YRXCghfW80KLDV495ff7yF5oiwK56GniqowZ3RG9Jxp5MXoJQgsLV1VMQFMAmsY69yz8eoxRH3wl9L0dMyndLulhWWzNwPMQ2I0yAWdzA/pksVmwTJTFenB3MHCiWc5rEwJ3yofe6NZZnZQrYyL9r1TNnVwfTwRUiykPiLSk4x9Mi6DX7RamDAxc8u3gDVfjPsTOTagBOEGUWlGAL54KE/E6sgCQ5DEAt12chk8AxbjBFLPgV+/idrzS0lZHOL+IVBI9D0i3Bq1yZcSIqcjZB0M3IbxbPm4gLAYOWEiTUN2ecsEHHg9nt6rhgffVoqSbCCFPbpC0xf7WOC3+BQORIZECOCC7cUAciXq3xn+GuxpFE40RWRJeKAK7bBQ21X89ABIXlQFkFddZ9kRvlZ2Pnl0oeF+2pjnZu0Yc2czNfZEQF2P7BKIdLrgMgxG89snxAY8qAYTCKyQw6xTG87wkjDcpy1wzsZLP3WsOuO7cAm7b27xU0jRKq8Cw4d1hDoyRG+RdS53F8RFJzVMaNNYgxU2tfRwUvXpTRXiOheeRVvh25+YGVnjakUXjx/dSDnOw4ETHGHD+7styDkeSfc3BdSZxswzc6OehgMI+xsCxeeRym15QUm9hxvg8X7Bfz/0WulgFwgzrm11TVynZYOmvyHpiZKoqQyQyKahIrfhwuchCr7lMsZ4a+umIkNkKxCLZnI+T7jd+eGFMgKItjz3kTTxRl3IhaJG3LbPmwRUJynMxQKdMi4Uf0qy0U7+i8hIJ9m50QXc+3tw2bwDSbx22XYJ9Wf14gxx5G5SPTb1JVCbhe4fxNt91xIxCow2zk62tzbYfRe6dfmDmgYHkv2PIEtMJZK8iKLDjFfu2ZUxsKT2A5g1q17og6o9MeXeuFS3mzJXJYFQZd+3UzlFR9qwkFkby9mg5y4XSeMvRLOHPt/H/r5SpEqBE6a9MadZYt61FBV152CUEzd43ihXtrAa0XH9HdsiySBcWI1SpM3mv9rRP0DiLjMUzHw/K1D8TE2f07zW4t/9kvE11tFj/NpICixQAAAAA="
    sdmf_old_shares[1] = "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"
    sdmf_old_shares[2] = "VGFob2UgbXV0YWJsZSBjb250YWluZXIgdjEKdQlEA47ESLbTdKdpLJXCpBxd5OH239tl5hvAiz1dvGdE5rIOpf8cbfxbPcwNF+Y5dM92uBVbmV6KAAAAAAAAB/wAAAAAAAAJ0AAAAAFOWSw7jSx7WXzaMpdleJYXwYsRCV82jNA5oex9m2YhXSnb2POh+vvC1LE1NAfRc9GOb2zQG84Xdsx1Jub2brEeKkyt0sRIttN0p2kslcKkHF3k4fbf22XmAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABamJprL6ecrsOoFKdrXUmWveLq8nzEGDOjFnyK9detI3noX3uyK2MwSnFdAfyN0tuAwoAAAAAAAAAFQAAAAAAAAAVAAABjwAAAo8AAAMXAAADNwAAAAAAAAM+AAAAAAAAB/wwggEgMA0GCSqGSIb3DQEBAQUAA4IBDQAwggEIAoIBAQC1IkainlJF12IBXBQdpRK1zXB7a26vuEYqRmQM09YjC6sQjCs0F2ICk8n9m/2Kw4l16eIEboB2Au9pODCE+u/dEAakEFh4qidTMn61rbGUbsLK8xzuWNW22ezzz9/nPia0HDrulXt51/FYtfnnAuD1RJGXJv/8tDllE9FL/18TzlH4WuB6Fp8FTgv7QdbZAfWJHDGFIpVCJr1XxOCsSZNFJIqGwZnD2lsChiWw5OJDbKd8otqN1hIbfHyMyfMOJ/BzRzvZXaUt4Dv5nf93EmQDWClxShRwpuX/NkZ5B2K9OFonFTbOCexm/MjMAdCBqebKKaiHFkiknUCn9eJQpZ5bAgERgV50VKj+AVTDfgTpqfO2vfo4wrufi6ZBb8QV7hllhUFBjYogQ9C96dnS7skv0s+cqFuUjwMILr5/rsbEmEMGvl0T0ytyAbtlXuowEFVj/YORNknM4yjY72YUtEPTlMpk0Cis7aIgTvu5qWMPER26PMApZuRqiwRsGIkaJIvOVOTHHjFYe3/YzdMkc7OZtqRMfQLtwVl2/zKQQV8b/a9vaT6q3mRLRd4P3esaAFe/+7sR/t+9tmB+a8kxtKM6kmaVQJMbXJZ4aoHGfeLX0m35Rcvu2Bmph7QfSDjk/eaE3q55zYSoGWShmlhlw4Kwg84sMuhmcVhLvo0LovR8bKmbdgACtTh7+7gs/l5w1lOkgbF6w7rkXLNslK7L2KYF4SPFLUcABOOLy8EETxh7h7/z9d62EiPu9CNpRrCOLxUhn+JUS+DuAAd8jdiCodW233N1acXhZGnulDKR3hiNsMdEIsijRPemewASoSCFpVj4utEE+eVFM146xfgC6DX39GaQ2zT3YKsWX3GiLwKtGffwqV7IlZIcBEVqMfTXSTZsY+dZm1MxxCZH0Zd33VY0yggDE0Wua7Lx6Bnad5n91qmHAnwSEJE5YIhQM634omd6cq9Wk4seJCUIn+ucoknrpxp0IR9QMxpKSMRHRUg2K8ZegnY3YqFunRZKCfsq9ufQEKgjZN12AFqi551KPBdn4/3V5HK6xTv0P4robSsE/BvuIfByvRf/W7ZrDx+CFC4EEcsBOACOZCrkhhqd5TkYKbe9RA+vs56+9N5qZGurkxcoKviiyEncxvTuShD65DK/6x6kMDMgQv/EdZDI3x9GtHTnRBYXwDGnPJ19w+q2zC3e2XarbxTGYQIPEC5mYx0gAA0sbjf018NGfwBhl6SB54iGsa8uLvR3jHv6OSRJgwxL6j7P0Ts4Hv2EtO12P0Lv21pwi3JC1O/WviSrKCvrQD5lMHL9Uym3hwFi2zu0mqwZvxOAbGy7kfOPXkLYKOHTZLthzKj3PsdjeceWBfYIvPGKYcd6wDr36d1aXSYS4IWeApTS2AQ2lu0DUcgSefAvsA8NkgOklvJY1cjTMSg6j6cxQo48Bvl8RAWGLbr4h2S/8KwDGxwLsSv0Gop/gnFc3GzCsmL0EkEyHHWkCA8YRXCghfW80KLDV495ff7yF5oiwK56GniqowZ3RG9Jxp5MXoJQgsLV1VMQFMAmsY69yz8eoxRH3wl9L0dMyndLulhWWzNwPMQ2I0yAWdzA/pksVmwTJTFenB3MHCiWc5rEwJ3yofe6NZZnZQrYyL9r1TNnVwfTwRUiykPiLSk4x9Mi6DX7RamDAxc8u3gDVfjPsTOTagBOEGUWlGAL54KE/E6sgCQ5DEAt12chk8AxbjBFLPgV+/idrzS0lZHOL+IVBI9D0i3Bq1yZcSIqcjZB0M3IbxbPm4gLAYOWEiTUN2ecsEHHg9nt6rhgffVoqSbCCFPbpC0xf7WOC3+BQORIZECOCC7cUAciXq3xn+GuxpFE40RWRJeKAK7bBQ21X89ABIXlQFkFddZ9kRvlZ2Pnl0oeF+2pjnZu0Yc2czNfZEQF2P7BKIdLrgMgxG89snxAY8qAYTCKyQw6xTG87wkjDcpy1wzsZLP3WsOuO7cAm7b27xU0jRKq8Cw4d1hDoyRG+RdS53F8RFJzVMaNNYgxU2tfRwUvXpTRXiOheeRVvh25+YGVnjakUXjx/dSDnOw4ETHGHD+7styDkeSfc3BdSZxswzc6OehgMI+xsCxeeRym15QUm9hxvg8X7Bfz/0WulgFwgzrm11TVynZYOmvyHpiZKoqQyQyKahIrfhwuchCr7lMsZ4a+umIkNkKxCLZnI+T7jd+eGFMgKItjz3kTTxRl3IhaJG3LbPmwRUJynMxQKdMi4Uf0qy0U7+i8hIJ9m50QXc+3tw2bwDSbx22XYJ9Wf14gxx5G5SPTb1JVCbhe4fxNt91xIxCow2zk62tzbYfRe6dfmDmgYHkv2PIEtMJZK8iKLDjFfu2ZUxsKT2A5g1q17og6o9MeXeuFS3mzJXJYFQZd+3UzlFR9qwkFkby9mg5y4XSeMvRLOHPt/H/r5SpEqBE6a9MadZYt61FBV152CUEzd43ihXtrAa0XH9HdsiySBcWI1SpM3mv9rRP0DiLjMUzHw/K1D8TE2f07zW4t/9kvE11tFj/NpICixQAAAAA="
    sdmf_old_shares[3] = "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"
    sdmf_old_shares[4] = "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"
    sdmf_old_shares[5] = "VGFob2UgbXV0YWJsZSBjb250YWluZXIgdjEKdQlEA47ESLbTdKdpLJXCpBxd5OH239tl5hvAiz1dvGdE5rIOpf8cbfxbPcwNF+Y5dM92uBVbmV6KAAAAAAAAB/wAAAAAAAAJ0AAAAAFOWSw7jSx7WXzaMpdleJYXwYsRCV82jNA5oex9m2YhXSnb2POh+vvC1LE1NAfRc9GOb2zQG84Xdsx1Jub2brEeKkyt0sRIttN0p2kslcKkHF3k4fbf22XmAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABamJprL6ecrsOoFKdrXUmWveLq8nzEGDOjFnyK9detI3noX3uyK2MwSnFdAfyN0tuAwoAAAAAAAAAFQAAAAAAAAAVAAABjwAAAo8AAAMXAAADNwAAAAAAAAM+AAAAAAAAB/wwggEgMA0GCSqGSIb3DQEBAQUAA4IBDQAwggEIAoIBAQC1IkainlJF12IBXBQdpRK1zXB7a26vuEYqRmQM09YjC6sQjCs0F2ICk8n9m/2Kw4l16eIEboB2Au9pODCE+u/dEAakEFh4qidTMn61rbGUbsLK8xzuWNW22ezzz9/nPia0HDrulXt51/FYtfnnAuD1RJGXJv/8tDllE9FL/18TzlH4WuB6Fp8FTgv7QdbZAfWJHDGFIpVCJr1XxOCsSZNFJIqGwZnD2lsChiWw5OJDbKd8otqN1hIbfHyMyfMOJ/BzRzvZXaUt4Dv5nf93EmQDWClxShRwpuX/NkZ5B2K9OFonFTbOCexm/MjMAdCBqebKKaiHFkiknUCn9eJQpZ5bAgERgV50VKj+AVTDfgTpqfO2vfo4wrufi6ZBb8QV7hllhUFBjYogQ9C96dnS7skv0s+cqFuUjwMILr5/rsbEmEMGvl0T0ytyAbtlXuowEFVj/YORNknM4yjY72YUtEPTlMpk0Cis7aIgTvu5qWMPER26PMApZuRqiwRsGIkaJIvOVOTHHjFYe3/YzdMkc7OZtqRMfQLtwVl2/zKQQV8b/a9vaT6q3mRLRd4P3esaAFe/+7sR/t+9tmB+a8kxtKM6kmaVQJMbXJZ4aoHGfeLX0m35Rcvu2Bmph7QfSDjk/eaE3q55zYSoGWShmlhlw4Kwg84sMuhmcVhLvo0LovR8bKmbdgACtTh7+7gs/l5w1lOkgbF6w7rkXLNslK7L2KYF4SPFLUcAA6dlE140Fc7FgB77PeM5Phv+bypQEYtyfLQHxd+OxlG3AAoIM8M4XulprmLd4gGMobS2Bv9CmwB5LpK/ySHE1QWjdwATPCZX1tHUtVypOtrtOfOkMYjd/DU7JblA8fBAD0y+uskwDv9pXPsxvV6bR6Sz5uILxm5VSG4ASRNU4AXhi6UiMUKZHBmcmEgDE0Wua7Lx6Bnad5n91qmHAnwSEJE5YIhQM634omd6cq9Wk4seJCUIn+ucoknrpxp0IR9QMxpKSMRHRUg2K8ZegnY3YqFunRZKCfsq9ufQEKgjZN12AFqi551KPBdn4/3V5HK6xTv0P4robSsE/BvuIfByvRf/W7ZrDx+CFC4EEcsBOACOZCrkhhqd5TkYKbe9RA+vs56+9N5qZGurkxcoKviiyEncxvTuShD65DK/6x6kMDMgQv/EdZDI3x9GtHTnRBYXwDGnPJ19w+q2zC3e2XarbxTGYQIPEC5mYx0gAA0sbjf018NGfwBhl6SB54iGsa8uLvR3jHv6OSRJgwxL6j7P0Ts4Hv2EtO12P0Lv21pwi3JC1O/WviSrKCvrQD5lMHL9Uym3hwFi2zu0mqwZvxOAbGy7kfOPXkLYKOHTZLthzKj3PsdjeceWBfYIvPGKYcd6wDr36d1aXSYS4IWeApTS2AQ2lu0DUcgSefAvsA8NkgOklvJY1cjTMSg6j6cxQo48Bvl8RAWGLbr4h2S/8KwDGxwLsSv0Gop/gnFc3GzCsmL0EkEyHHWkCA8YRXCghfW80KLDV495ff7yF5oiwK56GniqowZ3RG9Jxp5MXoJQgsLV1VMQFMAmsY69yz8eoxRH3wl9L0dMyndLulhWWzNwPMQ2I0yAWdzA/pksVmwTJTFenB3MHCiWc5rEwJ3yofe6NZZnZQrYyL9r1TNnVwfTwRUiykPiLSk4x9Mi6DX7RamDAxc8u3gDVfjPsTOTagBOEGUWlGAL54KE/E6sgCQ5DEAt12chk8AxbjBFLPgV+/idrzS0lZHOL+IVBI9D0i3Bq1yZcSIqcjZB0M3IbxbPm4gLAYOWEiTUN2ecsEHHg9nt6rhgffVoqSbCCFPbpC0xf7WOC3+BQORIZECOCC7cUAciXq3xn+GuxpFE40RWRJeKAK7bBQ21X89ABIXlQFkFddZ9kRvlZ2Pnl0oeF+2pjnZu0Yc2czNfZEQF2P7BKIdLrgMgxG89snxAY8qAYTCKyQw6xTG87wkjDcpy1wzsZLP3WsOuO7cAm7b27xU0jRKq8Cw4d1hDoyRG+RdS53F8RFJzVMaNNYgxU2tfRwUvXpTRXiOheeRVvh25+YGVnjakUXjx/dSDnOw4ETHGHD+7styDkeSfc3BdSZxswzc6OehgMI+xsCxeeRym15QUm9hxvg8X7Bfz/0WulgFwgzrm11TVynZYOmvyHpiZKoqQyQyKahIrfhwuchCr7lMsZ4a+umIkNkKxCLZnI+T7jd+eGFMgKItjz3kTTxRl3IhaJG3LbPmwRUJynMxQKdMi4Uf0qy0U7+i8hIJ9m50QXc+3tw2bwDSbx22XYJ9Wf14gxx5G5SPTb1JVCbhe4fxNt91xIxCow2zk62tzbYfRe6dfmDmgYHkv2PIEtMJZK8iKLDjFfu2ZUxsKT2A5g1q17og6o9MeXeuFS3mzJXJYFQZd+3UzlFR9qwkFkby9mg5y4XSeMvRLOHPt/H/r5SpEqBE6a9MadZYt61FBV152CUEzd43ihXtrAa0XH9HdsiySBcWI1SpM3mv9rRP0DiLjMUzHw/K1D8TE2f07zW4t/9kvE11tFj/NpICixQAAAAA="
    sdmf_old_shares[6] = "VGFob2UgbXV0YWJsZSBjb250YWluZXIgdjEKdQlEA47ESLbTdKdpLJXCpBxd5OH239tl5hvAiz1dvGdE5rIOpf8cbfxbPcwNF+Y5dM92uBVbmV6KAAAAAAAAB/wAAAAAAAAJ0AAAAAFOWSw7jSx7WXzaMpdleJYXwYsRCV82jNA5oex9m2YhXSnb2POh+vvC1LE1NAfRc9GOb2zQG84Xdsx1Jub2brEeKkyt0sRIttN0p2kslcKkHF3k4fbf22XmAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABamJprL6ecrsOoFKdrXUmWveLq8nzEGDOjFnyK9detI3noX3uyK2MwSnFdAfyN0tuAwoAAAAAAAAAFQAAAAAAAAAVAAABjwAAAo8AAAMXAAADNwAAAAAAAAM+AAAAAAAAB/wwggEgMA0GCSqGSIb3DQEBAQUAA4IBDQAwggEIAoIBAQC1IkainlJF12IBXBQdpRK1zXB7a26vuEYqRmQM09YjC6sQjCs0F2ICk8n9m/2Kw4l16eIEboB2Au9pODCE+u/dEAakEFh4qidTMn61rbGUbsLK8xzuWNW22ezzz9/nPia0HDrulXt51/FYtfnnAuD1RJGXJv/8tDllE9FL/18TzlH4WuB6Fp8FTgv7QdbZAfWJHDGFIpVCJr1XxOCsSZNFJIqGwZnD2lsChiWw5OJDbKd8otqN1hIbfHyMyfMOJ/BzRzvZXaUt4Dv5nf93EmQDWClxShRwpuX/NkZ5B2K9OFonFTbOCexm/MjMAdCBqebKKaiHFkiknUCn9eJQpZ5bAgERgV50VKj+AVTDfgTpqfO2vfo4wrufi6ZBb8QV7hllhUFBjYogQ9C96dnS7skv0s+cqFuUjwMILr5/rsbEmEMGvl0T0ytyAbtlXuowEFVj/YORNknM4yjY72YUtEPTlMpk0Cis7aIgTvu5qWMPER26PMApZuRqiwRsGIkaJIvOVOTHHjFYe3/YzdMkc7OZtqRMfQLtwVl2/zKQQV8b/a9vaT6q3mRLRd4P3esaAFe/+7sR/t+9tmB+a8kxtKM6kmaVQJMbXJZ4aoHGfeLX0m35Rcvu2Bmph7QfSDjk/eaE3q55zYSoGWShmlhlw4Kwg84sMuhmcVhLvo0LovR8bKmbdgACtTh7+7gs/l5w1lOkgbF6w7rkXLNslK7L2KYF4SPFLUcAA6dlE140Fc7FgB77PeM5Phv+bypQEYtyfLQHxd+OxlG3AAlyHZU7RfTJjbHu1gjabWZsTu+7nAeRVG6/ZSd4iMQ1ZgAWDSFSPvKzcFzRcuRlVgKUf0HBce1MCF8SwpUbPPEyfVJty4xLZ7DvNU/Eh/R6BarsVAagVXdp+GtEu0+fok7nilT4LchmHo8DE0Wua7Lx6Bnad5n91qmHAnwSEJE5YIhQM634omd6cq9Wk4seJCUIn+ucoknrpxp0IR9QMxpKSMRHRUg2K8ZegnY3YqFunRZKCfsq9ufQEKgjZN12AFqi551KPBdn4/3V5HK6xTv0P4robSsE/BvuIfByvRf/W7ZrDx+CFC4EEcsBOACOZCrkhhqd5TkYKbe9RA+vs56+9N5qZGurkxcoKviiyEncxvTuShD65DK/6x6kMDMgQv/EdZDI3x9GtHTnRBYXwDGnPJ19w+q2zC3e2XarbxTGYQIPEC5mYx0gAA0sbjf018NGfwBhl6SB54iGsa8uLvR3jHv6OSRJgwxL6j7P0Ts4Hv2EtO12P0Lv21pwi3JC1O/WviSrKCvrQD5lMHL9Uym3hwFi2zu0mqwZvxOAbGy7kfOPXkLYKOHTZLthzKj3PsdjeceWBfYIvPGKYcd6wDr36d1aXSYS4IWeApTS2AQ2lu0DUcgSefAvsA8NkgOklvJY1cjTMSg6j6cxQo48Bvl8RAWGLbr4h2S/8KwDGxwLsSv0Gop/gnFc3GzCsmL0EkEyHHWkCA8YRXCghfW80KLDV495ff7yF5oiwK56GniqowZ3RG9Jxp5MXoJQgsLV1VMQFMAmsY69yz8eoxRH3wl9L0dMyndLulhWWzNwPMQ2I0yAWdzA/pksVmwTJTFenB3MHCiWc5rEwJ3yofe6NZZnZQrYyL9r1TNnVwfTwRUiykPiLSk4x9Mi6DX7RamDAxc8u3gDVfjPsTOTagBOEGUWlGAL54KE/E6sgCQ5DEAt12chk8AxbjBFLPgV+/idrzS0lZHOL+IVBI9D0i3Bq1yZcSIqcjZB0M3IbxbPm4gLAYOWEiTUN2ecsEHHg9nt6rhgffVoqSbCCFPbpC0xf7WOC3+BQORIZECOCC7cUAciXq3xn+GuxpFE40RWRJeKAK7bBQ21X89ABIXlQFkFddZ9kRvlZ2Pnl0oeF+2pjnZu0Yc2czNfZEQF2P7BKIdLrgMgxG89snxAY8qAYTCKyQw6xTG87wkjDcpy1wzsZLP3WsOuO7cAm7b27xU0jRKq8Cw4d1hDoyRG+RdS53F8RFJzVMaNNYgxU2tfRwUvXpTRXiOheeRVvh25+YGVnjakUXjx/dSDnOw4ETHGHD+7styDkeSfc3BdSZxswzc6OehgMI+xsCxeeRym15QUm9hxvg8X7Bfz/0WulgFwgzrm11TVynZYOmvyHpiZKoqQyQyKahIrfhwuchCr7lMsZ4a+umIkNkKxCLZnI+T7jd+eGFMgKItjz3kTTxRl3IhaJG3LbPmwRUJynMxQKdMi4Uf0qy0U7+i8hIJ9m50QXc+3tw2bwDSbx22XYJ9Wf14gxx5G5SPTb1JVCbhe4fxNt91xIxCow2zk62tzbYfRe6dfmDmgYHkv2PIEtMJZK8iKLDjFfu2ZUxsKT2A5g1q17og6o9MeXeuFS3mzJXJYFQZd+3UzlFR9qwkFkby9mg5y4XSeMvRLOHPt/H/r5SpEqBE6a9MadZYt61FBV152CUEzd43ihXtrAa0XH9HdsiySBcWI1SpM3mv9rRP0DiLjMUzHw/K1D8TE2f07zW4t/9kvE11tFj/NpICixQAAAAA="
    sdmf_old_shares[7] = "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"
    sdmf_old_shares[8] = "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"
    sdmf_old_shares[9] = "VGFob2UgbXV0YWJsZSBjb250YWluZXIgdjEKdQlEA47ESLbTdKdpLJXCpBxd5OH239tl5hvAiz1dvGdE5rIOpf8cbfxbPcwNF+Y5dM92uBVbmV6KAAAAAAAAB/wAAAAAAAAJ0AAAAAFOWSw7jSx7WXzaMpdleJYXwYsRCV82jNA5oex9m2YhXSnb2POh+vvC1LE1NAfRc9GOb2zQG84Xdsx1Jub2brEeKkyt0sRIttN0p2kslcKkHF3k4fbf22XmAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABamJprL6ecrsOoFKdrXUmWveLq8nzEGDOjFnyK9detI3noX3uyK2MwSnFdAfyN0tuAwoAAAAAAAAAFQAAAAAAAAAVAAABjwAAAo8AAAMXAAADNwAAAAAAAAM+AAAAAAAAB/wwggEgMA0GCSqGSIb3DQEBAQUAA4IBDQAwggEIAoIBAQC1IkainlJF12IBXBQdpRK1zXB7a26vuEYqRmQM09YjC6sQjCs0F2ICk8n9m/2Kw4l16eIEboB2Au9pODCE+u/dEAakEFh4qidTMn61rbGUbsLK8xzuWNW22ezzz9/nPia0HDrulXt51/FYtfnnAuD1RJGXJv/8tDllE9FL/18TzlH4WuB6Fp8FTgv7QdbZAfWJHDGFIpVCJr1XxOCsSZNFJIqGwZnD2lsChiWw5OJDbKd8otqN1hIbfHyMyfMOJ/BzRzvZXaUt4Dv5nf93EmQDWClxShRwpuX/NkZ5B2K9OFonFTbOCexm/MjMAdCBqebKKaiHFkiknUCn9eJQpZ5bAgERgV50VKj+AVTDfgTpqfO2vfo4wrufi6ZBb8QV7hllhUFBjYogQ9C96dnS7skv0s+cqFuUjwMILr5/rsbEmEMGvl0T0ytyAbtlXuowEFVj/YORNknM4yjY72YUtEPTlMpk0Cis7aIgTvu5qWMPER26PMApZuRqiwRsGIkaJIvOVOTHHjFYe3/YzdMkc7OZtqRMfQLtwVl2/zKQQV8b/a9vaT6q3mRLRd4P3esaAFe/+7sR/t+9tmB+a8kxtKM6kmaVQJMbXJZ4aoHGfeLX0m35Rcvu2Bmph7QfSDjk/eaE3q55zYSoGWShmlhlw4Kwg84sMuhmcVhLvo0LovR8bKmbdgABUSzNKiMx0E91q51/WH6ASL0fDEOLef9oxuyBX5F5cpoABojmWkDX3k3FKfgNHIeptE3lxB8HHzxDfSD250psyfNCAAwGsKbMxbmI2NpdTozZ3SICrySwgGkatA1gsDOJmOnTzgAXVnLiODzHiLFAI/MsXcR71fmvb7UghLA1b8pq66KAyl+aopjsD29AKG5hrXt9hLIp6shvfrzaPGIid5C8IxYIrjgBj1YohGgDE0Wua7Lx6Bnad5n91qmHAnwSEJE5YIhQM634omd6cq9Wk4seJCUIn+ucoknrpxp0IR9QMxpKSMRHRUg2K8ZegnY3YqFunRZKCfsq9ufQEKgjZN12AFqi551KPBdn4/3V5HK6xTv0P4robSsE/BvuIfByvRf/W7ZrDx+CFC4EEcsBOACOZCrkhhqd5TkYKbe9RA+vs56+9N5qZGurkxcoKviiyEncxvTuShD65DK/6x6kMDMgQv/EdZDI3x9GtHTnRBYXwDGnPJ19w+q2zC3e2XarbxTGYQIPEC5mYx0gAA0sbjf018NGfwBhl6SB54iGsa8uLvR3jHv6OSRJgwxL6j7P0Ts4Hv2EtO12P0Lv21pwi3JC1O/WviSrKCvrQD5lMHL9Uym3hwFi2zu0mqwZvxOAbGy7kfOPXkLYKOHTZLthzKj3PsdjeceWBfYIvPGKYcd6wDr36d1aXSYS4IWeApTS2AQ2lu0DUcgSefAvsA8NkgOklvJY1cjTMSg6j6cxQo48Bvl8RAWGLbr4h2S/8KwDGxwLsSv0Gop/gnFc3GzCsmL0EkEyHHWkCA8YRXCghfW80KLDV495ff7yF5oiwK56GniqowZ3RG9Jxp5MXoJQgsLV1VMQFMAmsY69yz8eoxRH3wl9L0dMyndLulhWWzNwPMQ2I0yAWdzA/pksVmwTJTFenB3MHCiWc5rEwJ3yofe6NZZnZQrYyL9r1TNnVwfTwRUiykPiLSk4x9Mi6DX7RamDAxc8u3gDVfjPsTOTagBOEGUWlGAL54KE/E6sgCQ5DEAt12chk8AxbjBFLPgV+/idrzS0lZHOL+IVBI9D0i3Bq1yZcSIqcjZB0M3IbxbPm4gLAYOWEiTUN2ecsEHHg9nt6rhgffVoqSbCCFPbpC0xf7WOC3+BQORIZECOCC7cUAciXq3xn+GuxpFE40RWRJeKAK7bBQ21X89ABIXlQFkFddZ9kRvlZ2Pnl0oeF+2pjnZu0Yc2czNfZEQF2P7BKIdLrgMgxG89snxAY8qAYTCKyQw6xTG87wkjDcpy1wzsZLP3WsOuO7cAm7b27xU0jRKq8Cw4d1hDoyRG+RdS53F8RFJzVMaNNYgxU2tfRwUvXpTRXiOheeRVvh25+YGVnjakUXjx/dSDnOw4ETHGHD+7styDkeSfc3BdSZxswzc6OehgMI+xsCxeeRym15QUm9hxvg8X7Bfz/0WulgFwgzrm11TVynZYOmvyHpiZKoqQyQyKahIrfhwuchCr7lMsZ4a+umIkNkKxCLZnI+T7jd+eGFMgKItjz3kTTxRl3IhaJG3LbPmwRUJynMxQKdMi4Uf0qy0U7+i8hIJ9m50QXc+3tw2bwDSbx22XYJ9Wf14gxx5G5SPTb1JVCbhe4fxNt91xIxCow2zk62tzbYfRe6dfmDmgYHkv2PIEtMJZK8iKLDjFfu2ZUxsKT2A5g1q17og6o9MeXeuFS3mzJXJYFQZd+3UzlFR9qwkFkby9mg5y4XSeMvRLOHPt/H/r5SpEqBE6a9MadZYt61FBV152CUEzd43ihXtrAa0XH9HdsiySBcWI1SpM3mv9rRP0DiLjMUzHw/K1D8TE2f07zW4t/9kvE11tFj/NpICixQAAAAA="
    sdmf_old_cap = "URI:SSK:gmjgofw6gan57gwpsow6gtrz3e:5adm6fayxmu3e4lkmfvt6lkkfix34ai2wop2ioqr4bgvvhiol3kq"
    sdmf_old_contents = "This is a test file.\n"
    def copy_sdmf_shares(self):
        # We'll basically be short-circuiting the upload process.
        servernums = self.g.servers_by_number.keys()
        assert len(servernums) == 10

        assignments = zip(self.sdmf_old_shares.keys(), servernums)
        # Get the storage index.
        cap = uri.from_string(self.sdmf_old_cap)
        si = cap.get_storage_index()

        # Now execute each assignment by writing the storage.
        for (share, servernum) in assignments:
            sharedata = base64.b64decode(self.sdmf_old_shares[share])
            storedir = self.get_serverdir(servernum)
            storage_path = os.path.join(storedir, "shares",
                                        storage_index_to_dir(si))
            fileutil.make_dirs(storage_path)
            fileutil.write(os.path.join(storage_path, "%d" % share),
                           sharedata)
        # ...and verify that the shares are there.
        shares = self.find_uri_shares(self.sdmf_old_cap)
        assert len(shares) == 10

    def test_new_downloader_can_read_old_shares(self):
        self.basedir = "mutable/Interoperability/new_downloader_can_read_old_shares"
        self.set_up_grid()
        self.copy_sdmf_shares()
        nm = self.g.clients[0].nodemaker
        n = nm.create_from_cap(self.sdmf_old_cap)
        d = n.download_best_version()
        d.addCallback(self.failUnlessEqual, self.sdmf_old_contents)
        return d

class DifferentEncoding(unittest.TestCase):
    def setUp(self):
        self._storage = s = FakeStorage()
        self.nodemaker = make_nodemaker(s)

    def test_filenode(self):
        # create a file with 3-of-20, then modify it with a client configured
        # to do 3-of-10. #1510 tracks a failure here
        self.nodemaker.default_encoding_parameters["n"] = 20
        d = self.nodemaker.create_mutable_file("old contents")
        def _created(n):
            filecap = n.get_cap().to_string()
            del n # we want a new object, not the cached one
            self.nodemaker.default_encoding_parameters["n"] = 10
            n2 = self.nodemaker.create_from_cap(filecap)
            return n2
        d.addCallback(_created)
        def modifier(old_contents, servermap, first_time):
            return "new contents"
        d.addCallback(lambda n: n.modify(modifier))
        return d
