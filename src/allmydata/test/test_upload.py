
import os, shutil
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.python.failure import Failure
from twisted.python import log
from twisted.internet import defer
from foolscap.api import fireEventually

import allmydata # for __full_version__
from allmydata import uri, monitor, client
from allmydata.immutable import upload, encode
from allmydata.interfaces import FileTooLargeError, NoSharesError, \
     NotEnoughSharesError
from allmydata.util.assertutil import precondition
from allmydata.util.deferredutil import DeferredListShouldSucceed
from no_network import GridTestMixin
from common_util import ShouldFailMixin
from allmydata.storage_client import StorageFarmBroker
from allmydata.storage.server import storage_index_to_dir

MiB = 1024*1024

def extract_uri(results):
    return results.uri

# Some of these took longer than 480 seconds on Zandr's arm box, but this may
# have been due to an earlier test ERROR'ing out due to timeout, which seems
# to screw up subsequent tests.
timeout = 960

class Uploadable(unittest.TestCase):
    def shouldEqual(self, data, expected):
        self.failUnless(isinstance(data, list))
        for e in data:
            self.failUnless(isinstance(e, str))
        s = "".join(data)
        self.failUnlessEqual(s, expected)

    def test_filehandle_random_key(self):
        return self._test_filehandle(convergence=None)

    def test_filehandle_convergent_encryption(self):
        return self._test_filehandle(convergence="some convergence string")

    def _test_filehandle(self, convergence):
        s = StringIO("a"*41)
        u = upload.FileHandle(s, convergence=convergence)
        d = u.get_size()
        d.addCallback(self.failUnlessEqual, 41)
        d.addCallback(lambda res: u.read(1))
        d.addCallback(self.shouldEqual, "a")
        d.addCallback(lambda res: u.read(80))
        d.addCallback(self.shouldEqual, "a"*40)
        d.addCallback(lambda res: u.close()) # this doesn't close the filehandle
        d.addCallback(lambda res: s.close()) # that privilege is reserved for us
        return d

    def test_filename(self):
        basedir = "upload/Uploadable/test_filename"
        os.makedirs(basedir)
        fn = os.path.join(basedir, "file")
        f = open(fn, "w")
        f.write("a"*41)
        f.close()
        u = upload.FileName(fn, convergence=None)
        d = u.get_size()
        d.addCallback(self.failUnlessEqual, 41)
        d.addCallback(lambda res: u.read(1))
        d.addCallback(self.shouldEqual, "a")
        d.addCallback(lambda res: u.read(80))
        d.addCallback(self.shouldEqual, "a"*40)
        d.addCallback(lambda res: u.close())
        return d

    def test_data(self):
        s = "a"*41
        u = upload.Data(s, convergence=None)
        d = u.get_size()
        d.addCallback(self.failUnlessEqual, 41)
        d.addCallback(lambda res: u.read(1))
        d.addCallback(self.shouldEqual, "a")
        d.addCallback(lambda res: u.read(80))
        d.addCallback(self.shouldEqual, "a"*40)
        d.addCallback(lambda res: u.close())
        return d

class ServerError(Exception):
    pass

class SetDEPMixin:
    def set_encoding_parameters(self, k, happy, n, max_segsize=1*MiB):
        p = {"k": k,
             "happy": happy,
             "n": n,
             "max_segment_size": max_segsize,
             }
        self.node.DEFAULT_ENCODING_PARAMETERS = p

class FakeStorageServer:
    def __init__(self, mode):
        self.mode = mode
        self.allocated = []
        self.queries = 0
        self.version = { "http://allmydata.org/tahoe/protocols/storage/v1" :
                         { "maximum-immutable-share-size": 2**32 },
                         "application-version": str(allmydata.__full_version__),
                         }
        if mode == "small":
            self.version = { "http://allmydata.org/tahoe/protocols/storage/v1" :
                             { "maximum-immutable-share-size": 10 },
                             "application-version": str(allmydata.__full_version__),
                             }


    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        d = fireEventually()
        d.addCallback(lambda res: _call())
        return d

    def allocate_buckets(self, storage_index, renew_secret, cancel_secret,
                         sharenums, share_size, canary):
        #print "FakeStorageServer.allocate_buckets(num=%d, size=%d)" % (len(sharenums), share_size)
        if self.mode == "first-fail":
            if self.queries == 0:
                raise ServerError
        if self.mode == "second-fail":
            if self.queries == 1:
                raise ServerError
        self.queries += 1
        if self.mode == "full":
            return (set(), {},)
        elif self.mode == "already got them":
            return (set(sharenums), {},)
        else:
            for shnum in sharenums:
                self.allocated.append( (storage_index, shnum) )
            return (set(),
                    dict([( shnum, FakeBucketWriter(share_size) )
                          for shnum in sharenums]),
                    )

