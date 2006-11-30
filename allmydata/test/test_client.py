
from twisted.trial import unittest

from allmydata import client

class Basic(unittest.TestCase):
    def testLoadable(self):
        c = client.Client("yumyum", "")
        c.startService()
        return c.stopService()

