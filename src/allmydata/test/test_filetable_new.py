
from zope.interface import implements
from twisted.trial import unittest
from twisted.internet import defer
from allmydata import filetable_new as ft
from allmydata import workqueue
from cStringIO import StringIO

class FakeOpener(object):
    implements(ft.IOpener)
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

    def test_addpath(self):
        o = FakeOpener()
        wq = FakeWorkQueue()
        st = ft.MutableCHKDirectorySubTree()
        st.new()
        st.set_uri(None)
        file_three = ft.CHKFileSpecification()
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
        st = ft.ImmutableDirectorySubTree()
        st.new()
        one = ft.SubTreeNode(st)
        two = ft.SubTreeNode(st)
        three = ft.SubTreeNode(st)
        st.root.node_children["one"] = one
        st.root.node_children["two"] = two
        two.node_children["three"] = three
        file_four = ft.CHKFileSpecification()
        file_four.set_uri("file_four_uri")
        two.child_specifications["four"] = file_four
        data = st.serialize()
        st_new = ft.ImmutableDirectorySubTree()
        st_new.unserialize(data)

        st_four = ft.ImmutableDirectorySubTree()
        st_four.new()
        st_four.root.node_children["five"] = ft.SubTreeNode(st_four)

        o = FakeOpener({("CHK-File", "file_four_uri"): st_four})
        d = st.get([], o)
        def _got_root(root):
            self.failUnless(ft.IDirectoryNode.providedBy(root))
            self.failUnlessEqual(root.list(), ["one", "two"])
        d.addCallback(_got_root)
        d.addCallback(lambda res: st.get(["two"], o))
        def _got_two(_two):
            self.failUnless(ft.IDirectoryNode.providedBy(_two))
            self.failUnlessEqual(_two.list(), ["four", "three"])
        d.addCallback(_got_two)

        d.addCallback(lambda res: st.get(["two", "four"], o))
        def _got_four(_four):
            self.failUnless(ft.IDirectoryNode.providedBy(_four))
            self.failUnlessEqual(_four.list(), ["five"])
        d.addCallback(_got_four)

class MultipleSubTrees(unittest.TestCase):

    def test_open(self):
        st = ft.ImmutableDirectorySubTree()
        st.new()
        # populate it with some internal directories and child links and see
        # if we can follow them
        one = ft.SubTreeNode(st)
        two = ft.SubTreeNode(st)
        three = ft.SubTreeNode(st)
        st.root.node_children["one"] = one
        st.root.node_children["two"] = two
        two.node_children["three"] = three

    def test_addpath(self):
        wq = FakeWorkQueue()
        st1 = ft.MutableCHKDirectorySubTree()
        st1.new()
        st1.set_uri(None)
        one = ft.SubTreeNode(st1)
        two = ft.SubTreeNode(st1)
        st1.root.node_children["one"] = one
        one.node_children["two"] = two
        three = ft.CHKDirectorySpecification()
        three.set_uri("dir_three_uri")
        two.child_specifications["three"] = three

        st2 = ft.MutableCHKDirectorySubTree()
        st2.new()
        st2.set_uri(None)
        four = ft.SubTreeNode(st2)
        five = ft.SubTreeNode(st2)
        st2.root.node_children["four"] = four
        four.node_children["five"] = five

        file_six = ft.CHKFileSpecification()
        file_six.set_uri("file_six_uri")

        o = FakeOpener({("CHK-Directory", "dir_three_uri"): st2})

        d = defer.succeed(None)
        #d.addCallback(lambda res:
        #              st1.get(["one", "two", "three", "four", "five"], o))
        def _got_five(res):
            self.failUnless(ft.IDirectoryNode.providedBy(res))
            self.failUnlessIdentical(res, five)
        #d.addCallback(_got_five)

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