class FakeBucketWriter:
    # a diagnostic version of storageserver.BucketWriter
    def __init__(self, size):
        self.data = StringIO()
        self.closed = False
        self._size = size

    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, "remote_" + methname)
            return meth(*args, **kwargs)
        d = fireEventually()
        d.addCallback(lambda res: _call())
        return d

    def remote_write(self, offset, data):
        precondition(not self.closed)
        precondition(offset >= 0)
        precondition(offset+len(data) <= self._size,
                     "offset=%d + data=%d > size=%d" %
                     (offset, len(data), self._size))
        self.data.seek(offset)
        self.data.write(data)

    def remote_close(self):
        precondition(not self.closed)
        self.closed = True

    def remote_abort(self):
        log.err(RuntimeError("uh oh, I was asked to abort"))

class FakeClient:
    DEFAULT_ENCODING_PARAMETERS = {"k":25,
                                   "happy": 75,
                                   "n": 100,
                                   "max_segment_size": 1*MiB,
                                   }
    def __init__(self, mode="good", num_servers=50):
        self.num_servers = num_servers
        if type(mode) is str:
            mode = dict([i,mode] for i in range(num_servers))
        peers = [ ("%20d"%fakeid, FakeStorageServer(mode[fakeid]))
                  for fakeid in range(self.num_servers) ]
        self.storage_broker = StorageFarmBroker(None, permute_peers=True)
        for (serverid, server) in peers:
            self.storage_broker.test_add_server(serverid, server)
        self.last_peers = [p[1] for p in peers]

    def log(self, *args, **kwargs):
        pass
    def get_encoding_parameters(self):
        return self.DEFAULT_ENCODING_PARAMETERS
    def get_storage_broker(self):
        return self.storage_broker
    _secret_holder = client.SecretHolder("lease secret", "convergence secret")

class GotTooFarError(Exception):
    pass

class GiganticUploadable(upload.FileHandle):
    def __init__(self, size):
        self._size = size
        self._fp = 0

    def get_encryption_key(self):
        return defer.succeed("\x00" * 16)
    def get_size(self):
        return defer.succeed(self._size)
    def read(self, length):
        left = self._size - self._fp
        length = min(left, length)
        self._fp += length
        if self._fp > 1000000:
            # terminate the test early.
            raise GotTooFarError("we shouldn't be allowed to get this far")
        return defer.succeed(["\x00" * length])
    def close(self):
        pass

DATA = """
Once upon a time, there was a beautiful princess named Buttercup. She lived
in a magical land where every file was stored securely among millions of
machines, and nobody ever worried about their data being lost ever again.
The End.
"""
assert len(DATA) > upload.Uploader.URI_LIT_SIZE_THRESHOLD

SIZE_ZERO = 0
SIZE_SMALL = 16
SIZE_LARGE = len(DATA)

def upload_data(uploader, data):
    u = upload.Data(data, convergence=None)
    return uploader.upload(u)
def upload_filename(uploader, filename):
    u = upload.FileName(filename, convergence=None)
    return uploader.upload(u)
def upload_filehandle(uploader, fh):
    u = upload.FileHandle(fh, convergence=None)
    return uploader.upload(u)

