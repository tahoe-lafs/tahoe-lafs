
import os
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.python.failure import Failure
from twisted.python import log
from twisted.internet import defer
from foolscap.api import fireEventually

import allmydata # for __full_version__
from allmydata import uri, monitor
from allmydata.immutable import upload
from allmydata.interfaces import IFileURI, FileTooLargeError, NotEnoughSharesError
from allmydata.util.assertutil import precondition
from allmydata.util.deferredutil import DeferredListShouldSucceed
from no_network import GridTestMixin
from common_util import ShouldFailMixin

MiB = 1024*1024

def extract_uri(results):
    return results.uri

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
        log.err("uh oh, I was asked to abort")

class FakeClient:
    DEFAULT_ENCODING_PARAMETERS = {"k":25,
                                   "happy": 75,
                                   "n": 100,
                                   "max_segment_size": 1*MiB,
                                   }
    def __init__(self, mode="good", num_servers=50):
        self.mode = mode
        self.num_servers = num_servers
        if mode == "some_big_some_small":
            self.peers = []
            for fakeid in range(num_servers):
                if fakeid % 2:
                    self.peers.append( ("%20d" % fakeid,
                                        FakeStorageServer("good")) )
                else:
                    self.peers.append( ("%20d" % fakeid,
                                        FakeStorageServer("small")) )
        else:
            self.peers = [ ("%20d"%fakeid, FakeStorageServer(self.mode),)
                           for fakeid in range(self.num_servers) ]
    def log(self, *args, **kwargs):
        pass
    def get_permuted_peers(self, storage_index, include_myself):
        self.last_peers = [p[1] for p in self.peers]
        return self.peers
    def get_encoding_parameters(self):
        return self.DEFAULT_ENCODING_PARAMETERS

    def get_renewal_secret(self):
        return ""
    def get_cancel_secret(self):
        return ""

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

class GoodServer(unittest.TestCase, ShouldFailMixin):
    def setUp(self):
        self.node = FakeClient(mode="good")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def set_encoding_parameters(self, k, happy, n, max_segsize=1*MiB):
        p = {"k": k,
             "happy": happy,
             "n": n,
             "max_segment_size": max_segsize,
             }
        self.node.DEFAULT_ENCODING_PARAMETERS = p

    def _check_small(self, newuri, size):
        u = IFileURI(newuri)
        self.failUnless(isinstance(u, uri.LiteralFileURI))
        self.failUnlessEqual(len(u.data), size)

    def _check_large(self, newuri, size):
        u = IFileURI(newuri)
        self.failUnless(isinstance(u, uri.CHKFileURI))
        self.failUnless(isinstance(u.storage_index, str))
        self.failUnlessEqual(len(u.storage_index), 16)
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

class FullServer(unittest.TestCase):
    def setUp(self):
        self.node = FakeClient(mode="full")
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node

    def _should_fail(self, f):
        self.failUnless(isinstance(f, Failure) and f.check(NotEnoughSharesError), f)

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
        u = IFileURI(newuri)
        self.failUnless(isinstance(u, uri.CHKFileURI))
        self.failUnless(isinstance(u.storage_index, str))
        self.failUnlessEqual(len(u.storage_index), 16)
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
        self.node = FakeClient(mode="some_big_some_small", num_servers=20)
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

class EncodingParameters(GridTestMixin, unittest.TestCase):
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
