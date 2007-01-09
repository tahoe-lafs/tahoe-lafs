
import os
from twisted.trial import unittest
from allmydata import workqueue
from allmydata.util import idlib

class FakeWorkQueue(workqueue.WorkQueue):

    def __init__(self, basedir):
        workqueue.WorkQueue.__init__(self, basedir)
        self.dispatched_steps = []

    def dispatch_step(self, steptype, lines):
        self.dispatched_steps.append(steptype, lines)

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

