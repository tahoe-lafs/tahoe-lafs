
from twisted.trial import unittest
from twisted.internet import defer
from allmydata import uri
from allmydata.immutable import filenode, download
from allmydata.checker_results import CheckerResults, CheckAndRepairResults
from allmydata.mutable.node import MutableFileNode
from allmydata.util import hashutil

class NotANode:
    pass

class Node(unittest.TestCase):
    def test_chk_filenode(self):
        u = uri.CHKFileURI(key="\x00"*16,
                           uri_extension_hash="\x00"*32,
                           needed_shares=3,
                           total_shares=10,
                           size=1000)
        c = None
        fn1 = filenode.FileNode(u, c)
        fn2 = filenode.FileNode(u.to_string(), c)
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

        return d

    def test_mutable_filenode(self):
        client = None
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

class Checker(unittest.TestCase):
    def test_chk_filenode(self):
        u = uri.CHKFileURI(key="\x00"*16,
                           uri_extension_hash="\x00"*32,
                           needed_shares=3,
                           total_shares=10,
                           size=1000)
        c = None
        fn1 = filenode.FileNode(u, c)

        fn1.checker_class = FakeImmutableChecker
        fn1.verifier_class = FakeImmutableVerifier

        d = fn1.check()
        def _check_checker_results(cr):
            self.failUnless(cr.is_healthy())
        d.addCallback(_check_checker_results)

        d.addCallback(lambda res: fn1.check(verify=True))
        d.addCallback(_check_checker_results)

        # TODO: check-and-repair

        return d

    def test_literal_filenode(self):
        DATA = "I am a short file."
        u = uri.LiteralFileURI(data=DATA)
        c = None
        fn1 = filenode.LiteralFileNode(u, c)

        d = fn1.check()
        def _check_checker_results(cr):
            self.failUnlessEqual(cr, None)
        d.addCallback(_check_checker_results)

        d.addCallback(lambda res: fn1.check(verify=True))
        d.addCallback(_check_checker_results)

        return d

    def test_mutable_filenode(self):
        client = None
        wk = "\x00"*16
        fp = "\x00"*32
        rk = hashutil.ssk_readkey_hash(wk)
        si = hashutil.ssk_storage_index_hash(rk)

        u = uri.WriteableSSKFileURI("\x00"*16, "\x00"*32)
        n = MutableFileNode(client).init_from_uri(u)

        n.checker_class = FakeMutableChecker
        n.check_and_repairer_class = FakeMutableCheckAndRepairer

        d = n.check()
        def _check_checker_results(cr):
            self.failUnless(cr.is_healthy())
        d.addCallback(_check_checker_results)

        d.addCallback(lambda res: n.check(verify=True))
        d.addCallback(_check_checker_results)

        return d

class FakeMutableChecker:
    def __init__(self, node):
        self.r = CheckerResults(node.get_storage_index())
        self.r.set_healthy(True)

    def check(self, verify):
        return defer.succeed(self.r)

class FakeMutableCheckAndRepairer:
    def __init__(self, node):
        cr = CheckerResults(node.get_storage_index())
        cr.set_healthy(True)
        self.r = CheckAndRepairResults(node.get_storage_index())
        self.r.pre_repair_results = self.r.post_repair_results = cr

    def check(self, verify):
        return defer.succeed(self.r)

class FakeImmutableChecker:
    def __init__(self, client, storage_index, needed_shares, total_shares):
        self.r = CheckerResults(storage_index)
        self.r.set_healthy(True)

    def start(self):
        return defer.succeed(self.r)

def FakeImmutableVerifier(client,
                          storage_index, needed_shares, total_shares, size,
                          ueb_hash):
    return FakeImmutableChecker(client,
                                storage_index, needed_shares, total_shares)
