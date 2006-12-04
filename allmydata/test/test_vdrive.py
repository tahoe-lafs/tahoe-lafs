
import os
from twisted.trial import unittest
from twisted.internet import defer
from allmydata import vdrive, filetable

class LocalDirNode(filetable.MutableDirectoryNode):
    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        return defer.maybeDeferred(_call)


class Traverse(unittest.TestCase):
    def make_tree(self, basedir):
        os.makedirs(basedir)
        root = LocalDirNode(basedir)
        self.d1 = d1 = root.add_directory("d1")
        self.d2 = d2 = root.add_directory("d2")
        root.add_file("a", "a")
        root.add_file("b", "b")
        d1.add_file("1.a", "1.a")
        d1.add_file("1.b", "1.b")
        d2.add_file("2.a", "2.a")
        d2.add_file("2.b", "2.b")
        return root

    def test_one(self):
        basedir = "test_vdrive/one"
        root = self.make_tree(basedir)
        v = vdrive.VDrive()
        v.set_root(root)

        d = v.get_dir("")
        d.addCallback(lambda dir: self.failUnlessEqual(dir, root))
        d.addCallback(lambda res: v.get_dir("/d1"))
        def _check(dir):
            self.failUnless(isinstance(dir, LocalDirNode))
            self.failUnlessEqual(dir._basedir, self.d1._basedir)
        d.addCallback(_check)

        
        d.addCallback(lambda res: v.listdir(""))
        d.addCallback(lambda files:
                      self.failUnlessEqual(sorted(files),
                                           ["a", "b", "d1", "d2"]))
        d.addCallback(lambda res: v.listdir("/"))
        d.addCallback(lambda files:
                      self.failUnlessEqual(sorted(files),
                                           ["a", "b", "d1", "d2"]))
        d.addCallback(lambda res: v.listdir("d1"))
        d.addCallback(lambda files:
                      self.failUnlessEqual(sorted(files),
                                           ["1.a", "1.b"]))

        d.addCallback(lambda res: v.make_directory("", "d3"))
        d.addCallback(lambda res: v.listdir(""))
        d.addCallback(lambda files:
                      self.failUnlessEqual(sorted(files),
                                           ["a", "b", "d1", "d2", "d3"]))

        d.addCallback(lambda res: v.make_directory("d2", "d2.1"))
        d.addCallback(lambda res: v.listdir("/d2"))
        d.addCallback(lambda files:
                      self.failUnlessEqual(sorted(files),
                                           ["2.a", "2.b", "d2.1"]))
        return d

