
from twisted.trial import unittest
from twisted.application import service

from foolscap import Tub, eventual
from foolscap.logging import log

from allmydata import upload, offloaded
from allmydata.util import hashutil

class FakeCHKUploadHelper(offloaded.CHKUploadHelper):
    def remote_upload(self, reader):
        return {'uri_extension_hash': hashutil.uri_extension_hash("")}

class FakeHelper(offloaded.Helper):
    chk_upload_helper_class = FakeCHKUploadHelper

class FakeClient(service.MultiService):
    def log(self, msg, **kwargs):
        return log.msg(msg, **kwargs)
    def get_push_to_ourselves(self):
        return True
    def get_encoding_parameters(self):
        return None

def flush_but_dont_ignore(res):
    d = eventual.flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

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


        h = FakeHelper(".")
        h.setServiceParent(self.s)
        self.helper_furl = t.registerReference(h)

    def tearDown(self):
        d = self.s.stopService()
        d.addCallback(eventual.fireEventually)
        d.addBoth(flush_but_dont_ignore)
        return d


    def test_one(self):
        u = upload.Uploader(self.helper_furl)
        u.setServiceParent(self.s)

        # wait a few turns
        d = eventual.fireEventually()
        d.addCallback(eventual.fireEventually)
        d.addCallback(eventual.fireEventually)

        def _ready(res):
            assert u._helper

            DATA = "I need help\n" * 1000
            return u.upload_data(DATA)
        d.addCallback(_ready)
        def _uploaded(uri):
            assert "CHK" in uri
        d.addCallback(_uploaded)

        return d