class GoodServer(unittest.TestCase, ShouldFailMixin, SetDEPMixin):
    def setUp(self):
        self.node = FakeClient(mode="good")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _check_small(self, newuri, size):
        u = uri.from_string(newuri)
        self.failUnless(isinstance(u, uri.LiteralFileURI))
        self.failUnlessEqual(len(u.data), size)

    def _check_large(self, newuri, size):
        u = uri.from_string(newuri)
        self.failUnless(isinstance(u, uri.CHKFileURI))
        self.failUnless(isinstance(u.get_storage_index(), str))
        self.failUnlessEqual(len(u.get_storage_index()), 16)
        self.failUnless(isinstance(u.key, str))
        self.failUnlessEqual(len(u.key), 16)
        self.failUnlessEqual(u.size, size)

    def get_data(self, size):
        return DATA[:size]

    def test_too_large(self):
        # we've removed the 4GiB share size limit (see ticket #346 for
        # details), but still have an 8-byte field, so the limit is now
        # 2**64, so make sure we reject files larger than that.
        k = 3; happy = 7; n = 10
        self.set_encoding_parameters(k, happy, n)
        big = k*(2**64)
        data1 = GiganticUploadable(big)
        d = self.shouldFail(FileTooLargeError, "test_too_large-data1",
                            "This file is too large to be uploaded (data_size)",
                            self.u.upload, data1)
        data2 = GiganticUploadable(big-3)
        d.addCallback(lambda res:
                      self.shouldFail(FileTooLargeError,
                                      "test_too_large-data2",
                                      "This file is too large to be uploaded (offsets)",
                                      self.u.upload, data2))
        # I don't know where the actual limit is.. it depends upon how large
        # the hash trees wind up. It's somewhere close to k*4GiB-ln2(size).
        return d

    def test_data_zero(self):
        data = self.get_data(SIZE_ZERO)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_small, SIZE_ZERO)
        return d

    def test_data_small(self):
        data = self.get_data(SIZE_SMALL)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_small, SIZE_SMALL)
        return d

    def test_data_large(self):
        data = self.get_data(SIZE_LARGE)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_data_large_odd_segments(self):
        data = self.get_data(SIZE_LARGE)
        segsize = int(SIZE_LARGE / 2.5)
        # we want 3 segments, since that's not a power of two
        self.set_encoding_parameters(25, 75, 100, segsize)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_filehandle_zero(self):
        data = self.get_data(SIZE_ZERO)
        d = upload_filehandle(self.u, StringIO(data))
        d.addCallback(extract_uri)
        d.addCallback(self._check_small, SIZE_ZERO)
        return d

    def test_filehandle_small(self):
        data = self.get_data(SIZE_SMALL)
        d = upload_filehandle(self.u, StringIO(data))
        d.addCallback(extract_uri)
        d.addCallback(self._check_small, SIZE_SMALL)
        return d

    def test_filehandle_large(self):
        data = self.get_data(SIZE_LARGE)
        d = upload_filehandle(self.u, StringIO(data))
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_filename_zero(self):
        fn = "Uploader-test_filename_zero.data"
        f = open(fn, "wb")
        data = self.get_data(SIZE_ZERO)
        f.write(data)
        f.close()
        d = upload_filename(self.u, fn)
        d.addCallback(extract_uri)
        d.addCallback(self._check_small, SIZE_ZERO)
        return d

    def test_filename_small(self):
        fn = "Uploader-test_filename_small.data"
        f = open(fn, "wb")
        data = self.get_data(SIZE_SMALL)
        f.write(data)
        f.close()
        d = upload_filename(self.u, fn)
        d.addCallback(extract_uri)
        d.addCallback(self._check_small, SIZE_SMALL)
        return d

    def test_filename_large(self):
        fn = "Uploader-test_filename_large.data"
        f = open(fn, "wb")
        data = self.get_data(SIZE_LARGE)
        f.write(data)
        f.close()
        d = upload_filename(self.u, fn)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

class ServerErrors(unittest.TestCase, ShouldFailMixin, SetDEPMixin):
    def make_node(self, mode, num_servers=10):
        self.node = FakeClient(mode, num_servers)
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _check_large(self, newuri, size):
        u = uri.from_string(newuri)
        self.failUnless(isinstance(u, uri.CHKFileURI))
        self.failUnless(isinstance(u.get_storage_index(), str))
        self.failUnlessEqual(len(u.get_storage_index()), 16)
        self.failUnless(isinstance(u.key, str))
        self.failUnlessEqual(len(u.key), 16)
        self.failUnlessEqual(u.size, size)

    def test_first_error(self):
        mode = dict([(0,"good")] + [(i,"first-fail") for i in range(1,10)])
        self.make_node(mode)
        d = upload_data(self.u, DATA)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_first_error_all(self):
        self.make_node("first-fail")
        d = self.shouldFail(NoSharesError, "first_error_all",
                            "peer selection failed",
                            upload_data, self.u, DATA)
        def _check((f,)):
            self.failUnlessIn("placed 0 shares out of 100 total", str(f.value))
            # there should also be a 'last failure was' message
            self.failUnlessIn("ServerError", str(f.value))
        d.addCallback(_check)
        return d

    def test_second_error(self):
        # we want to make sure we make it to a third pass. This means that
        # the first pass was insufficient to place all shares, and at least
        # one of second pass servers (other than the last one) accepted a
        # share (so we'll believe that a third pass will be useful). (if
        # everyone but the last server throws an error, then we'll send all
        # the remaining shares to the last server at the end of the second
        # pass, and if that succeeds, we won't make it to a third pass).
        #
        # we can achieve this 97.5% of the time by using 40 servers, having
        # 39 of them fail on the second request, leaving only one to succeed
        # on the second request. (we need to keep the number of servers low
        # enough to ensure a second pass with 100 shares).
        mode = dict([(0,"good")] + [(i,"second-fail") for i in range(1,40)])
        self.make_node(mode, 40)
        d = upload_data(self.u, DATA)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_second_error_all(self):
        self.make_node("second-fail")
        d = self.shouldFail(NotEnoughSharesError, "second_error_all",
                            "peer selection failed",
                            upload_data, self.u, DATA)
        def _check((f,)):
            self.failUnlessIn("placed 10 shares out of 100 total", str(f.value))
            # there should also be a 'last failure was' message
            self.failUnlessIn("ServerError", str(f.value))
        d.addCallback(_check)
        return d

