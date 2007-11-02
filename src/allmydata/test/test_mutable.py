
import itertools
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
    counter = itertools.count(1)
    all_contents = {}

    def init_from_uri(self, myuri):
        self._uri = myuri
        self.writekey = myuri.writekey
        return self
    def create(self, initial_contents):
        count = self.counter.next()
        self.init_from_uri(uri.WriteableSSKFileURI("key%d" % count,
                                                   "fingerprint%d" % count))
        self.all_contents[self._uri] = initial_contents
        return defer.succeed(None)
    def download_to_data(self):
        return defer.succeed(self.all_contents[self._uri])
    def replace(self, newdata):
        self.all_contents[self._uri] = newdata
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
        self.expected_manifest = []

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
            self.expected_manifest.append(u_v)

            d = n.list()
            d.addCallback(lambda res: self.failUnlessEqual(res, {}))
            d.addCallback(lambda res: n.has_child("missing"))
            d.addCallback(lambda res: self.failIf(res))
            fake_file_uri = uri.WriteableSSKFileURI("a"*16,"b"*32)
            ffu_v = fake_file_uri.get_verifier().to_string()
            self.expected_manifest.append(ffu_v)
            d.addCallback(lambda res: n.set_uri("child", fake_file_uri))
            d.addCallback(lambda res: self.failUnlessEqual(res, None))

            d.addCallback(lambda res: n.create_empty_directory("subdir"))
            def _created(subdir):
                self.failUnless(isinstance(subdir, FakeNewDirectoryNode))
                self.subdir = subdir
                new_v = subdir.get_verifier()
                self.expected_manifest.append(new_v)
            d.addCallback(_created)

            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted(["child", "subdir"])))

            d.addCallback(lambda res: n.build_manifest())
            def _check_manifest(manifest):
                self.failUnlessEqual(sorted(manifest),
                                     sorted(self.expected_manifest))
            d.addCallback(_check_manifest)

            def _add_subsubdir(res):
                return self.subdir.create_empty_directory("subsubdir")
            d.addCallback(_add_subsubdir)
            d.addCallback(lambda res: n.get_child_at_path("subdir/subsubdir"))
            d.addCallback(lambda subsubdir:
                          self.failUnless(isinstance(subsubdir,
                                                     FakeNewDirectoryNode)))
            d.addCallback(lambda res: n.get_child_at_path(""))
            d.addCallback(lambda res: self.failUnlessEqual(res.get_uri(),
                                                           n.get_uri()))

            d.addCallback(lambda res: n.get_metadata_for("child"))
            d.addCallback(lambda metadata: self.failUnlessEqual(metadata, {}))

            d.addCallback(lambda res: n.delete("subdir"))
            d.addCallback(lambda old_child:
                          self.failUnlessEqual(old_child.get_uri(),
                                               self.subdir.get_uri()))

            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted(["child"])))

            return d

        d.addCallback(_check)

        return d

