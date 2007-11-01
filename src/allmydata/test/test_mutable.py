
from twisted.trial import unittest
from twisted.internet import defer

from allmydata import mutable, uri
from allmydata.mutable import split_netstring
from allmydata.util.hashutil import netstring

class Netstring(unittest.TestCase):
    def test_split(self):
        a = netstring("hello") + netstring("world")
        self.failUnlessEqual(split_netstring(a, 2), ("hello", "world"))
        self.failUnlessEqual(split_netstring(a, 2, False), ("hello", "world"))
        self.failUnlessEqual(split_netstring(a, 2, True),
                             ("hello", "world", ""))
        self.failUnlessRaises(ValueError, split_netstring, a+" extra", 2)
        self.failUnlessRaises(ValueError, split_netstring, a+" extra", 2, False)

    def test_extra(self):
        a = netstring("hello")
        self.failUnlessEqual(split_netstring(a, 1, True), ("hello", ""))
        b = netstring("hello") + "extra stuff"
        self.failUnlessEqual(split_netstring(b, 1, True),
                             ("hello", "extra stuff"))

    def test_nested(self):
        a = netstring("hello") + netstring("world") + "extra stuff"
        b = netstring("a") + netstring("is") + netstring(a) + netstring(".")
        top = split_netstring(b, 4)
        self.failUnlessEqual(len(top), 4)
        self.failUnlessEqual(top[0], "a")
        self.failUnlessEqual(top[1], "is")
        self.failUnlessEqual(top[2], a)
        self.failUnlessEqual(top[3], ".")
        self.failUnlessRaises(ValueError, split_netstring, a, 2)
        self.failUnlessRaises(ValueError, split_netstring, a, 2, False)
        bottom = split_netstring(a, 2, True)
        self.failUnlessEqual(bottom, ("hello", "world", "extra stuff"))

class FakeFilenode(mutable.MutableFileNode):
    def init_from_uri(self, myuri):
        self._uri = myuri
        self.writekey = myuri.writekey
        return self
    def create(self, initial_contents):
        self.contents = initial_contents
        self.init_from_uri(uri.WriteableSSKFileURI("key", "fingerprint"))
        return defer.succeed(None)
    def download_to_data(self):
        return defer.succeed(self.contents)
    def replace(self, newdata):
        self.contents = newdata
        return defer.succeed(None)
    def is_readonly(self):
        return False
    def get_readonly(self):
        return "fake readonly"

class FakeNewDirectoryNode(mutable.NewDirectoryNode):
    filenode_class = FakeFilenode

class MyClient:
    def __init__(self):
        pass

    def create_empty_dirnode(self):
        n = FakeNewDirectoryNode(self)
        d = n.create()
        d.addCallback(lambda res: n)
        return d

    def create_dirnode_from_uri(self, u):
        return FakeNewDirectoryNode(self).init_from_uri(u)

    def create_mutable_file(self, contents=""):
        n = FakeFilenode(self)
        d = n.create(contents)
        d.addCallback(lambda res: n)
        return d
    def create_mutable_file_from_uri(self, u):
        return FakeFilenode(self).init_from_uri(u)


class Filenode(unittest.TestCase):
    def setUp(self):
        self.client = MyClient()

    def test_create(self):
        d = self.client.create_mutable_file()
        def _created(n):
            d = n.replace("contents 1")
            d.addCallback(lambda res: self.failUnlessIdentical(res, None))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.replace("contents 2"))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d

    def test_create_with_initial_contents(self):
        d = self.client.create_mutable_file("contents 1")
        def _created(n):
            d = n.download_to_data()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.replace("contents 2"))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d

class Dirnode(unittest.TestCase):
    def setUp(self):
        self.client = MyClient()

    def test_create(self):
        d = self.client.create_empty_dirnode()
        def _check(n):
            self.failUnless(n.is_mutable())
            u = n.get_uri()
            self.failUnless(u)
            self.failUnless(u.startswith("URI:DIR2:"), u)
            u_ro = n.get_immutable_uri()
            self.failUnless(u_ro.startswith("URI:DIR2-RO:"), u_ro)
            u_v = n.get_verifier()
            self.failUnless(u_v.startswith("URI:DIR2-Verifier:"), u_v)

            d = n.list()
            d.addCallback(lambda res: self.failUnlessEqual(res, {}))
            d.addCallback(lambda res: n.has_child("missing"))
            d.addCallback(lambda res: self.failIf(res))
            fake_file_uri = uri.WriteableSSKFileURI("a"*16,"b"*32)
            d.addCallback(lambda res: n.set_uri("child", fake_file_uri))
            d.addCallback(lambda res: self.failUnlessEqual(res, None))
            d.addCallback(lambda res: n.list())
            def _check_list(children):
                self.failUnless("child" in children)
            d.addCallback(_check_list)

            return d

        d.addCallback(_check)

        return d

