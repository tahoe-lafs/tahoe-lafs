
import os
from twisted.trial import unittest
from foolscap.eventual import fireEventually, flushEventualQueue

from allmydata import introducer_and_vdrive
from allmydata.util import testutil

class Basic(testutil.SignalMixin, unittest.TestCase):
    def test_loadable(self):
        basedir = "introducer_and_vdrive.Basic.test_loadable"
        os.mkdir(basedir)
        q = introducer_and_vdrive.IntroducerAndVdrive(basedir)
        d = fireEventually(None)
        d.addCallback(lambda res: q.startService())
        d.addCallback(lambda res: q.when_tub_ready())
        def _check_parameters(res):
            i = q.getServiceNamed("introducer")
            self.failUnlessEqual(i._encoding_parameters, (3, 7, 10))
        d.addCallback(_check_parameters)
        d.addCallback(lambda res: q.stopService())
        d.addCallback(flushEventualQueue)
        return d

    def test_set_parameters(self):
        basedir = "introducer_and_vdrive.Basic.test_set_parameters"
        os.mkdir(basedir)
        f = open(os.path.join(basedir, "encoding_parameters"), "w")
        f.write("25 75 100")
        f.close()
        q = introducer_and_vdrive.IntroducerAndVdrive(basedir)
        d = fireEventually(None)
        d.addCallback(lambda res: q.startService())
        d.addCallback(lambda res: q.when_tub_ready())
        def _check_parameters(res):
            i = q.getServiceNamed("introducer")
            self.failUnlessEqual(i._encoding_parameters, (25, 75, 100))
        d.addCallback(_check_parameters)
        d.addCallback(lambda res: q.stopService())
        d.addCallback(flushEventualQueue)
        return d

