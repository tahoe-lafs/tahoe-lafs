
import os
from zope.interface import implements
from twisted.trial import unittest
from allmydata import filetable_new as ft
from allmydata import workqueue
from cStringIO import StringIO

class FakeOpener(object):
    implements(ft.IOpener)

class FakeWorkQueue(object):
    implements(workqueue.IWorkQueue)
    def create_tempfile(self):
        return (StringIO(), "dummy_filename")
    def create_boxname(self):
        return "dummy_boxname"
    def add_upload_chk(self, source_filename, stash_uri_in_boxname):
        pass
    def add_upload_ssk(self, source_filename, write_capability,
                       previous_version):
        pass
    def add_retain_ssk(self, read_capability):
        pass
    def add_unlink_ssk(self, write_capability):
        pass
    def add_retain_uri_from_box(self, boxname):
        pass
    def add_addpath(self, boxname, path):
        pass
    def add_unlink_uri(self, uri):
        pass
    def add_delete_tempfile(self, filename):
        pass
    def add_delete_box(self, boxname):
        pass


class OneSubTree(unittest.TestCase):
    def test_create_empty_immutable(self):
        st = ft.ImmutableDirectorySubTree()
        st.new()
        self.failIf(st.is_mutable())
        d = st.get([], FakeOpener())
        def _got_root(root):
            self.failUnless(ft.IDirectoryNode.providedBy(root))
            self.failUnlessEqual(root.list(), [])
        d.addCallback(_got_root)
        return d

    def test_immutable_1(self):
        st = ft.ImmutableDirectorySubTree()
        st.new()
        # now populate it (by modifying the internal data structures) with
        # some internal directories
        one = ft.SubTreeNode(st)
        two = ft.SubTreeNode(st)
        three = ft.SubTreeNode(st)
        st.root.node_children["one"] = one
        st.root.node_children["two"] = two
        two.node_children["three"] = three

        # now examine it
        self.failIf(st.is_mutable())
        o = FakeOpener()
        d = st.get([], o)
        def _got_root(root):
            self.failUnless(ft.IDirectoryNode.providedBy(root))
            self.failUnlessEqual(root.list(), ["one", "two"])
        d.addCallback(_got_root)
        d.addCallback(lambda res: st.get(["one"], o))
        def _got_one(_one):
            self.failUnlessIdentical(one, _one)
            self.failUnless(ft.IDirectoryNode.providedBy(_one))
            self.failUnlessEqual(_one.list(), [])
        d.addCallback(_got_one)
        d.addCallback(lambda res: st.get(["two"], o))
        def _got_two(_two):
            self.failUnlessIdentical(two, _two)
            self.failUnless(ft.IDirectoryNode.providedBy(_two))
            self.failUnlessEqual(_two.list(), ["three"])
        d.addCallback(_got_two)
        d.addCallback(lambda res: st.get(["two", "three"], o))
        def _got_three(_three):
            self.failUnlessIdentical(three, _three)
            self.failUnless(ft.IDirectoryNode.providedBy(_three))
            self.failUnlessEqual(_three.list(), [])
        d.addCallback(_got_three)
        d.addCallback(lambda res: st.get(["missing"], o))
        d.addCallback(self.failUnlessEqual, None)
        return d

    def test_mutable_1(self):
        o = FakeOpener()
        wq = FakeWorkQueue()
        st = ft.MutableCHKDirectorySubTree()
        st.new()
        st.set_uri(None)
        self.failUnless(st.is_mutable())
        d = st.get([], o)
        def _got_root(root):
            self.failUnless(ft.IDirectoryNode.providedBy(root))
            self.failUnlessEqual(root.list(), [])
        d.addCallback(_got_root)
        file_three = ft.CHKFileSpecification()
        file_three.set_uri("file_three_uri")
        d.addCallback(lambda res: st.add(["one", "two", "three"], file_three,
                                         o, wq))
        d.addCallback(lambda res: st.get(["one"], o))
        def _got_one(one):
            self.failUnless(ft.IDirectoryNode.providedBy(one))
            self.failUnlessEqual(one.list(), ["two"])
        d.addCallback(_got_one)
        d.addCallback(lambda res: st.get(["one", "two"], o))
        def _got_two(two):
            self.failUnless(ft.IDirectoryNode.providedBy(two))
            self.failUnlessEqual(two.list(), ["three"])
            self.failUnlessIdentical(two.child_specifications["three"],
                                     file_three)
        d.addCallback(_got_two)
        return d

