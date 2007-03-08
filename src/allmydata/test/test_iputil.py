
from twisted.trial import unittest
from allmydata.util.iputil import get_local_addresses

class ListAddresses(unittest.TestCase):
    def test_list(self):
        d = get_local_addresses()
        def _check(addresses):
            self.failUnless(len(addresses) >= 1) # always have localhost
            self.failUnless("127.0.0.1" in addresses)
            print addresses
        d.addCallbacks(_check)
        return d

