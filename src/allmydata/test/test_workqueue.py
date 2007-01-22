
import os
from twisted.trial import unittest
from twisted.internet import defer
from allmydata import workqueue
from allmydata.util import idlib
from allmydata.filetree.file import CHKFileNode

class FakeWorkQueue(workqueue.WorkQueue):

    def __init__(self, basedir):
        workqueue.WorkQueue.__init__(self, basedir)
        self.dispatched_steps = []

    def dispatch_step(self, steptype, lines):
        self.dispatched_steps.append((steptype, lines))
        return defer.succeed(None)

class Reuse(unittest.TestCase):
    def wq(self, testname):
        return FakeWorkQueue("test_workqueue/Reuse/%s/workqueue" % testname)

    def testOne(self):
        wq = self.wq("testOne")
        # steps must be retained from one session to the next
        wq.add_upload_chk("source_filename", "box1")
        wq.add_unlink_uri("someuri")
        # files in the tmpdir are not: these are either in the process of
        # being added or in the process of being removed.
        tmpfile = os.path.join(wq.tmpdir, "foo")
        f = open(tmpfile, "w")
        f.write("foo")
        f.close()
        # files created with create_tempfile *are* retained, however
        f, filename = wq.create_tempfile()
        filename = os.path.join(wq.filesdir, filename)
        f.write("bar")
        f.close()

        del wq
        wq2 = self.wq("testOne")
        steps = wq2.get_all_steps()
        self.failUnlessEqual(steps[0], ("upload_chk",
                                        ["source_filename", "box1"]))
        self.failUnlessEqual(steps[1], ("unlink_uri", ["someuri"]))
        self.failIf(os.path.exists(tmpfile))
        self.failUnless(os.path.exists(filename))


class Items(unittest.TestCase):
    def wq(self, testname):
        return FakeWorkQueue("test_workqueue/Items/%s/workqueue" % testname)

    def testTempfile(self):
        wq = self.wq("testTempfile")
        (f, filename) = wq.create_tempfile(".chkdir")
        self.failUnless(filename.endswith(".chkdir"))
        data = "this is some random data: %s\n" % idlib.b2a(os.urandom(15))
        f.write(data)
        f.close()
        f2 = wq.open_tempfile(filename)
        data2 = f2.read()
        f2.close()
        self.failUnlessEqual(data, data2)

    def testBox(self):
        wq = self.wq("testBox")
        boxname = wq.create_boxname()
        wq.write_to_box(boxname, CHKFileNode().new("uri goes here"))
        out = wq.read_from_box(boxname)
        self.failUnless(isinstance(out, CHKFileNode))
        self.failUnlessEqual(out.get_uri(), "uri goes here")

    def testCHK(self):
        wq = self.wq("testCHK")
        wq.add_upload_chk("source_filename", "box1")
        wq.add_retain_uri_from_box("box1")
        wq.add_addpath("box1", ["home", "warner", "foo.txt"])
        wq.add_delete_box("box1")
        wq.add_unlink_uri("olduri")

        self.failUnlessEqual(wq.count_pending_steps(), 5)
        stepname, steptype, lines = wq.get_next_step()
        self.failUnlessEqual(steptype, "upload_chk")
        steps = wq.get_all_steps()
        self.failUnlessEqual(steps[0], ("upload_chk",
                                        ["source_filename", "box1"]))
        self.failUnlessEqual(steps[1], ("retain_uri_from_box",
                                        ["box1"]))
        self.failUnlessEqual(steps[2], ("addpath",
                                        ["box1", "home", "warner", "foo.txt"]))
        self.failUnlessEqual(steps[3], ("delete_box",
                                        ["box1"]))
        self.failUnlessEqual(steps[4], ("unlink_uri",
                                        ["olduri"]))

    def testCHK2(self):
        wq = self.wq("testCHK2")
        wq.add_upload_chk("source_filename", "box1")
        wq.add_retain_uri_from_box("box1")
        wq.add_addpath("box1", ["home", "warner", "foo.txt"])
        wq.add_delete_box("box1")
        wq.add_unlink_uri("olduri")

        # then this batch happens a bit later
        (f, tmpfilename) = wq.create_tempfile(".chkdir")
        f.write("some data")
        f.close()
        wq.add_upload_chk(os.path.join(wq.filesdir, tmpfilename), "box2")
        wq.add_delete_tempfile(tmpfilename)
        wq.add_retain_uri_from_box("box2")
        wq.add_delete_box("box2")
        wq.add_unlink_uri("oldchk")

        self.failUnlessEqual(wq.count_pending_steps(), 10)
        steps = wq.get_all_steps()

        self.failUnlessEqual(steps[0], ("upload_chk",
                                        ["source_filename", "box1"]))
        self.failUnlessEqual(steps[1], ("retain_uri_from_box",
                                        ["box1"]))
        self.failUnlessEqual(steps[2], ("addpath",
                                        ["box1", "home", "warner", "foo.txt"]))
        self.failUnlessEqual(steps[3],
                             ("upload_chk",
                              [os.path.join(wq.filesdir, tmpfilename),
                               "box2"]))
        self.failUnlessEqual(steps[4],
                             ("retain_uri_from_box", ["box2"]))
        self.failUnlessEqual(steps[5], ("delete_box",
                                        ["box1"]))
        self.failUnlessEqual(steps[6], ("unlink_uri",
                                        ["olduri"]))
        self.failUnlessEqual(steps[7],
                             ("delete_tempfile", [tmpfilename]))
        self.failUnlessEqual(steps[8], ("delete_box", ["box2"]))
        self.failUnlessEqual(steps[9], ("unlink_uri", ["oldchk"]))

    def testRun(self):
        wq = self.wq("testRun")
        wq.add_upload_chk("source_filename", "box1")
        wq.add_retain_uri_from_box("box1")
        wq.add_addpath("box1", ["home", "warner", "foo.txt"])
        wq.add_delete_box("box1")
        wq.add_unlink_uri("olduri")

        # this tempfile should be deleted after the last step completes
        (f, tmpfilename) = wq.create_tempfile(".dummy")
        tmpfilename = os.path.join(wq.filesdir, tmpfilename)
        f.write("stuff")
        f.close()
        self.failUnless(os.path.exists(tmpfilename))
        # likewise this unreferenced box should get deleted
        boxname = wq.create_boxname()
        wq.write_to_box(boxname, CHKFileNode().new("uri here"))
        boxfile = os.path.join(wq.boxesdir, boxname)
        self.failUnless(os.path.exists(boxfile))

        d = wq.flush()
        def _check(res):
            self.failUnlessEqual(len(wq.dispatched_steps), 5)
            self.failUnlessEqual(wq.dispatched_steps[0][0], "upload_chk")
            self.failIf(os.path.exists(tmpfilename))
            self.failIf(os.path.exists(boxfile))
        d.addCallback(_check)
        return d
