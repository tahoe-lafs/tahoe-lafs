
from twisted.trial import unittest
from foolscap.eventual import flushEventualQueue

from allmydata import queen

class Basic(unittest.TestCase):
    def test_loadable(self):
        q = queen.Queen()
        d = q.startService()
        d.addCallback(lambda res: q.stopService())
        d.addCallback(flushEventualQueue)
        return d

