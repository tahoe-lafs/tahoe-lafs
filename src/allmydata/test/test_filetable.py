
import os
from twisted.trial import unittest
from allmydata.filetable import (MutableDirectoryNode,
                                 BadFileError, BadNameError)
from allmydata.vdrive import FileNode, DirectoryNode


class FileTable(unittest.TestCase):
    def test_files(self):
        os.mkdir("filetable")
        basedir = os.path.abspath("filetable")
        root = MutableDirectoryNode(basedir, "root")
        self.failUnlessEqual(root.list(), [])
        root.add("one", FileNode("vid-one"))
        root.add("two", FileNode("vid-two"))
        self.failUnlessEqual(root.list(), [("one", FileNode("vid-one")),
                                           ("two", FileNode("vid-two"))])
        root.remove("two")
        self.failUnlessEqual(root.list(), [("one", FileNode("vid-one"))])
        self.failUnlessRaises(BadFileError, root.remove, "two")
        self.failUnlessRaises(BadFileError, root.remove, "three")

        self.failUnlessEqual(root.get("one"), FileNode("vid-one"))
        self.failUnlessRaises(BadFileError, root.get, "missing")
        self.failUnlessRaises(BadNameError, root.get, "/etc/passwd") # evil
        self.failUnlessRaises(BadNameError, root.get, "..") # sneaky
        self.failUnlessRaises(BadNameError, root.get, ".") # dumb

        # now play with directories
        subdir1 = root.add("subdir1", DirectoryNode("subdir1.furl"))
        self.failUnless(isinstance(subdir1, DirectoryNode))
        subdir1a = root.get("subdir1")
        self.failUnless(isinstance(subdir1a, DirectoryNode))
        self.failUnlessEqual(subdir1a, subdir1)
        entries = root.list()
        self.failUnlessEqual(len(entries), 2)
        one_index = entries.index( ("one", FileNode("vid-one")) )
        subdir_index = 1 - one_index
        self.failUnlessEqual(entries[subdir_index][0], "subdir1")
        subdir2 = entries[subdir_index][1]
        self.failUnless(isinstance(subdir2, DirectoryNode))

        self.failUnlessEqual(len(root.list()), 2)

        self.failUnlessRaises(BadNameError, # replacing an existing child
                              root.add,
                              "subdir1", DirectoryNode("subdir1.furl"))

        root.remove("subdir1")
        self.failUnlessEqual(root.list(), [("one", FileNode("vid-one"))])



