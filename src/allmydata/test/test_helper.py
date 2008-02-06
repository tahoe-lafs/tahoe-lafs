
import os
from twisted.trial import unittest
from twisted.application import service

from foolscap import Tub, eventual
from foolscap.logging import log

from allmydata import upload, offloaded
from allmydata.util import hashutil, fileutil, idlib
from pycryptopp.cipher.aes import AES

MiB = 1024*1024

class CHKUploadHelper_fake(offloaded.CHKUploadHelper):
    def start_encrypted(self, eu):
        d = eu.get_size()
        def _got_size(size):
            d2 = eu.get_all_encoding_parameters()
            def _got_parms(parms):
                needed_shares, happy, total_shares, segsize = parms
                return (hashutil.uri_extension_hash(""),
                        needed_shares, total_shares, size)
            d2.addCallback(_got_parms)
            return d2
        d.addCallback(_got_size)
        return d

class CHKUploadHelper_already_uploaded(offloaded.CHKUploadHelper):
    def start(self):
        res = {'uri_extension_hash': hashutil.uri_extension_hash("")}
        return (res, None)

class FakeClient(service.MultiService):
    DEFAULT_ENCODING_PARAMETERS = {"k":25,
                                   "happy": 75,
                                   "n": 100,
                                   "max_segment_size": 1*MiB,
                                   }
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

def upload_data(uploader, data):
    u = upload.Data(data)
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

        # wait a few turns
        d = eventual.fireEventually()
        d.addCallback(eventual.fireEventually)
        d.addCallback(eventual.fireEventually)

        def _ready(res):
            assert u._helper

            DATA = "I need help\n" * 1000
            return upload_data(u, DATA)
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
        DATA = "I need help\n" * 1000

        # we want to make sure that an upload which fails (leaving the
        # ciphertext in the CHK_encoding/ directory) does not prevent a later
        # attempt to upload that file from working. We simulate this by
        # populating the directory manually.
        key = hashutil.key_hash(DATA)[:16]
        encryptor = AES(key)
        SI = hashutil.storage_index_hash(key)
        SI_s = idlib.b2a(SI)
        encfile = os.path.join(self.basedir, "CHK_encoding", SI_s)
        f = open(encfile, "wb")
        f.write(encryptor.process(DATA))
        f.close()

        u = upload.Uploader(self.helper_furl)
        u.setServiceParent(self.s)

        # wait a few turns
        d = eventual.fireEventually()
        d.addCallback(eventual.fireEventually)
        d.addCallback(eventual.fireEventually)

        def _ready(res):
            assert u._helper
            return upload_data(u, DATA)
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

        # wait a few turns
        d = eventual.fireEventually()
        d.addCallback(eventual.fireEventually)
        d.addCallback(eventual.fireEventually)

        def _ready(res):
            assert u._helper

            DATA = "I need help\n" * 1000
            return upload_data(u, DATA)
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