class FullServer(unittest.TestCase):
    def setUp(self):
        self.node = FakeClient(mode="full")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _should_fail(self, f):
        self.failUnless(isinstance(f, Failure) and f.check(NoSharesError), f)

    def test_data_large(self):
        data = DATA
        d = upload_data(self.u, data)
        d.addBoth(self._should_fail)
        return d

class PeerSelection(unittest.TestCase):

    def make_client(self, num_servers=50):
        self.node = FakeClient(mode="good", num_servers=num_servers)
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def get_data(self, size):
        return DATA[:size]

    def _check_large(self, newuri, size):
        u = uri.from_string(newuri)
        self.failUnless(isinstance(u, uri.CHKFileURI))
        self.failUnless(isinstance(u.get_storage_index(), str))
        self.failUnlessEqual(len(u.get_storage_index()), 16)
        self.failUnless(isinstance(u.key, str))
        self.failUnlessEqual(len(u.key), 16)
        self.failUnlessEqual(u.size, size)

    def set_encoding_parameters(self, k, happy, n, max_segsize=1*MiB):
        p = {"k": k,
             "happy": happy,
             "n": n,
             "max_segment_size": max_segsize,
             }
        self.node.DEFAULT_ENCODING_PARAMETERS = p

    def test_one_each(self):
        # if we have 50 shares, and there are 50 peers, and they all accept a
        # share, we should get exactly one share per peer

        self.make_client()
        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(25, 30, 50)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            for p in self.node.last_peers:
                allocated = p.allocated
                self.failUnlessEqual(len(allocated), 1)
                self.failUnlessEqual(p.queries, 1)
        d.addCallback(_check)
        return d

    def test_two_each(self):
        # if we have 100 shares, and there are 50 peers, and they all accept
        # all shares, we should get exactly two shares per peer

        self.make_client()
        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(50, 75, 100)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            for p in self.node.last_peers:
                allocated = p.allocated
                self.failUnlessEqual(len(allocated), 2)
                self.failUnlessEqual(p.queries, 2)
        d.addCallback(_check)
        return d

    def test_one_each_plus_one_extra(self):
        # if we have 51 shares, and there are 50 peers, then one peer gets
        # two shares and the rest get just one

        self.make_client()
        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(24, 41, 51)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            got_one = []
            got_two = []
            for p in self.node.last_peers:
                allocated = p.allocated
                self.failUnless(len(allocated) in (1,2), len(allocated))
                if len(allocated) == 1:
                    self.failUnlessEqual(p.queries, 1)
                    got_one.append(p)
                else:
                    self.failUnlessEqual(p.queries, 2)
                    got_two.append(p)
            self.failUnlessEqual(len(got_one), 49)
            self.failUnlessEqual(len(got_two), 1)
        d.addCallback(_check)
        return d

    def test_four_each(self):
        # if we have 200 shares, and there are 50 peers, then each peer gets
        # 4 shares. The design goal is to accomplish this with only two
        # queries per peer.

        self.make_client()
        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(100, 150, 200)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            for p in self.node.last_peers:
                allocated = p.allocated
                self.failUnlessEqual(len(allocated), 4)
                self.failUnlessEqual(p.queries, 2)
        d.addCallback(_check)
        return d

    def test_three_of_ten(self):
        # if we have 10 shares and 3 servers, I want to see 3+3+4 rather than
        # 4+4+2

        self.make_client(3)
        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(3, 5, 10)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            counts = {}
            for p in self.node.last_peers:
                allocated = p.allocated
                counts[len(allocated)] = counts.get(len(allocated), 0) + 1
            histogram = [counts.get(i, 0) for i in range(5)]
            self.failUnlessEqual(histogram, [0,0,0,2,1])
        d.addCallback(_check)
        return d

    def test_some_big_some_small(self):
        # 10 shares, 20 servers, but half the servers don't support a
        # share-size large enough for our file
        mode = dict([(i,{0:"good",1:"small"}[i%2]) for i in range(20)])
        self.node = FakeClient(mode, num_servers=20)
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(3, 5, 10)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            # we should have put one share each on the big peers, and zero
            # shares on the small peers
            total_allocated = 0
            for p in self.node.last_peers:
                if p.mode == "good":
                    self.failUnlessEqual(len(p.allocated), 1)
                elif p.mode == "small":
                    self.failUnlessEqual(len(p.allocated), 0)
                total_allocated += len(p.allocated)
            self.failUnlessEqual(total_allocated, 10)
        d.addCallback(_check)
        return d


