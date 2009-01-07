import os
from twisted.trial import unittest
from twisted.application import service

from foolscap import Tub, eventual
from foolscap.logging import log

from allmydata import offloaded, storage
from allmydata.immutable import upload
from allmydata.util import hashutil, fileutil, mathutil
from pycryptopp.cipher.aes import AES

MiB = 1024*1024

DATA = "I need help\n" * 1000

class CHKUploadHelper_fake(offloaded.CHKUploadHelper):
    def start_encrypted(self, eu):
        d = eu.get_size()
        def _got_size(size):
            d2 = eu.get_all_encoding_parameters()
            def _got_parms(parms):
                needed_shares, happy, total_shares, segsize = parms
                ueb_data = {"needed_shares": needed_shares,
                            "total_shares": total_shares,
                            "segment_size": segsize,
                            "size": size,
                            }
                self._results.uri_extension_data = ueb_data
                return (hashutil.uri_extension_hash(""),
                        needed_shares, total_shares, size)
            d2.addCallback(_got_parms)
            return d2
        d.addCallback(_got_size)
        return d

class CHKUploadHelper_already_uploaded(offloaded.CHKUploadHelper):
    def start(self):
        res = upload.UploadResults()
        res.uri_extension_hash = hashutil.uri_extension_hash("")

        # we're pretending that the file they're trying to upload was already
        # present in the grid. We return some information about the file, so
        # the client can decide if they like the way it looks. The parameters
        # used here are chosen to match the defaults.
        PARAMS = FakeClient.DEFAULT_ENCODING_PARAMETERS
        ueb_data = {"needed_shares": PARAMS["k"],
                    "total_shares": PARAMS["n"],
                    "segment_size": min(PARAMS["max_segment_size"], len(DATA)),
                    "size": len(DATA),
                    }
        res.uri_extension_data = ueb_data
        return (res, None)

class FakeClient(service.MultiService):
    DEFAULT_ENCODING_PARAMETERS = {"k":25,
                                   "happy": 75,
                                   "n": 100,
                                   "max_segment_size": 1*MiB,
                                   }
    stats_provider = None
    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)
    def get_encoding_parameters(self):
        return self.DEFAULT_ENCODING_PARAMETERS
    def get_permuted_peers(self, service_name, storage_index):
        return []

def flush_but_dont_ignore(res):
    d = eventual.flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

def wait_a_few_turns(ignored=None):
    d = eventual.fireEventually()
    d.addCallback(eventual.fireEventually)
    d.addCallback(eventual.fireEventually)
    d.addCallback(eventual.fireEventually)
    d.addCallback(eventual.fireEventually)
    d.addCallback(eventual.fireEventually)
    return d

def upload_data(uploader, data, convergence):
    u = upload.Data(data, convergence=convergence)
    return uploader.upload(u)

class AssistedUpload(unittest.TestCase):
    def setUp(self):
        self.s = FakeClient()
        self.s.startService()

        self.tub = t = Tub()
        t.setServiceParent(self.s)
        self.s.tub = t
        # we never actually use this for network traffic, so it can use a
        # bogus host/port
        t.setLocation("bogus:1234")

    def setUpHelper(self, basedir):
        fileutil.make_dirs(basedir)
        self.helper = h = offloaded.Helper(basedir)
        h.chk_upload_helper_class = CHKUploadHelper_fake
        h.setServiceParent(self.s)
        self.helper_furl = self.tub.registerReference(h)

    def tearDown(self):
        d = self.s.stopService()
        d.addCallback(eventual.fireEventually)
        d.addBoth(flush_but_dont_ignore)
        return d


    def test_one(self):
        self.basedir = "helper/AssistedUpload/test_one"
        self.setUpHelper(self.basedir)
        u = upload.Uploader(self.helper_furl)
        u.setServiceParent(self.s)

        d = wait_a_few_turns()

        def _ready(res):
            assert u._helper

            return upload_data(u, DATA, convergence="some convergence string")
        d.addCallback(_ready)
        def _uploaded(results):
            uri = results.uri
            assert "CHK" in uri
        d.addCallback(_uploaded)

        def _check_empty(res):
            files = os.listdir(os.path.join(self.basedir, "CHK_encoding"))
            self.failUnlessEqual(files, [])
            files = os.listdir(os.path.join(self.basedir, "CHK_incoming"))
            self.failUnlessEqual(files, [])
        d.addCallback(_check_empty)

        return d

    def test_previous_upload_failed(self):
        self.basedir = "helper/AssistedUpload/test_previous_upload_failed"
        self.setUpHelper(self.basedir)

        # we want to make sure that an upload which fails (leaving the
        # ciphertext in the CHK_encoding/ directory) does not prevent a later
        # attempt to upload that file from working. We simulate this by
        # populating the directory manually. The hardest part is guessing the
        # storage index.

        k = FakeClient.DEFAULT_ENCODING_PARAMETERS["k"]
        n = FakeClient.DEFAULT_ENCODING_PARAMETERS["n"]
        max_segsize = FakeClient.DEFAULT_ENCODING_PARAMETERS["max_segment_size"]
        segsize = min(max_segsize, len(DATA))
        # this must be a multiple of 'required_shares'==k
        segsize = mathutil.next_multiple(segsize, k)

        key = hashutil.convergence_hash(k, n, segsize, DATA, "test convergence string")
        assert len(key) == 16
        encryptor = AES(key)
        SI = hashutil.storage_index_hash(key)
        SI_s = storage.si_b2a(SI)
        encfile = os.path.join(self.basedir, "CHK_encoding", SI_s)
        f = open(encfile, "wb")
        f.write(encryptor.process(DATA))
        f.close()

        u = upload.Uploader(self.helper_furl)
        u.setServiceParent(self.s)

        d = wait_a_few_turns()

        def _ready(res):
            assert u._helper
            return upload_data(u, DATA, convergence="test convergence string")
        d.addCallback(_ready)
        def _uploaded(results):
            uri = results.uri
            assert "CHK" in uri
        d.addCallback(_uploaded)

        def _check_empty(res):
            files = os.listdir(os.path.join(self.basedir, "CHK_encoding"))
            self.failUnlessEqual(files, [])
            files = os.listdir(os.path.join(self.basedir, "CHK_incoming"))
            self.failUnlessEqual(files, [])
        d.addCallback(_check_empty)

        return d

    def test_already_uploaded(self):
        self.basedir = "helper/AssistedUpload/test_already_uploaded"
        self.setUpHelper(self.basedir)
        self.helper.chk_upload_helper_class = CHKUploadHelper_already_uploaded
        u = upload.Uploader(self.helper_furl)
        u.setServiceParent(self.s)

        d = wait_a_few_turns()

        def _ready(res):
            assert u._helper

            return upload_data(u, DATA, convergence="some convergence string")
        d.addCallback(_ready)
        def _uploaded(results):
            uri = results.uri
            assert "CHK" in uri
        d.addCallback(_uploaded)

        def _check_empty(res):
            files = os.listdir(os.path.join(self.basedir, "CHK_encoding"))
            self.failUnlessEqual(files, [])
            files = os.listdir(os.path.join(self.basedir, "CHK_incoming"))
            self.failUnlessEqual(files, [])
        d.addCallback(_check_empty)

        return d
