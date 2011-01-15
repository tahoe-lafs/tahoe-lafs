#!/usr/bin/env python

from twisted.trial import unittest

class T(unittest.TestCase):
    def test_version(self):
        import fakedependency
        self.failUnlessEqual(fakedependency.__version__, '1.0.0')
