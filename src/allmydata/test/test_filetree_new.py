
#from zope.interface import implements
from twisted.trial import unittest
from twisted.internet import defer
#from allmydata.filetree.interfaces import IOpener, IDirectoryNode
#from allmydata.filetree.directory import (ImmutableDirectorySubTree,
#                                          SubTreeNode,
#                                          CHKDirectorySubTree)
#from allmydata.filetree.specification import (CHKFileSpecification,
#                                              CHKDirectorySpecification)
from allmydata import workqueue
from cStringIO import StringIO

"""
class FakeOpener(object):
    implements(IOpener)
    def __init__(self, objects={}):
        self.objects = objects
    def open(self, subtree_specification, parent_is_mutable):
        #print "open", subtree_specification, subtree_specification.serialize(), parent_is_mutable
        return defer.succeed(self.objects[subtree_specification.serialize()])

class FakeWorkQueue(object):
    implements(workqueue.IWorkQueue)
    def __init__(self):
        self.first_commands = []
        self.last_commands = []
        self.tempfile_number = 0
        self.boxname_number = 0
    def dump_commands(self):
        return self.first_commands + self.last_commands
    def clear_commands(self):
        self.first_commands = []
        self.last_commands = []

    def create_tempfile(self, suffix=""):
        self.tempfile_number += 1
        self.first_commands.append("create_tempfile-%d" % self.tempfile_number)
        return (StringIO(), "dummy_filename-%d" % self.tempfile_number)
    def create_boxname(self):
        self.boxname_number += 1
        self.first_commands.append("create_boxname-%d" % self.boxname_number)
        return "dummy_boxname-%d" % self.boxname_number
    def add_upload_chk(self, source_filename, stash_uri_in_boxname):
        self.first_commands.append(("upload_chk", source_filename,
                                    stash_uri_in_boxname))
    def add_upload_ssk(self, source_filename, write_capability,
                       previous_version):
        self.first_commands.append(("upload_ssk", source_filename,
                                    write_capability, previous_version))
    def add_retain_ssk(self, read_capability):
        self.last_commands.append(("retain_ssk", read_capability))
    def add_unlink_ssk(self, write_capability):
        self.last_commands.append(("unlink_ssk", write_capability))
    def add_retain_uri_from_box(self, boxname):
        self.last_commands.append(("retain_uri_from_box", boxname))
    def add_addpath(self, boxname, path):
        self.first_commands.append(("addpath", boxname, path))
    def add_unlink_uri(self, uri):
        self.last_commands.append(("unlink_uri", uri))
    def add_delete_tempfile(self, filename):
        self.first_commands.append(("delete_tempfile", filename))
    def add_delete_box(self, boxname):
        self.last_commands.append(("delete_box", boxname))



class OneSubTree(unittest.TestCase):
    def test_create_empty_immutable(self):
        st = ImmutableDirectorySubTree()
        st.new()
        self.failIf(st.is_mutable())
        d = st.get([], FakeOpener())
        def _got_root(root):
            self.failUnless(IDirectoryNode.providedBy(root))
            self.failUnlessEqual(root.list(), [])
        d.addCallback(_got_root)
        return d

    def test_immutable_1(self):
        st = ImmutableDirectorySubTree()
        st.new()
        # now populate it (by modifying the internal data structures) with
        # some internal directories
        one = SubTreeNode(st)
        two = SubTreeNode(st)
        three = SubTreeNode(st)
        st.root.node_children["one"] = one
        st.root.node_children["two"] = two
        two.node_children["three"] = three

        # now examine it
        self.failIf(st.is_mutable())
        o = FakeOpener()
        d = st.get([], o)
        def _got_root(root):
            self.failUnless(IDirectoryNode.providedBy(root))
            self.failUnlessEqual(root.list(), ["one", "two"])
        d.addCallback(_got_root)
        d.addCallback(lambda res: st.get(["one"], o))
        def _got_one(_one):
            self.failUnlessIdentical(one, _one)
            self.failUnless(IDirectoryNode.providedBy(_one))
            self.failUnlessEqual(_one.list(), [])
        d.addCallback(_got_one)
        d.addCallback(lambda res: st.get(["two"], o))
        def _got_two(_two):
            self.failUnlessIdentical(two, _two)
            self.failUnless(IDirectoryNode.providedBy(_two))
            self.failUnlessEqual(_two.list(), ["three"])
        d.addCallback(_got_two)
        d.addCallback(lambda res: st.get(["two", "three"], o))
        def _got_three(_three):
            self.failUnlessIdentical(three, _three)
            self.failUnless(IDirectoryNode.providedBy(_three))
            self.failUnlessEqual(_three.list(), [])
        d.addCallback(_got_three)
        d.addCallback(lambda res: st.get(["missing"], o))
        d.addCallback(self.failUnlessEqual, None)
        return d

    def test_mutable_1(self):
        o = FakeOpener()
        wq = FakeWorkQueue()
        st = MutableCHKDirectorySubTree()
        st.new()
        st.set_uri(None)
        self.failUnless(st.is_mutable())
        d = st.get([], o)
        def _got_root(root):
            self.failUnless(IDirectoryNode.providedBy(root))
            self.failUnlessEqual(root.list(), [])
        d.addCallback(_got_root)
        file_three = CHKFileSpecification()
        file_three.set_uri("file_three_uri")
        d.addCallback(lambda res: st.add(["one", "two", "three"], file_three,
                                         o, wq))
        d.addCallback(lambda res: st.get(["one"], o))
        def _got_one(one):
            self.failUnless(IDirectoryNode.providedBy(one))
            self.failUnlessEqual(one.list(), ["two"])
        d.addCallback(_got_one)
        d.addCallback(lambda res: st.get(["one", "two"], o))
        def _got_two(two):
            self.failUnless(IDirectoryNode.providedBy(two))
            self.failUnlessEqual(two.list(), ["three"])
            self.failUnlessIdentical(two.child_specifications["three"],
                                     file_three)
        d.addCallback(_got_two)
        return d

    def test_addpath(self):
        o = FakeOpener()
        wq = FakeWorkQueue()
        st = MutableCHKDirectorySubTree()
        st.new()
        st.set_uri(None)
        file_three = CHKFileSpecification()
        file_three.set_uri("file_three_uri")
        d = st.add(["one", "two", "three"], file_three, o, wq)
        def _done(res):
            expected = [
                "create_tempfile-1",
                "create_boxname-1",
                ('upload_chk', 'dummy_filename-1', 'dummy_boxname-1'),
                ('delete_tempfile', 'dummy_filename-1'),
                ('addpath', 'dummy_boxname-1', []),
                ('retain_uri_from_box', 'dummy_boxname-1'),
                ('delete_box', 'dummy_boxname-1'),
                ('unlink_uri', None),
                ]
            self.failUnlessEqual(wq.dump_commands(), expected)
            #print
            #for c in wq.dump_commands():
            #    print c
        d.addCallback(_done)
        return d

    def test_serialize(self):
        st = ImmutableDirectorySubTree()
        st.new()
        one = SubTreeNode(st)
        two = SubTreeNode(st)
        three = SubTreeNode(st)
        st.root.node_children["one"] = one
        st.root.node_children["two"] = two
        two.node_children["three"] = three
        file_four = CHKFileSpecification()
        file_four.set_uri("file_four_uri")
        two.child_specifications["four"] = file_four
        data = st.serialize()
        st_new = ImmutableDirectorySubTree()
        st_new.unserialize(data)

        st_four = ImmutableDirectorySubTree()
        st_four.new()
        st_four.root.node_children["five"] = SubTreeNode(st_four)

        o = FakeOpener({("CHK-File", "file_four_uri"): st_four})
        d = st.get([], o)
        def _got_root(root):
            self.failUnless(IDirectoryNode.providedBy(root))
            self.failUnlessEqual(root.list(), ["one", "two"])
        d.addCallback(_got_root)
        d.addCallback(lambda res: st.get(["two"], o))
        def _got_two(_two):
            self.failUnless(IDirectoryNode.providedBy(_two))
            self.failUnlessEqual(_two.list(), ["four", "three"])
        d.addCallback(_got_two)

        d.addCallback(lambda res: st.get(["two", "four"], o))
        def _got_four(_four):
            self.failUnless(IDirectoryNode.providedBy(_four))
            self.failUnlessEqual(_four.list(), ["five"])
        d.addCallback(_got_four)

class MultipleSubTrees(unittest.TestCase):

    def test_open(self):
        st = ImmutableDirectorySubTree()
        st.new()
        # populate it with some internal directories and child links and see
        # if we can follow them
        one = SubTreeNode(st)
        two = SubTreeNode(st)
        three = SubTreeNode(st)
        st.root.node_children["one"] = one
        st.root.node_children["two"] = two
        two.node_children["three"] = three

    def test_addpath(self):
        wq = FakeWorkQueue()
        st1 = MutableCHKDirectorySubTree()
        st1.new()
        st1.set_uri(None)
        one = SubTreeNode(st1)
        two = SubTreeNode(st1)
        st1.root.node_children["one"] = one
        one.node_children["two"] = two
        three = CHKDirectorySpecification()
        three.set_uri("dir_three_uri")
        two.child_specifications["three"] = three

        st2 = MutableCHKDirectorySubTree()
        st2.new()
        st2.set_uri(None)
        four = SubTreeNode(st2)
        five = SubTreeNode(st2)
        st2.root.node_children["four"] = four
        four.node_children["five"] = five

        file_six = CHKFileSpecification()
        file_six.set_uri("file_six_uri")

        o = FakeOpener({("CHK-Directory", "dir_three_uri"): st2})

        d = defer.succeed(None)
        d.addCallback(lambda res:
                      st1.get(["one", "two", "three", "four", "five"], o))
        def _got_five(res):
            self.failUnless(IDirectoryNode.providedBy(res))
            self.failUnlessIdentical(res, five)
        d.addCallback(_got_five)

        d.addCallback(lambda res:
                      st1.add(["one", "two", "six"],
                              file_six, o, wq))
        def _done(res):
            expected = [
                "create_tempfile-1",
                "create_boxname-1",
                ('upload_chk', 'dummy_filename-1', 'dummy_boxname-1'),
                ('delete_tempfile', 'dummy_filename-1'),
                # one/two/six only modifies the top-most CHKDirectory, so
                # the addpath that gets scheduled is targeted at the root
                ('addpath', 'dummy_boxname-1', []),
                ('retain_uri_from_box', 'dummy_boxname-1'),
                ('delete_box', 'dummy_boxname-1'),
                ('unlink_uri', None),
                ]
            self.failUnlessEqual(wq.dump_commands(), expected)
            wq.clear_commands()
        d.addCallback(_done)

        d.addCallback(lambda res:
                      st1.add(["one", "two", "three", "four", "six"],
                              file_six, o, wq))
        def _done2(res):
            expected = [
                "create_tempfile-2",
                "create_boxname-2",
                ('upload_chk', 'dummy_filename-2', 'dummy_boxname-2'),
                ('delete_tempfile', 'dummy_filename-2'),
                # one/two/three/four/six modifies the lower CHKDirectory, so
                # we schedule an addpath of the link that points from the
                # upper CHKDirectory to the lower one (at one/two/three).
                ('addpath', 'dummy_boxname-2', ["one", "two", "three"]),
                ('retain_uri_from_box', 'dummy_boxname-2'),
                ('delete_box', 'dummy_boxname-2'),
                ('unlink_uri', None),
                ]
            self.failUnlessEqual(wq.dump_commands(), expected)
        d.addCallback(_done2)


        return d

del OneSubTree
del MultipleSubTrees

class Redirect(unittest.TestCase):
    pass
"""

