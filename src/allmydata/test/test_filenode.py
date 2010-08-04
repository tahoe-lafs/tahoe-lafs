
from twisted.trial import unittest
from allmydata import uri, client
from allmydata.monitor import Monitor
from allmydata.immutable.literal import LiteralFileNode
from allmydata.immutable.filenode import ImmutableFileNode
from allmydata.mutable.filenode import MutableFileNode
from allmydata.util import hashutil
from allmydata.util.consumer import download_to_data

class NotANode:
    pass

class FakeClient:
    # just enough to let the node acquire a downloader (which it won't use),
    # and to get default encoding parameters
    def getServiceNamed(self, name):
        return None
    def get_encoding_parameters(self):
        return {"k": 3, "n": 10}
    def get_storage_broker(self):
        return None
    def get_history(self):
        return None
    _secret_holder = client.SecretHolder("lease secret", "convergence secret")

class Node(unittest.TestCase):
    def test_chk_filenode(self):
        u = uri.CHKFileURI(key="\x00"*16,
                           uri_extension_hash="\x00"*32,
                           needed_shares=3,
                           total_shares=10,
                           size=1000)
        fn1 = ImmutableFileNode(u, None, None, None, None)
        fn2 = ImmutableFileNode(u, None, None, None, None)
        self.failUnlessEqual(fn1, fn2)
        self.failIfEqual(fn1, "I am not a filenode")
        self.failIfEqual(fn1, NotANode())
        self.failUnlessEqual(fn1.get_uri(), u.to_string())
        self.failUnlessEqual(fn1.get_cap(), u)
        self.failUnlessEqual(fn1.get_readcap(), u)
        self.failUnless(fn1.is_readonly())
        self.failIf(fn1.is_mutable())
        self.failIf(fn1.is_unknown())
        self.failUnless(fn1.is_allowed_in_immutable_directory())
        self.failUnlessEqual(fn1.get_write_uri(), None)
        self.failUnlessEqual(fn1.get_readonly_uri(), u.to_string())
        self.failUnlessEqual(fn1.get_size(), 1000)
        self.failUnlessEqual(fn1.get_storage_index(), u.get_storage_index())
        fn1.raise_error()
        fn2.raise_error()
        d = {}
        d[fn1] = 1 # exercise __hash__
        v = fn1.get_verify_cap()
        self.failUnless(isinstance(v, uri.CHKFileVerifierURI))
        self.failUnlessEqual(fn1.get_repair_cap(), v)
        self.failUnless(v.is_readonly())
        self.failIf(v.is_mutable())


    def test_literal_filenode(self):
        DATA = "I am a short file."
        u = uri.LiteralFileURI(data=DATA)
        fn1 = LiteralFileNode(u)
        fn2 = LiteralFileNode(u)
        self.failUnlessEqual(fn1, fn2)
        self.failIfEqual(fn1, "I am not a filenode")
        self.failIfEqual(fn1, NotANode())
        self.failUnlessEqual(fn1.get_uri(), u.to_string())
        self.failUnlessEqual(fn1.get_cap(), u)
        self.failUnlessEqual(fn1.get_readcap(), u)
        self.failUnless(fn1.is_readonly())
        self.failIf(fn1.is_mutable())
        self.failIf(fn1.is_unknown())
        self.failUnless(fn1.is_allowed_in_immutable_directory())
        self.failUnlessEqual(fn1.get_write_uri(), None)
        self.failUnlessEqual(fn1.get_readonly_uri(), u.to_string())
        self.failUnlessEqual(fn1.get_size(), len(DATA))
        self.failUnlessEqual(fn1.get_storage_index(), None)
        fn1.raise_error()
        fn2.raise_error()
        d = {}
        d[fn1] = 1 # exercise __hash__

        v = fn1.get_verify_cap()
        self.failUnlessEqual(v, None)
        self.failUnlessEqual(fn1.get_repair_cap(), None)

        d = download_to_data(fn1)
        def _check(res):
            self.failUnlessEqual(res, DATA)
        d.addCallback(_check)

        d.addCallback(lambda res: download_to_data(fn1, 1, 5))
        def _check_segment(res):
            self.failUnlessEqual(res, DATA[1:1+5])
        d.addCallback(_check_segment)

        return d

    def test_mutable_filenode(self):
        client = FakeClient()
        wk = "\x00"*16
        rk = hashutil.ssk_readkey_hash(wk)
        si = hashutil.ssk_storage_index_hash(rk)

        u = uri.WriteableSSKFileURI("\x00"*16, "\x00"*32)
        n = MutableFileNode(None, None, client.get_encoding_parameters(),
                            None).init_from_cap(u)

        self.failUnlessEqual(n.get_writekey(), wk)
        self.failUnlessEqual(n.get_readkey(), rk)
        self.failUnlessEqual(n.get_storage_index(), si)
        # these items are populated on first read (or create), so until that
        # happens they'll be None
        self.failUnlessEqual(n.get_privkey(), None)
        self.failUnlessEqual(n.get_encprivkey(), None)
        self.failUnlessEqual(n.get_pubkey(), None)

        self.failUnlessEqual(n.get_uri(), u.to_string())
        self.failUnlessEqual(n.get_write_uri(), u.to_string())
        self.failUnlessEqual(n.get_readonly_uri(), u.get_readonly().to_string())
        self.failUnlessEqual(n.get_cap(), u)
        self.failUnlessEqual(n.get_readcap(), u.get_readonly())
        self.failUnless(n.is_mutable())
        self.failIf(n.is_readonly())
        self.failIf(n.is_unknown())
        self.failIf(n.is_allowed_in_immutable_directory())
        n.raise_error()

        n2 = MutableFileNode(None, None, client.get_encoding_parameters(),
                             None).init_from_cap(u)
        self.failUnlessEqual(n, n2)
        self.failIfEqual(n, "not even the right type")
        self.failIfEqual(n, u) # not the right class
        n.raise_error()
        d = {n: "can these be used as dictionary keys?"}
        d[n2] = "replace the old one"
        self.failUnlessEqual(len(d), 1)

        nro = n.get_readonly()
        self.failUnless(isinstance(nro, MutableFileNode))

        self.failUnlessEqual(nro.get_readonly(), nro)
        self.failUnlessEqual(nro.get_cap(), u.get_readonly())
        self.failUnlessEqual(nro.get_readcap(), u.get_readonly())
        self.failUnless(nro.is_mutable())
        self.failUnless(nro.is_readonly())
        self.failIf(nro.is_unknown())
        self.failIf(nro.is_allowed_in_immutable_directory())
        nro_u = nro.get_uri()
        self.failUnlessEqual(nro_u, nro.get_readonly_uri())
        self.failUnlessEqual(nro_u, u.get_readonly().to_string())
        self.failUnlessEqual(nro.get_write_uri(), None)
        self.failUnlessEqual(nro.get_repair_cap(), None) # RSAmut needs writecap
        nro.raise_error()

        v = n.get_verify_cap()
        self.failUnless(isinstance(v, uri.SSKVerifierURI))
        self.failUnlessEqual(n.get_repair_cap(), n._uri) # TODO: n.get_uri()

class LiteralChecker(unittest.TestCase):
    def test_literal_filenode(self):
        DATA = "I am a short file."
        u = uri.LiteralFileURI(data=DATA)
        fn1 = LiteralFileNode(u)

        d = fn1.check(Monitor())
        def _check_checker_results(cr):
            self.failUnlessEqual(cr, None)
        d.addCallback(_check_checker_results)

        d.addCallback(lambda res: fn1.check(Monitor(), verify=True))
        d.addCallback(_check_checker_results)

        return d
