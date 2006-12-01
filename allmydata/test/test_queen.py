
from twisted.trial import unittest

from allmydata import queen

class Basic(unittest.TestCase):
    def test_loadable(self):
        q = queen.Queen()
        q.startService()
        return q.stopService()
