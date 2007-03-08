
from twisted.trial import unittest

from allmydata import queen

class Basic(unittest.TestCase):
    def test_loadable(self):
        q = queen.Queen()
        d = q.startService()
        d.addCallback(lambda res: q.stopService())
        return d

