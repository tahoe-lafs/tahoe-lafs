#! /usr/bin/python

from twisted.trial import unittest

from allmydata.util import ring

class Ring(unittest.TestCase):
    def test_1(self):
        self.failUnlessEquals(ring.distance(8, 9), 1)
        self.failUnlessEquals(ring.distance(9, 8), 2**160-1)
        self.failUnlessEquals(ring.distance(2, 2**160-1), 2**160-3)
        self.failUnlessEquals(ring.distance(2**160-1, 2), 3)
        self.failUnlessEquals(ring.distance(0, 2**159), 2**159)
        self.failUnlessEquals(ring.distance(2**159, 0), 2**159)
        self.failUnlessEquals(ring.distance(2**159-1, 2**159+1), 2)
        self.failUnlessEquals(ring.distance(2**159-1, 1), 2**159+2)
        self.failUnlessEquals(ring.distance(2**159-1, 2**159-1), 0)
        self.failUnlessEquals(ring.distance(0, 0), 0)

