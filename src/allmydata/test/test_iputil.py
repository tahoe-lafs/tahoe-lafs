
from twisted.trial import unittest
from allmydata.util import iputil

class ListAddresses(unittest.TestCase):
    def test_list_async(self):
        d = iputil.get_local_addresses_async()
        def _check(addresses):
            self.failUnless(len(addresses) >= 1) # always have localhost
            self.failUnless("127.0.0.1" in addresses)
        d.addCallbacks(_check)
        return d

    def test_list(self):
        addresses = iputil.get_local_addresses()
        self.failUnless(len(addresses) >= 1) # always have localhost
        self.failUnless("127.0.0.1" in addresses)

