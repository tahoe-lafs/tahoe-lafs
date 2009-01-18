import os

from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import log
from twisted.web import client, http

from allmydata.test.common import SystemTestMixin


class TestCase(SystemTestMixin, unittest.TestCase):

    def setAmbientUploadAuthority(self,ambientUploadAuthority):
        self.ambientUploadAuthority = ambientUploadAuthority

    def _test_ambient_upload_authority(self):
        self.webip = "127.0.0.1"
        self.webport = 3456
        self.basedir = self.mktemp()

        # set up an introducer and a node
        d = self.set_up_nodes(1)
        d.addCallback(self._test_ambient_upload_authority2)
        d.addErrback(self.fail)
        return d

    def _set_up_nodes_extra_config(self):
        # we need to remove the 'webport' old-style config file
        # or else the node won't start
        os.remove(os.path.join(self.getdir("client0"), "webport"))
        f = open(os.path.join(self.getdir("client0"), "tahoe.cfg"), "wt")
        f.write("\n")
        f.write("[node]\n")
        f.write("web.ambient_upload_authority = %s\n" % ("false","true")[self.ambientUploadAuthority])
        f.write("web.port = tcp:%d:interface=%s\n" % (self.webport, self.webip))
        f.write("\n")
        f.write("[client]\n")
        f.write("introducer.furl = %s\n" % self.introducer_furl)
        f.write("\n")
        f.write("[storage]\n")
        f.write("enabled = true\n")
        f.write("\n")
        f.close()


    def _test_ambient_upload_authority2(self, ignored=None):
        content_type = 'multipart/form-data; boundary=----------ThIs_Is_tHe_bouNdaRY_$'
        body = '------------ThIs_Is_tHe_bouNdaRY_$\r\nContent-Disposition: form-data; name="t"\r\n\r\nupload\r\n------------ThIs_Is_tHe_bouNdaRY_$\r\nContent-Disposition: form-data; name="file"; filename="file1.txt"\r\nContent-Type: application/octet-stream\r\n\r\nsome test text\r\n------------ThIs_Is_tHe_bouNdaRY_$--\r\n'
        headers = {'Content-Type': content_type,
                   'Content-Length': len(body)}

        deferreds = []
        expected = (http.BAD_REQUEST, http.OK)[self.ambientUploadAuthority]

        # try to upload using the local web client
        def tryRequest(pathetc, method, postdata=None, headers=None):
            url = "http://%s:%d/%s" % (self.webip, self.webport, pathetc)
            f = client.HTTPClientFactory(url,method, postdata, headers)
            f.deferred.addCallback(self._cbCheckResponse,[f,expected])
            f.deferred.addErrback(self._cbCheckResponse,[f,expected])
            deferreds.append(f.deferred)
            reactor.connectTCP(self.webip, self.webport, f)

        tryRequest("uri","PUT","non contents\r\n")
        tryRequest("uri?t=mkdir","PUT")
        tryRequest("uri?t=mkdir","POST")
        tryRequest("uri?t=upload","POST",body,headers)

        # give us one deferred that will fire iff all of the above succeed
        dlist = defer.DeferredList(deferreds,fireOnOneCallback=False,
                                   fireOnOneErrback=True)
        dlist.addErrback(self.fail)

        return dlist

    def _cbCheckResponse(self, ignored, cmp):
        r = cmp[0]
        expected = cmp[1]
        self.failUnless(int(r.status) == expected)


class TestAmbientUploadAuthorityEnabled(TestCase):
    def setUp(self):
        TestCase.setUp(self)
        self.setAmbientUploadAuthority(True)

    def test_ambient_upload_authority_enabled(self):
        return self._test_ambient_upload_authority()

class TestAmbientUploadAuthorityDisabled(TestCase):
    def setUp(self):
        TestCase.setUp(self)
        self.setAmbientUploadAuthority(False)

    def test_ambient_upload_authority_disabled(self):
        return self._test_ambient_upload_authority()
