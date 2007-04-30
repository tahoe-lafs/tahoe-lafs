
from twisted.trial import unittest
from foolscap.eventual import flushEventualQueue

from allmydata import introducer_and_vdrive
from allmydata.util import testutil

class Basic(testutil.SignalMixin, unittest.TestCase):
    def test_loadable(self):
        q = introducer_and_vdrive.IntroducerAndVdrive()
        d = q.startService()
        d.addCallback(lambda res: q.stopService())
        d.addCallback(flushEventualQueue)
        return d

