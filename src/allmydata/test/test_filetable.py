
import os
from twisted.trial import unittest
from allmydata.filetable import (MutableDirectoryNode,
                                 BadDirectoryError, BadFileError, BadNameError)


class FileTable(unittest.TestCase):
    def test_files(self):
        os.mkdir("filetable")
        root = MutableDirectoryNode(os.path.abspath("filetable"), "root")
        self.failUnlessEqual(root.list(), [])
        root.add_file("one", "vid-one")
        root.add_file("two", "vid-two")
        self.failUnlessEqual(root.list(), [("one", "vid-one"),
                                           ("two", "vid-two")])
        root.remove("two")
        self.failUnlessEqual(root.list(), [("one", "vid-one")])
        self.failUnlessRaises(BadFileError, root.remove, "two")
        self.failUnlessRaises(BadFileError, root.remove, "three")

        self.failUnlessEqual(root.get("one"), "vid-one")
        self.failUnlessRaises(BadFileError, root.get, "missing")
        self.failUnlessRaises(BadNameError, root.get, "/etc/passwd") # evil
        self.failUnlessRaises(BadNameError, root.get, "..") # sneaky
        self.failUnlessRaises(BadNameError, root.get, ".") # dumb

        # now play with directories
        subdir1 = root.add_directory("subdir1")
        self.failUnless(isinstance(subdir1, MutableDirectoryNode))
        subdir1a = root.get("subdir1")
        self.failUnless(isinstance(subdir1a, MutableDirectoryNode))
        self.failUnlessEqual(subdir1a._basedir, subdir1._basedir)
        entries = root.list()
        self.failUnlessEqual(len(entries), 2)
        one_index = entries.index( ("one", "vid-one") )
        subdir_index = 1 - one_index
        self.failUnlessEqual(entries[subdir_index][0], "subdir1")
        subdir2 = entries[subdir_index][1]
        self.failUnless(isinstance(subdir2, MutableDirectoryNode))

        self.failUnlessEqual(subdir1.list(), [])
        self.failUnlessEqual(subdir2.list(), [])

        subdir1.add_file("subone", "vid-subone")
        self.failUnlessEqual(subdir1.list(), [("subone", "vid-subone")])
        self.failUnlessEqual(subdir2.list(), [("subone", "vid-subone")])

        self.failUnlessEqual(len(root.list()), 2)

        self.failUnlessRaises(BadDirectoryError, root.add_directory, "subdir1")
        self.failUnlessRaises(BadDirectoryError, root.add_directory, "one")

        root.remove("subdir1")
        self.failUnlessEqual(root.list(), [("one", "vid-one")])



