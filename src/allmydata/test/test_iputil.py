#!/usr/bin/env python

from allmydata.util import iputil, testutil

from twisted.trial import unittest
import re, sys

DOTTED_QUAD_RE=re.compile("^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")

class ListAddresses(unittest.TestCase):
    def test_get_local_ip_for(self):
        addr = iputil.get_local_ip_for('127.0.0.1')
        self.failUnless(DOTTED_QUAD_RE.match(addr))

    def test_list_async(self):
        d = iputil.get_local_addresses_async()
        def _check(addresses):
            self.failUnless(len(addresses) >= 1) # always have localhost
            self.failUnless("127.0.0.1" in addresses, addresses)
        d.addCallbacks(_check)
        return d
    test_list_async.timeout=2
