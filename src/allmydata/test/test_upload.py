# -*- coding: utf-8 -*-

import os, shutil
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.python.failure import Failure
from twisted.internet import defer
from foolscap.api import fireEventually

import allmydata # for __full_version__
from allmydata import uri, monitor, client
from allmydata.immutable import upload, encode
from allmydata.interfaces import FileTooLargeError, UploadUnhappinessError
from allmydata.util import log
from allmydata.util.assertutil import precondition
from allmydata.util.deferredutil import DeferredListShouldSucceed
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common_util import ShouldFailMixin
from allmydata.util.happinessutil import servers_of_happiness, \
                                         shares_by_server, merge_servers
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


    def callRemoteOnly(self, methname, *args, **kwargs):
        d = self.callRemote(methname, *args, **kwargs)
        del d # callRemoteOnly ignores this
        return None


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
        pass

class FakeClient:
    DEFAULT_ENCODING_PARAMETERS = {"k":25,
                                   "happy": 25,
                                   "n": 100,
                                   "max_segment_size": 1*MiB,
                                   }
    def __init__(self, mode="good", num_servers=50):
        self.num_servers = num_servers
        if type(mode) is str:
            mode = dict([i,mode] for i in range(num_servers))
        servers = [ ("%20d"%fakeid, FakeStorageServer(mode[fakeid]))
                    for fakeid in range(self.num_servers) ]
        self.storage_broker = StorageFarmBroker(None, permute_peers=True)
        for (serverid, rref) in servers:
            self.storage_broker.test_add_rref(serverid, rref)
        self.last_servers = [s[1] for s in servers]

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
        self.set_encoding_parameters(25, 25, 100, segsize)
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
        self.set_encoding_parameters(k=25, happy=1, n=50)
        d = upload_data(self.u, DATA)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        return d

    def test_first_error_all(self):
        self.make_node("first-fail")
        d = self.shouldFail(UploadUnhappinessError, "first_error_all",
                            "server selection failed",
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
        d = self.shouldFail(UploadUnhappinessError, "second_error_all",
                            "server selection failed",
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
        self.failUnless(isinstance(f, Failure) and f.check(UploadUnhappinessError), f)

    def test_data_large(self):
        data = DATA
        d = upload_data(self.u, data)
        d.addBoth(self._should_fail)
        return d

class ServerSelection(unittest.TestCase):

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
        # if we have 50 shares, and there are 50 servers, and they all accept
        # a share, we should get exactly one share per server

        self.make_client()
        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(25, 30, 50)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            for s in self.node.last_servers:
                allocated = s.allocated
                self.failUnlessEqual(len(allocated), 1)
                self.failUnlessEqual(s.queries, 1)
        d.addCallback(_check)
        return d

    def test_two_each(self):
        # if we have 100 shares, and there are 50 servers, and they all
        # accept all shares, we should get exactly two shares per server

        self.make_client()
        data = self.get_data(SIZE_LARGE)
        # if there are 50 servers, then happy needs to be <= 50
        self.set_encoding_parameters(50, 50, 100)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            for s in self.node.last_servers:
                allocated = s.allocated
                self.failUnlessEqual(len(allocated), 2)
                self.failUnlessEqual(s.queries, 2)
        d.addCallback(_check)
        return d

    def test_one_each_plus_one_extra(self):
        # if we have 51 shares, and there are 50 servers, then one server
        # gets two shares and the rest get just one

        self.make_client()
        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(24, 41, 51)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            got_one = []
            got_two = []
            for s in self.node.last_servers:
                allocated = s.allocated
                self.failUnless(len(allocated) in (1,2), len(allocated))
                if len(allocated) == 1:
                    self.failUnlessEqual(s.queries, 1)
                    got_one.append(s)
                else:
                    self.failUnlessEqual(s.queries, 2)
                    got_two.append(s)
            self.failUnlessEqual(len(got_one), 49)
            self.failUnlessEqual(len(got_two), 1)
        d.addCallback(_check)
        return d

    def test_four_each(self):
        # if we have 200 shares, and there are 50 servers, then each server
        # gets 4 shares. The design goal is to accomplish this with only two
        # queries per server.

        self.make_client()
        data = self.get_data(SIZE_LARGE)
        # if there are 50 servers, then happy should be no more than 50 if we
        # want this to work.
        self.set_encoding_parameters(100, 50, 200)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            for s in self.node.last_servers:
                allocated = s.allocated
                self.failUnlessEqual(len(allocated), 4)
                self.failUnlessEqual(s.queries, 2)
        d.addCallback(_check)
        return d

    def test_three_of_ten(self):
        # if we have 10 shares and 3 servers, I want to see 3+3+4 rather than
        # 4+4+2

        self.make_client(3)
        data = self.get_data(SIZE_LARGE)
        self.set_encoding_parameters(3, 3, 10)
        d = upload_data(self.u, data)
        d.addCallback(extract_uri)
        d.addCallback(self._check_large, SIZE_LARGE)
        def _check(res):
            counts = {}
            for s in self.node.last_servers:
                allocated = s.allocated
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
            # we should have put one share each on the big servers, and zero
            # shares on the small servers
            total_allocated = 0
            for p in self.node.last_servers:
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

# copied from python docs because itertools.combinations was added in
# python 2.6 and we support >= 2.4.
def combinations(iterable, r):
    # combinations('ABCD', 2) --> AB AC AD BC BD CD
    # combinations(range(4), 3) --> 012 013 023 123
    pool = tuple(iterable)
    n = len(pool)
    if r > n:
        return
    indices = range(r)
    yield tuple(pool[i] for i in indices)
    while True:
        for i in reversed(range(r)):
            if indices[i] != i + n - r:
                break
        else:
            return
        indices[i] += 1
        for j in range(i+1, r):
            indices[j] = indices[j-1] + 1
        yield tuple(pool[i] for i in indices)

def is_happy_enough(servertoshnums, h, k):
    """ I calculate whether servertoshnums achieves happiness level h. I do this with a naïve "brute force search" approach. (See src/allmydata/util/happinessutil.py for a better algorithm.) """
    if len(servertoshnums) < h:
        return False
    # print "servertoshnums: ", servertoshnums, h, k
    for happysetcombo in combinations(servertoshnums.iterkeys(), h):
        # print "happysetcombo: ", happysetcombo
        for subsetcombo in combinations(happysetcombo, k):
            shnums = reduce(set.union, [ servertoshnums[s] for s in subsetcombo ])
            # print "subsetcombo: ", subsetcombo, ", shnums: ", shnums
            if len(shnums) < k:
                # print "NOT HAAPP{Y", shnums, k
                return False
    # print "HAAPP{Y"
    return True

class FakeServerTracker:
    def __init__(self, serverid, buckets):
        self.serverid = serverid
        self.buckets = buckets

class EncodingParameters(GridTestMixin, unittest.TestCase, SetDEPMixin,
    ShouldFailMixin):
    def find_all_shares(self, unused=None):
        """Locate shares on disk. Returns a dict that maps
        server to set of sharenums.
        """
        assert self.g, "I tried to find a grid at self.g, but failed"
        servertoshnums = {} # k: server, v: set(shnum)

        for i, c in self.g.servers_by_number.iteritems():
            for (dirp, dirns, fns) in os.walk(c.sharedir):
                for fn in fns:
                    try:
                        sharenum = int(fn)
                    except TypeError:
                        # Whoops, I guess that's not a share file then.
                        pass
                    else:
                        servertoshnums.setdefault(i, set()).add(sharenum)

        return servertoshnums

    def _do_upload_with_broken_servers(self, servers_to_break):
        """
        I act like a normal upload, but before I send the results of
        Tahoe2ServerSelector to the Encoder, I break the first
        servers_to_break ServerTrackers in the upload_servers part of the
        return result.
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
        selector = upload.Tahoe2ServerSelector("dglev", "test", status)
        storage_index = encoder.get_param("storage_index")
        share_size = encoder.get_param("share_size")
        block_size = encoder.get_param("block_size")
        num_segments = encoder.get_param("num_segments")
        d = selector.get_shareholders(broker, sh, storage_index,
                                      share_size, block_size, num_segments,
                                      10, 3, 4)
        def _have_shareholders((upload_trackers, already_servers)):
            assert servers_to_break <= len(upload_trackers)
            for index in xrange(servers_to_break):
                tracker = list(upload_trackers)[index]
                for share in tracker.buckets.keys():
                    tracker.buckets[share].abort()
            buckets = {}
            servermap = already_servers.copy()
            for tracker in upload_trackers:
                buckets.update(tracker.buckets)
                for bucket in tracker.buckets:
                    servermap.setdefault(bucket, set()).add(tracker.serverid)
            encoder.set_shareholders(buckets, servermap)
            d = encoder.start()
            return d
        d.addCallback(_have_shareholders)
        return d

    def _has_happy_share_distribution(self):
        servertoshnums = self.find_all_shares()
        k = self.g.clients[0].DEFAULT_ENCODING_PARAMETERS['k']
        h = self.g.clients[0].DEFAULT_ENCODING_PARAMETERS['happy']
        return is_happy_enough(servertoshnums, h, k)

    def _add_server(self, server_number, readonly=False):
        assert self.g, "I tried to find a grid at self.g, but failed"
        ss = self.g.make_server(server_number, readonly)
        log.msg("just created a server, number: %s => %s" % (server_number, ss,))
        self.g.add_server(server_number, ss)

    def _add_server_with_share(self, server_number, share_number=None,
                               readonly=False):
        self._add_server(server_number, readonly)
        if share_number is not None:
            self._copy_share_to_server(share_number, server_number)


    def _copy_share_to_server(self, share_number, server_number):
        ss = self.g.servers_by_number[server_number]
        # Copy share i from the directory associated with the first
        # storage server to the directory associated with this one.
        assert self.g, "I tried to find a grid at self.g, but failed"
        assert self.shares, "I tried to find shares at self.shares, but failed"
        old_share_location = self.shares[share_number][2]
        new_share_location = os.path.join(ss.storedir, "shares")
        si = uri.from_string(self.uri).get_storage_index()
        new_share_location = os.path.join(new_share_location,
                                          storage_index_to_dir(si))
        if not os.path.exists(new_share_location):
            os.makedirs(new_share_location)
        new_share_location = os.path.join(new_share_location,
                                          str(share_number))
        if old_share_location != new_share_location:
            shutil.copy(old_share_location, new_share_location)
        shares = self.find_uri_shares(self.uri)
        # Make sure that the storage server has the share.
        self.failUnless((share_number, ss.my_nodeid, new_share_location)
                        in shares)

    def _setup_grid(self):
        """
        I set up a NoNetworkGrid with a single server and client.
        """
        self.set_up_grid(num_clients=1, num_servers=1)

    def _setup_and_upload(self, **kwargs):
        """
        I set up a NoNetworkGrid with a single server and client,
        upload a file to it, store its uri in self.uri, and store its
        sharedata in self.shares.
        """
        self._setup_grid()
        client = self.g.clients[0]
        client.DEFAULT_ENCODING_PARAMETERS['happy'] = 1
        if "n" in kwargs and "k" in kwargs:
            client.DEFAULT_ENCODING_PARAMETERS['k'] = kwargs['k']
            client.DEFAULT_ENCODING_PARAMETERS['n'] = kwargs['n']
        data = upload.Data("data" * 10000, convergence="")
        self.data = data
        d = client.upload(data)
        def _store_uri(ur):
            self.uri = ur.uri
        d.addCallback(_store_uri)
        d.addCallback(lambda ign:
            self.find_uri_shares(self.uri))
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
        # Used by test_happy_semantics and test_preexisting_share_behavior
        # to set up the grid.
        self.node = FakeClient(mode="good", num_servers=ns)
        self.u = upload.Uploader()
        self.u.running = True
        self.u.parent = self.node


    def test_happy_semantics(self):
        self._setUp(2)
        DATA = upload.Data("kittens" * 10000, convergence="")
        # These parameters are unsatisfiable with only 2 servers.
        self.set_encoding_parameters(k=3, happy=5, n=10)
        d = self.shouldFail(UploadUnhappinessError, "test_happy_semantics",
                            "shares could be placed or found on only 2 "
                            "server(s). We were asked to place shares on "
                            "at least 5 server(s) such that any 3 of them "
                            "have enough shares to recover the file",
                            self.u.upload, DATA)
        # Let's reset the client to have 10 servers
        d.addCallback(lambda ign:
            self._setUp(10))
        # These parameters are satisfiable with 10 servers.
        d.addCallback(lambda ign:
            self.set_encoding_parameters(k=3, happy=5, n=10))
        d.addCallback(lambda ign:
            self.u.upload(DATA))
        # Let's reset the client to have 7 servers
        # (this is less than n, but more than h)
        d.addCallback(lambda ign:
            self._setUp(7))
        # These parameters are satisfiable with 7 servers.
        d.addCallback(lambda ign:
            self.set_encoding_parameters(k=3, happy=5, n=10))
        d.addCallback(lambda ign:
            self.u.upload(DATA))
        return d

    def test_aborted_shares(self):
        self.basedir = "upload/EncodingParameters/aborted_shares"
        self.set_up_grid(num_servers=4)
        c = self.g.clients[0]
        DATA = upload.Data(100* "kittens", convergence="")
        # These parameters are unsatisfiable with only 4 servers, but should
        # work with 5, as long as the original 4 are not stuck in the open
        # BucketWriter state (open() but not
        parms = {"k":2, "happy":5, "n":5, "max_segment_size": 1*MiB}
        c.DEFAULT_ENCODING_PARAMETERS = parms
        d = self.shouldFail(UploadUnhappinessError, "test_aborted_shares",
                            "shares could be placed on only 4 "
                            "server(s) such that any 2 of them have enough "
                            "shares to recover the file, but we were asked "
                            "to place shares on at least 5 such servers",
                            c.upload, DATA)
        # now add the 5th server
        d.addCallback(lambda ign: self._add_server(4, False))
        # and this time the upload ought to succeed
        d.addCallback(lambda ign: c.upload(DATA))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d


    def test_problem_layout_comment_52(self):
        def _basedir():
            self.basedir = self.mktemp()
        _basedir()
        # This scenario is at
        # http://allmydata.org/trac/tahoe/ticket/778#comment:52
        #
        # The scenario in comment:52 proposes that we have a layout
        # like:
        # server 0: shares 1 - 9
        # server 1: share 0, read-only
        # server 2: share 0, read-only
        # server 3: share 0, read-only
        # To get access to the shares, we will first upload to one
        # server, which will then have shares 0 - 9. We'll then
        # add three new servers, configure them to not accept any new
        # shares, then write share 0 directly into the serverdir of each,
        # and then remove share 0 from server 0 in the same way.
        # Then each of servers 1 - 3 will report that they have share 0,
        # and will not accept any new share, while server 0 will report that
        # it has shares 1 - 9 and will accept new shares.
        # We'll then set 'happy' = 4, and see that an upload fails
        # (as it should)
        d = self._setup_and_upload()
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=1, share_number=0,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=2, share_number=0,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=3, share_number=0,
                                        readonly=True))
        # Remove the first share from server 0.
        def _remove_share_0_from_server_0():
            share_location = self.shares[0][2]
            os.remove(share_location)
        d.addCallback(lambda ign:
            _remove_share_0_from_server_0())
        # Set happy = 4 in the client.
        def _prepare():
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return client
        d.addCallback(lambda ign:
            _prepare())
        # Uploading data should fail
        d.addCallback(lambda client:
            self.shouldFail(UploadUnhappinessError,
                            "test_problem_layout_comment_52_test_1",
                            "shares could be placed or found on 4 server(s), "
                            "but they are not spread out evenly enough to "
                            "ensure that any 3 of these servers would have "
                            "enough shares to recover the file. "
                            "We were asked to place shares on at "
                            "least 4 servers such that any 3 of them have "
                            "enough shares to recover the file",
                            client.upload, upload.Data("data" * 10000,
                                                       convergence="")))

        # Do comment:52, but like this:
        # server 2: empty
        # server 3: share 0, read-only
        # server 1: share 0, read-only
        # server 0: shares 0-9
        d.addCallback(lambda ign:
            _basedir())
        d.addCallback(lambda ign:
            self._setup_and_upload())
        d.addCallback(lambda ign:
            self._add_server(server_number=2))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=3, share_number=0,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=1, share_number=0,
                                        readonly=True))
        def _prepare2():
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return client
        d.addCallback(lambda ign:
            _prepare2())
        d.addCallback(lambda client:
            self.shouldFail(UploadUnhappinessError,
                            "test_problem_layout_comment_52_test_2",
                            "shares could be placed on only 3 server(s) such "
                            "that any 3 of them have enough shares to recover "
                            "the file, but we were asked to place shares on "
                            "at least 4 such servers.",
                            client.upload, upload.Data("data" * 10000,
                                                       convergence="")))
        return d


    def test_problem_layout_comment_53(self):
        # This scenario is at
        # http://allmydata.org/trac/tahoe/ticket/778#comment:53
        #
        # Set up the grid to have one server
        def _change_basedir(ign):
            self.basedir = self.mktemp()
        _change_basedir(None)
        # We start by uploading all of the shares to one server.
        # Next, we'll add three new servers to our NoNetworkGrid. We'll add
        # one share from our initial upload to each of these.
        # The counterintuitive ordering of the share numbers is to deal with
        # the permuting of these servers -- distributing the shares this
        # way ensures that the Tahoe2ServerSelector sees them in the order
        # described below.
        d = self._setup_and_upload()
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=1, share_number=2))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=2, share_number=0))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=3, share_number=1))
        # So, we now have the following layout:
        # server 0: shares 0 - 9
        # server 1: share 2
        # server 2: share 0
        # server 3: share 1
        # We change the 'happy' parameter in the client to 4.
        # The Tahoe2ServerSelector will see the servers permuted as:
        # 2, 3, 1, 0
        # Ideally, a reupload of our original data should work.
        def _reset_encoding_parameters(ign, happy=4):
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = happy
            return client
        d.addCallback(_reset_encoding_parameters)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))


        # This scenario is basically comment:53, but changed so that the
        # Tahoe2ServerSelector sees the server with all of the shares before
        # any of the other servers.
        # The layout is:
        # server 2: shares 0 - 9
        # server 3: share 0
        # server 1: share 1
        # server 4: share 2
        # The Tahoe2ServerSelector sees the servers permuted as:
        # 2, 3, 1, 4
        # Note that server 0 has been replaced by server 4; this makes it
        # easier to ensure that the last server seen by Tahoe2ServerSelector
        # has only one share.
        d.addCallback(_change_basedir)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=2, share_number=0))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=3, share_number=1))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=1, share_number=2))
        # Copy all of the other shares to server number 2
        def _copy_shares(ign):
            for i in xrange(0, 10):
                self._copy_share_to_server(i, 2)
        d.addCallback(_copy_shares)
        # Remove the first server, and add a placeholder with share 0
        d.addCallback(lambda ign:
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=4, share_number=0))
        # Now try uploading.
        d.addCallback(_reset_encoding_parameters)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))


        # Try the same thing, but with empty servers after the first one
        # We want to make sure that Tahoe2ServerSelector will redistribute
        # shares as necessary, not simply discover an existing layout.
        # The layout is:
        # server 2: shares 0 - 9
        # server 3: empty
        # server 1: empty
        # server 4: empty
        d.addCallback(_change_basedir)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        d.addCallback(lambda ign:
            self._add_server(server_number=2))
        d.addCallback(lambda ign:
            self._add_server(server_number=3))
        d.addCallback(lambda ign:
            self._add_server(server_number=1))
        d.addCallback(lambda ign:
            self._add_server(server_number=4))
        d.addCallback(_copy_shares)
        d.addCallback(lambda ign:
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
        d.addCallback(_reset_encoding_parameters)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        # Make sure that only as many shares as necessary to satisfy
        # servers of happiness were pushed.
        d.addCallback(lambda results:
            self.failUnlessEqual(results.pushed_shares, 3))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d

    def test_problem_layout_ticket_1124(self):
        self.basedir = self.mktemp()
        d = self._setup_and_upload(k=2, n=4)

        # server 0: shares 0, 1, 2, 3
        # server 1: shares 0, 3
        # server 2: share 1
        # server 3: share 2
        # With this layout, an upload should just be satisfied that the current distribution is good enough, right?
        def _setup(ign):
            self._add_server_with_share(server_number=0, share_number=None)
            self._add_server_with_share(server_number=1, share_number=0)
            self._add_server_with_share(server_number=2, share_number=1)
            self._add_server_with_share(server_number=3, share_number=2)
            # Copy shares
            self._copy_share_to_server(3, 1)
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return client

        d.addCallback(_setup)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d
    test_problem_layout_ticket_1124.todo = "Fix this after 1.7.1 release."

    def test_happiness_with_some_readonly_servers(self):
        # Try the following layout
        # server 2: shares 0-9
        # server 4: share 0, read-only
        # server 3: share 1, read-only
        # server 1: share 2, read-only
        self.basedir = self.mktemp()
        d = self._setup_and_upload()
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=2, share_number=0))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=3, share_number=1,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=1, share_number=2,
                                        readonly=True))
        # Copy all of the other shares to server number 2
        def _copy_shares(ign):
            for i in xrange(1, 10):
                self._copy_share_to_server(i, 2)
        d.addCallback(_copy_shares)
        # Remove server 0, and add another in its place
        d.addCallback(lambda ign:
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=4, share_number=0,
                                        readonly=True))
        def _reset_encoding_parameters(ign, happy=4):
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = happy
            return client
        d.addCallback(_reset_encoding_parameters)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d


    def test_happiness_with_all_readonly_servers(self):
        # server 3: share 1, read-only
        # server 1: share 2, read-only
        # server 2: shares 0-9, read-only
        # server 4: share 0, read-only
        # The idea with this test is to make sure that the survey of
        # read-only servers doesn't undercount servers of happiness
        self.basedir = self.mktemp()
        d = self._setup_and_upload()
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=4, share_number=0,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=3, share_number=1,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=1, share_number=2,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=2, share_number=0,
                                        readonly=True))
        def _copy_shares(ign):
            for i in xrange(1, 10):
                self._copy_share_to_server(i, 2)
        d.addCallback(_copy_shares)
        d.addCallback(lambda ign:
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
        def _reset_encoding_parameters(ign, happy=4):
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = happy
            return client
        d.addCallback(_reset_encoding_parameters)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d


    def test_dropped_servers_in_encoder(self):
        # The Encoder does its own "servers_of_happiness" check if it
        # happens to lose a bucket during an upload (it assumes that
        # the layout presented to it satisfies "servers_of_happiness"
        # until a failure occurs)
        #
        # This test simulates an upload where servers break after server
        # selection, but before they are written to.
        def _set_basedir(ign=None):
            self.basedir = self.mktemp()
        _set_basedir()
        d = self._setup_and_upload();
        # Add 5 servers
        def _do_server_setup(ign):
            self._add_server(server_number=1)
            self._add_server(server_number=2)
            self._add_server(server_number=3)
            self._add_server(server_number=4)
            self._add_server(server_number=5)
        d.addCallback(_do_server_setup)
        # remove the original server
        # (necessary to ensure that the Tahoe2ServerSelector will distribute
        #  all the shares)
        def _remove_server(ign):
            server = self.g.servers_by_number[0]
            self.g.remove_server(server.my_nodeid)
        d.addCallback(_remove_server)
        # This should succeed; we still have 4 servers, and the
        # happiness of the upload is 4.
        d.addCallback(lambda ign:
            self._do_upload_with_broken_servers(1))
        # Now, do the same thing over again, but drop 2 servers instead
        # of 1. This should fail, because servers_of_happiness is 4 and
        # we can't satisfy that.
        d.addCallback(_set_basedir)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        d.addCallback(_do_server_setup)
        d.addCallback(_remove_server)
        d.addCallback(lambda ign:
            self.shouldFail(UploadUnhappinessError,
                            "test_dropped_servers_in_encoder",
                            "shares could be placed on only 3 server(s) "
                            "such that any 3 of them have enough shares to "
                            "recover the file, but we were asked to place "
                            "shares on at least 4",
                            self._do_upload_with_broken_servers, 2))
        # Now do the same thing over again, but make some of the servers
        # readonly, break some of the ones that aren't, and make sure that
        # happiness accounting is preserved.
        d.addCallback(_set_basedir)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        def _do_server_setup_2(ign):
            self._add_server(1)
            self._add_server(2)
            self._add_server(3)
            self._add_server_with_share(4, 7, readonly=True)
            self._add_server_with_share(5, 8, readonly=True)
        d.addCallback(_do_server_setup_2)
        d.addCallback(_remove_server)
        d.addCallback(lambda ign:
            self._do_upload_with_broken_servers(1))
        d.addCallback(_set_basedir)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        d.addCallback(_do_server_setup_2)
        d.addCallback(_remove_server)
        d.addCallback(lambda ign:
            self.shouldFail(UploadUnhappinessError,
                            "test_dropped_servers_in_encoder",
                            "shares could be placed on only 3 server(s) "
                            "such that any 3 of them have enough shares to "
                            "recover the file, but we were asked to place "
                            "shares on at least 4",
                            self._do_upload_with_broken_servers, 2))
        return d


    def test_merge_servers(self):
        # merge_servers merges a list of upload_servers and a dict of
        # shareid -> serverid mappings.
        shares = {
                    1 : set(["server1"]),
                    2 : set(["server2"]),
                    3 : set(["server3"]),
                    4 : set(["server4", "server5"]),
                    5 : set(["server1", "server2"]),
                 }
        # if not provided with a upload_servers argument, it should just
        # return the first argument unchanged.
        self.failUnlessEqual(shares, merge_servers(shares, set([])))
        trackers = []
        for (i, server) in [(i, "server%d" % i) for i in xrange(5, 9)]:
            t = FakeServerTracker(server, [i])
            trackers.append(t)
        expected = {
                    1 : set(["server1"]),
                    2 : set(["server2"]),
                    3 : set(["server3"]),
                    4 : set(["server4", "server5"]),
                    5 : set(["server1", "server2", "server5"]),
                    6 : set(["server6"]),
                    7 : set(["server7"]),
                    8 : set(["server8"]),
                   }
        self.failUnlessEqual(expected, merge_servers(shares, set(trackers)))
        shares2 = {}
        expected = {
                    5 : set(["server5"]),
                    6 : set(["server6"]),
                    7 : set(["server7"]),
                    8 : set(["server8"]),
                   }
        self.failUnlessEqual(expected, merge_servers(shares2, set(trackers)))
        shares3 = {}
        trackers = []
        expected = {}
        for (i, server) in [(i, "server%d" % i) for i in xrange(10)]:
            shares3[i] = set([server])
            t = FakeServerTracker(server, [i])
            trackers.append(t)
            expected[i] = set([server])
        self.failUnlessEqual(expected, merge_servers(shares3, set(trackers)))


    def test_servers_of_happiness_utility_function(self):
        # These tests are concerned with the servers_of_happiness()
        # utility function, and its underlying matching algorithm. Other
        # aspects of the servers_of_happiness behavior are tested
        # elsehwere These tests exist to ensure that
        # servers_of_happiness doesn't under or overcount the happiness
        # value for given inputs.

        # servers_of_happiness expects a dict of
        # shnum => set(serverids) as a preexisting shares argument.
        test1 = {
                 1 : set(["server1"]),
                 2 : set(["server2"]),
                 3 : set(["server3"]),
                 4 : set(["server4"])
                }
        happy = servers_of_happiness(test1)
        self.failUnlessEqual(4, happy)
        test1[4] = set(["server1"])
        # We've added a duplicate server, so now servers_of_happiness
        # should be 3 instead of 4.
        happy = servers_of_happiness(test1)
        self.failUnlessEqual(3, happy)
        # The second argument of merge_servers should be a set of objects with
        # serverid and buckets as attributes. In actual use, these will be
        # ServerTracker instances, but for testing it is fine to make a
        # FakeServerTracker whose job is to hold those instance variables to
        # test that part.
        trackers = []
        for (i, server) in [(i, "server%d" % i) for i in xrange(5, 9)]:
            t = FakeServerTracker(server, [i])
            trackers.append(t)
        # Recall that test1 is a server layout with servers_of_happiness
        # = 3.  Since there isn't any overlap between the shnum ->
        # set([serverid]) correspondences in test1 and those in trackers,
        # the result here should be 7.
        test2 = merge_servers(test1, set(trackers))
        happy = servers_of_happiness(test2)
        self.failUnlessEqual(7, happy)
        # Now add an overlapping server to trackers. This is redundant,
        # so it should not cause the previously reported happiness value
        # to change.
        t = FakeServerTracker("server1", [1])
        trackers.append(t)
        test2 = merge_servers(test1, set(trackers))
        happy = servers_of_happiness(test2)
        self.failUnlessEqual(7, happy)
        test = {}
        happy = servers_of_happiness(test)
        self.failUnlessEqual(0, happy)
        # Test a more substantial overlap between the trackers and the
        # existing assignments.
        test = {
            1 : set(['server1']),
            2 : set(['server2']),
            3 : set(['server3']),
            4 : set(['server4']),
        }
        trackers = []
        t = FakeServerTracker('server5', [4])
        trackers.append(t)
        t = FakeServerTracker('server6', [3, 5])
        trackers.append(t)
        # The value returned by servers_of_happiness is the size
        # of a maximum matching in the bipartite graph that
        # servers_of_happiness() makes between serverids and share
        # numbers. It should find something like this:
        # (server 1, share 1)
        # (server 2, share 2)
        # (server 3, share 3)
        # (server 5, share 4)
        # (server 6, share 5)
        #
        # and, since there are 5 edges in this matching, it should
        # return 5.
        test2 = merge_servers(test, set(trackers))
        happy = servers_of_happiness(test2)
        self.failUnlessEqual(5, happy)
        # Zooko's first puzzle:
        # (from http://allmydata.org/trac/tahoe-lafs/ticket/778#comment:156)
        #
        # server 1: shares 0, 1
        # server 2: shares 1, 2
        # server 3: share 2
        #
        # This should yield happiness of 3.
        test = {
            0 : set(['server1']),
            1 : set(['server1', 'server2']),
            2 : set(['server2', 'server3']),
        }
        self.failUnlessEqual(3, servers_of_happiness(test))
        # Zooko's second puzzle:
        # (from http://allmydata.org/trac/tahoe-lafs/ticket/778#comment:158)
        #
        # server 1: shares 0, 1
        # server 2: share 1
        #
        # This should yield happiness of 2.
        test = {
            0 : set(['server1']),
            1 : set(['server1', 'server2']),
        }
        self.failUnlessEqual(2, servers_of_happiness(test))


    def test_shares_by_server(self):
        test = dict([(i, set(["server%d" % i])) for i in xrange(1, 5)])
        sbs = shares_by_server(test)
        self.failUnlessEqual(set([1]), sbs["server1"])
        self.failUnlessEqual(set([2]), sbs["server2"])
        self.failUnlessEqual(set([3]), sbs["server3"])
        self.failUnlessEqual(set([4]), sbs["server4"])
        test1 = {
                    1 : set(["server1"]),
                    2 : set(["server1"]),
                    3 : set(["server1"]),
                    4 : set(["server2"]),
                    5 : set(["server2"])
                }
        sbs = shares_by_server(test1)
        self.failUnlessEqual(set([1, 2, 3]), sbs["server1"])
        self.failUnlessEqual(set([4, 5]), sbs["server2"])
        # This should fail unless the serverid part of the mapping is a set
        test2 = {1: "server1"}
        self.shouldFail(AssertionError,
                       "test_shares_by_server",
                       "",
                       shares_by_server, test2)


    def test_existing_share_detection(self):
        self.basedir = self.mktemp()
        d = self._setup_and_upload()
        # Our final setup should look like this:
        # server 1: shares 0 - 9, read-only
        # server 2: empty
        # server 3: empty
        # server 4: empty
        # The purpose of this test is to make sure that the server selector
        # knows about the shares on server 1, even though it is read-only.
        # It used to simply filter these out, which would cause the test
        # to fail when servers_of_happiness = 4.
        d.addCallback(lambda ign:
            self._add_server_with_share(1, 0, True))
        d.addCallback(lambda ign:
            self._add_server(2))
        d.addCallback(lambda ign:
            self._add_server(3))
        d.addCallback(lambda ign:
            self._add_server(4))
        def _copy_shares(ign):
            for i in xrange(1, 10):
                self._copy_share_to_server(i, 1)
        d.addCallback(_copy_shares)
        d.addCallback(lambda ign:
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
        def _prepare_client(ign):
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return client
        d.addCallback(_prepare_client)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d


    def test_query_counting(self):
        # If server selection fails, Tahoe2ServerSelector prints out a lot
        # of helpful diagnostic information, including query stats.
        # This test helps make sure that that information is accurate.
        self.basedir = self.mktemp()
        d = self._setup_and_upload()
        def _setup(ign):
            for i in xrange(1, 11):
                self._add_server(server_number=i)
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid)
            c = self.g.clients[0]
            # We set happy to an unsatisfiable value so that we can check the
            # counting in the exception message. The same progress message
            # is also used when the upload is successful, but in that case it
            # only gets written to a log, so we can't see what it says.
            c.DEFAULT_ENCODING_PARAMETERS['happy'] = 45
            return c
        d.addCallback(_setup)
        d.addCallback(lambda c:
            self.shouldFail(UploadUnhappinessError, "test_query_counting",
                            "10 queries placed some shares",
                            c.upload, upload.Data("data" * 10000,
                                                  convergence="")))
        # Now try with some readonly servers. We want to make sure that
        # the readonly server share discovery phase is counted correctly.
        def _reset(ign):
            self.basedir = self.mktemp()
            self.g = None
        d.addCallback(_reset)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        def _then(ign):
            for i in xrange(1, 11):
                self._add_server(server_number=i)
            self._add_server(server_number=11, readonly=True)
            self._add_server(server_number=12, readonly=True)
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid)
            c = self.g.clients[0]
            c.DEFAULT_ENCODING_PARAMETERS['happy'] = 45
            return c
        d.addCallback(_then)
        d.addCallback(lambda c:
            self.shouldFail(UploadUnhappinessError, "test_query_counting",
                            "2 placed none (of which 2 placed none due to "
                            "the server being full",
                            c.upload, upload.Data("data" * 10000,
                                                  convergence="")))
        # Now try the case where the upload process finds a bunch of the
        # shares that it wants to place on the first server, including
        # the one that it wanted to allocate there. Though no shares will
        # be allocated in this request, it should still be called
        # productive, since it caused some homeless shares to be
        # removed.
        d.addCallback(_reset)
        d.addCallback(lambda ign:
            self._setup_and_upload())

        def _next(ign):
            for i in xrange(1, 11):
                self._add_server(server_number=i)
            # Copy all of the shares to server 9, since that will be
            # the first one that the selector sees.
            for i in xrange(10):
                self._copy_share_to_server(i, 9)
            # Remove server 0, and its contents
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid)
            # Make happiness unsatisfiable
            c = self.g.clients[0]
            c.DEFAULT_ENCODING_PARAMETERS['happy'] = 45
            return c
        d.addCallback(_next)
        d.addCallback(lambda c:
            self.shouldFail(UploadUnhappinessError, "test_query_counting",
                            "1 queries placed some shares",
                            c.upload, upload.Data("data" * 10000,
                                                  convergence="")))
        return d


    def test_upper_limit_on_readonly_queries(self):
        self.basedir = self.mktemp()
        d = self._setup_and_upload()
        def _then(ign):
            for i in xrange(1, 11):
                self._add_server(server_number=i, readonly=True)
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid)
            c = self.g.clients[0]
            c.DEFAULT_ENCODING_PARAMETERS['k'] = 2
            c.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            c.DEFAULT_ENCODING_PARAMETERS['n'] = 4
            return c
        d.addCallback(_then)
        d.addCallback(lambda client:
            self.shouldFail(UploadUnhappinessError,
                            "test_upper_limit_on_readonly_queries",
                            "sent 8 queries to 8 servers",
                            client.upload,
                            upload.Data('data' * 10000, convergence="")))
        return d


    def test_exception_messages_during_server_selection(self):
        # server 1: read-only, no shares
        # server 2: read-only, no shares
        # server 3: read-only, no shares
        # server 4: read-only, no shares
        # server 5: read-only, no shares
        # This will fail, but we want to make sure that the log messages
        # are informative about why it has failed.
        self.basedir = self.mktemp()
        d = self._setup_and_upload()
        d.addCallback(lambda ign:
            self._add_server(server_number=1, readonly=True))
        d.addCallback(lambda ign:
            self._add_server(server_number=2, readonly=True))
        d.addCallback(lambda ign:
            self._add_server(server_number=3, readonly=True))
        d.addCallback(lambda ign:
            self._add_server(server_number=4, readonly=True))
        d.addCallback(lambda ign:
            self._add_server(server_number=5, readonly=True))
        d.addCallback(lambda ign:
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
        def _reset_encoding_parameters(ign, happy=4):
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = happy
            return client
        d.addCallback(_reset_encoding_parameters)
        d.addCallback(lambda client:
            self.shouldFail(UploadUnhappinessError, "test_selection_exceptions",
                            "placed 0 shares out of 10 "
                            "total (10 homeless), want to place shares on at "
                            "least 4 servers such that any 3 of them have "
                            "enough shares to recover the file, "
                            "sent 5 queries to 5 servers, 0 queries placed "
                            "some shares, 5 placed none "
                            "(of which 5 placed none due to the server being "
                            "full and 0 placed none due to an error)",
                            client.upload,
                            upload.Data("data" * 10000, convergence="")))


        # server 1: read-only, no shares
        # server 2: broken, no shares
        # server 3: read-only, no shares
        # server 4: read-only, no shares
        # server 5: read-only, no shares
        def _reset(ign):
            self.basedir = self.mktemp()
        d.addCallback(_reset)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        d.addCallback(lambda ign:
            self._add_server(server_number=1, readonly=True))
        d.addCallback(lambda ign:
            self._add_server(server_number=2))
        def _break_server_2(ign):
            serverid = self.g.servers_by_number[2].my_nodeid
            self.g.break_server(serverid)
        d.addCallback(_break_server_2)
        d.addCallback(lambda ign:
            self._add_server(server_number=3, readonly=True))
        d.addCallback(lambda ign:
            self._add_server(server_number=4, readonly=True))
        d.addCallback(lambda ign:
            self._add_server(server_number=5, readonly=True))
        d.addCallback(lambda ign:
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid))
        d.addCallback(_reset_encoding_parameters)
        d.addCallback(lambda client:
            self.shouldFail(UploadUnhappinessError, "test_selection_exceptions",
                            "placed 0 shares out of 10 "
                            "total (10 homeless), want to place shares on at "
                            "least 4 servers such that any 3 of them have "
                            "enough shares to recover the file, "
                            "sent 5 queries to 5 servers, 0 queries placed "
                            "some shares, 5 placed none "
                            "(of which 4 placed none due to the server being "
                            "full and 1 placed none due to an error)",
                            client.upload,
                            upload.Data("data" * 10000, convergence="")))
        # server 0, server 1 = empty, accepting shares
        # This should place all of the shares, but still fail with happy=4.
        # We want to make sure that the exception message is worded correctly.
        d.addCallback(_reset)
        d.addCallback(lambda ign:
            self._setup_grid())
        d.addCallback(lambda ign:
            self._add_server(server_number=1))
        d.addCallback(_reset_encoding_parameters)
        d.addCallback(lambda client:
            self.shouldFail(UploadUnhappinessError, "test_selection_exceptions",
                            "shares could be placed or found on only 2 "
                            "server(s). We were asked to place shares on at "
                            "least 4 server(s) such that any 3 of them have "
                            "enough shares to recover the file.",
                            client.upload, upload.Data("data" * 10000,
                                                       convergence="")))
        # servers 0 - 4 = empty, accepting shares
        # This too should place all the shares, and this too should fail,
        # but since the effective happiness is more than the k encoding
        # parameter, it should trigger a different error message than the one
        # above.
        d.addCallback(_reset)
        d.addCallback(lambda ign:
            self._setup_grid())
        d.addCallback(lambda ign:
            self._add_server(server_number=1))
        d.addCallback(lambda ign:
            self._add_server(server_number=2))
        d.addCallback(lambda ign:
            self._add_server(server_number=3))
        d.addCallback(lambda ign:
            self._add_server(server_number=4))
        d.addCallback(_reset_encoding_parameters, happy=7)
        d.addCallback(lambda client:
            self.shouldFail(UploadUnhappinessError, "test_selection_exceptions",
                            "shares could be placed on only 5 server(s) such "
                            "that any 3 of them have enough shares to recover "
                            "the file, but we were asked to place shares on "
                            "at least 7 such servers.",
                            client.upload, upload.Data("data" * 10000,
                                                       convergence="")))
        # server 0: shares 0 - 9
        # server 1: share 0, read-only
        # server 2: share 0, read-only
        # server 3: share 0, read-only
        # This should place all of the shares, but fail with happy=4.
        # Since the number of servers with shares is more than the number
        # necessary to reconstitute the file, this will trigger a different
        # error message than either of those above.
        d.addCallback(_reset)
        d.addCallback(lambda ign:
            self._setup_and_upload())
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=1, share_number=0,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=2, share_number=0,
                                        readonly=True))
        d.addCallback(lambda ign:
            self._add_server_with_share(server_number=3, share_number=0,
                                        readonly=True))
        d.addCallback(_reset_encoding_parameters, happy=7)
        d.addCallback(lambda client:
            self.shouldFail(UploadUnhappinessError, "test_selection_exceptions",
                            "shares could be placed or found on 4 server(s), "
                            "but they are not spread out evenly enough to "
                            "ensure that any 3 of these servers would have "
                            "enough shares to recover the file. We were asked "
                            "to place shares on at least 7 servers such that "
                            "any 3 of them have enough shares to recover the "
                            "file",
                            client.upload, upload.Data("data" * 10000,
                                                       convergence="")))
        return d


    def test_problem_layout_comment_187(self):
        # #778 comment 187 broke an initial attempt at a share
        # redistribution algorithm. This test is here to demonstrate the
        # breakage, and to test that subsequent algorithms don't also
        # break in the same way.
        self.basedir = self.mktemp()
        d = self._setup_and_upload(k=2, n=3)

        # server 1: shares 0, 1, 2, readonly
        # server 2: share 0, readonly
        # server 3: share 0
        def _setup(ign):
            self._add_server_with_share(server_number=1, share_number=0,
                                        readonly=True)
            self._add_server_with_share(server_number=2, share_number=0,
                                        readonly=True)
            self._add_server_with_share(server_number=3, share_number=0)
            # Copy shares
            self._copy_share_to_server(1, 1)
            self._copy_share_to_server(2, 1)
            # Remove server 0
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid)
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 3
            return client

        d.addCallback(_setup)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d
    test_problem_layout_comment_187.todo = "this isn't fixed yet"

    def test_problem_layout_ticket_1118(self):
        # #1118 includes a report from a user who hit an assertion in
        # the upload code with this layout.
        self.basedir = self.mktemp()
        d = self._setup_and_upload(k=2, n=4)

        # server 0: no shares
        # server 1: shares 0, 3
        # server 3: share 1
        # server 2: share 2
        # The order that they get queries is 0, 1, 3, 2
        def _setup(ign):
            self._add_server(server_number=0)
            self._add_server_with_share(server_number=1, share_number=0)
            self._add_server_with_share(server_number=2, share_number=2)
            self._add_server_with_share(server_number=3, share_number=1)
            # Copy shares
            self._copy_share_to_server(3, 1)
            storedir = self.get_serverdir(0)
            # remove the storedir, wiping out any existing shares
            shutil.rmtree(storedir)
            # create an empty storedir to replace the one we just removed
            os.mkdir(storedir)
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return client

        d.addCallback(_setup)
        # Note: actually it should succeed! See
        # test_problem_layout_ticket_1128. But ticket 1118 is just to
        # make it realize that it has failed, so if it raises
        # UploadUnhappinessError then we'll give it the green light
        # for now.
        d.addCallback(lambda ignored:
            self.shouldFail(UploadUnhappinessError,
                            "test_problem_layout_ticket_1118",
                            "",
                            self.g.clients[0].upload, upload.Data("data" * 10000,
                                                       convergence="")))
        return d

    def test_problem_layout_ticket_1128(self):
        # #1118 includes a report from a user who hit an assertion in
        # the upload code with this layout.
        self.basedir = self.mktemp()
        d = self._setup_and_upload(k=2, n=4)

        # server 0: no shares
        # server 1: shares 0, 3
        # server 3: share 1
        # server 2: share 2
        # The order that they get queries is 0, 1, 3, 2
        def _setup(ign):
            self._add_server(server_number=0)
            self._add_server_with_share(server_number=1, share_number=0)
            self._add_server_with_share(server_number=2, share_number=2)
            self._add_server_with_share(server_number=3, share_number=1)
            # Copy shares
            self._copy_share_to_server(3, 1)
            storedir = self.get_serverdir(0)
            # remove the storedir, wiping out any existing shares
            shutil.rmtree(storedir)
            # create an empty storedir to replace the one we just removed
            os.mkdir(storedir)
            client = self.g.clients[0]
            client.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return client

        d.addCallback(_setup)
        d.addCallback(lambda client:
                          client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d
    test_problem_layout_ticket_1128.todo = "Invent a smarter uploader that uploads successfully in this case."

    def test_upload_succeeds_with_some_homeless_shares(self):
        # If the upload is forced to stop trying to place shares before
        # it has placed (or otherwise accounted) for all of them, but it
        # has placed enough to satisfy the upload health criteria that
        # we're using, it should still succeed.
        self.basedir = self.mktemp()
        d = self._setup_and_upload()
        def _server_setup(ign):
            # Add four servers so that we have a layout like this:
            # server 1: share 0, read-only
            # server 2: share 1, read-only
            # server 3: share 2, read-only
            # server 4: share 3, read-only
            # If we set happy = 4, the upload will manage to satisfy
            # servers of happiness, but not place all of the shares; we
            # want to test that the upload is declared successful in
            # this case.
            self._add_server_with_share(server_number=1, share_number=0,
                                        readonly=True)
            self._add_server_with_share(server_number=2, share_number=1,
                                        readonly=True)
            self._add_server_with_share(server_number=3, share_number=2,
                                        readonly=True)
            self._add_server_with_share(server_number=4, share_number=3,
                                        readonly=True)
            # Remove server 0.
            self.g.remove_server(self.g.servers_by_number[0].my_nodeid)
            # Set the client appropriately
            c = self.g.clients[0]
            c.DEFAULT_ENCODING_PARAMETERS['happy'] = 4
            return c
        d.addCallback(_server_setup)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d


    def test_uploader_skips_over_servers_with_only_one_share(self):
        # We want to make sure that the redistribution logic ignores
        # servers with only one share, since placing these shares
        # elsewhere will at best keep happiness the same as it was, and
        # at worst hurt it.
        self.basedir = self.mktemp()
        d = self._setup_and_upload()
        def _server_setup(ign):
            # Add some servers so that the upload will need to
            # redistribute, but will first pass over a couple of servers
            # that don't have enough shares to redistribute before
            # finding one that does have shares to redistribute.
            self._add_server_with_share(server_number=1, share_number=0)
            self._add_server_with_share(server_number=2, share_number=2)
            self._add_server_with_share(server_number=3, share_number=1)
            self._add_server_with_share(server_number=8, share_number=4)
            self._add_server_with_share(server_number=5, share_number=5)
            self._add_server_with_share(server_number=10, share_number=7)
            for i in xrange(4):
                self._copy_share_to_server(i, 2)
            return self.g.clients[0]
        d.addCallback(_server_setup)
        d.addCallback(lambda client:
            client.upload(upload.Data("data" * 10000, convergence="")))
        d.addCallback(lambda ign:
            self.failUnless(self._has_happy_share_distribution()))
        return d


    def test_server_selector_bucket_abort(self):
        # If server selection for an upload fails due to an unhappy
        # layout, the server selection process should abort the buckets it
        # allocates before failing, so that the space can be re-used.
        self.basedir = self.mktemp()
        self.set_up_grid(num_servers=5)

        # Try to upload a file with happy=7, which is unsatisfiable with
        # the current grid. This will fail, but should not take up any
        # space on the storage servers after it fails.
        client = self.g.clients[0]
        client.DEFAULT_ENCODING_PARAMETERS['happy'] = 7
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(UploadUnhappinessError,
                            "test_server_selection_bucket_abort",
                            "",
                            client.upload, upload.Data("data" * 10000,
                                                       convergence="")))
        # wait for the abort messages to get there.
        def _turn_barrier(res):
            return fireEventually(res)
        d.addCallback(_turn_barrier)
        def _then(ignored):
            for server in self.g.servers_by_number.values():
                self.failUnlessEqual(server.allocated_size(), 0)
        d.addCallback(_then)
        return d


    def test_encoder_bucket_abort(self):
        # If enough servers die in the process of encoding and uploading
        # a file to make the layout unhappy, we should cancel the
        # newly-allocated buckets before dying.
        self.basedir = self.mktemp()
        self.set_up_grid(num_servers=4)

        client = self.g.clients[0]
        client.DEFAULT_ENCODING_PARAMETERS['happy'] = 7

        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(UploadUnhappinessError,
                            "test_encoder_bucket_abort",
                            "",
                            self._do_upload_with_broken_servers, 1))
        def _turn_barrier(res):
            return fireEventually(res)
        d.addCallback(_turn_barrier)
        def _then(ignored):
            for server in self.g.servers_by_number.values():
                self.failUnlessEqual(server.allocated_size(), 0)
        d.addCallback(_then)
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
#  upload with exactly 75 servers (shares_of_happiness)
#  have a download fail
#  cancel a download (need to implement more cancel stuff)

# from test_encode:
# NoNetworkGrid, upload part of ciphertext, kill server, continue upload
# check with Kevan, they want to live in test_upload, existing tests might cover
#     def test_lost_one_shareholder(self): # these are upload-side tests
#     def test_lost_one_shareholder_early(self):
#     def test_lost_many_shareholders(self):
#     def test_lost_all_shareholders(self):