class StorageIndex(unittest.TestCase):
    def test_params_must_matter(self):
        DATA = "I am some data"
        u = upload.Data(DATA, convergence="")
        eu = upload.EncryptAnUploadable(u)
        d1 = eu.get_storage_index()

        # CHK means the same data should encrypt the same way
        u = upload.Data(DATA, convergence="")
        eu = upload.EncryptAnUploadable(u)
        d1a = eu.get_storage_index()

        # but if we use a different convergence string it should be different
        u = upload.Data(DATA, convergence="wheee!")
        eu = upload.EncryptAnUploadable(u)
        d1salt1 = eu.get_storage_index()

        # and if we add yet a different convergence it should be different again
        u = upload.Data(DATA, convergence="NOT wheee!")
        eu = upload.EncryptAnUploadable(u)
        d1salt2 = eu.get_storage_index()

        # and if we use the first string again it should be the same as last time
        u = upload.Data(DATA, convergence="wheee!")
        eu = upload.EncryptAnUploadable(u)
        d1salt1a = eu.get_storage_index()

        # and if we change the encoding parameters, it should be different (from the same convergence string with different encoding parameters)
        u = upload.Data(DATA, convergence="")
        u.encoding_param_k = u.default_encoding_param_k + 1
        eu = upload.EncryptAnUploadable(u)
        d2 = eu.get_storage_index()

        # and if we use a random key, it should be different than the CHK
        u = upload.Data(DATA, convergence=None)
        eu = upload.EncryptAnUploadable(u)
        d3 = eu.get_storage_index()
        # and different from another instance
        u = upload.Data(DATA, convergence=None)
        eu = upload.EncryptAnUploadable(u)
        d4 = eu.get_storage_index()

        d = DeferredListShouldSucceed([d1,d1a,d1salt1,d1salt2,d1salt1a,d2,d3,d4])
        def _done(res):
            si1, si1a, si1salt1, si1salt2, si1salt1a, si2, si3, si4 = res
            self.failUnlessEqual(si1, si1a)
            self.failIfEqual(si1, si2)
            self.failIfEqual(si1, si3)
            self.failIfEqual(si1, si4)
            self.failIfEqual(si3, si4)
            self.failIfEqual(si1salt1, si1)
            self.failIfEqual(si1salt1, si1salt2)
            self.failIfEqual(si1salt2, si1)
            self.failUnlessEqual(si1salt1, si1salt1a)
        d.addCallback(_done)
        return d