import os.path
from allmydata.filetree import directory, redirect, vdrive
from allmydata.filetree.interfaces import (ISubTree, INode, IDirectoryNode, IFileNode)
from allmydata.filetree.file import CHKFileNode
from allmydata.util import bencode

class InPairs(unittest.TestCase):
    def test_in_pairs(self):
        l = range(8)
        pairs = list(directory.in_pairs(l))
        self.failUnlessEqual(pairs, [(0,1), (2,3), (4,5), (6,7)])

class Stuff(unittest.TestCase):

    def makeVirtualDrive(self, basedir, root_node=None):
        wq = workqueue.WorkQueue(os.path.join(basedir, "1.workqueue"))
        dl = None
        if not root_node:
            root_node = directory.LocalFileSubTreeNode()
            root_node.new("rootdirtree.save")
        v = vdrive.VirtualDrive(wq, dl, root_node)
        return v

    def failUnlessListsAreEqual(self, list1, list2):
        self.failUnlessEqual(sorted(list1), sorted(list2))

    def failUnlessContentsAreEqual(self, c1, c2):
        c1a = dict([(k,v.serialize_node()) for k,v in c1.items()])
        c2a = dict([(k,v.serialize_node()) for k,v in c2.items()])
        self.failUnlessEqual(c1a, c2a)

    def testDirectory(self):
        stm = vdrive.SubTreeMaker(None, None)

        # create an empty directory (stored locally)
        subtree = directory.LocalFileSubTree()
        subtree.new("dirtree.save")
        self.failUnless(ISubTree.providedBy(subtree))

        # get the root IDirectoryNode (which is still empty) and examine it
        (found_path, root, remaining_path) = subtree.get_node_for_path([])
        self.failUnlessEqual(found_path, [])
        self.failUnlessEqual(remaining_path, [])
        self.failUnless(INode.providedBy(root))
        self.failUnless(IDirectoryNode.providedBy(root))
        self.failUnlessListsAreEqual(root.list().keys(), [])
        self.failUnlessIdentical(root.get_subtree(), subtree)

        # now add some children to it
        subdir1 = root.add_subdir("subdir1")
        file1 = CHKFileNode()
        file1.new("uri1")
        root.add("foo.txt", file1)
        self.failUnlessListsAreEqual(root.list().keys(),
                                     ["foo.txt", "subdir1"])
        self.failUnlessIdentical(root.get("foo.txt"), file1)
        subdir1a = root.get("subdir1")
        self.failUnlessIdentical(subdir1, subdir1a)
        del subdir1a
        self.failUnless(IDirectoryNode.providedBy(subdir1))
        self.failUnlessListsAreEqual(subdir1.list().keys(), [])
        self.failUnlessIdentical(subdir1.get_subtree(), subtree)

        subdir2 = subdir1.add_subdir("subdir2")
        subdir3 = subdir2.add_subdir("subdir3")
        subdir4 = subdir2.add_subdir("subdir4")

        subdir2.delete("subdir4")
        self.failUnlessListsAreEqual(subdir2.list().keys(), ["subdir3"])

        del root, subdir1, subdir2, subdir3, subdir4
        # leaving file1 for later use

        # now serialize it and examine the results
        f = StringIO()
        subtree.serialize_subtree_to_file(f)
        data = f.getvalue()
        #print data
        unpacked = bencode.bdecode(data)
        #print unpacked
        del f, data, unpacked

        node = subtree.create_node_now()
        self.failUnless(isinstance(node, directory.LocalFileSubTreeNode))
        node_s = node.serialize_node()
        self.failUnless(isinstance(node_s, str))
        self.failUnless(node_s.startswith("LocalFileDirectory:"))
        self.failUnless("dirtree.save" in node_s)
        del node, node_s

        d = defer.maybeDeferred(subtree.update_now, None)
        def _updated(node):
            # now reconstruct it
            return stm.make_subtree_from_node(node, False)
        d.addCallback(_updated)

        def _opened(new_subtree):
            res = new_subtree.get_node_for_path([])
            (found_path, root, remaining_path) = res
            self.failUnlessEqual(found_path, [])
            self.failUnlessEqual(remaining_path, [])
            self.failUnless(INode.providedBy(root))
            self.failUnless(IDirectoryNode.providedBy(root))
            self.failUnlessListsAreEqual(root.list().keys(),
                                         ["foo.txt", "subdir1"])
            file1a = root.get("foo.txt")
            self.failUnless(INode(file1a))
            self.failUnless(isinstance(file1a, CHKFileNode))
            self.failUnless(IFileNode(file1a))
            self.failUnlessEqual(file1a.get_uri(), "uri1")
            subdir1 = root.get("subdir1")
            subdir2 = subdir1.get("subdir2")
            self.failUnlessListsAreEqual(subdir2.list().keys(), ["subdir3"])
            subdir2.delete("subdir3")
            self.failUnlessListsAreEqual(subdir2.list().keys(), [])
        d.addCallback(_opened)
        return d

    def testVdrive(self):
        topdir = directory.LocalFileSubTree().new("vdrive-dirtree.save")
        topdir.update_now(None)
        root = redirect.LocalFileRedirection().new("vdrive-root",
                                                   topdir.create_node_now())
        root.update_now(None)
        v = self.makeVirtualDrive("vdrive", root.create_node_now())

        d = v.list([])
        def _listed(contents):
            self.failUnlessEqual(contents, {})
        d.addCallback(_listed)

        child1 = CHKFileNode().new("uri1")
        d.addCallback(lambda res: v.add_node(["a"], child1))
        d.addCallback(lambda res: v.workqueue.flush())
        d.addCallback(lambda res: v.list([]))
        def _listed2(contents):
            self.failUnlessListsAreEqual(contents.keys(), ["a"])
            self.failUnlessContentsAreEqual(contents, {"a": child1})
        d.addCallback(_listed2)
        child2 = CHKFileNode().new("uri2")
        child3 = CHKFileNode().new("uri3")
        d.addCallback(lambda res: v.add_node(["b","c"], child2))
        d.addCallback(lambda res: v.add_node(["b","d"], child3))
        d.addCallback(lambda res: v.workqueue.flush())
        d.addCallback(lambda res: v.list([]))
        def _listed3(contents):
            self.failUnlessListsAreEqual(contents.keys(), ["a","b"])
        d.addCallback(_listed3)
        d.addCallback(lambda res: v.list(["b"]))
        def _listed4(contents):
            self.failUnlessListsAreEqual(contents.keys(), ["c","d"])
            self.failUnlessContentsAreEqual(contents,
                                            {"c": child2, "d": child3})
        d.addCallback(_listed4)

        return d

