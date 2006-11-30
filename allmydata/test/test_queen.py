
from twisted.trial import unittest

from allmydata import queen

class Basic(unittest.TestCase):
    def testLoadable(self):
        q = queen.Queen()
        q.startService()
        return q.stopService()