class EncodingParameters(GridTestMixin, unittest.TestCase, SetDEPMixin,
    ShouldFailMixin):
    def _do_upload_with_broken_servers(self, servers_to_break):
        """
        I act like a normal upload, but before I send the results of
        Tahoe2PeerSelector to the Encoder, I break the first servers_to_break
        PeerTrackers in the used_peers part of the return result.
        """
        assert self.g, "I tried to find a grid at self.g, but failed"
        broker = self.g.clients[0].storage_broker
        sh     = self.g.clients[0]._secret_holder
        data = upload.Data("data" * 10000, convergence="")
        data.encoding_param_k = 3
        data.encoding_param_happy = 4
        data.encoding_param_n = 10
        uploadable = upload.EncryptAnUploadable(data)
        encoder = encode.Encoder()
        encoder.set_encrypted_uploadable(uploadable)
        status = upload.UploadStatus()
        selector = upload.Tahoe2PeerSelector("dglev", "test", status)
        storage_index = encoder.get_param("storage_index")
        share_size = encoder.get_param("share_size")
        block_size = encoder.get_param("block_size")
        num_segments = encoder.get_param("num_segments")
        d = selector.get_shareholders(broker, sh, storage_index,
                                      share_size, block_size, num_segments,
                                      10, 4)
        def _have_shareholders((used_peers, already_peers)):
            assert servers_to_break <= len(used_peers)
            for index in xrange(servers_to_break):
                server = list(used_peers)[index]
                for share in server.buckets.keys():
                    server.buckets[share].abort()
            buckets = {}
            servermap = already_peers.copy()
            for peer in used_peers:
                buckets.update(peer.buckets)
                for bucket in peer.buckets:
                    servermap[bucket] = peer.peerid
            encoder.set_shareholders(buckets, servermap)
            d = encoder.start()
            return d
        d.addCallback(_have_shareholders)
        return d

    def _add_server_with_share(self, server_number, share_number=None,
                               readonly=False):
        assert self.g, "I tried to find a grid at self.g, but failed"
        assert self.shares, "I tried to find shares at self.shares, but failed"
        ss = self.g.make_server(server_number, readonly)
        self.g.add_server(server_number, ss)
        if share_number:
            # Copy share i from the directory associated with the first 
            # storage server to the directory associated with this one.
            old_share_location = self.shares[share_number][2]
            new_share_location = os.path.join(ss.storedir, "shares")
            si = uri.from_string(self.uri).get_storage_index()
            new_share_location = os.path.join(new_share_location,
                                              storage_index_to_dir(si))
            if not os.path.exists(new_share_location):
                os.makedirs(new_share_location)
            new_share_location = os.path.join(new_share_location,
                                              str(share_number))
            shutil.copy(old_share_location, new_share_location)
            shares = self.find_shares(self.uri)
            # Make sure that the storage server has the share.
            self.failUnless((share_number, ss.my_nodeid, new_share_location)
                            in shares)

    def _setup_and_upload(self):
        """
        I set up a NoNetworkGrid with a single server and client,
        upload a file to it, store its uri in self.uri, and store its
        sharedata in self.shares.
        """
        self.set_up_grid(num_clients=1, num_servers=1)
        client = self.g.clients[0]
        client.DEFAULT_ENCODING_PARAMETERS['happy'] = 1
        data = upload.Data("data" * 10000, convergence="")
        self.data = data
        d = client.upload(data)
        def _store_uri(ur):
            self.uri = ur.uri
        d.addCallback(_store_uri)
        d.addCallback(lambda ign:
            self.find_shares(self.uri))
        def _store_shares(shares):
            self.shares = shares
        d.addCallback(_store_shares)
        return d

    def test_configure_parameters(self):
        self.basedir = self.mktemp()
        hooks = {0: self._set_up_nodes_extra_config}
        self.set_up_grid(client_config_hooks=hooks)
        c0 = self.g.clients[0]

        DATA = "data" * 100
        u = upload.Data(DATA, convergence="")
        d = c0.upload(u)
        d.addCallback(lambda ur: c0.create_node_from_uri(ur.uri))
        m = monitor.Monitor()
        d.addCallback(lambda fn: fn.check(m))
        def _check(cr):
            data = cr.get_data()
            self.failUnlessEqual(data["count-shares-needed"], 7)
            self.failUnlessEqual(data["count-shares-expected"], 12)
        d.addCallback(_check)
        return d

    def _setUp(self, ns):
        # Used by test_happy_semantics and test_prexisting_share_behavior
        # to set up the grid.
        self.node = FakeClient(mode="good", num_servers=ns)
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def test_happy_semantics(self):
        self._setUp(2)
        DATA = upload.Data("kittens" * 10000, convergence="")
        # These parameters are unsatisfiable with the client that we've made
        # -- we'll use them to test that the semnatics work correctly.
        self.set_encoding_parameters(k=3, happy=5, n=10)
        d = self.shouldFail(NotEnoughSharesError, "test_happy_semantics",
                            "shares could only be placed on 2 servers "
                            "(5 were requested)",
                            self.u.upload, DATA)
        # Let's reset the client to have 10 servers
        d.addCallback(lambda ign:
            self._setUp(10))
        # These parameters are satisfiable with the client we've made.
        d.addCallback(lambda ign:
            self.set_encoding_parameters(k=3, happy=5, n=10))
        # this should work
        d.addCallback(lambda ign:
            self.u.upload(DATA))
        # Let's reset the client to have 7 servers
        # (this is less than n, but more than h)
        d.addCallback(lambda ign:
            self._setUp(7))
        # These encoding parameters should still be satisfiable with our 
        # client setup
        d.addCallback(lambda ign:
            self.set_encoding_parameters(k=3, happy=5, n=10))
        # This, then, should work.
        d.addCallback(lambda ign:
            self.u.upload(DATA))
        return d

    def test_problem_layouts(self):
        self.basedir = self.mktemp()
        # This scenario is at 
        # http://allmydata.org/trac/tahoe/ticket/778#comment:52
        #
        # The scenario in comment:52 proposes that we have a layout
        # like:
        # server 1: share 1
        # server 2: share 1
        # server 3: share 1
        # server 4: shares 2 - 10
        # To get access to the shares, we will first upload to one 
        # server, which will then have shares 1 - 10. We'll then 
        # add three new servers, configure them to not accept any new
        # shares, then write share 1 directly into the serverdir of each.
        # Then each of servers 1 - 3 will report that they have share 1, 
        # and will not accept any new share, while server 4 will report that
        # it has shares 2 - 10 and will accept new shares.
        # We'll then set 'happy' = 4, and see that an upload fails
        # (as it should)
        d = self._setup_and_upload()
        d.addCallback(lambda ign:
            self._add_server_with_share(1, 0, True))
        d.addCallback(lambda ign:
            self._add_server_with_share(2, 0, True))
        d.addCallback(lambda ign:
            self._add_server_with_share(3, 0, True))
        # Remove the first share from server 0.
        def _remove_share_0():
            share_location = self.shares[0][2]
            os.remove(share_location)
        d.addCallback(lambda ign:
            _remove_share_0())
        # Set happy = 4 in the client.
        def _prepare():
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return client
        d.addCallback(lambda ign:
            _prepare())
        # Uploading data should fail
        d.addCallback(lambda client:
            self.shouldFail(NotEnoughSharesError, "test_happy_semantics",
                            "shares could only be placed on 1 servers "
                            "(4 were requested)",
                            client.upload, upload.Data("data" * 10000,
                                                       convergence="")))


        # This scenario is at
        # http://allmydata.org/trac/tahoe/ticket/778#comment:53
        #
        # Set up the grid to have one server
        def _change_basedir(ign):
            self.basedir = self.mktemp()
        d.addCallback(_change_basedir)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        # We want to have a layout like this:
        # server 1: share 1
        # server 2: share 2
        # server 3: share 3
        # server 4: shares 1 - 10
        # (this is an expansion of Zooko's example because it is easier
        #  to code, but it will fail in the same way)
        # To start, we'll create a server with shares 1-10 of the data 
        # we're about to upload.
        # Next, we'll add three new servers to our NoNetworkGrid. We'll add
        # one share from our initial upload to each of these.
        # The counterintuitive ordering of the share numbers is to deal with 
        # the permuting of these servers -- distributing the shares this 
        # way ensures that the Tahoe2PeerSelector sees them in the order 
        # described above.
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=1, share_number=2))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=2, share_number=0))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=3, share_number=1))
        # So, we now have the following layout:
        # server 0: shares 1 - 10
        # server 1: share 0
        # server 2: share 1
        # server 3: share 2
        # We want to change the 'happy' parameter in the client to 4. 
        # We then want to feed the upload process a list of peers that
        # server 0 is at the front of, so we trigger Zooko's scenario.
        # Ideally, a reupload of our original data should work.
        def _reset_encoding_parameters(ign):
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return client
        d.addCallback(_reset_encoding_parameters)
        # We need this to get around the fact that the old Data 
        # instance already has a happy parameter set.
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        return d


    def test_dropped_servers_in_encoder(self):
        def _set_basedir(ign=None):
            self.basedir = self.mktemp()
        _set_basedir()
        d = self._setup_and_upload();
        # Add 5 servers, with one share each from the original
        def _do_server_setup(ign):
            self._add_server_with_share(1, 1)
            self._add_server_with_share(2)
            self._add_server_with_share(3)
            self._add_server_with_share(4)
            self._add_server_with_share(5)
        d.addCallback(_do_server_setup)
        # remove the original server
        # (necessary to ensure that the Tahoe2PeerSelector will distribute
        #  all the shares)
        def _remove_server(ign):
            server = self.g.servers_by_number[0]
            self.g.remove_server(server.my_nodeid)
        d.addCallback(_remove_server)
        # This should succeed.
        d.addCallback(lambda ign:
            self._do_upload_with_broken_servers(1))
        # Now, do the same thing over again, but drop 2 servers instead
        # of 1. This should fail.
        d.addCallback(_set_basedir)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        d.addCallback(_do_server_setup)
        d.addCallback(_remove_server)
        d.addCallback(lambda ign:
            self.shouldFail(NotEnoughSharesError,
                            "test_dropped_server_in_encoder", "",
                            self._do_upload_with_broken_servers, 2))
        return d


    def test_servers_with_unique_shares(self):
        # servers_with_unique_shares expects a dict of 
        # shnum => peerid as a preexisting shares argument.
        test1 = {
                 1 : "server1",
                 2 : "server2",
                 3 : "server3",
                 4 : "server4"
                }
        unique_servers = upload.servers_with_unique_shares(test1)
        self.failUnlessEqual(4, len(unique_servers))
        for server in ["server1", "server2", "server3", "server4"]:
            self.failUnlessIn(server, unique_servers)
        test1[4] = "server1"
        # Now there should only be 3 unique servers.
        unique_servers = upload.servers_with_unique_shares(test1)
        self.failUnlessEqual(3, len(unique_servers))
        for server in ["server1", "server2", "server3"]:
            self.failUnlessIn(server, unique_servers)
        # servers_with_unique_shares expects a set of PeerTracker
        # instances as a used_peers argument, but only uses the peerid
        # instance variable to assess uniqueness. So we feed it some fake
        # PeerTrackers whose only important characteristic is that they 
        # have peerid set to something.
        class FakePeerTracker:
            pass
        trackers = []
        for server in ["server5", "server6", "server7", "server8"]:
            t = FakePeerTracker()
            t.peerid = server
            trackers.append(t)
        # Recall that there are 3 unique servers in test1. Since none of
        # those overlap with the ones in trackers, we should get 7 back
        unique_servers = upload.servers_with_unique_shares(test1, set(trackers))
        self.failUnlessEqual(7, len(unique_servers))
        expected_servers = ["server" + str(i) for i in xrange(1, 9)]
        expected_servers.remove("server4")
        for server in expected_servers:
            self.failUnlessIn(server, unique_servers)
        # Now add an overlapping server to trackers.
        t = FakePeerTracker()
        t.peerid = "server1"
        trackers.append(t)
        unique_servers = upload.servers_with_unique_shares(test1, set(trackers))
        self.failUnlessEqual(7, len(unique_servers))
        for server in expected_servers:
            self.failUnlessIn(server, unique_servers)


    def test_shares_by_server(self):
        test = {
                    1 : "server1",
                    2 : "server2",
                    3 : "server3",
                    4 : "server4"
               }
        shares_by_server = upload.shares_by_server(test)
        self.failUnlessEqual(set([1]), shares_by_server["server1"])
        self.failUnlessEqual(set([2]), shares_by_server["server2"])
        self.failUnlessEqual(set([3]), shares_by_server["server3"])
        self.failUnlessEqual(set([4]), shares_by_server["server4"])
        test1 = {
                    1 : "server1",
                    2 : "server1",
                    3 : "server1",
                    4 : "server2",
                    5 : "server2"
                }
        shares_by_server = upload.shares_by_server(test1)
        self.failUnlessEqual(set([1, 2, 3]), shares_by_server["server1"])
        self.failUnlessEqual(set([4, 5]), shares_by_server["server2"])


    def _set_up_nodes_extra_config(self, clientdir):
        cfgfn = os.path.join(clientdir, "tahoe.cfg")
        oldcfg = open(cfgfn, "r").read()
        f = open(cfgfn, "wt")
        f.write(oldcfg)
        f.write("\n")
        f.write("[client]\n")
        f.write("shares.needed = 7\n")
        f.write("shares.total = 12\n")
        f.write("\n")
        f.close()
        return None

# TODO:
#  upload with exactly 75 peers (shares_of_happiness)
#  have a download fail
#  cancel a download (need to implement more cancel stuff)
