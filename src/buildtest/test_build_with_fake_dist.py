#!/usr/bin/env python

from twisted.trial import unittest

class T(unittest.TestCase):
    def test_version(self):
        import pycryptopp
        if pycryptopp.__version__ != '0.5.24':
            raise unittest.SkipTest("We can't tell if this worked because this system has a different version of pycryptopp already installed. See comment in misc/build_helpers/test-with-fake-dists.py for details.")
        # If you tried to build 9.9.99 then you would have gotten an exception and stopped before you even ran this test, so I guess you succeeded!
