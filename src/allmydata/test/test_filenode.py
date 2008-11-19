
from twisted.trial import unittest
from allmydata import uri
from allmydata.monitor import Monitor
from allmydata.immutable import filenode, download
from allmydata.mutable.node import MutableFileNode
from allmydata.util import hashutil, cachedir
from allmydata.test.common import download_to_data

class NotANode:
    pass

class FakeClient:
    # just enough to let the node acquire a downloader (which it won't use),
    # and to get default encoding parameters
    def getServiceNamed(self, name):
        return None
    def get_encoding_parameters(self):
        return {"k": 3, "n": 10}

class Node(unittest.TestCase):
    def test_chk_filenode(self):
        u = uri.CHKFileURI(key="\x00"*16,
                           uri_extension_hash="\x00"*32,
                           needed_shares=3,
                           total_shares=10,
                           size=1000)
        c = FakeClient()
        cf = cachedir.CacheFile("none")
        fn1 = filenode.FileNode(u, c, cf)
        fn2 = filenode.FileNode(u.to_string(), c, cf)
        self.failUnlessEqual(fn1, fn2)
        self.failIfEqual(fn1, "I am not a filenode")
        self.failIfEqual(fn1, NotANode())
        self.failUnlessEqual(fn1.get_uri(), u.to_string())
        self.failUnlessEqual(fn1.is_readonly(), True)
        self.failUnlessEqual(fn1.is_mutable(), False)
        self.failUnlessEqual(fn1.get_readonly_uri(), u.to_string())
        self.failUnlessEqual(fn1.get_size(), 1000)
        self.failUnlessEqual(fn1.get_storage_index(), u.storage_index)
        d = {}
        d[fn1] = 1 # exercise __hash__
        v = fn1.get_verifier()
        self.failUnless(isinstance(v, uri.CHKFileVerifierURI))


    def test_literal_filenode(self):
        DATA = "I am a short file."
        u = uri.LiteralFileURI(data=DATA)
        c = None
        fn1 = filenode.LiteralFileNode(u, c)
        fn2 = filenode.LiteralFileNode(u.to_string(), c)
        self.failUnlessEqual(fn1, fn2)
        self.failIfEqual(fn1, "I am not a filenode")
        self.failIfEqual(fn1, NotANode())
        self.failUnlessEqual(fn1.get_uri(), u.to_string())
        self.failUnlessEqual(fn1.is_readonly(), True)
        self.failUnlessEqual(fn1.is_mutable(), False)
        self.failUnlessEqual(fn1.get_readonly_uri(), u.to_string())
        self.failUnlessEqual(fn1.get_size(), len(DATA))
        self.failUnlessEqual(fn1.get_storage_index(), None)
        d = {}
        d[fn1] = 1 # exercise __hash__

        v = fn1.get_verifier()
        self.failUnlessEqual(v, None)

        d = fn1.download(download.Data())
        def _check(res):
            self.failUnlessEqual(res, DATA)
        d.addCallback(_check)

        d.addCallback(lambda res: fn1.download_to_data())
        d.addCallback(_check)

        d.addCallback(lambda res: download_to_data(fn1))
        d.addCallback(_check)

        d.addCallback(lambda res: download_to_data(fn1, 1, 5))
        def _check_segment(res):
            self.failUnlessEqual(res, DATA[1:1+5])
        d.addCallback(_check_segment)

        return d

    def test_mutable_filenode(self):
        client = FakeClient()
        wk = "\x00"*16
        fp = "\x00"*32
        rk = hashutil.ssk_readkey_hash(wk)
        si = hashutil.ssk_storage_index_hash(rk)

        u = uri.WriteableSSKFileURI("\x00"*16, "\x00"*32)
        n = MutableFileNode(client).init_from_uri(u)

        self.failUnlessEqual(n.get_writekey(), wk)
        self.failUnlessEqual(n.get_readkey(), rk)
        self.failUnlessEqual(n.get_storage_index(), si)
        # these itmes are populated on first read (or create), so until that
        # happens they'll be None
        self.failUnlessEqual(n.get_privkey(), None)
        self.failUnlessEqual(n.get_encprivkey(), None)
        self.failUnlessEqual(n.get_pubkey(), None)

        self.failUnlessEqual(n.get_uri(), u.to_string())
        self.failUnlessEqual(n.get_readonly_uri(), u.get_readonly().to_string())
        self.failUnlessEqual(n.is_mutable(), True)
        self.failUnlessEqual(n.is_readonly(), False)

        n2 = MutableFileNode(client).init_from_uri(u)
        self.failUnlessEqual(n, n2)
        self.failIfEqual(n, "not even the right type")
        self.failIfEqual(n, u) # not the right class
        d = {n: "can these be used as dictionary keys?"}
        d[n2] = "replace the old one"
        self.failUnlessEqual(len(d), 1)

        nro = n.get_readonly()
        self.failUnless(isinstance(nro, MutableFileNode))

        self.failUnlessEqual(nro.get_readonly(), nro)
        nro_u = nro.get_uri()
        self.failUnlessEqual(nro_u, nro.get_readonly_uri())
        self.failUnlessEqual(nro_u, u.get_readonly().to_string())
        self.failUnlessEqual(nro.is_mutable(), True)
        self.failUnlessEqual(nro.is_readonly(), True)

        v = n.get_verifier()
        self.failUnless(isinstance(v, uri.SSKVerifierURI))

class LiteralChecker(unittest.TestCase):
    def test_literal_filenode(self):
        DATA = "I am a short file."
        u = uri.LiteralFileURI(data=DATA)
        fn1 = filenode.LiteralFileNode(u, None)

        d = fn1.check(Monitor())
        def _check_checker_results(cr):
            self.failUnlessEqual(cr, None)
        d.addCallback(_check_checker_results)

        d.addCallback(lambda res: fn1.check(Monitor(), verify=True))
        d.addCallback(_check_checker_results)

        return d
