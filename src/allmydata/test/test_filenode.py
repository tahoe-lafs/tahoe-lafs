
from twisted.trial import unittest
from allmydata import filenode, uri, download

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
        self.failUnlessEqual(fn1.get_readonly_uri(), u.to_string())
        self.failUnlessEqual(fn1.get_size(), 1000)
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
        self.failUnlessEqual(fn1.get_readonly_uri(), u.to_string())
        self.failUnlessEqual(fn1.get_size(), len(DATA))
        d = {}
        d[fn1] = 1 # exercise __hash__

        v = fn1.get_verifier()
        self.failUnlessEqual(v, None)

        self.failUnlessEqual(fn1.check(), None)
        target = download.Data()
        d = fn1.download(target)
        def _check(res):
            self.failUnlessEqual(res, DATA)
        d.addCallback(_check)

        d.addCallback(lambda res: fn1.download_to_data())
        d.addCallback(_check)
        return d

